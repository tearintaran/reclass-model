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

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAINTENANCE_DB = os.environ.get("RECLASS_MAINTENANCE_DB", "postgres")

# Pure-logic imports (no DB driver needed) -> the PS4 tests always run.
from engine.scoring import EvidenceEvent, classify  # noqa: E402
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

    def test_vcep_proband_count_override_lowers_threshold(self):
        # The generic default needs >=5 enriched cases; the ClinGen Cardiomyopathy
        # Expert Panel proband-count spec (Kelly et al. 2018) fires PS4_Supporting at
        # >=2 probands (PM2 supplied separately) even with no control cohort.
        counts = [{"case_count": 3, "control_count": 0}]
        self.assertIsNone(rean.cohort_to_ps4_event(counts))  # default needs 5
        ev = rean.cohort_to_ps4_event(counts, gene="MYH7")
        self.assertIsNotNone(ev, "MYH7 VCEP override fires at >= 2 probands")
        self.assertEqual(ev.applied_strength, "supporting")

    def test_vcep_proband_count_strength_bands(self):
        # >=2 supporting, >=6 moderate, >=15 strong (Cardiomyopathy/Hearing Loss VCEP).
        def strength(n, gene):
            ev = rean.cohort_to_ps4_event(
                [{"case_count": n, "control_count": 0}], gene=gene
            )
            return ev.applied_strength if ev else None

        self.assertIsNone(strength(1, "MYBPC3"))            # below proband floor
        self.assertEqual(strength(2, "MYBPC3"), "supporting")
        self.assertEqual(strength(6, "GJB2"), "moderate")   # hearing-loss gene
        self.assertEqual(strength(15, "TNNT2"), "strong")

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
