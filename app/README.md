# Maison Solenne review app

A Streamlit app for the Gold schema exported November 5. Includes an order-level risk queue, original-document evidence next to proposed order lines, and an approval workflow. No pipeline source is moved, replaced, or executed, and Gold, Silver, Bronze, and reference data are never written to directly.

Approvals write only to three new, app-owned tables: `gold.approved_sales_orders` (a snapshot of approved/auto-included lines, upserted by a durable `gold_line_id`), `gold.review_events` (an append-only audit log of every edit, inventory override, approve/auto-approve, and reject decision, with who, when, before → after, and the reason), and `gold.xref_corrections` (an append-only log of reviewer-confirmed item-code → SKU corrections, for a pipeline engineer to merge into `retailer_item_xref` by hand). See "Approval workflow" below.

## Upload to your fork

Upload the `app` directory at the root of `romisokol/brick-by-brick-po-intake` (GitHub: Add file → Upload files). Commit the new files. Then pull your fork in your Databricks Git folder, or import the app directory into a separate workspace folder. The app source directory is `app`, not the pipeline folder.

## Deploy in Databricks

1. Create a new Databricks App and select this `app/` folder as source.
2. Add your existing SQL warehouse as an app resource. Its resource key must be `sql-warehouse`, matching app.yaml.
3. Grant the app service principal CAN USE on that warehouse, USE CATALOG on `databricks-esteelauder`, USE SCHEMA on `gold`, and SELECT on `gold.gold_sales_orders`.
4. For source previews, also grant USE SCHEMA on `bronze` and SELECT on `bronze.bronze_documents`. Raw documents contain buyer details; restrict the app to authorized team members until regional access rules are implemented.
5. For approvals, the app identity also needs CREATE TABLE on `gold` the first time it runs (it creates `approved_sales_orders`, `review_events`, and `xref_corrections` if missing), or have a pipeline engineer create those three tables ahead of time and grant the app identity MODIFY on just those three. Either way, the app identity never gets write access to `gold_sales_orders` itself.
6. Enable "User authorization" on the app so Databricks forwards the signed-in reviewer's identity (`X-Forwarded-Email`) to the app. Without it, approvals are blocked rather than attributed to the app's own service principal.
7. Deploy and open the app. A clear connection error is shown if configuration or permissions are missing. There is no silent demo-data fallback.
8. Check app line count against SQL Editor, then select a known multi-PO document and a PDF/image/transcript. Confirm customer, line count, and source evidence match.

The app uses its own Databricks OAuth identity. Do not paste tokens in code or Git. Deployment details may vary by workspace; see https://docs.databricks.com/aws/en/dev-tools/databricks-apps/tutorial-streamlit and https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-runtime.

## Local run

From app/: `python -m pip install -r requirements.txt`, configure Databricks SDK authentication plus DATABRICKS_WAREHOUSE_ID, then `streamlit run app.py`. Local testing needs authorized workspace access. A local run has no `X-Forwarded-Email` header, so reading Gold still works but Approve is blocked by design — test approvals in the deployed app.

## Approval workflow

- **Approve:** on any order (Needs attention or Ready), the "Approve this order" panel lets a reviewer edit key fields (SKU match, quantity, price, resolved customer/ship-to, inventory proposal), add a reason, and approve. This upserts the order's lines into `approved_sales_orders` and appends EDIT / INVENTORY_OVERRIDE + APPROVE events to `review_events`.
- **Reject:** a separate "Reject order" form logs a REJECT event to `review_events` with a required reason. It does not remove or retract any lines already in `approved_sales_orders` (e.g. from auto-include) — rejection is an audit statement, not an undo. The order's rejection banner shows the latest REJECT event, if any.
- **Suggested SKU matches:** for lines missing a SKU match or not READY, the panel suggests up to 5 candidate SKUs drawn from material_ids already resolved elsewhere in the session's loaded Gold snapshot (not the full product catalog, which the app has no access to), ranked by text similarity to the line's source item code/description. Clicking a suggestion pre-fills it into the editable table; the reviewer can still change it before approving.
- **Inventory-override acceptance:** `proposed_atp_outcome` and `proposed_ship_quantity` are editable alongside the other fields, and a change to either is logged as its own `INVENTORY_OVERRIDE` event (not a generic EDIT) so it's distinguishable in the audit trail.
- **Cross-reference feedback (HIL-6):** when an approval changes a line's `material_id`, the before/after mapping (keyed by the retailer's own item code) is appended to `xref_corrections` with the reviewer and reason. This never writes to the pipeline's own `retailer_item_xref` table directly — it's a durable, auditable feed for a pipeline engineer to review and merge in.
- **Mock ERP export:** once an order has at least one approved line, a "Download mock ERP export" button serializes its approved lines into a plausible ERP order payload (JSON: header + lines). This is illustrative only — there is no real outbound integration.
- **Auto-include:** orders that are already READY (no FAILED or REVIEW lines) are included into `approved_sales_orders` automatically on load, attributed to `system:auto-ready-rule` with `approval_type = 'AUTO'`, per the mentor's suggestion. A reviewer can still open a READY order and re-approve manually after a correction.
- **Identity:** `approved_by` / the audit log's `actor` come only from the `X-Forwarded-Email` header Databricks forwards for the signed-in user. There is no fallback to the app's service identity, so approvals, rejections, and corrections from a local `streamlit run` (no forwarded header) are blocked.
- **Staleness:** before writing, the app re-reads the order straight from Gold and compares it against what was on screen. If Gold changed underneath the reviewer, the approval is refused with a message to refresh, rather than silently overwritten.
- **Line identity:** Gold's `(source_doc_id, po_number, line_number)` key can be incomplete (null line numbers), so `approved_sales_orders`/`review_events` key off a durable `gold_line_id` (Gold's own `line_id` if present, else a content hash, flagged `line_id_is_fallback`). A fallback id will not survive a re-extraction of the source document — ask the pipeline team for a durable `line_id` column in Gold.
- **Not yet implemented:** roles / region isolation, server-side pagination beyond the 20,000-line cap, and a true (non-mock) ERP integration.
- Queue ordering: unknown/lowest confidence first, then currency and descending known value. Currency totals are not mixed or silently converted. Priced-line coverage is displayed.
- Ready order share is not measured STP or accuracy. Ground truth is never used as production input. No false-confidence or accuracy metric is invented.
- No region isolation or auditor/reviewer roles are implemented yet. This is a restricted team preview, not GOV-2–4 completion.
- Sources are read through Bronze bytes; formats PDF/image/text can preview; XLSX downloads only. Email attachment rows show their parent email, explicitly labeled. Source files above 16 MB do not preview. Document ID selection is parameterized.
- Gold is loaded to a session-local snapshot (up to 20,000 lines). Refresh from Gold loads new data; no global cross-user data cache. Larger data requires server-side pagination.
- Complexity metrics, evaluation feedback, SLA, ingestion cost, and maintainable retailer-rule auditing remain separate work.

## Requirements covered in this milestone

Partial HIL-1 (queue), partial HIL-2 (evidence, now editable), HIL-3 (approve, edit, reject, SKU-suggestion picker, inventory-override acceptance), partial HIL-4 (audit trail: who/when/before/after/reason; append-only), HIL-5 (approved-orders table + mock ERP export), partial HIL-6 (xref corrections are logged for a pipeline engineer to merge; not yet merged automatically). The full mentor requirements remain in ../REQUIREMENTS.md. This app is not a claim of full Tier 1 or Tier 2 completion — in particular, ACC-3 (the CS-lead dashboard: volume/channel/STP/backlog/reasons/inventory) has no app page since Overview was removed, and roles/region isolation (GOV-2–4) and pagination beyond 20,000 lines remain unbuilt.
