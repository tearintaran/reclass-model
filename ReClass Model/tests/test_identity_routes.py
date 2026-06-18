"""Tests for the new identity-matching routes (job1 task 3).

Covers the ClinVar Allele ID route, the SPDI route, the MANE-transcript + coding-HGVS
route, and an ambiguity case for each that is *flagged*, not silently resolved. All
fixtures are tiny and in-memory; the SPDI/indel tests use an InMemoryReference, so
nothing touches the network or the real genome.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_identity_routes -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.reference import InMemoryReference
from engine.normalize import normalize_transcript, transcript_hgvs_key
from evidence.clingen import (
    ClinGenEvidenceProvider,
    ClinGenIndex,
    allele_id_of,
    spdi_of,
    transcript_key_of,
)


def _crit(name, direction, strength):
    return {"criterion": name, "direction": direction, "strength": strength,
            "source": "clingen", "version": "ERepo"}


def _cg(case_id, criteria, *, clinvar_id="-", expected="Pathogenic", **extra):
    case = {"id": case_id, "gene": "GENE", "expected": expected,
            "signals": {"criteria": criteria},
            "provenance": {"source": "ClinGen ERepo", "clinvar_id": clinvar_id}}
    for k, v in extra.items():
        if k in ("allele_id", "spdi"):
            case["provenance"][k] = v
        else:
            case[k] = v
    return case


REF_CHR1 = InMemoryReference({"1": "GAAAAT"})  # NC_000001.11 == contig "1"


# --------------------------------------------------------------------------- #
# Identity-field extraction helpers                                            #
# --------------------------------------------------------------------------- #
class TestIdentityExtractors(unittest.TestCase):
    def test_allele_id_rejects_sentinels(self):
        self.assertIsNone(allele_id_of({"provenance": {"allele_id": "-"}}))
        self.assertEqual(allele_id_of({"provenance": {"allele_id": "A123"}}), "A123")

    def test_spdi_extracted(self):
        self.assertEqual(spdi_of({"provenance": {"spdi": "NC_000001.11:1:A:T"}}),
                         "NC_000001.11:1:A:T")
        self.assertIsNone(spdi_of({"provenance": {}}))

    def test_transcript_key_from_block_and_provenance(self):
        self.assertEqual(transcript_key_of({"transcript": {"mane_select": "NM_1.3", "hgvs_c": "c.1A>G"}}),
                         "NM_1:c.1A>G")
        self.assertEqual(transcript_key_of({"provenance": {"refseq_transcript": "NM_1.4", "hgvs_c": "c.1A>G"}}),
                         "NM_1:c.1A>G")
        self.assertIsNone(transcript_key_of({"gene": "X"}))

    def test_normalize_transcript_strips_version(self):
        self.assertEqual(normalize_transcript("NM_000277.3"), "NM_000277")
        self.assertEqual(normalize_transcript("nm_000277"), "NM_000277")
        self.assertIsNone(normalize_transcript(""))

    def test_transcript_hgvs_key_version_agnostic(self):
        self.assertEqual(transcript_hgvs_key("NM_1.3", "c.1A>G"),
                         transcript_hgvs_key("NM_1.99", "c.1A>G"))
        self.assertIsNone(transcript_hgvs_key("NM_1.3", None))


# --------------------------------------------------------------------------- #
# ClinVar Allele ID route                                                      #
# --------------------------------------------------------------------------- #
class TestAlleleIdRoute(unittest.TestCase):
    def setUp(self):
        cg = _cg("CG-A", [_crit("PVS1", "pathogenic", "very_strong")], allele_id="A555")
        self.provider = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg]))

    def test_allele_id_index_built(self):
        self.assertEqual(self.provider.index.allele_ids, {"A555"})

    def test_match_by_allele_id(self):
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999", "allele_id": "A555"}}
        bundle = self.provider.fetch(case)
        self.assertEqual(bundle.match["route"], "clinvar_allele_id")
        self.assertEqual(bundle.match["match_type"], "clinvar_allele_id")
        self.assertEqual([e.acmg_criterion for e in bundle.events], ["PVS1"])
        self.assertEqual(bundle.match["clingen_case_id"], "CG-A")

    def test_variation_id_wins_over_allele_id(self):
        cg = _cg("CG-V", [_crit("PM2", "pathogenic", "moderate")],
                 clinvar_id="200", allele_id="A555")
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg]))
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "200", "allele_id": "A555"}}
        self.assertEqual(prov.fetch(case).match["route"], "variation_id")

    def test_no_allele_id_falls_through(self):
        case = {"expected": "VUS", "provenance": {"variation_id": "999"}}
        self.assertEqual(self.provider.fetch(case).match["match_type"], "none")

    def test_ambiguous_allele_id_imports_nothing(self):
        cg_a = _cg("CG-A", [_crit("PVS1", "pathogenic", "very_strong")], allele_id="A777")
        cg_b = _cg("CG-B", [_crit("BA1", "benign", "stand_alone")], allele_id="A777", expected="Benign")
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg_a, cg_b]))
        bundle = prov.fetch({"expected": "Pathogenic",
                             "provenance": {"variation_id": "999", "allele_id": "A777"}})
        self.assertTrue(bundle.match["ambiguous"])
        self.assertEqual(bundle.events, [])
        self.assertIn("ambiguous_fallback_match", bundle.warnings)
        self.assertEqual(bundle.match["candidate_ids"], ["CG-A", "CG-B"])


# --------------------------------------------------------------------------- #
# SPDI route                                                                   #
# --------------------------------------------------------------------------- #
class TestSpdiRoute(unittest.TestCase):
    def test_match_by_spdi_snv(self):
        # ClinGen record keyed by a coordinate locus; ClinVar case carries only an SPDI.
        cg = _cg("CG-S", [_crit("PVS1", "pathogenic", "very_strong")],
                 locus={"chrom": "1", "pos": 2, "ref": "A", "alt": "T"})
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg], reference=REF_CHR1),
                                       reference=REF_CHR1)
        case = {"expected": "Pathogenic",
                "provenance": {"variation_id": "999", "spdi": "NC_000001.11:1:A:T"}}
        bundle = prov.fetch(case)
        self.assertEqual(bundle.match["route"], "spdi")
        self.assertEqual(bundle.match["canonical_key"], "GRCh38-1-2-A-T")
        self.assertEqual([e.acmg_criterion for e in bundle.events], ["PVS1"])

    def test_match_by_spdi_deletion_left_aligned(self):
        # SPDI pure deletion is reference-anchored; a repeat-shifted ClinGen indel key
        # joins it after left-alignment.
        cg = _cg("CG-D", [_crit("PM2", "pathogenic", "moderate")],
                 locus={"chrom": "1", "pos": 1, "ref": "GA", "alt": "G"})
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg], reference=REF_CHR1),
                                       reference=REF_CHR1)
        # delete an A from the A-run (0-based 4 -> deletes the A at 1-based 5).
        case = {"expected": "Pathogenic",
                "provenance": {"variation_id": "999", "spdi": "NC_000001.11:4:A:"}}
        bundle = prov.fetch(case)
        self.assertEqual(bundle.match["route"], "spdi")
        self.assertEqual(bundle.match["canonical_key"], "GRCh38-1-1-GA-G")

    def test_locus_block_beats_spdi(self):
        # A native locus is used directly (route stays the canonical route, not spdi).
        cg = _cg("CG-S", [_crit("PVS1", "pathogenic", "very_strong")],
                 locus={"chrom": "1", "pos": 2, "ref": "A", "alt": "T"})
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg], reference=REF_CHR1),
                                       reference=REF_CHR1)
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999",
                "spdi": "NC_000001.11:1:A:T"},
                "locus": {"chrom": "1", "pos": 2, "ref": "A", "alt": "T"}}
        self.assertEqual(prov.fetch(case).match["route"], "canonical_snv")


# --------------------------------------------------------------------------- #
# MANE-transcript + coding-HGVS route                                         #
# --------------------------------------------------------------------------- #
class TestTranscriptRoute(unittest.TestCase):
    def setUp(self):
        cg = _cg("CG-T", [_crit("PVS1", "pathogenic", "very_strong")],
                 transcript={"mane_select": "NM_000277.3", "hgvs_c": "c.1A>G"})
        self.provider = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg]))

    def test_transcript_index_built(self):
        self.assertEqual(self.provider.index.transcript_keys, {"NM_000277:c.1A>G"})

    def test_match_by_transcript_version_agnostic(self):
        # ClinVar names the same transcript at a DIFFERENT version -> still joins.
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999"},
                "transcript": {"mane_select": "NM_000277.4", "hgvs_c": "c.1A>G"}}
        bundle = self.provider.fetch(case)
        self.assertEqual(bundle.match["route"], "hgvs_c_mane")
        self.assertEqual(bundle.match["transcript_key"], "NM_000277:c.1A>G")
        self.assertEqual([e.acmg_criterion for e in bundle.events], ["PVS1"])

    def test_no_transcript_falls_through(self):
        case = {"expected": "VUS", "provenance": {"variation_id": "999"}}
        self.assertEqual(self.provider.fetch(case).match["match_type"], "none")

    def test_ambiguous_transcript_imports_nothing(self):
        cg_a = _cg("CG-A", [_crit("PVS1", "pathogenic", "very_strong")],
                   transcript={"refseq": "NM_9.1", "hgvs_c": "c.5C>T"})
        cg_b = _cg("CG-B", [_crit("BP4", "benign", "supporting")], expected="Benign",
                   transcript={"refseq": "NM_9.2", "hgvs_c": "c.5C>T"})
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg_a, cg_b]))
        case = {"expected": "Pathogenic", "provenance": {"variation_id": "999"},
                "transcript": {"mane_select": "NM_9.3", "hgvs_c": "c.5C>T"}}
        bundle = prov.fetch(case)
        self.assertTrue(bundle.match["ambiguous"])
        self.assertEqual(bundle.events, [])
        self.assertIn("ambiguous_fallback_match", bundle.warnings)


# --------------------------------------------------------------------------- #
# Strict route priority across all tiers                                       #
# --------------------------------------------------------------------------- #
class TestRoutePriority(unittest.TestCase):
    def test_allele_id_beats_canonical_and_transcript(self):
        # One ClinGen record reachable by allele ID, coordinate, and transcript; the
        # strongest available fallback (allele ID) must be the reported route.
        cg = _cg("CG-X", [_crit("PVS1", "pathogenic", "very_strong")],
                 allele_id="A1", locus={"chrom": "1", "pos": 2, "ref": "A", "alt": "T"},
                 transcript={"mane_select": "NM_1.1", "hgvs_c": "c.1A>G"})
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg], reference=REF_CHR1),
                                       reference=REF_CHR1)
        case = {"expected": "Pathogenic",
                "provenance": {"variation_id": "999", "allele_id": "A1"},
                "locus": {"chrom": "1", "pos": 2, "ref": "A", "alt": "T"},
                "transcript": {"mane_select": "NM_1.2", "hgvs_c": "c.1A>G"}}
        self.assertEqual(prov.fetch(case).match["route"], "clinvar_allele_id")


if __name__ == "__main__":
    unittest.main()
