"""Read-only access. No pipeline or business table writes."""
import os
from contextlib import contextmanager
from urllib.parse import urlparse

CATALOG = 'databricks-esteelauder'
GOLD = f'`{CATALOG}`.gold.gold_sales_orders'
BRONZE = f'`{CATALOG}`.bronze.bronze_documents'

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
    import pandas as pd
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
