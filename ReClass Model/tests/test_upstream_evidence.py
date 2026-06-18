"""Offline unit tests for the validated upstream evidence adapters (job1 task 1).

One test class per adapter -- de novo, phasing, segregation, phenotype, functional,
disease-mechanism, case-control -- each covering the three required outcomes: a
*present* (actionable) input, an *absent* input (explicit no-call record), and a
*malformed* input. Also covers the recorded provenance contract (source version,
checksum, access date) and the aggregate provider. Everything runs from in-memory
fixtures and the committed configs; no network, no large data files.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_upstream_evidence -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import classify
from evidence.model import CohortCounts
from evidence.upstream import (
    CaseControlAdapter,
    DeNovoAdapter,
    DiseaseMechanismAdapter,
    FunctionalAssayAdapter,
    PhasingAdapter,
    PhenotypeAdapter,
    SegregationAdapter,
    UpstreamEvidenceProvider,
    derive_upstream_events,
    odds_ratio_from_counts,
)


def _evidence(evidence_type, record):
    return {"evidence": {evidence_type: record}}


def _crit(bundle):
    assert len(bundle.events) == 1, bundle.events
    e = bundle.events[0]
    return e.acmg_criterion, e.evidence_direction, e.applied_strength


class TestProvenanceContract(unittest.TestCase):
    """Every adapter records source version, checksum, and access date (task 1)."""

    def test_called_record_carries_full_provenance(self):
        adapter = DeNovoAdapter(access_date="2026-06-17")
        bundle = adapter.fetch(_evidence("de_novo", {"confirmed_parentage": True}))
        rec = bundle.source_records[0]
        self.assertEqual(rec["status"], "called")
        self.assertTrue(rec["called"])
        self.assertEqual(rec["source_version"], "upstream_de_novo_v1")
        self.assertEqual(rec["access_date"], "2026-06-17")
        self.assertTrue(rec["checksum"])           # SHA-256 of the source record
        self.assertEqual(rec["checksum_algorithm"], "sha256")
        # The event also carries provenance in raw (outside the engine hash).
        self.assertIn("provenance", bundle.events[0].raw)
        self.assertEqual(bundle.events[0].raw["provenance"]["checksum"], rec["checksum"])

    def test_record_can_override_source_version_and_access_date(self):
        bundle = DeNovoAdapter().fetch(_evidence("de_novo", {
            "confirmed_parentage": True, "source_version": "lab_v9", "access_date": "2025-01-01"}))
        rec = bundle.source_records[0]
        self.assertEqual(rec["source_version"], "lab_v9")
        self.assertEqual(rec["access_date"], "2025-01-01")

    def test_checksum_is_deterministic(self):
        a = DeNovoAdapter().fetch(_evidence("de_novo", {"confirmed_parentage": True}))
        b = DeNovoAdapter().fetch(_evidence("de_novo", {"confirmed_parentage": True}))
        self.assertEqual(a.source_records[0]["checksum"], b.source_records[0]["checksum"])

    def test_absent_record_is_explicit_no_call(self):
        bundle = DeNovoAdapter(access_date="2026-06-17").fetch({"evidence": {}})
        self.assertEqual(bundle.events, [])
        self.assertEqual(bundle.warnings, ["de_novo_absent"])
        rec = bundle.source_records[0]
        self.assertEqual(rec["status"], "absent")
        self.assertFalse(rec["called"])
        self.assertIsNone(rec["checksum"])         # no record content to checksum
        self.assertEqual(rec["access_date"], "2026-06-17")  # the "checked on" date
        self.assertFalse(bundle.match["de_novo_match"])

    def test_malformed_record_is_recorded_not_raised(self):
        bundle = DeNovoAdapter().fetch(_evidence("de_novo", "not-a-dict"))
        self.assertEqual(bundle.events, [])
        self.assertEqual(bundle.warnings, ["de_novo_malformed"])
        self.assertEqual(bundle.source_records[0]["status"], "malformed")


class TestDeNovo(unittest.TestCase):
    def test_confirmed_de_novo_is_ps2(self):
        b = DeNovoAdapter().fetch(_evidence("de_novo", {"confirmed_parentage": True}))
        self.assertEqual(_crit(b), ("PS2", "pathogenic", "strong"))

    def test_assumed_de_novo_is_pm6(self):
        b = DeNovoAdapter().fetch(_evidence("de_novo", {"confirmed_parentage": False}))
        self.assertEqual(_crit(b), ("PM6", "pathogenic", "moderate"))

    def test_multiple_confirmed_observations_upgrade_strength(self):
        b = DeNovoAdapter().fetch(_evidence("de_novo", {
            "observations": [{"confirmed": True}, {"confirmed": True}]}))  # 4.0 pts
        self.assertEqual(_crit(b), ("PS2", "pathogenic", "very_strong"))

    def test_phenotype_inconsistent_is_no_call(self):
        b = DeNovoAdapter().fetch(_evidence("de_novo", {
            "confirmed_parentage": True, "phenotype_consistent": False}))
        self.assertEqual(b.events, [])
        self.assertEqual(b.match["status"], "present_no_call")
        self.assertEqual(b.warnings, ["de_novo_no_call"])

    def test_de_novo_points_recorded_in_raw(self):
        b = DeNovoAdapter().fetch(_evidence("de_novo", {"confirmed_parentage": True}))
        self.assertEqual(b.events[0].raw["de_novo_points"], 2.0)


class TestPhasing(unittest.TestCase):
    def test_trans_pathogenic_recessive_is_pm3(self):
        b = PhasingAdapter().fetch(_evidence("phasing", {
            "phase": "trans", "partner_classification": "pathogenic", "inheritance": "recessive"}))
        self.assertEqual(_crit(b), ("PM3", "pathogenic", "moderate"))

    def test_cis_pathogenic_is_bp2(self):
        b = PhasingAdapter().fetch(_evidence("phasing", {
            "phase": "cis", "partner_classification": "pathogenic"}))
        self.assertEqual(_crit(b), ("BP2", "benign", "supporting"))

    def test_trans_pathogenic_dominant_is_bp2(self):
        b = PhasingAdapter().fetch(_evidence("phasing", {
            "phase": "trans", "partner_classification": "likely_pathogenic", "inheritance": "dominant"}))
        self.assertEqual(_crit(b), ("BP2", "benign", "supporting"))

    def test_benign_partner_is_no_call(self):
        b = PhasingAdapter().fetch(_evidence("phasing", {
            "phase": "trans", "partner_classification": "benign", "inheritance": "recessive"}))
        self.assertEqual(b.events, [])
        self.assertEqual(b.match["status"], "present_no_call")

    def test_absent(self):
        self.assertEqual(PhasingAdapter().fetch({"evidence": {}}).match["status"], "absent")

    def test_malformed(self):
        self.assertEqual(PhasingAdapter().fetch(_evidence("phasing", 5)).warnings, ["phasing_malformed"])


class TestSegregation(unittest.TestCase):
    def test_strong_cosegregation_is_pp1_strong(self):
        b = SegregationAdapter().fetch(_evidence("segregation", {"meioses": 7}))
        self.assertEqual(_crit(b), ("PP1", "pathogenic", "strong"))

    def test_non_segregation_is_bs4(self):
        b = SegregationAdapter().fetch(_evidence("segregation", {"meioses": 7, "segregates": False}))
        self.assertEqual(_crit(b), ("BS4", "benign", "strong"))

    def test_below_threshold_is_no_call(self):
        b = SegregationAdapter().fetch(_evidence("segregation", {"meioses": 2}))
        self.assertEqual(b.events, [])
        self.assertEqual(b.match["status"], "present_no_call")

    def test_absent(self):
        self.assertEqual(SegregationAdapter().fetch({"evidence": {}}).match["status"], "absent")

    def test_malformed(self):
        self.assertEqual(SegregationAdapter().fetch(_evidence("segregation", "x")).warnings,
                         ["segregation_malformed"])


class TestPhenotype(unittest.TestCase):
    def test_high_specificity_is_pp4_moderate(self):
        b = PhenotypeAdapter().fetch(_evidence("phenotype", {"specificity": "high"}))
        self.assertEqual(_crit(b), ("PP4", "pathogenic", "moderate"))

    def test_low_specificity_is_no_call(self):
        b = PhenotypeAdapter().fetch(_evidence("phenotype", {"specificity": "low"}))
        self.assertEqual(b.events, [])

    def test_absent(self):
        self.assertEqual(PhenotypeAdapter().fetch({"evidence": {}}).match["status"], "absent")

    def test_malformed(self):
        self.assertEqual(PhenotypeAdapter().fetch(_evidence("phenotype", [1, 2])).warnings,
                         ["phenotype_malformed"])


class TestFunctional(unittest.TestCase):
    def test_damaging_is_ps3(self):
        b = FunctionalAssayAdapter().fetch(_evidence("functional", {"result": "damaging"}))
        self.assertEqual(_crit(b), ("PS3", "pathogenic", "strong"))

    def test_normal_is_bs3(self):
        b = FunctionalAssayAdapter().fetch(_evidence("functional", {"result": "normal"}))
        self.assertEqual(_crit(b), ("BS3", "benign", "strong"))

    def test_intermediate_is_no_call(self):
        b = FunctionalAssayAdapter().fetch(_evidence("functional", {"result": "intermediate"}))
        self.assertEqual(b.events, [])

    def test_absent(self):
        self.assertEqual(FunctionalAssayAdapter().fetch({"evidence": {}}).match["status"], "absent")

    def test_malformed(self):
        self.assertEqual(FunctionalAssayAdapter().fetch(_evidence("functional", 3.0)).warnings,
                         ["functional_malformed"])


class TestDiseaseMechanism(unittest.TestCase):
    def test_missense_in_missense_mechanism_gene_is_pp2(self):
        b = DiseaseMechanismAdapter().fetch(_evidence("disease_mechanism", {
            "consequence": "missense", "missense_mechanism": True}))
        self.assertEqual(_crit(b), ("PP2", "pathogenic", "supporting"))

    def test_missense_in_truncating_only_gene_is_bp1(self):
        b = DiseaseMechanismAdapter().fetch(_evidence("disease_mechanism", {
            "consequence": "missense", "lof_mechanism": True}))
        self.assertEqual(_crit(b), ("BP1", "benign", "supporting"))

    def test_non_missense_is_no_call(self):
        b = DiseaseMechanismAdapter().fetch(_evidence("disease_mechanism", {
            "consequence": "frameshift", "lof_mechanism": True}))
        self.assertEqual(b.events, [])

    def test_absent(self):
        self.assertEqual(DiseaseMechanismAdapter().fetch({"evidence": {}}).match["status"], "absent")

    def test_malformed(self):
        self.assertEqual(DiseaseMechanismAdapter().fetch(_evidence("disease_mechanism", "x")).warnings,
                         ["disease_mechanism_malformed"])


class TestCaseControl(unittest.TestCase):
    def _record(self, **kw):
        base = {"odds_ratio": 8.0, "ci_low": 3.0, "case_count": 40, "case_total": 100,
                "control_count": 5, "control_total": 100}
        base.update(kw)
        return base

    def test_significant_enrichment_is_ps4(self):
        b = CaseControlAdapter().fetch(_evidence("case_control", self._record()))
        self.assertEqual(_crit(b), ("PS4", "pathogenic", "moderate"))  # OR 8 -> moderate (>=5)

    def test_strong_when_or_very_high(self):
        b = CaseControlAdapter().fetch(_evidence("case_control", self._record(odds_ratio=25.0)))
        self.assertEqual(_crit(b)[2], "strong")

    def test_not_significant_is_no_call_but_keeps_cohort(self):
        # No CI / p-value -> significance cannot be established -> no-call, never assumed.
        b = CaseControlAdapter().fetch(_evidence("case_control", {
            "odds_ratio": 8.0, "case_count": 40, "case_total": 100,
            "control_count": 5, "control_total": 100}))
        self.assertEqual(b.events, [])
        self.assertEqual(b.match["status"], "present_no_call")
        self.assertIsInstance(b.cohort_counts, CohortCounts)
        self.assertEqual(b.cohort_counts.denominator, 200)

    def test_cohort_counts_populated_on_call(self):
        b = CaseControlAdapter().fetch(_evidence("case_control", self._record()))
        cc = b.cohort_counts
        self.assertEqual((cc.case_count, cc.case_total), (40, 100))
        self.assertEqual((cc.control_count, cc.control_total), (5, 100))
        self.assertEqual(cc.denominator, 200)
        self.assertAlmostEqual(cc.odds_ratio, 8.0)

    def test_odds_ratio_computed_from_counts(self):
        b = CaseControlAdapter().fetch(_evidence("case_control", {
            "case_count": 40, "case_total": 100, "control_count": 5, "control_total": 100,
            "ci_low": 3.0}))
        # OR = (40*95)/(60*5) = 12.667
        self.assertAlmostEqual(b.cohort_counts.odds_ratio, (40 * 95) / (60 * 5))
        self.assertEqual(b.events[0].acmg_criterion, "PS4")

    def test_odds_ratio_from_counts_guards_zero_denominator(self):
        self.assertIsNone(odds_ratio_from_counts(
            {"case_count": 0, "case_total": 100, "control_count": 0, "control_total": 100}))

    def test_absent(self):
        self.assertEqual(CaseControlAdapter().fetch({"evidence": {}}).match["status"], "absent")

    def test_malformed_odds_ratio(self):
        b = CaseControlAdapter().fetch(_evidence("case_control", {"odds_ratio": "abc"}))
        self.assertEqual(b.warnings, ["case_control_malformed"])


class TestSignalsBlockAndBareRecord(unittest.TestCase):
    def test_reads_from_signals_block_too(self):
        b = DeNovoAdapter().fetch({"signals": {"de_novo": {"confirmed_parentage": True}}})
        self.assertEqual(b.events[0].acmg_criterion, "PS2")

    def test_accepts_bare_record(self):
        b = PhenotypeAdapter().fetch({"specificity": "high"})
        self.assertEqual(b.events[0].acmg_criterion, "PP4")


class TestAggregateProvider(unittest.TestCase):
    def test_merges_all_called_evidence(self):
        case = {"evidence": {
            "de_novo": {"confirmed_parentage": True},        # PS2 strong
            "functional": {"result": "damaging"},            # PS3 strong
            "phenotype": {"specificity": "high"},            # PP4 moderate
        }}
        bundle = UpstreamEvidenceProvider().fetch(case)
        crits = [e.acmg_criterion for e in bundle.events]
        # Order follows UPSTREAM_ADAPTER_CLASSES: de_novo, ..., phenotype, functional.
        self.assertEqual(crits, ["PS2", "PP4", "PS3"])
        self.assertEqual(bundle.match["evidence_types_called"], ["de_novo", "phenotype", "functional"])

    def test_aggregate_attaches_cohort_counts(self):
        case = {"evidence": {"case_control": {
            "odds_ratio": 8.0, "ci_low": 3.0, "case_count": 40, "case_total": 100,
            "control_count": 5, "control_total": 100}}}
        bundle = UpstreamEvidenceProvider().fetch(case)
        self.assertEqual(bundle.cohort_counts.denominator, 200)

    def test_events_feed_the_pure_engine(self):
        events = derive_upstream_events({"evidence": {
            "de_novo": {"confirmed_parentage": True},   # PS2 strong (+4)
            "functional": {"result": "damaging"},       # PS3 strong (+4)
        }})
        clf = classify(events)
        self.assertEqual(clf.total_points, 8.0)
        self.assertEqual(clf.tier, "Likely Pathogenic")

    def test_empty_case_is_all_absent(self):
        bundle = UpstreamEvidenceProvider().fetch({"evidence": {}})
        self.assertEqual(bundle.events, [])
        self.assertFalse(bundle.match["upstream_match"])
        self.assertIn("de_novo_absent", bundle.warnings)


if __name__ == "__main__":
    unittest.main()
