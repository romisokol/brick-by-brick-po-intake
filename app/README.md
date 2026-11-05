# Maison Solenne review app — first release

A read-only Streamlit app for the Gold schema exported November 5. Includes an overview, order-level risk queue, and original-document evidence next to proposed order lines. No pipeline source is moved, replaced, or executed. No writes to Gold, Silver, Bronze, or reference data occur.

## Upload to your fork

Upload the `app` directory at the root of `romisokol/brick-by-brick-po-intake` (GitHub: Add file → Upload files). Commit the new files. Then pull your fork in your Databricks Git folder, or import the app directory into a separate workspace folder. The app source directory is `app`, not the pipeline folder.

## Deploy in Databricks

1. Create a new Databricks App and select this `app/` folder as source.
2. Add your existing SQL warehouse as an app resource. Its resource key must be `sql-warehouse`, matching app.yaml.
3. Grant the app service principal CAN USE on that warehouse, USE CATALOG on `databricks-esteelauder`, USE SCHEMA on `gold`, and SELECT on `gold.gold_sales_orders`.
4. For source previews, also grant USE SCHEMA on `bronze` and SELECT on `bronze.bronze_documents`. Raw documents contain buyer details; restrict the app to authorized team members until regional access rules are implemented.
5. Deploy and open the app. A clear connection error is shown if configuration or permissions are missing. There is no silent demo-data fallback.
6. Check app line count against SQL Editor, then select a known multi-PO document and a PDF/image/transcript. Confirm customer, line count, and source evidence match.

The app uses its own Databricks OAuth identity. Do not paste tokens in code or Git. Deployment details may vary by workspace; see https://docs.databricks.com/aws/en/dev-tools/databricks-apps/tutorial-streamlit and https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-runtime.

## Local run

From app/: `python -m pip install -r requirements.txt`, configure Databricks SDK authentication plus DATABRICKS_WAREHOUSE_ID, then `streamlit run app.py`. Local testing needs authorized workspace access. The app has not been deployed or tested against the live workspace from ChatGPT.

## Honest scope / next implementation

- Approve, edit, reject, audit, accepted inventory overrides, SKU suggestions, approved-order storage, and mock ERP export are NOT implemented in this read-only milestone. Buttons are disabled explicitly.
- Build separate append-only review events and approved snapshots before enabling writes. Include authenticated human identity, before/after, reason, source version, a collision-safe line ID, idempotency, and concurrency protection. Never update pipeline-managed Gold directly.
- The supplied Gold key can be incomplete (null line numbers). Do not use `(source_doc_id, po_number, line_number)` as a unique edit target without a durable upstream line identifier.
- Queue ordering: unknown/lowest confidence first, then currency and descending known value. Currency totals are not mixed or silently converted. Priced-line coverage is displayed.
- Ready order share is not measured STP or accuracy. Ground truth is never used as production input. No false-confidence or accuracy metric is invented.
- No region isolation or auditor/reviewer roles are implemented yet. This is a restricted team preview, not GOV-2–4 completion.
- Sources are read through Bronze bytes; formats PDF/image/text can preview; XLSX downloads only. Email attachment rows show their parent email, explicitly labeled. Source files above 16 MB do not preview. Document ID selection is parameterized.
- Gold is loaded to a session-local snapshot (up to 20,000 lines). Refresh from Gold loads new data; no global cross-user data cache. Larger data requires server-side pagination.
- Complexity metrics, evaluation feedback, SLA, ingestion cost, and maintainable retailer-rule auditing remain separate work.

## Requirements covered in this milestone

Partial HIL-1 (queue), partial HIL-2 (read-only evidence), partial ACC-3 (volume, quality, reasons, inventory). The full mentor requirements remain in ../REQUIREMENTS.md. This app is not a claim of full Tier 1 or Tier 2 completion.
