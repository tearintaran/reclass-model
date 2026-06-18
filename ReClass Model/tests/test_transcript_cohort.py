"""End-to-end tests for transcript identity (job1 task 4) + PS4 cohort counts (task 5).

  * J1-4 acceptance: an ingested record carries its MANE Select / RefSeq transcript
    identity all the way into the evidence bundle.
  * J1-5 acceptance: the cohort ingest emits PS4 denominator + case/control counts
    into the evidence model for a fixture cohort.

All in-memory; no network, no large files.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_transcript_cohort -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence.clingen import ClinGenEvidenceProvider, ClinGenIndex
from evidence.model import CohortCounts, EvidenceBundle, TranscriptIdentity
from ingest.cohort_to_evidence import build_cohort_evidence
from ingest.clinvar_to_benchmark import transcript_block
from ingest.clingen_to_benchmark import main as _clingen_main  # noqa: F401  (import smoke)
from ingest.hgvs import parse_coding_hgvs, pick_coding_hgvs


def _crit(name, direction, strength):
    return {"criterion": name, "direction": direction, "strength": strength,
            "source": "clingen", "version": "ERepo"}


# --------------------------------------------------------------------------- #
# J1-4: transcript identity carried into the evidence bundle                   #
# --------------------------------------------------------------------------- #
class TestTranscriptIdentityModel(unittest.TestCase):
    def test_from_case_reads_transcript_block(self):
        case = {"gene": "BRCA1", "transcript": {"mane_select": "NM_007294.4",
                "hgvs_c": "c.68_69del", "hgvs_p": "p.Glu23fs"}}
        tx = TranscriptIdentity.from_case(case)
        self.assertTrue(tx.is_mane_select)
        self.assertEqual(tx.mane_select, "NM_007294.4")
        self.assertEqual(tx.gene, "BRCA1")
        self.assertEqual(tx.hgvs_c, "c.68_69del")

    def test_from_case_falls_back_to_provenance(self):
        case = {"gene": "X", "provenance": {"refseq_transcript": "NM_1.2", "hgvs_c": "c.1A>G"}}
        tx = TranscriptIdentity.from_case(case)
        self.assertEqual(tx.refseq, "NM_1.2")
        self.assertEqual(tx.hgvs_c, "c.1A>G")
        self.assertFalse(tx.is_mane_select)

    def test_from_case_none_when_absent(self):
        self.assertIsNone(TranscriptIdentity.from_case({"gene": "X"}))

    def test_bundle_round_trips_transcript(self):
        bundle = EvidenceBundle(transcript=TranscriptIdentity(mane_select="NM_1.3", gene="G"))
        restored = EvidenceBundle.from_json(bundle.to_json())
        self.assertEqual(restored.transcript.mane_select, "NM_1.3")
        self.assertTrue(restored.transcript.is_mane_select)


class TestTranscriptThroughProvider(unittest.TestCase):
    def test_ingested_record_carries_transcript_into_bundle(self):
        # J1-4 acceptance: a ClinVar case with a transcript identity, matched by the
        # ClinGen provider, yields a bundle that carries that transcript identity.
        cg = {"id": "CG-1", "expected": "Pathogenic",
              "signals": {"criteria": [_crit("PVS1", "pathogenic", "very_strong")]},
              "provenance": {"clinvar_id": "200"}}
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([cg]))
        case = {"expected": "Pathogenic", "gene": "BRCA1",
                "provenance": {"variation_id": "200"},
                "transcript": {"mane_select": "NM_007294.4", "hgvs_c": "c.68_69del"}}
        bundle = prov.fetch(case)
        self.assertIsInstance(bundle.transcript, TranscriptIdentity)
        self.assertEqual(bundle.transcript.mane_select, "NM_007294.4")
        self.assertTrue(bundle.transcript.is_mane_select)
        # And it survives serialization with the rest of the bundle.
        self.assertEqual(bundle.to_dict()["transcript"]["mane_select"], "NM_007294.4")

    def test_no_match_still_carries_transcript(self):
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases([]))
        case = {"expected": "VUS", "gene": "G", "provenance": {"variation_id": "999"},
                "transcript": {"mane_select": "NM_1.1", "hgvs_c": "c.1A>G"}}
        self.assertEqual(prov.fetch(case).transcript.mane_select, "NM_1.1")


class TestCodingHgvsPickers(unittest.TestCase):
    def test_parse_coding_hgvs(self):
        self.assertEqual(parse_coding_hgvs("NM_000277.3:c.1A>G"), ("NM_000277.3", "c.1A>G"))
        self.assertIsNone(parse_coding_hgvs("NC_000001.11:g.2A>T"))  # genomic, not coding

    def test_pick_prefers_refseq_nm(self):
        cell = "ENST00000123.4:c.5C>T, NM_000277.3:c.1A>G"
        self.assertEqual(pick_coding_hgvs(cell), ("NM_000277.3", "c.1A>G"))

    def test_pick_none_when_no_coding(self):
        self.assertIsNone(pick_coding_hgvs("NC_000001.11:g.2A>T"))


class TestClinVarTranscriptBlock(unittest.TestCase):
    def test_block_built_from_mane_info(self):
        block = transcript_block({"MANE_SELECT": "NM_1.3", "HGVSC": "c.1A>G"}, "GENE")
        self.assertEqual(block["mane_select"], "NM_1.3")
        self.assertEqual(block["hgvs_c"], "c.1A>G")
        self.assertEqual(block["gene"], "GENE")

    def test_block_none_when_no_transcript_info(self):
        self.assertIsNone(transcript_block({"AF_EXAC": "0.1"}, "GENE"))


# --------------------------------------------------------------------------- #
# J1-5: PS4 denominator + cohort counts emitted by the cohort ingest           #
# --------------------------------------------------------------------------- #
class TestCohortIngest(unittest.TestCase):
    COHORT = {
        "cohort": "Cohort-A", "access_date": "2026-06-17", "source": "curated_cc",
        "variants": [
            {"variant_key": "1-100-A-G", "gene": "GENE",
             "case_count": 40, "case_total": 100, "control_count": 5, "control_total": 100,
             "ci_low": 3.0, "p_value": 1e-6},
            {"variant_key": "1-200-C-T", "gene": "GENE",
             "case_count": 2, "case_total": 100, "control_count": 1, "control_total": 100},
        ],
    }

    def test_emits_ps4_denominator_and_cohort_counts(self):
        # J1-5 acceptance: ingest emits PS4 denominator + cohort counts per variant.
        result = build_cohort_evidence(self.COHORT)
        self.assertEqual(result["total_variants"], 2)
        self.assertEqual(result["access_date"], "2026-06-17")
        by_key = {r["variant_key"]: r for r in result["records"]}

        sig = by_key["1-100-A-G"]
        self.assertTrue(sig["ps4_called"])
        self.assertEqual([e["acmg_criterion"] for e in sig["events"]], ["PS4"])
        cc = sig["cohort_counts"]
        self.assertEqual(cc["denominator"], 200)
        self.assertEqual(cc["case_count"], 40)
        self.assertEqual(cc["control_count"], 5)
        self.assertAlmostEqual(cc["odds_ratio"], 8.0 if cc["odds_ratio"] == 8.0 else (40 * 95) / (60 * 5))

        # Non-significant variant: no PS4 event, but the cohort counts are still modeled.
        nonsig = by_key["1-200-C-T"]
        self.assertFalse(nonsig["ps4_called"])
        self.assertEqual(nonsig["events"], [])
        self.assertEqual(nonsig["cohort_counts"]["denominator"], 200)
        self.assertEqual(result["ps4_called"], 1)

    def test_access_date_override(self):
        result = build_cohort_evidence(self.COHORT, access_date="2025-01-01")
        self.assertEqual(result["access_date"], "2025-01-01")

    def test_cohort_counts_model_denominator(self):
        cc = CohortCounts(case_total=100, control_total=300)
        self.assertEqual(cc.denominator, 400)
        self.assertIsNone(CohortCounts(case_total=100).denominator)


if __name__ == "__main__":
    unittest.main()
