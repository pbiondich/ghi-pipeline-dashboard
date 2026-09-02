"""Tests for vault parsing, status aliases, GPN/no-deadline cards, and PATCH."""

import unittest
from datetime import date, timedelta
from pathlib import Path

from app.proposal_loader import (
    ACTIVE_STATUSES,
    ARCHIVED_STATUSES,
    Proposal,
    due_this_window,
    group_by_status,
    load_proposals,
    load_proposals_report,
    update_proposal_status,
)

FIXTURES = Path(__file__).parent / "fixtures"


class LoadFilterTests(unittest.TestCase):
    def setUp(self):
        self.report = load_proposals_report(str(FIXTURES))
        self.by_name = {p.name: p for p in self.report.proposals}

    def test_skips_brief_and_draft_files(self):
        names = {p.name for p in self.report.proposals}
        self.assertNotIn("Positioning Brief — UNICEF PHIT Phase 2", names)
        self.assertNotIn("Palladium — Data.FI Subcontract Concept Note", names)
        self.assertNotIn("Should be skipped even with proposal- prefix", names)

    def test_loads_real_proposal_shapes(self):
        self.assertIn("WHO — Data, analytics, and digital systems for HSPA (LTA)", self.by_name)
        self.assertIn("World Bank — El Salvador Improving Health Care (P506486 GPN)", self.by_name)
        self.assertGreaterEqual(len(self.report.proposals), 5)

    def test_missing_dir(self):
        report = load_proposals_report("/tmp/ghi-pipeline-does-not-exist")
        self.assertTrue(report.missing_dir)
        self.assertEqual(report.proposals, [])


class FieldWiringTests(unittest.TestCase):
    def setUp(self):
        self.by_name = {p.name: p for p in load_proposals(str(FIXTURES))}

    def test_geography_not_just_region(self):
        ghana = self.by_name["CDC Ghana — Protecting and Improving GHS via local partners"]
        self.assertEqual(ghana.region, "Ghana")
        gpn = self.by_name["World Bank — El Salvador Improving Health Care (P506486 GPN)"]
        self.assertEqual(gpn.region, "El Salvador")

    def test_fit_strong_maps_to_high(self):
        who = self.by_name["WHO — Data, analytics, and digital systems for HSPA (LTA)"]
        self.assertEqual(who.fit_rating, "high")

    def test_target_used_as_funder(self):
        withdrawn = self.by_name["Sample — Withdrawn concept"]
        self.assertEqual(withdrawn.funder, "Example Funder")

    def test_funder_from_title_when_missing(self):
        aws = self.by_name["AWS — OCL Modernization"]
        self.assertEqual(aws.funder, "AWS")

    def test_grant_id_falls_back_to_reference(self):
        who = self.by_name["WHO — Data, analytics, and digital systems for HSPA (LTA)"]
        self.assertEqual(who.grant_id, "WHO-SHQ-RFP-26-3028-P-LTA")

    def test_compact_program_amount(self):
        ghana = self.by_name["CDC Ghana — Protecting and Improving GHS via local partners"]
        self.assertEqual(ghana.amount_compact, "$125M")
        gpn = self.by_name["World Bank — El Salvador Improving Health Care (P506486 GPN)"]
        self.assertEqual(gpn.amount_compact, "$120M")

    def test_search_text_includes_geo_and_mechanism(self):
        ghana = self.by_name["CDC Ghana — Protecting and Improving GHS via local partners"]
        self.assertIn("ghana", ghana.search_text)
        self.assertIn("nofo", ghana.search_text)


class StatusAliasTests(unittest.TestCase):
    def test_hyphen_under_review(self):
        self.assertEqual(Proposal.canonicalize_status("under-review"), "under_review")
        self.assertEqual(Proposal.canonicalize_status("under_review"), "under_review")
        loaded = {p.name: p for p in load_proposals(str(FIXTURES))}
        self.assertEqual(loaded["AWS — OCL Modernization"].status, "under_review")

    def test_withdrawn_and_closed_are_archived(self):
        self.assertEqual(Proposal.canonicalize_status("withdrawn"), "no-go")
        self.assertEqual(Proposal.canonicalize_status("closed"), "no-go")
        loaded = {p.name: p for p in load_proposals(str(FIXTURES))}
        withdrawn = loaded["Sample — Withdrawn concept"]
        self.assertEqual(withdrawn.status, "no-go")
        self.assertIn(withdrawn.status, ARCHIVED_STATUSES)

    def test_unknown_status_does_not_invent_a_column(self):
        self.assertEqual(Proposal.canonicalize_status("mystery-lane"), "watching")


class DeadlineAndGpnTests(unittest.TestCase):
    def setUp(self):
        self.by_name = {p.name: p for p in load_proposals(str(FIXTURES))}
        self.gpn = self.by_name["World Bank — El Salvador Improving Health Care (P506486 GPN)"]
        self.who = self.by_name["WHO — Data, analytics, and digital systems for HSPA (LTA)"]

    def test_no_deadline_is_first_class_watchlist(self):
        self.assertIsNone(self.gpn.deadline)
        self.assertTrue(self.gpn.is_watchlist)
        self.assertTrue(self.gpn.is_gpn)
        self.assertEqual(self.gpn.deadline_status, "none")
        self.assertEqual(self.gpn.mechanism_label, "GPN")
        self.assertIsNone(self.gpn.days_until_deadline)

    def test_dated_live_bid_is_not_watchlist(self):
        self.assertFalse(self.who.is_watchlist)
        self.assertEqual(self.who.mechanism_label, "LTA")
        self.assertIsNotNone(self.who.deadline)

    def test_group_sort_puts_sooner_deadlines_first_and_keeps_gpn(self):
        groups = group_by_status(list(self.by_name.values()))
        watching = groups["watching"]
        names = [p.name for p in watching]
        self.assertIn(self.gpn.name, names)
        dated = [p for p in watching if p.deadline]
        self.assertEqual(dated, sorted(dated, key=lambda p: p.deadline))
        # Watchlist cards stay in the column, after dated ones.
        last_dated = max(i for i, p in enumerate(watching) if p.deadline)
        first_watch = min(i for i, p in enumerate(watching) if p.deadline is None)
        self.assertLess(last_dated, first_watch)

    def test_due_window_excludes_gpn_includes_dated(self):
        # Force a proposal due tomorrow via a constructed object.
        meta = {
            "name": "Due tomorrow",
            "status": "drafting",
            "deadline": (date.today() + timedelta(days=1)).isoformat(),
        }
        soon = Proposal(meta, "", "x.md", "proposal-x.md")
        far_meta = {
            "name": "Far",
            "status": "watching",
            "deadline": (date.today() + timedelta(days=90)).isoformat(),
        }
        far = Proposal(far_meta, "", "y.md", "proposal-y.md")
        window = due_this_window([soon, far, self.gpn], days=14)
        self.assertEqual([p.name for p in window], ["Due tomorrow"])
        self.assertIn(soon.status, ACTIVE_STATUSES)

    def test_due_window_skips_submitted_and_under_review(self):
        submitted = Proposal(
            {
                "name": "Already sent",
                "status": "submitted",
                "deadline": date.today().isoformat(),
            },
            "",
            "s.md",
            "proposal-s.md",
        )
        review = Proposal(
            {
                "name": "Waiting on funder",
                "status": "under_review",
                "deadline": (date.today() - timedelta(days=40)).isoformat(),
            },
            "",
            "r.md",
            "proposal-r.md",
        )
        self.assertEqual(submitted.deadline_status, "closed")
        self.assertEqual(review.deadline_status, "closed")
        self.assertEqual(due_this_window([submitted, review, self.gpn], days=14), [])

    def test_funded_without_deadline_is_not_watchlist(self):
        funded = self.by_name["Global Fund DHIA — South Africa Digital Health TA"]
        self.assertEqual(funded.status, "funded")
        self.assertFalse(funded.is_watchlist)

    def test_forecast_chip_and_year_stamp(self):
        fogarty = Proposal(
            {
                "name": "NIH Fogarty — Reciprocal Innovation",
                "status": "watching",
                "deadline": "2027-02-11",
                "mechanism": "Grant (forecast)",
                "tags": ["forecast"],
            },
            "",
            "f.md",
            "proposal-f.md",
        )
        self.assertTrue(fogarty.is_forecast)
        self.assertEqual(fogarty.mechanism_label, "Forecast")
        self.assertIn("2027", fogarty.deadline_stamp)
        self.assertNotIn(fogarty, due_this_window([fogarty], days=14))

    def test_name_falls_back_to_heading_when_untitled(self):
        untitled = Proposal(
            {"title": "Proposal Cdc India His Lab", "status": "no-go"},
            "# CDC India — Health Information & Lab Systems (JG-26-0143)\n",
            "x.md",
            "proposal-x.md",
        )
        self.assertEqual(
            untitled.name, "CDC India — Health Information & Lab Systems (JG-26-0143)"
        )

    def test_double_frontmatter_merges_real_record(self):
        loaded = {p.filename: p for p in load_proposals(str(FIXTURES))}
        india = loaded["proposal-cdc-india-double-fm.md"]
        self.assertEqual(
            india.name, "CDC India — Health Information & Laboratory Systems Strengthening"
        )
        self.assertNotEqual(india.name, "Untitled Proposal")
        self.assertEqual(india.status, "no-go")
        self.assertEqual(india.region, "India")
        self.assertEqual(india.grant_id, "CDC-RFA-JG-26-0143")
        self.assertIn("will not pursue", india.no_go_reason)
        self.assertEqual(india.amount_compact, "$6M")

    def test_deadline_notes_plural_and_geo_compact(self):
        p = Proposal(
            {
                "name": "EDCTP3 sample",
                "status": "submitted",
                "deadline_notes": "02-Sep-2026 at 17:00 Brussels",
                "geography": "Global (WHO HQ; beneficiary listed Switzerland)",
            },
            "",
            "e.md",
            "proposal-e.md",
        )
        self.assertIn("Brussels", p.deadline_note)
        self.assertEqual(p.region_compact, "Global")


class SurgicalPatchTests(unittest.TestCase):
    def test_patch_preserves_body_and_unrelated_keys(self):
        src = FIXTURES / "proposal-who-lta.md"
        dest = Path("/tmp/ghi-patch-who.md")
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        original = dest.read_text(encoding="utf-8")

        updated = update_proposal_status("/tmp", dest.stem, "under-review")
        self.assertEqual(updated.status, "under_review")

        text = dest.read_text(encoding="utf-8")
        self.assertIn("status: under_review", text)
        self.assertIn(f"updated: {date.today().isoformat()}", text)
        self.assertIn("fit: strong", text)
        self.assertIn("geography: Global (WHO HQ)", text)
        self.assertIn("Body stays put when status is patched.", text)
        # Key order: mechanism still follows reference — dumps() would reshuffle.
        self.assertLess(text.index("grant_id:"), text.index("mechanism:"))
        self.assertNotEqual(original.split("status:")[0], "")  # still has frontmatter

    def test_patch_rejects_unknown_status(self):
        src = FIXTURES / "proposal-funded.md"
        dest = Path("/tmp/ghi-patch-funded.md")
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(ValueError):
            update_proposal_status("/tmp", dest.stem, "mystery-lane")

    def test_patch_updates_both_double_frontmatter_blocks(self):
        src = FIXTURES / "proposal-cdc-india-double-fm.md"
        dest = Path("/tmp/ghi-patch-india.md")
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        updated = update_proposal_status("/tmp", dest.stem, "watching")
        self.assertEqual(updated.status, "watching")
        self.assertEqual(
            updated.name, "CDC India — Health Information & Laboratory Systems Strengthening"
        )
        text = dest.read_text(encoding="utf-8")
        self.assertEqual(text.count("status: watching"), 2)
        self.assertNotIn("no_go_reason", text)
        import frontmatter
        from app.proposal_loader import _absorb_extra_frontmatter

        post = frontmatter.loads(text)
        meta, body = _absorb_extra_frontmatter(dict(post.metadata), post.content)
        one = Proposal(meta, body, str(dest), dest.name)
        self.assertEqual(one.status, "watching")
        self.assertEqual(
            one.name, "CDC India — Health Information & Laboratory Systems Strengthening"
        )

    def test_patch_zimam_drafting_to_under_review(self):
        src = FIXTURES / "proposal-zimam-hie-maturity.md"
        dest = Path("/tmp/proposal-zimam-hie-maturity.md")
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        updated = update_proposal_status("/tmp", "proposal-zimam-hie-maturity", "under_review")
        self.assertEqual(updated.status, "under_review")
        self.assertEqual(updated.name, "ZIMAM — HIE Maturity + Data Quality Assessment")
        text = dest.read_text(encoding="utf-8")
        self.assertIn("status: under_review", text)
        self.assertIn("workspace: '[[work]]'", text)
        self.assertIn("[[organizations/zimam]]", text)
        self.assertIn("Concept note. Wikilinks", text)
        self.assertNotIn("---#", text)
        self.assertRegex(text, r"---\n+# ZIMAM")

    def test_patch_zimam_jordan_preserves_body_thematic_break(self):
        src = FIXTURES / "proposal-zimam-jordan-training.md"
        dest = Path("/tmp/proposal-zimam-jordan-training.md")
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        updated = update_proposal_status("/tmp", "proposal-zimam-jordan-training", "under_review")
        self.assertEqual(updated.status, "under_review")
        text = dest.read_text(encoding="utf-8")
        self.assertEqual(text.count("status: under_review"), 1)
        self.assertIn("## From Tolaria (zimam-data-standards-training)", text)
        self.assertIn("A thematic break in the body", text)
        self.assertNotIn("---#", text)
        self.assertRegex(text, r"---\n+# ZIMAM")

    def test_patch_no_go_reason_and_clear_on_move(self):
        src = FIXTURES / "proposal-cdc-ghana.md"
        dest = Path("/tmp/ghi-patch-ghana.md")
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        update_proposal_status("/tmp", dest.stem, "no-go", reason="Local prime gap")
        text = dest.read_text(encoding="utf-8")
        self.assertIn("no_go_reason: Local prime gap", text)
        update_proposal_status("/tmp", dest.stem, "watching")
        text = dest.read_text(encoding="utf-8")
        self.assertNotIn("no_go_reason", text)
        self.assertIn("status: watching", text)


if __name__ == "__main__":
    unittest.main()
