"""Unit tests for VCF/CSV batch variant import (job1 task 5).

Pure, offline. Verifies identity normalization, multiallelic splitting, duplicate
detection, invalid-row recording, the dry-run report, and the optional evidence-
resolution preview (with a deterministic in-memory resolver).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.evidence_resolver import EvidenceResolver  # noqa: E402
from evidence.revel import RevelProvider  # noqa: E402
from ingest import csv_import, vcf_import  # noqa: E402


def _resolver() -> EvidenceResolver:
    resolver = EvidenceResolver()
    resolver.register("revel", RevelProvider.from_scores({"1-100-A-G": 0.95}))
    return resolver


VCF = """\
##fileformat=VCFv4.2
#CHROM\tPOS\tID\tREF\tALT
1\t100\t.\tA\tG
1\t100\t.\tA\tG
2\t200\trs9\tC\tT,A
bad line here
1\tNaN\t.\tA\tG
"""


class TestParseVcf(unittest.TestCase):
    def test_skips_headers_and_splits_multiallelic(self) -> None:
        rows = vcf_import.parse_vcf(VCF)
        loci = [(r.get("chrom"), r.get("pos"), r.get("ref"), r.get("alt"))
                for r in rows if not r.get("error")]
        self.assertIn(("2", 200, "C", "T"), loci)
        self.assertIn(("2", 200, "C", "A"), loci)  # multiallelic split
        self.assertTrue(any(r.get("multiallelic") for r in rows))

    def test_malformed_lines_recorded(self) -> None:
        rows = vcf_import.parse_vcf(VCF)
        errors = {r["error"] for r in rows if r.get("error")}
        self.assertIn("malformed_vcf_line", errors)
        self.assertIn("non_integer_position", errors)


class TestImportVcf(unittest.TestCase):
    def test_dedup_and_dry_run_report(self) -> None:
        report = vcf_import.import_vcf(VCF)
        self.assertTrue(report["dry_run"])
        totals = report["totals"]
        # 1-100-A-G appears twice -> one unique, one duplicate row.
        self.assertEqual(totals["duplicate_rows"], 1)
        keys = {v["key"] for v in report["variants"]}
        self.assertIn("GRCh38-1-100-A-G", keys)
        self.assertIn("GRCh38-2-200-C-T", keys)
        self.assertIn("GRCh38-2-200-C-A", keys)
        self.assertTrue(any(d["key"] == "GRCh38-1-100-A-G" for d in report["duplicates"]))
        self.assertGreaterEqual(totals["invalid"], 2)

    def test_resolution_preview_attached_when_resolver_given(self) -> None:
        report = vcf_import.import_vcf(VCF, resolver=_resolver(), providers=["revel"])
        self.assertIsNotNone(report["resolution"])
        target = next(v for v in report["variants"] if v["key"] == "GRCh38-1-100-A-G")
        self.assertIn("resolution", target)
        self.assertGreaterEqual(target["resolution"]["events"], 1)
        self.assertIn("revel", target["resolution"]["provider_versions"])

    def test_no_resolution_without_resolver(self) -> None:
        report = vcf_import.import_vcf(VCF)
        self.assertIsNone(report["resolution"])
        self.assertNotIn("resolution", report["variants"][0])


CSV = """\
chromosome,position,ref,alt,gene
1,100,A,G,BRCA1
1,100,A,G,BRCA1
X,500,G,GA,DMD
"""

CSV_KEYS = """\
variant_key,gene
GRCh38-1-100-A-G,BRCA1
1-100-A-G,BRCA1
not-a-key,ZZZ
"""


class TestImportCsv(unittest.TestCase):
    def test_header_aliases_and_dedup(self) -> None:
        report = csv_import.import_csv(CSV)
        self.assertEqual(report["format"], "csv")
        keys = {v["key"] for v in report["variants"]}
        self.assertIn("GRCh38-1-100-A-G", keys)
        self.assertEqual(report["totals"]["duplicate_rows"], 1)

    def test_variant_key_column_parsed(self) -> None:
        report = csv_import.import_csv(CSV_KEYS)
        keys = {v["key"] for v in report["variants"]}
        self.assertEqual(keys, {"GRCh38-1-100-A-G"})  # the two key forms collapse
        self.assertEqual(report["totals"]["duplicate_rows"], 1)
        self.assertTrue(any(i["error"] == "unparseable_variant_key" for i in report["invalid"]))

    def test_missing_locus_columns_recorded(self) -> None:
        report = csv_import.import_csv("gene\nBRCA1\n")
        self.assertEqual(report["totals"]["unique_variants"], 0)
        self.assertTrue(any(i["error"] == "missing_locus_columns" for i in report["invalid"]))

    def test_tsv_delimiter_sniffed(self) -> None:
        report = csv_import.import_csv("chrom\tpos\tref\talt\n1\t100\tA\tG\n")
        self.assertEqual(report["totals"]["unique_variants"], 1)


if __name__ == "__main__":
    unittest.main()
