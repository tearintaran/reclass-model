"""Offline unit tests for the A3 computational providers (gap.md Track A3).

Covers the engine-level mappers (AlphaMissense, conservation, REVEL+AlphaMissense
consensus) and the cache-backed providers (AlphaMissense, conservation, gene
constraint). Everything runs from in-memory scores / the committed
computational_ext_v1.json config -- no network, no large data files.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_computational_providers -v
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import (  # noqa: E402
    classify_signals,
    derive_criteria_from_signals,
    load_computational_ext,
    resolve_missense_consensus,
)
from evidence.alphamissense import AlphaMissenseProvider  # noqa: E402
from evidence.computational import (  # noqa: E402
    ConservationProvider,
    GeneConstraintProvider,
    classify_constraint,
)
from evidence.revel import variant_key  # noqa: E402


def _one(events):
    assert len(events) == 1, events
    e = events[0]
    return e.source, e.acmg_criterion, e.evidence_direction, e.applied_strength


class TestAlphaMissenseMapper(unittest.TestCase):
    def test_high_score_is_pp3_moderate(self):
        ev = derive_criteria_from_signals({"alphamissense": 0.995})
        self.assertEqual(_one(ev), ("alphamissense", "PP3", "pathogenic", "moderate"))

    def test_mid_score_is_pp3_supporting(self):
        ev = derive_criteria_from_signals({"alphamissense": 0.7})
        self.assertEqual(_one(ev), ("alphamissense", "PP3", "pathogenic", "supporting"))

    def test_low_score_is_bp4_moderate(self):
        ev = derive_criteria_from_signals({"alphamissense": 0.05})
        self.assertEqual(_one(ev), ("alphamissense", "BP4", "benign", "moderate"))

    def test_ambiguous_band_yields_nothing(self):
        self.assertEqual(derive_criteria_from_signals({"alphamissense": 0.45}), [])


class TestConservationMapper(unittest.TestCase):
    def test_conserved_position_is_pp3_supporting(self):
        ev = derive_criteria_from_signals({"conservation": 2.5})
        self.assertEqual(_one(ev), ("conservation", "PP3", "pathogenic", "supporting"))

    def test_fast_evolving_position_is_bp4_supporting(self):
        ev = derive_criteria_from_signals({"conservation": -1.2})
        self.assertEqual(_one(ev), ("conservation", "BP4", "benign", "supporting"))

    def test_indeterminate_conservation_yields_nothing(self):
        self.assertEqual(derive_criteria_from_signals({"conservation": 0.8}), [])


class TestMissensePredictorConsensus(unittest.TestCase):
    def test_revel_only_is_byte_identical_to_legacy(self):
        # A REVEL-only signal must still produce the historical single REVEL event.
        ev = derive_criteria_from_signals({"revel": 0.95})
        self.assertEqual(_one(ev), ("revel", "PP3", "pathogenic", "strong"))
        self.assertEqual(ev[0].source_version, "REVEL")

    def test_alphamissense_only(self):
        ev = derive_criteria_from_signals({"alphamissense": 0.7})
        self.assertEqual(_one(ev), ("alphamissense", "PP3", "pathogenic", "supporting"))

    def test_agreement_takes_stronger_and_combines_to_one_event(self):
        # REVEL 0.95 (PP3 strong) + AlphaMissense 0.7 (PP3 supporting): agree -> ONE
        # PP3 at the stronger (strong), not two stacked predictors.
        ev = derive_criteria_from_signals({"revel": 0.95, "alphamissense": 0.7})
        self.assertEqual(len(ev), 1)
        self.assertEqual(_one(ev), ("revel+alphamissense", "PP3", "pathogenic", "strong"))
        self.assertTrue(ev[0].raw["agreement"])

    def test_agreement_benign(self):
        ev = derive_criteria_from_signals({"revel": 0.01, "alphamissense": 0.05})
        self.assertEqual(_one(ev), ("revel+alphamissense", "BP4", "benign", "strong"))

    def test_disagreement_conservative_is_no_call(self):
        # REVEL pathogenic vs AlphaMissense benign -> default conservative -> nothing.
        ev = derive_criteria_from_signals({"revel": 0.95, "alphamissense": 0.05})
        self.assertEqual(ev, [])

    def test_disagreement_policy_revel(self):
        comp = copy.deepcopy(load_computational_ext())
        comp["missense_consensus"]["disagreement_policy"] = "revel"
        ev = resolve_missense_consensus(0.95, 0.05, comp=comp)
        self.assertEqual(ev.acmg_criterion, "PP3")
        self.assertEqual(ev.evidence_direction, "pathogenic")
        self.assertFalse(ev.raw["agreement"])
        self.assertEqual(ev.raw["chosen"], "revel")

    def test_disagreement_policy_alphamissense(self):
        comp = copy.deepcopy(load_computational_ext())
        comp["missense_consensus"]["disagreement_policy"] = "alphamissense"
        ev = resolve_missense_consensus(0.95, 0.05, comp=comp)
        self.assertEqual(ev.acmg_criterion, "BP4")
        self.assertEqual(ev.evidence_direction, "benign")

    def test_consensus_feeds_pure_engine(self):
        clf = classify_signals({"alphamissense": 0.995,
                                "criteria": [{"criterion": "PVS1", "direction": "pathogenic",
                                              "strength": "very_strong"}]})
        self.assertEqual(clf.total_points, 10.0)  # PP3 moderate (+2) + PVS1 (+8)
        self.assertEqual(clf.tier, "Pathogenic")


class TestAlphaMissenseProvider(unittest.TestCase):
    def _case(self):
        return {"locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}}

    def test_resolved_score_emits_engine_event(self):
        prov = AlphaMissenseProvider.from_scores({variant_key("1", 100, "A", "G"): 0.995})
        bundle = prov.fetch(self._case())
        self.assertTrue(bundle.match["alphamissense_match"])
        self.assertEqual(len(bundle.events), 1)
        self.assertEqual(bundle.events[0].acmg_criterion, "PP3")
        self.assertEqual(prov.stats.matched, 1)

    def test_absent_score_is_empty_but_valid(self):
        prov = AlphaMissenseProvider.from_scores({})
        bundle = prov.fetch(self._case())
        self.assertEqual(bundle.events, [])
        self.assertEqual(bundle.warnings, ["no_alphamissense_score"])
        self.assertFalse(bundle.match["alphamissense_match"])

    def test_missing_locus_warns(self):
        bundle = AlphaMissenseProvider.from_scores({}).fetch({"gene": "X"})
        self.assertEqual(bundle.warnings, ["missing_locus"])

    def test_ambiguous_band_is_actionable_false(self):
        prov = AlphaMissenseProvider.from_scores({variant_key("1", 100, "A", "G"): 0.45})
        bundle = prov.fetch(self._case())
        self.assertEqual(bundle.events, [])
        self.assertIn("alphamissense_ambiguous_band", bundle.warnings)


class TestConservationProvider(unittest.TestCase):
    def test_conserved_position_emits_pp3(self):
        prov = ConservationProvider.from_scores({"1-100": 2.5})
        bundle = prov.fetch({"locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}})
        self.assertEqual(len(bundle.events), 1)
        self.assertEqual(bundle.events[0].acmg_criterion, "PP3")

    def test_absent_position_warns(self):
        bundle = ConservationProvider.from_scores({}).fetch(
            {"locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}})
        self.assertEqual(bundle.warnings, ["no_conservation_score"])


class TestGeneConstraintProvider(unittest.TestCase):
    def test_constrained_gene_classified_but_no_points(self):
        prov = GeneConstraintProvider.from_metrics(
            {"NF1": {"loeuf": 0.1, "pli": 0.99, "missense_z": 4.0}})
        bundle = prov.fetch({"gene": "NF1"})
        self.assertEqual(bundle.events, [])                 # context only, no ACMG points
        self.assertTrue(bundle.match["gene_constraint_match"])
        self.assertTrue(bundle.match["lof_constrained"])
        self.assertTrue(bundle.match["missense_constrained"])
        self.assertEqual(bundle.warnings, ["constraint_context_only"])

    def test_unconstrained_gene(self):
        cls = classify_constraint({"loeuf": 1.2, "pli": 0.1, "missense_z": 0.5})
        self.assertFalse(cls["lof_constrained"])
        self.assertFalse(cls["missense_constrained"])

    def test_unknown_gene_warns(self):
        bundle = GeneConstraintProvider.from_metrics({}).fetch({"gene": "ZZZ9"})
        self.assertEqual(bundle.warnings, ["no_constraint_data"])
        self.assertFalse(bundle.match["gene_constraint_match"])

    def test_pli_alone_marks_lof_constrained(self):
        cls = classify_constraint({"pli": 0.95})
        self.assertTrue(cls["lof_constrained"])


if __name__ == "__main__":
    unittest.main()
