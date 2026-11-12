"""Portfolio metrics are computed from Gold, never from the answer key."""
import difflib
import hashlib
import pandas as pd

ORDER_KEYS = ['source_doc_id', 'po_number', 'source_order_index']

# Fields a reviewer may correct before approving. Everything else is taken as extracted.
EDITABLE_FIELDS = ['material_id', 'quantity_each', 'expected_net_price',
    'master_customer_name', 'master_ship_to_name']

# The inventory proposal is editable too, but logged as its own event type
# (INVENTORY_OVERRIDE, not EDIT) so an override is distinguishable in the audit trail.
INVENTORY_FIELDS = ['proposed_atp_outcome', 'proposed_ship_quantity']

# Captured in the approved-orders snapshot; also the basis for detecting Gold drift.
SNAPSHOT_FIELDS = ['source_doc_id', 'po_number', 'source_order_index', 'line_number',
    'item_code', 'item_description', 'material_id', 'master_customer_name', 'master_ship_to_name',
    'quantity_each', 'expected_net_price', 'currency', 'channel', 'proposed_atp_outcome',
    'proposed_ship_quantity', 'overall_confidence', 'quality_status']

def orders_from_lines(lines):
    work = lines.copy()
    for col in ORDER_KEYS:
        if col not in work:
            work[col] = None
    work['review_line'] = work['needs_review'].fillna(True).astype(bool)
    work['failed_line'] = work['quality_status'].eq('FAILED')
    work['line_value'] = pd.to_numeric(work['quantity_each'], errors='coerce') * pd.to_numeric(work['expected_net_price'], errors='coerce')
    work['confidence'] = pd.to_numeric(work['overall_confidence'], errors='coerce')
    work['missing_confidence'] = work['confidence'].isna()
    result = work.groupby(ORDER_KEYS, dropna=False).agg(
        customer=('master_customer_name', 'first'), channel=('channel', 'first'),
        currency=('currency', 'first'), lines=('source_doc_id', 'size'),
        confidence=('confidence', 'min'), unknown_confidence=('missing_confidence', 'any'),
        needs_review=('review_line', 'any'), failed=('failed_line', 'any'),
        known_value=('line_value', lambda x: x.sum(min_count=1)),
        priced_lines=('line_value', 'count'),
    ).reset_index()
    result.loc[result['unknown_confidence'], 'confidence'] = float('nan')
    result['status'] = result.apply(lambda r: 'FAILED' if r['failed'] else 'REVIEW' if r['needs_review'] else 'READY', axis=1)
    # Value sorting is only meaningful within a currency. No implicit FX conversion.
    return result.sort_values(['confidence', 'currency', 'known_value'], ascending=[True, True, False], na_position='first').reset_index(drop=True)

def select_order(lines, order):
    mask = pd.Series(True, index=lines.index)
    for col in ORDER_KEYS:
        value = order[col]
        mask &= lines[col].isna() if pd.isna(value) else lines[col].eq(value)
    return lines.loc[mask].copy()


def _cell(row, column):
    value = row.get(column)
    return '' if value is None or pd.isna(value) else str(value)

def row_hash(row, columns=SNAPSHOT_FIELDS):
    return hashlib.sha256('|'.join(_cell(row, c) for c in columns).encode('utf-8')).hexdigest()

def line_identifier(row):
    """A durable id for one Gold line. Falls back to a content hash when Gold
    carries no upstream line_id; the fallback is flagged because it cannot
    survive a re-extraction of the source document (see app/README.md)."""
    line_id = row.get('line_id')
    if line_id is not None and not pd.isna(line_id) and str(line_id).strip():
        return str(line_id), False
    basis = ['source_doc_id', 'po_number', 'source_order_index', 'line_number',
        'item_code', 'item_description', 'quantity_as_written']
    return hashlib.sha256('|'.join(_cell(row, c) for c in basis).encode('utf-8')).hexdigest(), True

def diff_editable_fields(original_row, edited_row, fields=EDITABLE_FIELDS):
    """Field-level before/after for the given fields (editable fields by default,
    or INVENTORY_FIELDS to isolate inventory overrides into their own event type)."""
    changes = []
    for field in fields:
        before, after = _cell(original_row, field), _cell(edited_row, field)
        if before != after:
            changes.append((field, before, after))
    return changes

def suggest_skus(lines, line_row, limit=5):
    """Candidate SKUs for a line the pipeline couldn't confidently match, drawn
    from material_ids the pipeline already resolved elsewhere in this session's
    Gold snapshot (not the full product catalog, which this app has no access
    to) and ranked by text similarity to this line's source item code/description."""
    resolved = lines[lines['material_id'].notna() & lines['quality_status'].ne('FAILED')]
    catalog = (resolved[['material_id', 'item_description']].dropna(subset=['material_id'])
        .drop_duplicates(subset=['material_id']))
    current_material = line_row.get('material_id')
    if pd.notna(current_material):
        catalog = catalog[catalog['material_id'].ne(current_material)]
    needle = ' '.join(str(line_row.get(c) or '') for c in ('item_code', 'item_description')).strip().lower()
    if not needle or catalog.empty:
        return []
    scored = [(difflib.SequenceMatcher(None, needle, f'{row.material_id} {row.item_description}'.lower()).ratio(), row)
        for row in catalog.itertuples()]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [(row.material_id, row.item_description, score) for score, row in scored[:limit] if score > 0.3]

def build_erp_payload(order, line_records):
    """A mock ERP-ready sales order payload for an approved order: realistic
    enough to show what would cross the outbound interface, not a real integration."""
    return {
        'order_header': {
            'source_doc_id': order.get('source_doc_id'),
            'po_number': order.get('po_number'),
            'sold_to': order.get('master_customer_name') or order.get('customer'),
            'ship_to': line_records[0].get('master_ship_to_name') if line_records else None,
            'currency': order.get('currency'),
            'channel': order.get('channel'),
            'order_status': order.get('status'),
        },
        'order_lines': [{
            'line_number': r.get('line_number'),
            'material_id': r.get('material_id'),
            'item_description': r.get('item_description'),
            'quantity': r.get('quantity_each'),
            'unit_price': r.get('expected_net_price'),
            'currency': r.get('currency'),
            'availability': r.get('proposed_atp_outcome'),
            'ship_quantity': r.get('proposed_ship_quantity'),
        } for r in line_records],
    }

def ready_order_lines(lines, orders):
    """Every line belonging to a READY order: the set eligible for automatic inclusion."""
    ready_keys = orders.loc[orders.status.eq('READY'), ORDER_KEYS]
    return lines.merge(ready_keys, on=ORDER_KEYS, how='inner')
