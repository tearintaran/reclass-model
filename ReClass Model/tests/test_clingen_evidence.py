"""Unit tests for the ClinGen evidence index + provider.

Covers index construction, the deterministic duplicate-resolution policy, sentinel
ID handling, no-match behavior, and the EvidenceBundle a match produces.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import classify
from engine.reference import InMemoryReference
from evidence.clingen import (
    PROVIDER_NAME,
    PROVIDER_VERSION,
    ClinGenEvidenceProvider,
    ClinGenIndex,
    applied_criteria,
    event_to_criterion,
    is_valid_variation_id,
)


def _crit(name, direction, strength):
    return {
        "criterion": name,
        "direction": direction,
        "strength": strength,
        "source": "clingen",
        "version": "ERepo",
    }


def _cg_case(case_id, clinvar_id, expected, criteria):
    return {
        "id": case_id,
        "gene": "GENE",
        "expected": expected,
        "signals": {"criteria": criteria},
        "provenance": {"source": "ClinGen ERepo", "clinvar_id": clinvar_id},
    }


# A small in-memory ClinGen fixture with:
#  - a unique ID (200),
#  - a duplicated ID (100) with tier/criteria differences,
#  - two sentinel/blank IDs that must be excluded.
CASES = [
    _cg_case("CG-200", "200", "Pathogenic", [_crit("PVS1", "pathogenic", "very_strong")]),
    _cg_case("CG-100a", "100", "VUS", [_crit("PM2", "pathogenic", "moderate")]),
    _cg_case(
        "CG-100b",
        "100",
        "Pathogenic",
        [_crit("PM2", "pathogenic", "moderate"), _crit("PP3", "pathogenic", "supporting")],
    ),
    _cg_case("CG-sent", "-", "Benign", [_crit("BA1", "benign", "stand_alone")]),
    _cg_case("CG-blank", "", "Benign", [_crit("BA1", "benign", "stand_alone")]),
]


class TestValidId(unittest.TestCase):
    def test_sentinels_are_invalid(self):
        for bad in ("", "-", ".", "NA", "n/a", "None", "null", None, "  -  "):
            self.assertFalse(is_valid_variation_id(bad), bad)

    def test_real_ids_are_valid(self):
        for good in ("100", " 586 ", 12345):
            self.assertTrue(is_valid_variation_id(good), good)


class TestIndexConstruction(unittest.TestCase):
    def setUp(self):
        self.index = ClinGenIndex.from_cases(CASES)

    def test_excludes_sentinel_and_blank_ids(self):
        # Only 200 and 100 are valid; sentinel "-" and "" are skipped.
        self.assertEqual(self.index.variation_ids, {"200", "100"})
        self.assertEqual(self.index.skipped_invalid_id, 2)
        self.assertEqual(len(self.index), 2)

    def test_contains_and_candidates(self):
        self.assertIn("100", self.index)
        self.assertNotIn("999", self.index)
        self.assertEqual(len(self.index.candidates("100")), 2)
        self.assertEqual(self.index.candidates("999"), [])

    def test_duplicate_ids(self):
        self.assertEqual(self.index.duplicate_ids, ["100"])


class TestDeterministicResolution(unittest.TestCase):
    def setUp(self):
        self.index = ClinGenIndex.from_cases(CASES)

    def test_prefers_tier_matching_record(self):
        # ClinVar says Pathogenic -> pick the Pathogenic ClinGen record (CG-100b).
        self.assertEqual(self.index.resolve("100", "Pathogenic")["id"], "CG-100b")

    def test_falls_back_to_more_criteria_when_no_tier_match(self):
        # ClinVar says Benign (neither candidate matches) -> pick more criteria.
        self.assertEqual(self.index.resolve("100", "Benign")["id"], "CG-100b")

    def test_tier_match_beats_criteria_count(self):
        # ClinVar says VUS: CG-100a matches tier (1 criterion) and must win over the
        # richer-but-non-matching CG-100b.
        self.assertEqual(self.index.resolve("100", "VUS")["id"], "CG-100a")

    def test_stable_tiebreak_on_case_id(self):
        # No expected tier given and equal criteria counts -> sort by case id.
        index = ClinGenIndex.from_cases(
            [
                _cg_case("CG-z", "300", "VUS", [_crit("PM2", "pathogenic", "moderate")]),
                _cg_case("CG-a", "300", "VUS", [_crit("PM2", "pathogenic", "moderate")]),
            ]
        )
        self.assertEqual(index.resolve("300")["id"], "CG-a")

    def test_resolution_is_deterministic_regardless_of_input_order(self):
        forward = ClinGenIndex.from_cases(CASES).resolve("100", "Pathogenic")["id"]
        reverse = ClinGenIndex.from_cases(list(reversed(CASES))).resolve("100", "Pathogenic")["id"]
        self.assertEqual(forward, reverse)

    def test_no_match_returns_none(self):
        self.assertIsNone(self.index.resolve("999", "Pathogenic"))


class TestAppliedCriteriaProvenance(unittest.TestCase):
    def test_tags_provenance_without_changing_scoring(self):
        record = CASES[0]
        tagged = applied_criteria(record)
        self.assertEqual(tagged[0]["raw"]["provider"], PROVIDER_NAME)
        self.assertEqual(tagged[0]["raw"]["clingen_case_id"], "CG-200")
        # Original fields are preserved; the source record is untouched.
        self.assertEqual(tagged[0]["criterion"], "PVS1")
        self.assertNotIn("raw", record["signals"]["criteria"][0])


class TestProviderFetch(unittest.TestCase):
    def setUp(self):
        self.provider = ClinGenEvidenceProvider(ClinGenIndex.from_cases(CASES))

    def test_match_returns_events_and_match_block(self):
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "200"}}
        bundle = self.provider.fetch(case)
        self.assertEqual(len(bundle.events), 1)
        self.assertTrue(bundle.match["clingen_variation_id_match"])
        self.assertEqual(bundle.match["clingen_case_id"], "CG-200")
        self.assertEqual(bundle.provider_versions, {PROVIDER_NAME: PROVIDER_VERSION})
        self.assertEqual(bundle.variant_key, "clinvar_variation_id:200")
        self.assertEqual(bundle.warnings, [])

    def test_no_match_is_empty_bundle_with_warning(self):
        case = {"expected": "VUS", "provenance": {"variation_id": "999"}}
        bundle = self.provider.fetch(case)
        self.assertEqual(bundle.events, [])
        self.assertFalse(bundle.match["clingen_variation_id_match"])
        self.assertEqual(bundle.warnings, ["no_clingen_match"])

    def test_missing_variation_id_warns(self):
        bundle = self.provider.fetch({"expected": "VUS", "provenance": {"variation_id": "-"}})
        self.assertEqual(bundle.warnings, ["missing_variation_id"])
        self.assertEqual(bundle.events, [])

    def test_multiple_matches_warns(self):
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "100"}}
        bundle = self.provider.fetch(case)
        self.assertIn("multiple_clingen_matches", bundle.warnings)
        self.assertEqual(bundle.match["candidate_count"], 2)
        self.assertEqual(bundle.match["candidate_ids"], ["CG-100a", "CG-100b"])

    def test_label_disagreement_warns(self):
        # ClinVar Benign vs chosen ClinGen non-Benign -> label_disagreement.
        case = {"expected": "Benign", "provenance": {"variation_id": "200"}}
        bundle = self.provider.fetch(case)
        self.assertIn("label_disagreement", bundle.warnings)

    def test_accepts_bare_variation_id(self):
        bundle = self.provider.fetch("200")
        self.assertTrue(bundle.match["clingen_variation_id_match"])
        self.assertEqual(bundle.match["clingen_case_id"], "CG-200")

    def test_events_reconstruct_same_classification(self):
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "200"}}
        bundle = self.provider.fetch(case)
        # Round-tripping events through fixture-criterion form preserves the tier.
        criteria = [event_to_criterion(e) for e in bundle.events]
        from engine.scoring import classify_signals

        a = classify(bundle.events).tier
        b = classify_signals({"criteria": criteria}).tier
        self.assertEqual(a, b)


# --------------------------------------------------------------------------- #
# Canonical-key fallback join (Part A, tasks 5-6, 8)                          #
# --------------------------------------------------------------------------- #
def _cg_case_locus(case_id, expected, criteria, chrom, pos, ref, alt, clinvar_id="-"):
    case = _cg_case(case_id, clinvar_id, expected, criteria)
    case["locus"] = {"chrom": chrom, "pos": pos, "ref": ref, "alt": alt}
    return case


class TestCanonicalIndex(unittest.TestCase):
    def test_index_builds_canonical_keys_from_loci(self):
        cases = [
            _cg_case_locus("CG-A", "Pathogenic",
                           [_crit("PVS1", "pathogenic", "very_strong")], "1", 100, "A", "G"),
            _cg_case("CG-B", "200", "VUS", [_crit("PM2", "pathogenic", "moderate")]),
        ]
        idx = ClinGenIndex.from_cases(cases)
        self.assertIn("1-100-A-G", idx.canonical_keys)
        self.assertEqual(len(idx.candidates_by_key("1-100-A-G")), 1)
        self.assertEqual(idx.candidates_by_key("9-9-A-T"), [])

    def test_existing_real_fixture_has_no_canonical_keys(self):
        # ClinGen ERepo rows carry no locus today -> canonical index is empty, so the
        # variation-ID baseline is untouched (canonical matches are honestly zero).
        idx = ClinGenIndex.from_cases(CASES)
        self.assertEqual(idx.canonical_keys, set())


class TestCanonicalFallback(unittest.TestCase):
    def setUp(self):
        self.cases = [
            _cg_case_locus("CG-A", "Pathogenic",
                           [_crit("PVS1", "pathogenic", "very_strong")], "1", 100, "A", "G"),
        ]
        self.provider = ClinGenEvidenceProvider(ClinGenIndex.from_cases(self.cases))

    def test_variation_id_match_is_tagged(self):
        # The primary path now records match_type explicitly.
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases(CASES))
        bundle = prov.fetch({"expected": "Pathogenic", "provenance": {"variation_id": "200"}})
        self.assertEqual(bundle.match["match_type"], "variation_id")

    def test_canonical_key_match_when_no_variation_id(self):
        # ClinVar case has coordinates but a Variation ID ClinGen does not carry.
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999"},
                "locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G", "snv": True}}
        bundle = self.provider.fetch(case)
        self.assertEqual(bundle.match["match_type"], "canonical_key")
        self.assertEqual(bundle.match["canonical_key"], "GRCh38-1-100-A-G")
        self.assertEqual([e.acmg_criterion for e in bundle.events], ["PVS1"])
        self.assertEqual(bundle.match["clingen_case_id"], "CG-A")

    def test_no_locus_falls_through_to_historical_behavior(self):
        # Bare id / no coordinates -> unchanged "no_clingen_match", match_type none.
        bundle = self.provider.fetch({"expected": "VUS", "provenance": {"variation_id": "999"}})
        self.assertEqual(bundle.warnings, ["no_clingen_match"])
        self.assertEqual(bundle.match["match_type"], "none")

    def test_failed_normalization_is_not_a_clean_nonmatch(self):
        # An invalid alt must surface a blocking warning, not read as a clean miss.
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "-"},
                "locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "X"}}
        bundle = self.provider.fetch(case)
        self.assertEqual(bundle.match["match_type"], "none")
        self.assertFalse(bundle.match["normalized"])
        self.assertIn("normalization_failed", bundle.warnings)

    def test_reference_backed_indel_canonical_match(self):
        # ClinGen record stored at the left-aligned indel key; a repeat-shifted
        # ClinVar spelling matches it only via reference-backed normalization.
        ref = InMemoryReference({"1": "GAAAAT"})
        cg = _cg_case_locus("CG-INDEL", "Pathogenic",
                            [_crit("PM2", "pathogenic", "moderate")], "1", 1, "G", "GA")
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg], reference=ref),
                                       reference=ref)
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "-"},
                "locus": {"chrom": "1", "pos": 5, "ref": "A", "alt": "AA"}}
        bundle = prov.fetch(case)
        self.assertEqual(bundle.match["match_type"], "canonical_key")
        self.assertEqual(bundle.match["route"], "reference_backed_indel")
        self.assertEqual(bundle.match["normalization_method"], "reference_left_aligned")
        self.assertEqual(bundle.match["canonical_key"], "GRCh38-1-1-G-GA")


# --------------------------------------------------------------------------- #
# job1: HGVS-genomic (hgvs_g) fallback tier + strict priority + ambiguity      #
# --------------------------------------------------------------------------- #
def _cg_case_hgvs(case_id, expected, criteria, grch38_hgvs, clinvar_id="-"):
    """A ClinGen record with NO locus block, only a genomic HGVS token (indel)."""
    case = _cg_case(case_id, clinvar_id, expected, criteria)
    case["provenance"]["grch38_hgvs"] = grch38_hgvs
    return case


# NC_000001.11 (GRCh38 chr1) == contig "1" = G A A A A T over positions 1..6.
REF_CHR1 = InMemoryReference({"1": "GAAAAT"})


class TestHgvsGenomicFallback(unittest.TestCase):
    def test_indel_recovered_from_genomic_hgvs(self):
        # ClinGen carries only a genomic deletion HGVS; the index resolves it against
        # the reference so a ClinVar indel with the same coordinates matches (hgvs_g).
        cg = _cg_case_hgvs("CG-G", "Pathogenic",
                           [_crit("PVS1", "pathogenic", "very_strong")],
                           "NC_000001.11:g.2del")
        prov = ClinGenEvidenceProvider(
            ClinGenIndex.from_cases([cg], reference=REF_CHR1), reference=REF_CHR1)
        # g.2del == VCF 1-1-GA-G; a repeat-shifted ClinVar spelling (delete an A from
        # the AAAA run at pos2) left-aligns to the same key and still matches.
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999"},
                "locus": {"chrom": "1", "pos": 2, "ref": "AA", "alt": "A"}}
        bundle = prov.fetch(case)
        self.assertEqual(bundle.match["match_type"], "canonical_key")
        self.assertEqual(bundle.match["route"], "hgvs_g")
        self.assertEqual(bundle.match["canonical_key"], "GRCh38-1-1-GA-G")
        self.assertEqual([e.acmg_criterion for e in bundle.events], ["PVS1"])
        self.assertEqual(bundle.match["clingen_case_id"], "CG-G")

    def test_hgvs_g_needs_a_reference(self):
        # Without a reference the genomic indel token cannot be resolved -> no key,
        # so the case is an honest miss, never a guess.
        cg = _cg_case_hgvs("CG-G", "Pathogenic",
                           [_crit("PVS1", "pathogenic", "very_strong")],
                           "NC_000001.11:g.2del")
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg]))  # no reference
        self.assertEqual(prov.index.canonical_keys, set())
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999"},
                "locus": {"chrom": "1", "pos": 1, "ref": "GA", "alt": "G"}}
        bundle = prov.fetch(case)
        self.assertEqual(bundle.match["match_type"], "none")

    def test_variation_id_wins_over_hgvs_g(self):
        # A record reachable by BOTH a Variation ID and a coordinate must take the
        # stronger Variation ID route -- a weaker route never overrides it.
        cg = _cg_case_hgvs("CG-G", "Pathogenic",
                           [_crit("PVS1", "pathogenic", "very_strong")],
                           "NC_000001.11:g.2del", clinvar_id="555")
        prov = ClinGenEvidenceProvider(
            ClinGenIndex.from_cases([cg], reference=REF_CHR1), reference=REF_CHR1)
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "555"},
                "locus": {"chrom": "1", "pos": 1, "ref": "GA", "alt": "G"}}
        bundle = prov.fetch(case)
        self.assertEqual(bundle.match["match_type"], "variation_id")
        self.assertEqual(bundle.match["route"], "variation_id")


class TestFallbackAmbiguity(unittest.TestCase):
    def _two_records_at_one_key(self, criteria_a, criteria_b):
        cg_a = _cg_case_locus("CG-A", "Pathogenic", criteria_a, "1", 100, "A", "G")
        cg_b = _cg_case_locus("CG-B", "VUS", criteria_b, "1", 100, "A", "G")
        return ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg_a, cg_b]))

    def test_conflicting_criteria_is_ambiguous_and_imports_nothing(self):
        prov = self._two_records_at_one_key(
            [_crit("PVS1", "pathogenic", "very_strong")],
            [_crit("BA1", "benign", "stand_alone")],
        )
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999"},
                "locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G", "snv": True}}
        bundle = prov.fetch(case)
        self.assertTrue(bundle.match["ambiguous"])
        self.assertEqual(bundle.events, [])  # missing evidence stays unknown
        self.assertIn("ambiguous_fallback_match", bundle.warnings)
        # Raw match detail preserved for debugging.
        self.assertEqual(bundle.match["candidate_count"], 2)
        self.assertEqual(bundle.match["candidate_ids"], ["CG-A", "CG-B"])

    def test_equivalent_criteria_enriches_from_either(self):
        # Two records, same imported criteria -> deterministically equivalent -> enrich.
        prov = self._two_records_at_one_key(
            [_crit("PM2", "pathogenic", "moderate")],
            [_crit("PM2", "pathogenic", "moderate")],
        )
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999"},
                "locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G", "snv": True}}
        bundle = prov.fetch(case)
        self.assertFalse(bundle.match["ambiguous"])
        self.assertEqual([e.acmg_criterion for e in bundle.events], ["PM2"])
        self.assertIn("multiple_clingen_matches", bundle.warnings)

    def test_variation_id_multiple_records_is_not_ambiguous(self):
        # The ambiguity rule applies to FALLBACK routes only; the Variation ID join
        # keeps its deterministic duplicate-resolution policy and still enriches.
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases(CASES))
        bundle = prov.fetch({"expected": "Pathogenic", "provenance": {"variation_id": "100"}})
        self.assertFalse(bundle.match["ambiguous"])
        self.assertEqual(bundle.match["route"], "variation_id")
        self.assertTrue(bundle.events)


if __name__ == "__main__":
    unittest.main()
