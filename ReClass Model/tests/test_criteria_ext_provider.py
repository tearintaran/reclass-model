"""Offline unit tests for the extended ACMG/AMP evidence providers (job1 task 4).

Covers the scoring-level derivation (engine.scoring.derive_extended_criteria) and
each reusable provider in evidence.criteria_ext: PVS1, PS3/BS3, PM3, PP1/BS4, PP4,
splice, and CNV. Everything runs from the committed coverage_ext_v1.json config; no
network, no large data files.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_criteria_ext_provider -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import (
    classify,
    derive_extended_criteria,
    load_coverage_ext,
)
from evidence.criteria_ext import (
    ComplexIndelProvider,
    CopyNumberProvider,
    ExtendedEvidenceProvider,
    FunctionalAssayProvider,
    InTransPm3Provider,
    MitochondrialProvider,
    NonCodingProvider,
    PhenotypeProvider,
    Pvs1Provider,
    RepeatExpansionProvider,
    SegregationProvider,
    SpliceProvider,
    StructuralVariantProvider,
)


def _only(events):
    """Assert exactly one event and return (criterion, direction, strength)."""
    assert len(events) == 1, events
    e = events[0]
    return e.acmg_criterion, e.evidence_direction, e.applied_strength


class TestCoverageExtConfig(unittest.TestCase):
    def test_config_loads_and_is_cached(self):
        a = load_coverage_ext()
        b = load_coverage_ext()
        self.assertIs(a, b)  # cached for the default path
        self.assertEqual(a["version"], "1.1.0")
        for key in ("pvs1", "functional", "pm3", "segregation", "phenotype", "splice", "cnv",
                    "noncoding", "complex_indel", "mito", "repeat", "sv"):
            self.assertIn(key, a)

    def test_all_configured_strengths_are_valid_engine_strengths(self):
        from engine import config as C
        valid = set(C.STRENGTH_POINTS)
        ext = load_coverage_ext()
        # Spot-check that every emitted strength would be scorable by the engine.
        for strength in ext["pvs1"]["lof_consequences"].values():
            self.assertIn(strength, valid)


class TestPvs1(unittest.TestCase):
    def test_frameshift_lof_is_very_strong(self):
        ev = derive_extended_criteria({"pvs1": {"consequence": "frameshift", "lof_mechanism": True}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "very_strong"))

    def test_nmd_escape_downgrades_to_strong(self):
        ev = derive_extended_criteria(
            {"pvs1": {"consequence": "nonsense", "lof_mechanism": True, "nmd_escape": True}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "strong"))

    def test_start_lost_is_moderate(self):
        ev = derive_extended_criteria({"pvs1": {"consequence": "start_lost", "lof_mechanism": True}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "moderate"))

    def test_not_applicable_without_lof_mechanism(self):
        # require_lof_mechanism gates PVS1: a LoF consequence in a non-LoF gene -> nothing.
        ev = derive_extended_criteria({"pvs1": {"consequence": "frameshift", "lof_mechanism": False}})
        self.assertEqual(ev, [])

    def test_missense_consequence_is_not_pvs1(self):
        ev = derive_extended_criteria({"pvs1": {"consequence": "missense", "lof_mechanism": True}})
        self.assertEqual(ev, [])

    def test_provider_match_block(self):
        bundle = Pvs1Provider().fetch(
            {"signals": {"pvs1": {"consequence": "frameshift", "lof_mechanism": True}}})
        self.assertTrue(bundle.match["pvs1_match"])
        self.assertEqual(bundle.match["criterion"], "PVS1")
        self.assertEqual(len(bundle.events), 1)

    def test_provider_no_signal_warns(self):
        bundle = Pvs1Provider().fetch({"signals": {}})
        self.assertEqual(bundle.events, [])
        self.assertEqual(bundle.warnings, ["no_pvs1_signal"])

    def test_provider_not_applicable_warns(self):
        bundle = Pvs1Provider().fetch(
            {"signals": {"pvs1": {"consequence": "missense", "lof_mechanism": True}}})
        self.assertEqual(bundle.events, [])
        self.assertEqual(bundle.warnings, ["pvs1_not_applicable"])


class TestFunctional(unittest.TestCase):
    def test_damaging_default_strong_ps3(self):
        ev = derive_extended_criteria({"functional": {"result": "damaging"}})
        self.assertEqual(_only(ev), ("PS3", "pathogenic", "strong"))

    def test_normal_default_strong_bs3(self):
        ev = derive_extended_criteria({"functional": {"result": "normal"}})
        self.assertEqual(_only(ev), ("BS3", "benign", "strong"))

    def test_oddspath_pathogenic_bins(self):
        ev = derive_extended_criteria({"functional": {"result": "damaging", "oddspath": 5.0}})
        self.assertEqual(_only(ev), ("PS3", "pathogenic", "moderate"))  # 5.0 >= 4.3

    def test_oddspath_benign_bins(self):
        ev = derive_extended_criteria({"functional": {"result": "normal", "oddspath": 0.1}})
        self.assertEqual(_only(ev), ("BS3", "benign", "moderate"))  # 0.1 <= 0.23

    def test_intermediate_assay_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"functional": {"result": "intermediate"}}), [])

    def test_explicit_strength_override(self):
        ev = derive_extended_criteria(
            {"functional": {"result": "damaging", "strength": "supporting"}})
        self.assertEqual(_only(ev), ("PS3", "pathogenic", "supporting"))


class TestPm3(unittest.TestCase):
    def test_explicit_points_to_strong(self):
        ev = derive_extended_criteria({"pm3": {"points": 2.0}})
        self.assertEqual(_only(ev), ("PM3", "pathogenic", "strong"))

    def test_observations_sum_to_moderate(self):
        ev = derive_extended_criteria({"pm3": {"observations": [{"type": "trans_pathogenic"}]}})
        self.assertEqual(_only(ev), ("PM3", "pathogenic", "moderate"))  # 1.0 point

    def test_two_pathogenic_in_trans_is_strong(self):
        ev = derive_extended_criteria({"pm3": {"observations": [
            {"type": "trans_pathogenic"}, {"type": "trans_pathogenic"}]}})
        self.assertEqual(_only(ev), ("PM3", "pathogenic", "strong"))  # 2.0 points

    def test_below_threshold_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"pm3": {"points": 0.25}}), [])

    def test_points_recorded_in_raw(self):
        ev = derive_extended_criteria({"pm3": {"observations": [{"type": "trans_pathogenic"}]}})
        self.assertEqual(ev[0].raw["pm3_points"], 1.0)


class TestSegregation(unittest.TestCase):
    def test_pp1_moderate_at_5_meioses(self):
        ev = derive_extended_criteria({"segregation": {"meioses": 5, "segregates": True}})
        self.assertEqual(_only(ev), ("PP1", "pathogenic", "moderate"))

    def test_pp1_supporting_at_3_meioses(self):
        ev = derive_extended_criteria({"segregation": {"meioses": 3}})  # segregates defaults True
        self.assertEqual(_only(ev), ("PP1", "pathogenic", "supporting"))

    def test_below_threshold_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"segregation": {"meioses": 2}}), [])

    def test_bs4_non_segregation(self):
        ev = derive_extended_criteria({"segregation": {"meioses": 7, "segregates": False}})
        self.assertEqual(_only(ev), ("BS4", "benign", "strong"))


class TestPhenotype(unittest.TestCase):
    def test_high_specificity_is_pp4_moderate(self):
        ev = derive_extended_criteria({"phenotype": {"specificity": "high"}})
        self.assertEqual(_only(ev), ("PP4", "pathogenic", "moderate"))

    def test_string_signal_accepted(self):
        ev = derive_extended_criteria({"phenotype": "moderate"})
        self.assertEqual(_only(ev), ("PP4", "pathogenic", "supporting"))

    def test_low_specificity_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"phenotype": {"specificity": "low"}}), [])


class TestSplice(unittest.TestCase):
    def test_canonical_site_routes_to_pvs1(self):
        ev = derive_extended_criteria({"splice": {"canonical_site": True, "score": 0.99}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "very_strong"))

    def test_high_delta_is_pp3_strong(self):
        ev = derive_extended_criteria({"splice": {"score": 0.9}})
        self.assertEqual(_only(ev), ("PP3", "pathogenic", "strong"))

    def test_low_delta_is_bp4(self):
        ev = derive_extended_criteria({"splice": {"score": 0.05}})
        self.assertEqual(_only(ev), ("BP4", "benign", "supporting"))

    def test_indeterminate_delta_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"splice": {"score": 0.15}}), [])


class TestCnv(unittest.TestCase):
    def test_full_gene_del_is_pvs1(self):
        ev = derive_extended_criteria({"cnv": "del_full_gene_haploinsufficient"})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "very_strong"))

    def test_partial_inframe_del_is_pm4(self):
        ev = derive_extended_criteria({"cnv": {"category": "del_partial_inframe"}})
        self.assertEqual(_only(ev), ("PM4", "pathogenic", "moderate"))

    def test_unknown_category_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"cnv": {"category": "nonsense_category"}}), [])


class TestNonCoding(unittest.TestCase):
    def test_promoter_established_is_pm1_moderate(self):
        ev = derive_extended_criteria({"noncoding": "promoter_established"})
        self.assertEqual(_only(ev), ("PM1", "pathogenic", "moderate"))

    def test_deep_intronic_predicted_splice_is_pp3(self):
        ev = derive_extended_criteria({"noncoding": {"category": "deep_intronic_predicted_splice"}})
        self.assertEqual(_only(ev), ("PP3", "pathogenic", "supporting"))

    def test_no_predicted_effect_is_bp7(self):
        ev = derive_extended_criteria({"noncoding": "noncoding_no_predicted_effect"})
        self.assertEqual(_only(ev), ("BP7", "benign", "supporting"))

    def test_unknown_category_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"noncoding": "not_a_category"}), [])

    def test_provider_not_applicable_warns(self):
        bundle = NonCodingProvider().fetch({"signals": {"noncoding": "not_a_category"}})
        self.assertEqual(bundle.events, [])
        self.assertEqual(bundle.warnings, ["noncoding_not_applicable"])


class TestComplexIndel(unittest.TestCase):
    def test_frameshift_lof_is_pvs1(self):
        ev = derive_extended_criteria({"complex_indel": {"frame": "frameshift", "lof_mechanism": True}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "very_strong"))

    def test_frameshift_without_lof_yields_nothing(self):
        # A frameshift in a possible gain-of-function gene must not fire PVS1.
        self.assertEqual(
            derive_extended_criteria({"complex_indel": {"frame": "frameshift", "lof_mechanism": False}}), [])

    def test_inframe_nonrepeat_is_pm4(self):
        ev = derive_extended_criteria({"complex_indel": {"frame": "inframe"}})
        self.assertEqual(_only(ev), ("PM4", "pathogenic", "moderate"))

    def test_inframe_in_repeat_region_yields_nothing(self):
        # PM4 does not apply to an in-frame indel inside a repeat/low-complexity region.
        self.assertEqual(
            derive_extended_criteria({"complex_indel": {"frame": "inframe", "repeat_region": True}}), [])


class TestMitochondrial(unittest.TestCase):
    def test_common_homoplasmy_is_ba1(self):
        ev = derive_extended_criteria({"mito": {"af": 0.01}})  # >= mt ba1 0.005
        self.assertEqual(_only(ev), ("BA1", "benign", "stand_alone"))

    def test_mt_pm2_threshold_is_lower_than_autosomal(self):
        # 0.00001 is BELOW the mt PM2 cut-point (2e-5) -> PM2 here, but would be
        # nothing under the autosomal 1e-4 PM2 (this is the mtDNA divergence).
        ev = derive_extended_criteria({"mito": {"af": 0.00001}})
        self.assertEqual(_only(ev), ("PM2", "pathogenic", "supporting"))

    def test_intermediate_af_falls_through_to_heteroplasmy(self):
        ev = derive_extended_criteria(
            {"mito": {"af": 0.001, "heteroplasmy": 0.8, "het_segregates": True}})
        self.assertEqual(_only(ev), ("PS4", "pathogenic", "moderate"))

    def test_low_heteroplasmy_without_segregation_yields_nothing(self):
        self.assertEqual(
            derive_extended_criteria({"mito": {"heteroplasmy": 0.3, "het_segregates": False}}), [])


class TestRepeatExpansion(unittest.TestCase):
    def test_full_penetrance_expansion_is_pvs1(self):
        ev = derive_extended_criteria({"repeat": {"locus": "HTT", "repeat_count": 42}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "very_strong"))

    def test_reduced_penetrance_is_pvs1_moderate(self):
        ev = derive_extended_criteria({"repeat": {"locus": "HTT", "repeat_count": 37}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "moderate"))

    def test_normal_count_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"repeat": {"locus": "HTT", "repeat_count": 20}}), [])

    def test_unknown_locus_yields_nothing(self):
        self.assertEqual(derive_extended_criteria({"repeat": {"locus": "ZZZ9", "repeat_count": 500}}), [])

    def test_fmr1_premutation_band(self):
        ev = derive_extended_criteria({"repeat": {"locus": "FMR1", "repeat_count": 100}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "moderate"))  # premutation -> reduced


class TestStructuralVariant(unittest.TestCase):
    def test_haploinsufficient_deletion_is_pvs1(self):
        ev = derive_extended_criteria({"sv": "del_haploinsufficient_gene"})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "very_strong"))

    def test_dosage_gene_cross_check_blocks_non_member(self):
        # The category asserts haploinsufficiency, but the named gene is not established
        # dosage-sensitive -> not-applicable rather than a spurious PVS1.
        self.assertEqual(
            derive_extended_criteria(
                {"sv": {"category": "del_haploinsufficient_gene", "gene": "NOTADOSAGEGENE"}}), [])

    def test_dosage_gene_cross_check_allows_member(self):
        ev = derive_extended_criteria(
            {"sv": {"category": "del_haploinsufficient_gene", "gene": "TP53"}})
        self.assertEqual(_only(ev), ("PVS1", "pathogenic", "very_strong"))

    def test_benign_common_sv_is_ba1(self):
        ev = derive_extended_criteria({"sv": "sv_benign_common"})
        self.assertEqual(_only(ev), ("BA1", "benign", "stand_alone"))

    def test_dup_no_dosage_effect_is_bp4(self):
        ev = derive_extended_criteria({"sv": "dup_no_known_dosage_effect"})
        self.assertEqual(_only(ev), ("BP4", "benign", "supporting"))


class TestA2ProvidersResolveAndScore(unittest.TestCase):
    def test_each_new_provider_resolves_its_signal(self):
        signals = {
            "noncoding": "promoter_established",
            "complex_indel": {"frame": "frameshift", "lof_mechanism": True},
            "mito": {"af": 0.00001},
            "repeat": {"locus": "HTT", "repeat_count": 42},
            "sv": "del_haploinsufficient_gene",
        }
        case = {"signals": signals, "locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}}
        for prov in (NonCodingProvider(), ComplexIndelProvider(), MitochondrialProvider(),
                     RepeatExpansionProvider(), StructuralVariantProvider()):
            bundle = prov.fetch(case)
            self.assertEqual(len(bundle.events), 1, prov.name)
            self.assertTrue(bundle.match[f"{prov.name}_match"], prov.name)

    def test_aggregate_includes_new_classes_in_config_order(self):
        signals = {
            "pvs1": {"consequence": "frameshift", "lof_mechanism": True},
            "noncoding": "promoter_established",
            "repeat": {"locus": "HTT", "repeat_count": 42},
        }
        bundle = ExtendedEvidenceProvider().fetch({"signals": signals})
        crits = [e.acmg_criterion for e in bundle.events]
        # Order follows _EXT_MAPPERS: pvs1 ... noncoding ... repeat.
        self.assertEqual(crits, ["PVS1", "PM1", "PVS1"])
        self.assertEqual(bundle.match["providers_matched"], ["pvs1", "noncoding", "repeat"])

    def test_repeat_expansion_feeds_pure_engine(self):
        events = derive_extended_criteria({"repeat": {"locus": "HTT", "repeat_count": 42}})
        clf = classify(events)
        self.assertEqual(clf.tier, "Likely Pathogenic")  # PVS1 very_strong = +8
        self.assertEqual(clf.total_points, 8.0)


class TestProvidersAndAggregate(unittest.TestCase):
    def test_each_provider_resolves_its_own_signal(self):
        signals = {
            "pvs1": {"consequence": "frameshift", "lof_mechanism": True},
            "functional": {"result": "damaging"},
            "pm3": {"points": 1.0},
            "segregation": {"meioses": 5},
            "phenotype": {"specificity": "high"},
            "splice": {"score": 0.9},
            "cnv": "del_full_gene_haploinsufficient",
        }
        case = {"signals": signals, "locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}}
        for prov in (Pvs1Provider(), FunctionalAssayProvider(), InTransPm3Provider(),
                     SegregationProvider(), PhenotypeProvider(), SpliceProvider(),
                     CopyNumberProvider()):
            bundle = prov.fetch(case)
            self.assertEqual(len(bundle.events), 1, prov.name)
            self.assertEqual(bundle.variant_key, "GRCh38-1-100-A-G")

    def test_aggregate_merges_all_in_config_order(self):
        signals = {
            "pvs1": {"consequence": "nonsense", "lof_mechanism": True},
            "functional": {"result": "normal"},
            "splice": {"score": 0.9},
        }
        bundle = ExtendedEvidenceProvider().fetch({"signals": signals})
        crits = [e.acmg_criterion for e in bundle.events]
        # Order follows _EXT_MAPPERS: pvs1, functional, ..., splice.
        self.assertEqual(crits, ["PVS1", "BS3", "PP3"])
        self.assertTrue(bundle.match["coverage_ext_match"])
        self.assertEqual(bundle.match["providers_matched"], ["pvs1", "functional_assay", "splice"])

    def test_aggregate_with_no_signals_is_empty_but_valid(self):
        bundle = ExtendedEvidenceProvider().fetch({"signals": {}})
        self.assertEqual(bundle.events, [])
        self.assertFalse(bundle.match["coverage_ext_match"])
        # Each sub-provider contributed a no-signal warning.
        self.assertIn("no_pvs1_signal", bundle.warnings)

    def test_events_feed_the_pure_engine(self):
        # PVS1 very_strong (+8) -> Likely Pathogenic; events round-trip through classify.
        events = derive_extended_criteria({"pvs1": {"consequence": "frameshift", "lof_mechanism": True}})
        clf = classify(events)
        self.assertEqual(clf.tier, "Likely Pathogenic")
        self.assertEqual(clf.total_points, 8.0)
        self.assertEqual(clf.contributions[0].source, "pvs1")


if __name__ == "__main__":
    unittest.main()
