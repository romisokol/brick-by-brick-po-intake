"""Portfolio metrics are computed from Gold, never from the answer key."""
import pandas as pd

ORDER_KEYS = ['source_doc_id', 'po_number', 'source_order_index']

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
