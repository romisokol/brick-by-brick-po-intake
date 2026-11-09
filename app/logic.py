"""Portfolio metrics are computed from Gold, never from the answer key."""
import hashlib
import pandas as pd

ORDER_KEYS = ['source_doc_id', 'po_number', 'source_order_index']

# Fields a reviewer may correct before approving. Everything else is taken as extracted.
EDITABLE_FIELDS = ['material_id', 'quantity_each', 'expected_net_price',
    'master_customer_name', 'master_ship_to_name']

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

def diff_editable_fields(original_row, edited_row):
    """Field-level before/after for the fields a reviewer is allowed to correct."""
    changes = []
    for field in EDITABLE_FIELDS:
        before, after = _cell(original_row, field), _cell(edited_row, field)
        if before != after:
            changes.append((field, before, after))
    return changes

def ready_order_lines(lines, orders):
    """Every line belonging to a READY order: the set eligible for automatic inclusion."""
    ready_keys = orders.loc[orders.status.eq('READY'), ORDER_KEYS]
    return lines.merge(ready_keys, on=ORDER_KEYS, how='inner')
