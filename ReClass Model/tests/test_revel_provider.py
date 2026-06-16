"""Unit tests for the REVEL evidence provider (evidence/revel.py).

Covers locus extraction, the lookup index + JSON cache round-trip, the
EvidenceBundle a hit/miss produces, indeterminate-band handling, provider stats,
determinism for a fixed snapshot, and that scores routed through the provider
reproduce the engine's PP3/BP4 mapping exactly. All offline -- no REVEL zip needed.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import classify, classify_signals, derive_criteria_from_signals
from evidence.model import EvidenceBundle
from evidence.revel import (
    PROVIDER_NAME,
    PROVIDER_VERSION,
    RevelIndex,
    RevelProvider,
    locus_of,
    variant_key,
)


# A locus with a strongly pathogenic REVEL score (>= 0.932 -> PP3 strong) and one
# in the indeterminate band (0.290 < s < 0.644 -> no criterion).
PATHO_KEY = variant_key("1", 12345, "C", "A")
BAND_KEY = variant_key("2", 22222, "G", "T")
SCORES = {PATHO_KEY: 0.95, BAND_KEY: 0.5}


def _case(chrom, pos, ref, alt):
    return {"locus": {"chrom": chrom, "pos": pos, "ref": ref, "alt": alt,
                      "snv": True, "missense": True}}


class TestLocusExtraction(unittest.TestCase):
    def test_variant_key_normalizes_types(self):
        self.assertEqual(variant_key(1, "12345", "C", "A"), "1-12345-C-A")

    def test_locus_of_from_case_dict(self):
        self.assertEqual(locus_of(_case("1", 12345, "C", "A")), ("1", 12345, "C", "A"))

    def test_locus_of_from_bare_dict_tuple_string(self):
        self.assertEqual(locus_of({"chrom": "X", "pos": 5, "ref": "A", "alt": "G"}), ("X", 5, "A", "G"))
        self.assertEqual(locus_of(("1", 7, "A", "T")), ("1", 7, "A", "T"))
        self.assertEqual(locus_of("1-7-A-T"), ("1", 7, "A", "T"))

    def test_locus_of_returns_none_when_unresolvable(self):
        self.assertIsNone(locus_of({"locus": {"chrom": "1"}}))
        self.assertIsNone(locus_of("not-a-key"))
        self.assertIsNone(locus_of(None))


class TestRevelIndex(unittest.TestCase):
    def test_lookup_hit_and_miss(self):
        index = RevelIndex.from_scores(SCORES)
        self.assertEqual(index.lookup("1", 12345, "C", "A"), 0.95)
        self.assertIsNone(index.lookup("1", 12345, "C", "T"))
        self.assertEqual(len(index), 2)

    def test_cache_round_trip(self):
        index = RevelIndex.from_scores(SCORES)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "revel_cache.json")  # exercises mkdir
            index.to_cache(path)
            reloaded = RevelIndex.from_cache(path)
        self.assertEqual(reloaded.lookup("1", 12345, "C", "A"), 0.95)
        self.assertEqual(len(reloaded), 2)

    def test_from_cache_missing_file_is_empty(self):
        self.assertEqual(len(RevelIndex.from_cache("/no/such/cache.json")), 0)


class TestRevelProviderFetch(unittest.TestCase):
    def setUp(self):
        self.provider = RevelProvider.from_scores(SCORES)

    def test_hit_emits_pp3_event_and_provenance(self):
        bundle = self.provider.fetch(_case("1", 12345, "C", "A"))
        self.assertEqual(len(bundle.events), 1)
        self.assertEqual(bundle.events[0].acmg_criterion, "PP3")
        self.assertEqual(bundle.events[0].evidence_direction, "pathogenic")
        self.assertEqual(bundle.events[0].applied_strength, "strong")
        self.assertEqual(bundle.provider_versions, {PROVIDER_NAME: PROVIDER_VERSION})
        self.assertEqual(bundle.variant_key, PATHO_KEY)
        self.assertTrue(bundle.match["revel_match"])
        self.assertEqual(bundle.match["revel_score"], 0.95)
        self.assertTrue(bundle.match["actionable"])
        self.assertEqual(bundle.warnings, [])
        rec = bundle.source_records[0]
        self.assertEqual(rec["revel_score"], 0.95)
        self.assertEqual(rec["dataset"], PROVIDER_VERSION)
        self.assertEqual(rec["variant_key"], PATHO_KEY)

    def test_miss_is_empty_bundle_with_warning(self):
        bundle = self.provider.fetch(_case("1", 12345, "C", "T"))
        self.assertEqual(bundle.events, [])
        self.assertEqual(bundle.warnings, ["no_revel_score"])
        self.assertFalse(bundle.match["revel_match"])
        self.assertIsNone(bundle.match["revel_score"])
        self.assertEqual(bundle.source_records, [])

    def test_indeterminate_band_is_matched_but_eventless(self):
        bundle = self.provider.fetch(_case("2", 22222, "G", "T"))
        self.assertEqual(bundle.events, [])
        self.assertTrue(bundle.match["revel_match"])
        self.assertFalse(bundle.match["actionable"])
        self.assertEqual(bundle.warnings, ["revel_indeterminate_band"])

    def test_missing_locus_warns_and_does_not_raise(self):
        bundle = self.provider.fetch({"locus": {"chrom": "1"}})
        self.assertEqual(bundle.warnings, ["missing_locus"])
        self.assertEqual(bundle.events, [])
        self.assertFalse(bundle.match["revel_match"])

    def test_provider_stats(self):
        self.provider.fetch(_case("1", 12345, "C", "A"))  # matched
        self.provider.fetch(_case("2", 22222, "G", "T"))  # matched (band)
        self.provider.fetch(_case("1", 12345, "C", "T"))  # absent
        self.provider.fetch({"locus": {}})                # failed
        self.assertEqual(
            self.provider.stats.as_dict(),
            {"queried": 4, "matched": 2, "absent": 1, "failed": 1, "cached": 2},
        )

    def test_fetch_is_deterministic_for_fixed_snapshot(self):
        a = self.provider.fetch(_case("1", 12345, "C", "A")).to_dict()
        b = RevelProvider.from_scores(SCORES).fetch(_case("1", 12345, "C", "A")).to_dict()
        self.assertEqual(a, b)

    def test_reproduces_engine_mapping(self):
        # The provider's events must match what the engine derives from signals.revel,
        # so an existing fixture score routed through the provider scores identically.
        bundle = self.provider.fetch(_case("1", 12345, "C", "A"))
        expected = derive_criteria_from_signals({"revel": 0.95})
        self.assertEqual([e.acmg_criterion for e in bundle.events],
                         [e.acmg_criterion for e in expected])
        self.assertEqual(classify(bundle.events).tier,
                         classify_signals({"revel": 0.95}).tier)

    def test_bundle_json_round_trip(self):
        bundle = self.provider.fetch(_case("1", 12345, "C", "A"))
        restored = EvidenceBundle.from_json(bundle.to_json())
        self.assertEqual(restored.to_dict(), bundle.to_dict())


if __name__ == "__main__":
    unittest.main()
