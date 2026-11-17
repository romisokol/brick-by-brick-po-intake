import unittest

import database
from local_demo import load_local_lines, local_source_files


class LocalDemoTests(unittest.TestCase):
    def setUp(self):
        database._LOCAL_APPROVED = database.pd.DataFrame()
        database._LOCAL_EVENTS.clear()

    def test_fixture_matches_dashboard_totals(self):
        lines = load_local_lines()
        counts = lines["quality_status"].value_counts().to_dict()

        self.assertEqual(len(lines), 2717)
        self.assertEqual(lines["po_number"].nunique(), 193)
        self.assertEqual(counts, {"READY": 1637, "REVIEW": 1000, "FAILED": 80})
        self.assertAlmostEqual(counts["READY"] / len(lines) * 100, 60.3, places=1)

    def test_blank_po_rows_receive_deterministic_statuses(self):
        first = load_local_lines()
        second = load_local_lines()
        first = first[first.po_number.isna()][["source_doc_id", "line_number", "quality_status"]]
        second = second[second.po_number.isna()][["source_doc_id", "line_number", "quality_status"]]

        self.assertFalse(first.empty)
        self.assertEqual(first.to_dict("records"), second.to_dict("records"))

    def test_curated_walkthrough_cases_are_stable(self):
        lines = load_local_lines()
        cases = {
            ("DOC-00003", "4584495485"): {"READY"},
            ("DOC-00099", "ANT/PO/26/0736"): {"READY", "REVIEW"},
            ("DOC-00008", "4593430858"): {"READY", "REVIEW", "FAILED"},
        }
        for (doc_id, po_number), expected in cases.items():
            selected = lines[
                lines.source_doc_id.eq(doc_id) & lines.po_number.astype(str).eq(po_number)
            ]
            self.assertFalse(selected.empty)
            self.assertEqual(set(selected.display_quality_status), expected)

    def test_curated_attention_cases_have_fields_to_correct(self):
        lines = load_local_lines()
        editable = ["material_id", "quantity_each", "expected_net_price", "master_ship_to_name"]
        for doc_id, po_number in [
            ("DOC-00099", "ANT/PO/26/0736"),
            ("DOC-00008", "4593430858"),
        ]:
            selected = lines[
                lines.source_doc_id.eq(doc_id) & lines.po_number.astype(str).eq(po_number)
            ]
            attention = selected[selected.display_quality_status.isin(["REVIEW", "FAILED"])]

            self.assertFalse(attention.empty)
            self.assertTrue(attention[editable].isna().any(axis=1).any())
            self.assertTrue(attention[editable].notna().any(axis=1).all())

    def test_local_approval_is_separate_from_source_rows(self):
        source = load_local_lines()
        ready = source[source["quality_status"].eq("READY")].iloc[:2]
        rows = ready.to_dict("records")

        database.record_decision(rows, "local-demo-reviewer", "AUTO", [])
        approved = database.load_approved()

        self.assertEqual(len(approved), 2)
        self.assertTrue(approved["approval_type"].eq("AUTO").all())
        self.assertTrue(approved["approved_by"].eq("local-demo-reviewer").all())
        self.assertEqual(len(load_local_lines()), 2717)

    def test_reset_clears_only_demo_approvals(self):
        source = load_local_lines()
        rows = source[source["quality_status"].eq("READY")].iloc[:2].to_dict("records")
        database.record_decision(rows, "local-demo-reviewer", "AUTO", [])

        database.reset_demo_approvals()

        self.assertTrue(database.load_approved().empty)
        self.assertEqual(len(load_local_lines()), 2717)

    def test_demo_document_has_source_evidence(self):
        files = local_source_files("DOC-00003")

        self.assertFalse(files.empty)
        self.assertTrue(files.iloc[0]["content"])


if __name__ == "__main__":
    unittest.main()
