"""Tests for the GRCh38 genomic-HGVS locus parser (ingest/hgvs.py, job1 tasks 1+3).

Verifies that only version-pinned GRCh38 accessions yield a locus, so the canonical-
key fallback never matches on a GRCh37/36 coordinate -- for substitutions
(reference-free) and for indels (``del``/``dup``/``ins``/``delins``, resolved against
the reference for the ``hgvs_g`` tier).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.reference import InMemoryReference
from ingest.hgvs import (
    GRCH38_ACCESSIONS,
    locus_from_genomic_hgvs,
    locus_from_hgvs_list,
    parse_coding_hgvs,
    parse_genomic_hgvs,
    parse_genomic_indel,
    parse_spdi,
    pick_coding_hgvs,
    pick_grch38_genomic_hgvs,
)


class TestParseGenomicHgvs(unittest.TestCase):
    def test_grch38_snv(self):
        self.assertEqual(parse_genomic_hgvs("NC_000012.12:g.102917130T>C"),
                         ("12", 102917130, "T", "C"))

    def test_chrx_accession(self):
        self.assertEqual(parse_genomic_hgvs("NC_000023.11:g.100A>G"),
                         ("X", 100, "A", "G"))

    def test_mitochondrial_accession(self):
        self.assertEqual(parse_genomic_hgvs("NC_012920.1:g.1234G>A"),
                         ("MT", 1234, "G", "A"))

    def test_grch37_accession_rejected(self):
        # NC_000012.11 is GRCh37 -> must NOT be parsed as a GRCh38 locus.
        self.assertIsNone(parse_genomic_hgvs("NC_000012.11:g.103310908T>C"))

    def test_grch36_accession_rejected(self):
        self.assertIsNone(parse_genomic_hgvs("NC_000012.10:g.101835038T>C"))

    def test_deletion_form_skipped(self):
        # del/ins without explicit anchored bases needs the FASTA -> not parsed here.
        self.assertIsNone(parse_genomic_hgvs("NC_000012.12:g.102852851del"))

    def test_cdna_hgvs_ignored(self):
        self.assertIsNone(parse_genomic_hgvs("NM_000277.2:c.1A>G"))

    def test_garbage(self):
        self.assertIsNone(parse_genomic_hgvs(""))
        self.assertIsNone(parse_genomic_hgvs("not an hgvs"))


class TestLocusFromList(unittest.TestCase):
    def test_picks_grch38_from_full_erepo_cell(self):
        cell = ("NM_000277.2:c.1A>G, NC_000012.12:g.102917130T>C, "
                "NC_000012.11:g.103310908T>C, NC_000012.10:g.101835038T>C, "
                "NG_008690.1:g.5473A>G")
        loc = locus_from_hgvs_list(cell)
        self.assertEqual((loc["chrom"], loc["pos"], loc["ref"], loc["alt"]),
                         ("12", 102917130, "T", "C"))
        self.assertTrue(loc["snv"])
        self.assertEqual(loc["source_hgvs"], "NC_000012.12:g.102917130T>C")

    def test_mnv_flagged_not_snv(self):
        loc = locus_from_hgvs_list("NC_000001.11:g.100AC>GT")
        self.assertFalse(loc["snv"])

    def test_no_grch38_substitution_returns_none(self):
        # Only an indel form present -> no usable SNV locus.
        self.assertIsNone(locus_from_hgvs_list("NC_000012.12:g.102852851del"))

    def test_accession_table_is_complete(self):
        # 22 autosomes + X + Y + MT.
        self.assertEqual(len(GRCH38_ACCESSIONS), 25)
        self.assertEqual(set(GRCH38_ACCESSIONS.values()),
                         {str(i) for i in range(1, 23)} | {"X", "Y", "MT"})


# --------------------------------------------------------------------------- #
# Genomic indel HGVS -> VCF locus (job1 task 1, the hgvs_g fallback tier)      #
# --------------------------------------------------------------------------- #
class TestParseGenomicIndel(unittest.TestCase):
    def setUp(self):
        # NC_000001.11 is GRCh38 chr1. Contig "1" = positions 1..6 -> G A A A A T.
        self.ref = InMemoryReference({"1": "GAAAAT"})

    def test_single_base_deletion_is_vcf_anchored(self):
        # g.2del deletes the A at pos 2 -> VCF anchors on the base before (pos1 G).
        self.assertEqual(parse_genomic_indel("NC_000001.11:g.2del", self.ref),
                         ("1", 1, "GA", "G"))

    def test_range_deletion(self):
        # g.2_3del deletes AA -> anchor pos1 G.
        self.assertEqual(parse_genomic_indel("NC_000001.11:g.2_3del", self.ref),
                         ("1", 1, "GAA", "G"))

    def test_duplication(self):
        # g.2_3dup duplicates AA, inserted after pos3 -> anchor on pos3 A.
        self.assertEqual(parse_genomic_indel("NC_000001.11:g.2_3dup", self.ref),
                         ("1", 3, "A", "AAA"))

    def test_insertion(self):
        # g.1_2insTT inserts TT between pos1 and pos2 -> anchor on pos1 G.
        self.assertEqual(parse_genomic_indel("NC_000001.11:g.1_2insTT", self.ref),
                         ("1", 1, "G", "GTT"))

    def test_delins(self):
        # g.2_3delinsCC replaces AA with CC; both sides non-empty -> no anchor base.
        self.assertEqual(parse_genomic_indel("NC_000001.11:g.2_3delinsCC", self.ref),
                         ("1", 2, "AA", "CC"))

    def test_grch37_indel_rejected(self):
        # NC_000001.10 is GRCh37 -> never resolved as a GRCh38 indel.
        self.assertIsNone(parse_genomic_indel("NC_000001.10:g.2del", self.ref))

    def test_substitution_is_not_an_indel(self):
        self.assertIsNone(parse_genomic_indel("NC_000001.11:g.2A>T", self.ref))

    def test_requires_a_reference(self):
        # Without a reference the affected bases are unknown -> never guessed.
        self.assertIsNone(parse_genomic_indel("NC_000001.11:g.2del", None))

    def test_out_of_range_is_unparseable_not_a_guess(self):
        # A coordinate past the contig end raises ReferenceLookupError internally,
        # which the parser treats as unparseable (None), never a padded/wrong base.
        self.assertIsNone(parse_genomic_indel("NC_000001.11:g.99del", self.ref))


class TestLocusFromGenomicHgvs(unittest.TestCase):
    def setUp(self):
        self.ref = InMemoryReference({"1": "GAAAAT"})

    def test_substitution_needs_no_reference(self):
        self.assertEqual(locus_from_genomic_hgvs("NC_000001.11:g.2A>T"),
                         ("1", 2, "A", "T"))

    def test_indel_needs_reference(self):
        self.assertIsNone(locus_from_genomic_hgvs("NC_000001.11:g.2del"))
        self.assertEqual(locus_from_genomic_hgvs("NC_000001.11:g.2del", self.ref),
                         ("1", 1, "GA", "G"))


class TestPickGrch38GenomicHgvs(unittest.TestCase):
    def test_prefers_substitution_token(self):
        cell = ("NM_000277.2:c.1A>G, NC_000012.12:g.102917130T>C, "
                "NC_000012.11:g.103310908T>C")
        self.assertEqual(pick_grch38_genomic_hgvs(cell), "NC_000012.12:g.102917130T>C")

    def test_picks_indel_when_no_substitution(self):
        cell = ("NM_000277.2:c.806delT, NC_000012.12:g.102852851del, "
                "NC_000012.11:g.103246629del")
        self.assertEqual(pick_grch38_genomic_hgvs(cell), "NC_000012.12:g.102852851del")

    def test_rejects_non_grch38_indel(self):
        # Only GRCh37/36 indel tokens present -> nothing GRCh38 to pick.
        self.assertIsNone(pick_grch38_genomic_hgvs(
            "NC_000012.11:g.103246629del, NC_000012.10:g.101770759del"))

    def test_returns_none_for_empty(self):
        self.assertIsNone(pick_grch38_genomic_hgvs(""))
        self.assertIsNone(pick_grch38_genomic_hgvs("NM_000277.2:c.806del"))


# --------------------------------------------------------------------------- #
# NCBI SPDI -> VCF locus (job1 task 3, the SPDI identity route)                #
# --------------------------------------------------------------------------- #
class TestParseSpdi(unittest.TestCase):
    def setUp(self):
        # NC_000001.11 == contig "1" = positions 1..6 -> G A A A A T.
        self.ref = InMemoryReference({"1": "GAAAAT"})

    def test_substitution_needs_no_reference(self):
        # SPDI is 0-based: pos 1 -> 1-based 2.
        self.assertEqual(parse_spdi("NC_000001.11:1:A:T"), ("1", 2, "A", "T"))

    def test_mnv_substitution(self):
        self.assertEqual(parse_spdi("NC_000001.11:1:AA:GT"), ("1", 2, "AA", "GT"))

    def test_deletion_is_reference_anchored(self):
        # Delete the A at 1-based 2 -> VCF anchors on the base before (pos1 G).
        self.assertEqual(parse_spdi("NC_000001.11:1:A:", self.ref), ("1", 1, "GA", "G"))

    def test_deletion_by_length(self):
        # DEL given as a length 1 -> read the deleted base from the reference.
        self.assertEqual(parse_spdi("NC_000001.11:1:1:", self.ref), ("1", 1, "GA", "G"))

    def test_insertion_is_reference_anchored(self):
        # Insert TT after 0-based 1 (between 1-based 1 and 2) -> anchor on pos1 G.
        self.assertEqual(parse_spdi("NC_000001.11:1::TT", self.ref), ("1", 1, "G", "GTT"))

    def test_deletion_needs_a_reference(self):
        self.assertIsNone(parse_spdi("NC_000001.11:1:A:"))  # no anchor without a reference

    def test_grch37_spdi_rejected(self):
        self.assertIsNone(parse_spdi("NC_000001.10:1:A:T"))

    def test_identity_is_unparseable(self):
        self.assertIsNone(parse_spdi("NC_000001.11:1::"))

    def test_out_of_range_is_none(self):
        self.assertIsNone(parse_spdi("NC_000001.11:99:A:", self.ref))


# --------------------------------------------------------------------------- #
# Coding HGVS picker (job1 task 4, transcript identity)                        #
# --------------------------------------------------------------------------- #
class TestCodingHgvs(unittest.TestCase):
    def test_parse_refseq_coding(self):
        self.assertEqual(parse_coding_hgvs("NM_000277.3:c.1A>G"), ("NM_000277.3", "c.1A>G"))

    def test_parse_ensembl_coding(self):
        self.assertEqual(parse_coding_hgvs("ENST00000123.4:c.5C>T"), ("ENST00000123.4", "c.5C>T"))

    def test_genomic_is_not_coding(self):
        self.assertIsNone(parse_coding_hgvs("NC_000012.12:g.102917130T>C"))

    def test_pick_prefers_nm_over_ensembl(self):
        cell = "ENST00000123.4:c.5C>T, NM_000277.3:c.1A>G, NC_000012.12:g.1A>G"
        self.assertEqual(pick_coding_hgvs(cell), ("NM_000277.3", "c.1A>G"))

    def test_pick_none_when_no_coding(self):
        self.assertIsNone(pick_coding_hgvs("NC_000012.12:g.102917130T>C"))


if __name__ == "__main__":
    unittest.main()
