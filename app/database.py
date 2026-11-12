"""Gold, Silver, and Bronze business tables are read-only here -- never
updated, never written to directly. Writes are limited to two app-owned
approvals tables (see ensure_approval_tables below): an approved-orders
snapshot and an append-only review-events audit log."""
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd

from logic import row_hash, line_identifier, SNAPSHOT_FIELDS

CATALOG = 'databricks-esteelauder'
GOLD = f'`{CATALOG}`.gold.gold_sales_orders'
BRONZE = f'`{CATALOG}`.bronze.bronze_documents'
APPROVED_ORDERS = f'`{CATALOG}`.gold.approved_sales_orders'
REVIEW_EVENTS = f'`{CATALOG}`.gold.review_events'
XREF_CORRECTIONS = f'`{CATALOG}`.gold.xref_corrections'
ORDER_KEYS = ['source_doc_id', 'po_number', 'source_order_index']


class StaleGoldError(RuntimeError):
    """Raised when live Gold no longer matches what the reviewer saw, so an
    approval is refused instead of silently overwriting newer data."""

@contextmanager
def connection():
    from databricks import sql
    from databricks.sdk.core import Config
    cfg = Config()
    warehouse = os.environ.get('DATABRICKS_WAREHOUSE_ID')
    if not warehouse:
        raise RuntimeError('Attach a SQL warehouse resource with key sql-warehouse before deployment.')
    hostname = urlparse(cfg.host if '://' in cfg.host else 'https://' + cfg.host).netloc
    with sql.connect(server_hostname=hostname,
                     http_path=f'/sql/1.0/warehouses/{warehouse}',
                     credentials_provider=lambda: cfg.authenticate) as conn:
        yield conn

def query(statement, parameters=None):
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(statement, parameters or [])
        return pd.DataFrame.from_records(cursor.fetchall(), columns=[c[0] for c in cursor.description])

def load_lines():
    # Detect growth instead of silently presenting a truncated portfolio.
    df = query(f'SELECT * FROM {GOLD} LIMIT 20001')
    if len(df) > 20000:
        raise RuntimeError('More than 20,000 lines: server-side pagination is required before using this app.')
    return df

def source_files(doc_id):
    return query(f'''SELECT file_name, file_path, file_extension,
        CASE WHEN length(raw_content) <= 16000000 THEN raw_content ELSE NULL END AS content
        FROM {BRONZE} WHERE doc_id = ? LIMIT 20''', [doc_id])


# --- Approvals: an app-owned schema, separate from pipeline-managed Gold. ---

def ensure_approval_tables():
    """Creates the three app-owned tables if missing. Idempotent; never touches
    gold_sales_orders itself. A pipeline engineer can instead run this once
    and grant the app identity MODIFY on just these three tables."""
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS {APPROVED_ORDERS} (
            gold_line_id STRING NOT NULL,
            line_id_is_fallback BOOLEAN,
            source_doc_id STRING,
            po_number STRING,
            source_order_index DOUBLE,
            line_number DOUBLE,
            item_code STRING,
            item_description STRING,
            material_id STRING,
            master_customer_name STRING,
            master_ship_to_name STRING,
            quantity_each DOUBLE,
            expected_net_price DOUBLE,
            currency STRING,
            channel STRING,
            proposed_atp_outcome STRING,
            proposed_ship_quantity DOUBLE,
            overall_confidence DOUBLE,
            quality_status STRING,
            source_row_hash STRING,
            approval_type STRING NOT NULL,
            approved_by STRING NOT NULL,
            approved_at TIMESTAMP NOT NULL
        ) USING DELTA''')
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS {REVIEW_EVENTS} (
            event_id STRING NOT NULL,
            event_time TIMESTAMP NOT NULL,
            actor STRING NOT NULL,
            action STRING NOT NULL,
            gold_line_id STRING,
            source_doc_id STRING,
            po_number STRING,
            source_order_index DOUBLE,
            line_number DOUBLE,
            field_name STRING,
            before_value STRING,
            after_value STRING,
            reason STRING,
            source_row_hash STRING
        ) USING DELTA''')
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS {XREF_CORRECTIONS} (
            correction_id STRING NOT NULL,
            confirmed_at TIMESTAMP NOT NULL,
            confirmed_by STRING NOT NULL,
            source_doc_id STRING,
            po_number STRING,
            channel STRING,
            retailer_item_id STRING,
            item_description STRING,
            previous_material_id STRING,
            confirmed_material_id STRING,
            reason STRING
        ) USING DELTA''')

def load_approved():
    return query(f'SELECT * FROM {APPROVED_ORDERS}')

def load_rejections():
    """Latest REJECT event per order, if any -- review_events is append-only,
    so a prior reject is never overwritten, just superseded for display."""
    return query(f'''SELECT source_doc_id, po_number, source_order_index, actor, event_time, reason
        FROM (SELECT *, ROW_NUMBER() OVER (
                PARTITION BY source_doc_id, po_number, source_order_index
                ORDER BY event_time DESC) AS rn
            FROM {REVIEW_EVENTS} WHERE action = 'REJECT') WHERE rn = 1''')

def record_xref_correction(actor, source_doc_id, po_number, channel, retailer_item_id,
        item_description, previous_material_id, confirmed_material_id, reason):
    """Appends a reviewer-confirmed item-code -> SKU mapping. This never writes
    to the pipeline's own retailer_item_xref table (unknown to this app); it's a
    durable, auditable feed for a pipeline engineer to merge into it by hand."""
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(f'''INSERT INTO {XREF_CORRECTIONS}
            (correction_id, confirmed_at, confirmed_by, source_doc_id, po_number, channel,
             retailer_item_id, item_description, previous_material_id, confirmed_material_id, reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            [str(uuid.uuid4()), datetime.now(timezone.utc), actor, source_doc_id, po_number, channel,
             retailer_item_id, item_description, previous_material_id, confirmed_material_id, reason])

def gold_rows_for_order(order):
    """Re-reads an order's lines straight from Gold, bypassing the session
    snapshot, so an approval can be checked for staleness before it is written."""
    conditions, params = [], []
    for col in ORDER_KEYS:
        value = order[col]
        if pd.isna(value):
            conditions.append(f'{col} IS NULL')
        else:
            conditions.append(f'{col} = ?')
            params.append(value)
    return query(f'SELECT * FROM {GOLD} WHERE ' + ' AND '.join(conditions), params)

def check_not_stale(session_rows, live_rows):
    session_hashes = sorted(row_hash(r) for r in session_rows)
    live_hashes = sorted(row_hash(r) for r in live_rows.to_dict('records'))
    if session_hashes != live_hashes:
        raise StaleGoldError('Gold data for this order changed since it was loaded. Refresh and try again.')

def _approved_values(row, approval_type, actor, approved_at):
    gold_line_id, is_fallback = line_identifier(row)
    return [
        gold_line_id, is_fallback,
        row.get('source_doc_id'), row.get('po_number'), row.get('source_order_index'), row.get('line_number'),
        row.get('item_code'), row.get('item_description'), row.get('material_id'),
        row.get('master_customer_name'), row.get('master_ship_to_name'),
        row.get('quantity_each'), row.get('expected_net_price'), row.get('currency'), row.get('channel'),
        row.get('proposed_atp_outcome'), row.get('proposed_ship_quantity'), row.get('overall_confidence'),
        row.get('quality_status'), row_hash(row, SNAPSHOT_FIELDS), approval_type, actor, approved_at,
    ]

_MERGE_APPROVED = f'''MERGE INTO {APPROVED_ORDERS} AS t
USING (SELECT ? AS gold_line_id, ? AS line_id_is_fallback, ? AS source_doc_id, ? AS po_number,
    ? AS source_order_index, ? AS line_number, ? AS item_code, ? AS item_description, ? AS material_id,
    ? AS master_customer_name, ? AS master_ship_to_name, ? AS quantity_each, ? AS expected_net_price,
    ? AS currency, ? AS channel, ? AS proposed_atp_outcome, ? AS proposed_ship_quantity,
    ? AS overall_confidence, ? AS quality_status, ? AS source_row_hash, ? AS approval_type,
    ? AS approved_by, ? AS approved_at) AS s
ON t.gold_line_id = s.gold_line_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *'''

_INSERT_EVENT = f'''INSERT INTO {REVIEW_EVENTS}
(event_id, event_time, actor, action, gold_line_id, source_doc_id, po_number, source_order_index,
 line_number, field_name, before_value, after_value, reason, source_row_hash)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''

def record_decision(line_rows, actor, approval_type, events):
    """Upserts one approved-orders row per line (keyed on a durable
    gold_line_id, so re-approving after an edit updates rather than
    duplicates) and appends every event in `events` -- field edits plus the
    approve/auto-approve decision itself -- to the audit log. One connection
    for the whole order, so a partial failure can't leave a half-written
    decision."""
    now = datetime.now(timezone.utc)
    with connection() as conn, conn.cursor() as cursor:
        for row in line_rows:
            cursor.execute(_MERGE_APPROVED, _approved_values(row, approval_type, actor, now))
        for event in events:
            cursor.execute(_INSERT_EVENT, [
                str(uuid.uuid4()), now, actor, event['action'], event.get('gold_line_id'),
                event.get('source_doc_id'), event.get('po_number'), event.get('source_order_index'),
                event.get('line_number'), event.get('field_name'), event.get('before_value'),
                event.get('after_value'), event.get('reason'), event.get('source_row_hash'),
            ])
