"""Local-only fixtures for reviewing the app without Databricks credentials.

The fixture is built from the repository's synthetic ground-truth files.  It
matches the dashboard snapshot exactly: 193 POs and 2,717 lines, comprising
1,637 READY, 1,000 REVIEW, and 80 FAILED lines.
"""
from pathlib import Path

import pandas as pd


DATA_ROOT = Path(__file__).resolve().parent / "demo_data"


def _subset_sum(values, target, excluded=frozenset()):
    choices = {0: []}
    for index, value in enumerate(values):
        if index in excluded:
            continue
        for subtotal, selected in list(choices.items())[::-1]:
            candidate = subtotal + int(value)
            if candidate <= target and candidate not in choices:
                choices[candidate] = selected + [index]
        if target in choices:
            return choices[target]
    raise RuntimeError(f"Could not construct local demo partition totaling {target} lines.")


def load_local_lines():
    headers = pd.read_csv(DATA_ROOT / "ground_truth" / "headers.csv")
    manifest = pd.read_csv(DATA_ROOT / "ground_truth" / "manifest.csv")
    raw = pd.read_csv(DATA_ROOT / "ground_truth" / "lines.csv").iloc[:-2].copy()

    # One document may contain multiple POs, so these are review cases. Use a
    # stable string for blank PO numbers: NaN equality/hash behavior differs
    # across pandas runtimes and previously moved 14 lines between statuses in
    # Databricks even though the same fixture was correct locally.
    raw["_demo_po_key"] = raw["po_number"].fillna("__MISSING_PO__").astype(str)
    groups = raw.groupby(["doc_id", "_demo_po_key"]).size().reset_index(name="count")
    failed_indexes = _subset_sum(groups["count"].tolist(), 80)
    ready_indexes = _subset_sum(groups["count"].tolist(), 1637, frozenset(failed_indexes))
    failed_keys = set(map(tuple, groups.loc[failed_indexes, ["doc_id", "_demo_po_key"]].to_records(index=False)))
    ready_keys = set(map(tuple, groups.loc[ready_indexes, ["doc_id", "_demo_po_key"]].to_records(index=False)))

    def status_for(row):
        key = (row.doc_id, row._demo_po_key)
        return "FAILED" if key in failed_keys else "READY" if key in ready_keys else "REVIEW"

    raw["quality_status"] = raw.apply(status_for, axis=1)
    raw.drop(columns=["_demo_po_key"], inplace=True)

    # Stable presentation cases for the live demo. The underlying
    # quality_status stays unchanged so the portfolio still reconciles to
    # the dashboard (1,637 / 2,717 READY lines).
    raw["display_quality_status"] = raw["quality_status"]
    curated_patterns = {
        ("DOC-00003", "4584495485"): ["READY"],
        ("DOC-00099", "ANT/PO/26/0736"): ["READY"] * 4 + ["REVIEW"] * 9,
        ("DOC-00008", "4593430858"): ["FAILED"] * 5 + ["REVIEW"] * 6 + ["READY"] * 7,
    }
    for (doc_id, po_number), pattern in curated_patterns.items():
        indexes = raw.index[
            raw["doc_id"].eq(doc_id) & raw["po_number"].astype(str).eq(po_number)
        ].tolist()
        if indexes:
            if len(pattern) == 1:
                pattern = pattern * len(indexes)
            if len(pattern) != len(indexes):
                raise RuntimeError(
                    f"Curated demo case {doc_id} / {po_number} changed line count."
                )
            raw.loc[indexes, "display_quality_status"] = pattern
    raw = raw.merge(headers, on=["doc_id", "po_number"], how="left")
    raw = raw.merge(manifest[["doc_id", "channel", "relative_path"]], on="doc_id", how="left")
    raw["currency"] = raw["currency_x"].fillna(raw["currency_y"])
    raw["source_doc_id"] = raw["doc_id"]
    raw["source_order_index"] = raw.groupby("doc_id", dropna=False)["po_number"].transform(
        lambda values: pd.factorize(values, sort=False)[0]
    )
    raw["line_number"] = raw["line_no"]
    raw["line_id"] = raw.apply(lambda row: f"{row.doc_id}:{row.po_number}:{row.line_no}", axis=1)
    raw["needs_review"] = raw["quality_status"].ne("READY")
    raw["item_code"] = raw["item_code_shown"]
    raw["item_description"] = raw["raw_item_text"]
    raw["quantity_as_written"] = raw["qty_as_written"]
    raw["quantity_each"] = raw["qty_each"]
    raw["expected_net_price"] = raw["expected_net_price"]
    raw["master_customer_name"] = raw["customer_name_as_written"]
    raw["master_ship_to_name"] = raw["ship_to_as_written"]
    raw["sold_to_name_raw"] = raw["customer_name_as_written"]
    raw["ship_to_name_raw"] = raw["ship_to_as_written"]
    raw["proposed_atp_outcome"] = raw["expected_atp_outcome"]
    raw["proposed_ship_quantity"] = raw["expected_ship_qty"]
    raw["source_pointer"] = raw.apply(lambda row: f"{row.relative_path}, line {row.line_no}", axis=1)

    # Leave a small number of problem fields genuinely blank in the curated
    # walkthroughs.  The source values remain visible in the original document,
    # so the reviewer has a concrete correction to make in the approval editor.
    # These presentation-only gaps do not alter quality_status or portfolio KPIs.
    curated_blanks = {
        ("DOC-00099", "ANT/PO/26/0736"): {
            5: ("material_id",),
            7: ("expected_net_price",),
            9: ("quantity_each",),
            11: ("master_ship_to_name",),
        },
        ("DOC-00008", "4593430858"): {
            1: ("material_id",),
            2: ("expected_net_price",),
            3: ("quantity_each",),
            4: ("master_ship_to_name",),
            7: ("material_id", "expected_net_price"),
            8: ("quantity_each",),
        },
    }
    for (doc_id, po_number), line_gaps in curated_blanks.items():
        order_mask = raw["doc_id"].eq(doc_id) & raw["po_number"].astype(str).eq(po_number)
        for line_number, fields in line_gaps.items():
            line_mask = order_mask & raw["line_number"].eq(line_number)
            for field in fields:
                raw.loc[line_mask, field] = None

    raw["review_reasons"] = raw["display_quality_status"].map({
        "READY": "",
        "REVIEW": "Ship-to unresolved; Document price differs from contract price",
        "FAILED": "SKU unresolved; Invalid or unconverted quantity",
    })
    raw["overall_confidence"] = raw["quality_status"].map({"READY": .96, "REVIEW": .72, "FAILED": .38})
    return raw


def local_source_files(doc_id):
    manifest = pd.read_csv(DATA_ROOT / "ground_truth" / "manifest.csv")
    matches = manifest[manifest.doc_id.eq(doc_id)]
    records = []
    for row in matches.itertuples():
        path = DATA_ROOT / "documents" / row.relative_path
        if path.exists():
            records.append({"file_name": path.name, "file_path": str(path),
                "file_extension": path.suffix.lstrip("."), "content": path.read_bytes()})
    return pd.DataFrame(records, columns=["file_name", "file_path", "file_extension", "content"])
