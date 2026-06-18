"""Tests for continuous reanalysis + cohort PS4.

Two layers:

  * ``TestCohortPS4`` — pure, DB-free unit tests of the cohort -> PS4 mapping and
    its gene/disease overrides. These always run.
  * ``TestReanalysis`` — PostgreSQL 16 integration tests proving reanalysis avoids
    classification churn, records same-tier changes without paging, and alerts on
    tier crossings with linked old/new receipts. These **skip cleanly** when
    psycopg or PostgreSQL is unavailable, so a shared discover run never fails for
    another agent.

Run explicitly:
    export PATH="/usr/local/opt/postgresql@16/bin:$PATH"
    python -m unittest tests.test_reanalysis -v
"""
from __future__ import annotations

import math
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAINTENANCE_DB = os.environ.get("RECLASS_MAINTENANCE_DB", "postgres")

# Pure-logic imports (no DB driver needed) -> the PS4 tests always run.
from engine.scoring import EvidenceEvent  # noqa: E402
from monitoring import reanalysis as rean  # noqa: E402


class TestCohortPS4(unittest.TestCase):
    """De-identified cohort counts -> a single standardized PS4 event."""

    def test_enriched_cohort_yields_ps4(self):
        counts = [
            {"ancestry": "nfe", "case_count": 15, "control_count": 1},
            {"ancestry": "afr", "case_count": 10, "control_count": 1},
        ]
        ev = rean.cohort_to_ps4_event(counts)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.acmg_criterion, "PS4")
        self.assertEqual(ev.evidence_direction, "pathogenic")
        self.assertEqual(ev.applied_strength, "strong")  # 25 cases >= strong_cases
        self.assertEqual(ev.raw["cases"], 25)
        self.assertEqual(ev.raw["controls"], 2)

    def test_strength_escalates_with_case_count(self):
        # moderate band: >= moderate_cases (10) but < strong_cases (20).
        ev = rean.cohort_to_ps4_event(
            [{"case_count": 12, "control_count": 1}]
        )
        self.assertIsNotNone(ev)
        self.assertEqual(ev.applied_strength, "moderate")

    def test_too_few_cases_yields_no_event(self):
        self.assertIsNone(
            rean.cohort_to_ps4_event([{"case_count": 2, "control_count": 0}])
        )

    def test_not_enriched_yields_no_event(self):
        # Plenty of cases, but controls swamp the enrichment ratio.
        self.assertIsNone(
            rean.cohort_to_ps4_event([{"case_count": 25, "control_count": 25}])
        )

    def test_hearing_loss_proband_count_override_lowers_threshold(self):
        # The generic default needs >=5 enriched cases; the current ClinGen Hearing
        # Loss VCEP proband-count spec fires PS4_Supporting at >=2 unrelated probands
        # (PM2 supplied separately) even with no control cohort.
        counts = [{"case_count": 3, "control_count": 0}]
        self.assertIsNone(rean.cohort_to_ps4_event(counts))  # default needs 5
        ev = rean.cohort_to_ps4_event(counts, gene="COCH")
        self.assertIsNotNone(ev, "COCH Hearing Loss override fires at >= 2 probands")
        self.assertEqual(ev.applied_strength, "supporting")

    def test_hearing_loss_proband_count_strength_bands(self):
        # >=2 supporting, >=6 moderate, >=15 strong (Hearing Loss VCEP).
        def strength(n, gene):
            ev = rean.cohort_to_ps4_event(
                [{"case_count": n, "control_count": 0}], gene=gene
            )
            return ev.applied_strength if ev else None

        self.assertIsNone(strength(1, "COCH"))            # below proband floor
        self.assertEqual(strength(2, "COCH"), "supporting")
        self.assertEqual(strength(6, "KCNQ4"), "moderate")
        self.assertEqual(strength(15, "MYO6"), "strong")

    def test_cardiomyopathy_bare_proband_counts_yield_nothing(self):
        # Current Cardiomyopathy CSpecs require an odds-ratio CI, so a cardiomyopathy
        # gene reported only as a bare proband count (no denominators) gets no PS4 --
        # the historical simple proband-count shortcut must not fire for MYH7.
        counts = [{"case_count": 3, "control_count": 0}]
        self.assertIsNone(rean.cohort_to_ps4_event(counts, gene="MYH7"))
        # An OR rule is what governs the gene now (not the case-control default).
        self.assertIsInstance(rean.resolve_ps4_rule(gene="MYH7"), rean.PS4OddsRatioRule)

    def test_recessive_hearing_loss_gene_uses_default(self):
        # The current Hearing Loss proband-count PS4 text is autosomal-dominant
        # specific, so recessive GJB2 does not get the gene-wide shortcut.
        counts = [{"case_count": 3, "control_count": 0}]
        self.assertIsNone(rean.cohort_to_ps4_event(counts, gene="GJB2"))

    def test_unknown_gene_uses_conservative_default(self):
        # A gene with no VCEP rule falls back to the case-control default.
        counts = [{"case_count": 3, "control_count": 0}]
        self.assertIsNone(
            rean.cohort_to_ps4_event(counts, gene="ZZZ9"),
            "no proband-count shortcut without a VCEP-specified gene",
        )

    def test_events_with_cohort_ps4_appends_only_when_applicable(self):
        base = [EvidenceEvent(source="curated", acmg_criterion="PM2",
                              evidence_direction="pathogenic",
                              applied_strength="supporting")]
        none_counts = [{"case_count": 1, "control_count": 0}]
        self.assertEqual(
            len(rean.events_with_cohort_ps4(base, none_counts)), 1
        )
        rich_counts = [{"case_count": 25, "control_count": 1}]
        out = rean.events_with_cohort_ps4(base, rich_counts)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1].acmg_criterion, "PS4")


class TestOddsRatioCI(unittest.TestCase):
    """Pure case-control odds-ratio + 95% CI math (cardiomyopathy PS4, A4)."""

    def test_odds_ratio_and_wald_ci(self):
        # 2x2 = [[10, 90], [1, 99]] -> OR = (10*99)/(90*1) = 11.0; no empty cell, so
        # no continuity correction. CI is the standard Wald log-OR interval.
        odds, lo, hi = rean.odds_ratio_ci(10, 90, 1, 99)
        self.assertAlmostEqual(odds, 11.0, places=6)
        self.assertAlmostEqual(lo, 1.3805, places=3)
        self.assertAlmostEqual(hi, 87.66, places=1)
        self.assertLess(lo, odds)
        self.assertLess(odds, hi)

    def test_haldane_correction_keeps_or_finite_with_zero_cell(self):
        # A zero variant-control cell would divide by zero without the +0.5 fix.
        odds, lo, hi = rean.odds_ratio_ci(5, 95, 0, 100)
        self.assertTrue(math.isfinite(odds) and math.isfinite(lo) and math.isfinite(hi))
        self.assertGreater(odds, 1.0)
        self.assertLess(lo, odds)

    def test_ci_lower_to_strength_bins(self):
        rule = rean.CARDIOMYOPATHY_OR_RULE
        self.assertEqual(rean._ps4_or_strength(7.2, rule), "strong")     # >= 5.0
        self.assertEqual(rean._ps4_or_strength(3.6, rule), "moderate")   # >= 3.0
        self.assertEqual(rean._ps4_or_strength(1.8, rule), "supporting") # >= 1.5
        self.assertIsNone(rean._ps4_or_strength(1.2, rule))              # > floor, < bins
        self.assertIsNone(rean._ps4_or_strength(0.9, rule))              # includes OR=1


class TestCardiomyopathyOddsRatioPS4(unittest.TestCase):
    """End-to-end PS4 from case-control denominators for cardiomyopathy genes (A4)."""

    def test_strong_enrichment_with_denominators_fires_ps4(self):
        # 30/5000 cases vs 2/10000 controls -> OR ~30, CI lower ~7 -> PS4_Strong.
        counts = [{"case_count": 30, "control_count": 2,
                   "case_total": 5000, "control_total": 10000}]
        ev = rean.cohort_to_ps4_event(counts, gene="MYH7")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.acmg_criterion, "PS4")
        self.assertEqual(ev.applied_strength, "strong")
        self.assertEqual(ev.raw["mode"], "odds_ratio")
        self.assertGreater(ev.raw["odds_ratio"], 5.0)
        self.assertGreater(ev.raw["ci_lower"], 5.0)
        self.assertEqual(ev.raw["total_cases"], 5000)

    def test_non_significant_enrichment_yields_no_event(self):
        # 5/1000 vs 8/2000 -> OR ~1.25, CI lower < 1 -> no PS4 (interval includes 1).
        counts = [{"case_count": 5, "control_count": 8,
                   "case_total": 1000, "control_total": 2000}]
        self.assertIsNone(rean.cohort_to_ps4_event(counts, gene="MYBPC3"))

    def test_min_variant_cases_gate(self):
        # Hugely enriched but only 3 variant-positive cases (< min_variant_cases=4).
        counts = [{"case_count": 3, "control_count": 0,
                   "case_total": 10, "control_total": 100000}]
        self.assertIsNone(rean.cohort_to_ps4_event(counts, gene="TNNT2"))

    def test_denominators_summed_across_ancestries(self):
        counts = [
            {"case_count": 18, "control_count": 1, "case_total": 3000, "control_total": 6000},
            {"case_count": 14, "control_count": 1, "case_total": 2000, "control_total": 4000},
        ]
        ev = rean.cohort_to_ps4_event(counts, gene="ACTC1")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.raw["variant_cases"], 32)
        self.assertEqual(ev.raw["total_controls"], 10000)
        self.assertIn(ev.applied_strength, {"supporting", "moderate", "strong"})

    def test_explicit_rule_can_drive_a_crossing(self):
        # The OR-derived PS4 sums into the engine exactly like any other event.
        base = [EvidenceEvent(source="curated", acmg_criterion="PM2",
                              evidence_direction="pathogenic", applied_strength="moderate")]
        counts = [{"case_count": 30, "control_count": 2,
                   "case_total": 5000, "control_total": 10000}]
        out = rean.events_with_cohort_ps4(base, counts, gene="MYL2")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1].acmg_criterion, "PS4")
        self.assertEqual(out[-1].applied_strength, "strong")


# --------------------------------------------------------------------------- #
# DB-backed reanalysis integration tests                                      #
# --------------------------------------------------------------------------- #
_SKIP_REASON = ""
try:
    import psycopg
    from psycopg import sql

    import db.apply as applymod
    from storage import db as sdb
    from storage import classifications as crepo
    from storage import evidence as erepo
    from storage import alerts as arepo

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - exercised only without deps
    _IMPORTS_OK = False
    _SKIP_REASON = f"storage dependencies unavailable: {exc}"


def _pg_ready():
    if not _IMPORTS_OK:
        return False, _SKIP_REASON
    try:
        with psycopg.connect(dbname=MAINTENANCE_DB, connect_timeout=3):
            return True, ""
    except Exception as exc:  # pragma: no cover - exercised only without a server
        return False, f"PostgreSQL not available: {exc}"


PG_READY, PG_REASON = _pg_ready()


@unittest.skipUnless(PG_READY, PG_REASON)
class TestReanalysis(unittest.TestCase):
    """Reanalysis orchestration against a throwaway database."""

    @classmethod
    def setUpClass(cls):
        cls.db = f"reclass_test_{uuid.uuid4().hex[:10]}"
        cls.role = f"reclass_app_{uuid.uuid4().hex[:8]}"
        applymod.recreate_database(cls.db)
        cls.conn = sdb.connect(cls.db)
        sdb.ensure_app_role(cls.conn, cls.role)
        sdb.grant_app_role(cls.conn, cls.role)
        with cls.conn.cursor() as cur:
            cls.tenant = crepo.insert_tenant(cur, "Tenant R")
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            applymod.drop_database(cls.db)
            try:
                with psycopg.connect(dbname=MAINTENANCE_DB, autocommit=True) as c:
                    c.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(cls.role)
                        )
                    )
            except Exception:  # best-effort role cleanup
                pass

    def _session(self, tenant_id=None):
        return sdb.tenant_session(self.conn, tenant_id or self.tenant, role=self.role)

    def _new_variant(self, *, chrom, pos, ref="A", alt="G"):
        with self.conn.cursor() as cur:
            vid = crepo.upsert_variant(cur, chrom=chrom, pos=pos, ref=ref, alt=alt)
        self.conn.commit()
        return vid

    @staticmethod
    def _ev(criterion, strength, direction="pathogenic", source="curated"):
        return EvidenceEvent(source=source, acmg_criterion=criterion,
                             evidence_direction=direction, applied_strength=strength,
                             source_version="v1")

    def _seed(self, variant_id, events):
        """Persist an initial classification for a variant via reanalyze()."""
        with self._session() as cur:
            res = rean.reanalyze(
                cur, tenant_id=self.tenant, variant_id=variant_id, new_events=events,
            )
        self.assertTrue(res.changed)
        return res

    def test_no_churn_when_unchanged(self):
        vid = self._new_variant(chrom="1", pos=1001)
        events = [self._ev("PM2", "supporting")]  # +1 -> VUS
        self._seed(vid, events)

        with self._session() as cur:
            before = len(crepo.list_classifications(cur, variant_id=vid))
            res = rean.reanalyze(
                cur, tenant_id=self.tenant, variant_id=vid, new_events=events,
            )
            after = len(crepo.list_classifications(cur, variant_id=vid))

        self.assertFalse(res.changed, "identical evidence must not re-persist")
        self.assertEqual(before, after, "no churn: no new receipt written")

    def test_same_tier_change_records_without_alert(self):
        vid = self._new_variant(chrom="2", pos=2002)
        self._seed(vid, [self._ev("PM2", "supporting")])  # +1 -> VUS

        # Add evidence but stay within VUS (1 + 2 = 3).
        new_events = [self._ev("PM2", "supporting"), self._ev("PP3", "moderate")]
        with self._session() as cur:
            alerts_before = len(arepo.list_alerts(cur, variant_id=vid))
            res = rean.reanalyze(
                cur, tenant_id=self.tenant, variant_id=vid, new_events=new_events,
            )
            alerts_after = len(arepo.list_alerts(cur, variant_id=vid))
            events_logged = arepo.list_reanalysis_events(cur, variant_id=vid)

        self.assertTrue(res.changed)
        self.assertFalse(res.crossed)
        self.assertEqual(res.new_tier, "VUS")
        self.assertIsNone(res.alert_id, "same-tier change must not page")
        self.assertEqual(alerts_before, alerts_after, "no alert row for same tier")
        self.assertEqual(len(events_logged), 1, "same-tier change is still audited")
        self.assertFalse(events_logged[0]["crossed"])

    def test_tier_crossing_alerts_with_linked_receipts(self):
        vid = self._new_variant(chrom="3", pos=3003)
        seed = self._seed(vid, [self._ev("PM2", "supporting")])  # +1 -> VUS

        # Strong pathogenic evidence crosses VUS -> Likely Pathogenic (1 + 8 = 9).
        new_events = [self._ev("PM2", "supporting"), self._ev("PVS1", "very_strong")]
        with self._session() as cur:
            res = rean.reanalyze(
                cur, tenant_id=self.tenant, variant_id=vid, new_events=new_events,
                trigger="provider_version",
            )
            self.assertTrue(res.changed)
            self.assertTrue(res.crossed)
            self.assertEqual(res.old_tier, "VUS")
            self.assertEqual(res.new_tier, "Likely Pathogenic")
            self.assertIsNotNone(res.alert_id)

            alert = arepo.get_alert(cur, res.alert_id)
            self.assertEqual(alert["old_tier"], "VUS")
            self.assertEqual(alert["new_tier"], "Likely Pathogenic")

            logged = arepo.list_reanalysis_events(cur, variant_id=vid)[-1]
            self.assertEqual(str(logged["prior_classification_id"]),
                             seed.new_classification_id)
            self.assertEqual(str(logged["new_classification_id"]),
                             res.new_classification_id)
            self.assertEqual(str(logged["alert_id"]), res.alert_id)
            self.assertEqual(logged["trigger"], "provider_version")

            # Both receipts (old + new) are retained for audit.
            self.assertEqual(len(crepo.list_classifications(cur, variant_id=vid)), 2)

    def test_reanalysis_event_records_bundle_receipts(self):
        vid = self._new_variant(chrom="4", pos=4004)
        self._seed(vid, [self._ev("PM2", "supporting")])  # +1 -> VUS
        prior_bundle = str(uuid.uuid4())
        new_bundle = str(uuid.uuid4())

        new_events = [self._ev("PM2", "supporting"), self._ev("PVS1", "very_strong")]
        with self._session() as cur:
            res = rean.reanalyze(
                cur, tenant_id=self.tenant, variant_id=vid, new_events=new_events,
                prior_bundle_id=prior_bundle, new_bundle_id=new_bundle,
            )
            self.assertTrue(res.crossed)
            logged = arepo.list_reanalysis_events(cur, variant_id=vid)[-1]
        self.assertEqual(str(logged["prior_bundle_id"]), prior_bundle)
        self.assertEqual(str(logged["new_bundle_id"]), new_bundle)

    def test_cohort_ps4_can_drive_a_crossing(self):
        vid = self._new_variant(chrom="13", pos=32340000, ref="C", alt="T")
        key = crepo.variant_key("13", 32340000, "C", "T")
        base = [self._ev("PM2", "supporting"), self._ev("PP3", "strong")]  # 1 + 4 = 5 -> VUS
        self._seed(vid, base)

        with self.conn.cursor() as cur:
            erepo.upsert_research_variant(
                cur, variant_key=key, chrom="13", pos=32340000, ref="C", alt="T"
            )
            erepo.upsert_cohort_count(cur, variant_key=key, ancestry="nfe",
                                      case_count=15, control_count=1)
            erepo.upsert_cohort_count(cur, variant_key=key, ancestry="afr",
                                      case_count=10, control_count=1)
        self.conn.commit()

        with self.conn.cursor() as cur:
            counts = erepo.get_cohort_counts(cur, key)
        new_events = rean.events_with_cohort_ps4(base, counts)  # + PS4 strong (4) = 9
        self.assertEqual(len(new_events), 3)

        with self._session() as cur:
            res = rean.reanalyze(
                cur, tenant_id=self.tenant, variant_id=vid, new_events=new_events,
                trigger="evidence",
            )
        self.assertTrue(res.crossed)
        self.assertEqual(res.new_tier, "Likely Pathogenic")
        self.assertIsNotNone(res.alert_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
