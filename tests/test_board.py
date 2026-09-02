"""HTTP-level checks against fixture vault data."""

import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


class BoardAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["PROPOSALS_DIR"] = str(FIXTURES)
        # Import after env so the app module binds the fixture directory.
        from app import main as app_main

        app_main.PROPOSALS_DIR = str(FIXTURES)
        app_main.BRAIN_DIR = str(FIXTURES.parent)
        cls.client = TestClient(app_main.app)

    def test_board_renders_live_and_watchlist_cards(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.text
        self.assertIn("Win pipeline", body)
        self.assertIn("Submit before the deadline", body)
        self.assertNotIn(">Due in 30 days<", body)
        self.assertNotIn("Needs a look", body)
        self.assertIn("World Bank — El Salvador", body)
        self.assertIn("GPN · watchlist", body)
        self.assertIn("WHO — Data, analytics", body)
        self.assertIn("Ghana", body)
        self.assertIn("CDC India — Health Information", body)
        self.assertNotIn("Untitled Proposal", body)
        self.assertIn("Forecast", body)
        self.assertIn("2027", body)
        self.assertIn("CSA", body)
        rail = body.split('id="mainPipeline"', 1)[0]
        self.assertIn("Submit before the deadline", rail)
        self.assertNotIn("EDCTP3", rail)
        self.assertNotIn("Positioning Brief", body)
        self.assertNotIn("Palladium — Data.FI Subcontract Concept Note", body)
        low = body.lower()
        self.assertNotIn("custody", low)
        self.assertNotIn("child support", low)

    def test_brief_and_draft_are_not_in_api(self):
        r = self.client.get("/api/proposals")
        self.assertEqual(r.status_code, 200)
        names = {p["name"] for p in r.json()}
        self.assertNotIn("Positioning Brief — UNICEF PHIT Phase 2", names)
        self.assertIn("World Bank — El Salvador Improving Health Care (P506486 GPN)", names)
        gpn = next(p for p in r.json() if p["is_gpn"])
        self.assertTrue(gpn["is_watchlist"])
        self.assertIsNone(gpn["deadline"])

    def test_hyphenated_status_in_under_review_column(self):
        r = self.client.get("/")
        self.assertIn('data-status="under_review"', r.text)
        self.assertIn("AWS — OCL Modernization", r.text)

    def test_detail_shows_deadline_note_without_date(self):
        r = self.client.get("/proposal/proposal-wb-gpn")
        self.assertEqual(r.status_code, 200)
        self.assertIn("GPN · no bid window yet", r.text)
        self.assertIn("No bid deadline", r.text)
        self.assertIn("El Salvador", r.text)

    def test_patch_zimam_to_under_review(self):
        from app import main as app_main

        dest_dir = Path("/tmp/ghi-api-zimam")
        dest_dir.mkdir(exist_ok=True)
        src = FIXTURES / "proposal-zimam-hie-maturity.md"
        (dest_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        old = app_main.PROPOSALS_DIR
        app_main.PROPOSALS_DIR = str(dest_dir)
        try:
            r = self.client.patch(
                "/api/proposals/proposal-zimam-hie-maturity",
                json={"status": "under_review"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["status"], "under_review")
            text = (dest_dir / src.name).read_text(encoding="utf-8")
            self.assertIn("status: under_review", text)
            self.assertIn("[[work]]", text)
        finally:
            app_main.PROPOSALS_DIR = old

    def test_filter_control_is_present(self):
        r = self.client.get("/")
        self.assertIn('id="boardSearch"', r.text)
        self.assertIn("/static/board.js", r.text)
        self.assertIn("pipeline-scroll", r.text)
        self.assertIn('data-status="funded"', r.text)

    def test_watchlist_card_meta_wraps_instead_of_truncating(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.text
        gpn = body[body.find("World Bank — El Salvador") : body.find("World Bank — El Salvador") + 2500]
        self.assertIn("GPN published 20 Jan 2026. No bid deadline. Specific REOIs will post later.", gpn)
        self.assertIn("$120M", gpn)
        self.assertIn("meta-item note deadline deadline-none", gpn)
        self.assertIn("WB OP00419896 / P506486 — General Procurement Notice", gpn)
        css = self.client.get("/static/style.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn(".card-meta", css.text)
        self.assertIn("flex-wrap: wrap", css.text)
        self.assertIn(".meta-item.amount", css.text)
        self.assertNotIn(".meta-item {\n    color: var(--text-secondary);\n    white-space: nowrap;\n}", css.text)

    def test_funded_card_is_not_a_watchlist(self):
        r = self.client.get("/api/proposals")
        funded = next(p for p in r.json() if p["status"] == "funded")
        self.assertFalse(funded["is_watchlist"])
        board = self.client.get("/").text
        funded_idx = board.find("Global Fund DHIA")
        self.assertGreater(funded_idx, 0)
        snippet = board[funded_idx : funded_idx + 800]
        self.assertNotIn("Watch for a notice", snippet)
        self.assertNotIn("No deadline", snippet)
        self.assertNotIn("card-watchlist", snippet)


if __name__ == "__main__":
    unittest.main()
