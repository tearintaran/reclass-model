"""Tests for reference-anchored left-alignment and the reference providers.

Pure stdlib. Uses an in-memory reference for the alignment logic and a tiny temp
FASTA to exercise FastaReference, so nothing is downloaded.

Worked reference for the indel cases: contig "1" = "GAAAAT"
    pos: 1 2 3 4 5 6
    base:G A A A A T
A single-base insertion or deletion anywhere in the A-run (pos 2..5) is ambiguous;
the canonical LEFT-aligned form anchors on the 'G' at pos 1.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.normalize import (
    Variant,
    add_build,
    audit_loci,
    canonical_key,
    left_align,
    normalize_case,
    normalize_chrom,
    normalize_locus,
    normalize_with_reference,
    parse_key,
    provider_key,
    provider_key_of,
    strip_build,
    variant_key,
)
from engine.reference import (
    FastaReference,
    InMemoryReference,
    ReferenceLookupError,
)


REF = InMemoryReference({
    "1": "GAAAAT",   # G at 1, A-run 2..5, T at 6
    "2": "ACGT",
})


class TestLeftAlignNoReference(unittest.TestCase):
    """AC-3.4 loud hook must be preserved when no reference is supplied."""

    def test_default_none_raises(self):
        with self.assertRaises(NotImplementedError):
            left_align(Variant("1", 5, "A", "AA"))

    def test_explicit_none_raises(self):
        with self.assertRaises(NotImplementedError):
            left_align(Variant("1", 5, "A", "AA"), None)


class TestLeftAlignWithReference(unittest.TestCase):
    def test_snv_unchanged(self):
        v = left_align(Variant("1", 1, "G", "T"), REF)
        self.assertEqual((v.pos, v.ref, v.alt), (1, "G", "T"))

    def test_insertion_left_shifts_to_anchor(self):
        # Inserting an A in the run, written at pos 5, left-aligns to pos 1 G->GA.
        v = left_align(Variant("1", 5, "A", "AA"), REF)
        self.assertEqual((v.pos, v.ref, v.alt), (1, "G", "GA"))

    def test_deletion_left_shifts_to_anchor(self):
        # Deleting an A from the run, written at pos 4 (AA->A), left-aligns to pos 1.
        v = left_align(Variant("1", 4, "AA", "A"), REF)
        self.assertEqual((v.pos, v.ref, v.alt), (1, "GA", "G"))

    def test_repeat_shifted_inputs_converge(self):
        # The SAME insertion described at different positions in the repeat must
        # all normalize to one canonical representation.
        forms = [
            Variant("1", 2, "A", "AA"),
            Variant("1", 3, "A", "AA"),
            Variant("1", 4, "A", "AA"),
            Variant("1", 5, "A", "AA"),
        ]
        results = {(v.pos, v.ref, v.alt) for v in (left_align(f, REF) for f in forms)}
        self.assertEqual(results, {(1, "G", "GA")})

    def test_substitution_parsimony(self):
        # AC->AG at pos 1 should parsimoniously reduce to the SNV C->G at pos 2.
        v = left_align(Variant("2", 1, "AC", "AG"), REF)
        self.assertEqual((v.pos, v.ref, v.alt), (2, "C", "G"))

    def test_already_canonical_indel_unchanged(self):
        v = left_align(Variant("1", 1, "G", "GA"), REF)
        self.assertEqual((v.pos, v.ref, v.alt), (1, "G", "GA"))


class TestErrors(unittest.TestCase):
    def test_invalid_alt_raises(self):
        with self.assertRaises(ValueError):
            left_align(Variant("1", 1, "G", "X"), REF)

    def test_ref_equals_alt_raises(self):
        with self.assertRaises(ValueError):
            left_align(Variant("1", 1, "G", "G"), REF)

    def test_ref_mismatch_raises(self):
        # Variant claims REF 'A' at pos 1, but reference has 'G' there.
        with self.assertRaises(ValueError):
            left_align(Variant("1", 1, "A", "AA"), REF)

    def test_unknown_contig_raises(self):
        with self.assertRaises(ReferenceLookupError):
            left_align(Variant("ZZ", 1, "A", "AA"), REF)

    def test_multiallelic_rejected(self):
        with self.assertRaises(ValueError):
            left_align(Variant("1", 5, "A", "AA,AAA"), REF)


class TestNormalizeWithReference(unittest.TestCase):
    def test_split_then_align_multiallelic(self):
        out = normalize_with_reference(Variant("1", 5, "A", "AA,AAA"), REF)
        self.assertEqual(
            [(v.pos, v.ref, v.alt) for v in out],
            [(1, "G", "GA"), (1, "G", "GAA")],
        )

    def test_variant_key(self):
        self.assertEqual(variant_key(Variant("1", 100, "a", "g")), "GRCh38-1-100-A-G")


class TestInMemoryReference(unittest.TestCase):
    def test_sequence_and_base(self):
        self.assertEqual(REF.sequence("1", 1, 6), "GAAAAT")
        self.assertEqual(REF.base_at("1", 6), "T")

    def test_out_of_range_raises(self):
        with self.assertRaises(ReferenceLookupError):
            REF.sequence("1", 0, 1)
        with self.assertRaises(ReferenceLookupError):
            REF.base_at("1", 7)

    def test_unknown_contig_raises(self):
        with self.assertRaises(ReferenceLookupError):
            REF.sequence("nope", 1, 1)


class TestFastaReference(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "mini.fa")
        # contig 1 single line; contig 2 wrapped at 4 bases/line.
        with open(self.path, "w") as f:
            f.write(">1 desc\nGAAAAT\n>2\nACGT\nACGT\n")
        self.ref = FastaReference(self.path)

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def test_contig_lengths(self):
        self.assertEqual(self.ref.contig_length("1"), 6)
        self.assertEqual(self.ref.contig_length("2"), 8)

    def test_single_line_sequence(self):
        self.assertEqual(self.ref.sequence("1", 1, 6), "GAAAAT")
        self.assertEqual(self.ref.base_at("1", 1), "G")

    def test_wrapped_sequence_across_lines(self):
        self.assertEqual(self.ref.sequence("2", 1, 8), "ACGTACGT")
        self.assertEqual(self.ref.sequence("2", 4, 5), "TA")

    def test_left_align_through_fasta(self):
        v = left_align(Variant("1", 5, "A", "AA"), self.ref)
        self.assertEqual((v.pos, v.ref, v.alt), (1, "G", "GA"))

    def test_out_of_range_raises(self):
        with self.assertRaises(ReferenceLookupError):
            self.ref.sequence("1", 1, 99)


# --------------------------------------------------------------------------- #
# Canonical variant identity (Part A, tasks 1-2)                              #
# --------------------------------------------------------------------------- #
class TestCanonicalIdentity(unittest.TestCase):
    def test_provider_and_canonical_key(self):
        self.assertEqual(provider_key("1", 100, "a", "g"), "1-100-A-G")
        self.assertEqual(canonical_key("1", 100, "A", "G"), "GRCh38-1-100-A-G")

    def test_chr_prefix_is_stripped(self):
        self.assertEqual(normalize_chrom("chr1"), "1")
        self.assertEqual(provider_key("chr1", 100, "A", "G"), "1-100-A-G")

    def test_build_round_trip(self):
        self.assertEqual(add_build("1-100-A-G"), "GRCh38-1-100-A-G")
        self.assertEqual(provider_key_of("GRCh38-1-100-A-G"), "1-100-A-G")
        self.assertEqual(provider_key_of("1-100-A-G"), "1-100-A-G")  # idempotent
        self.assertEqual(strip_build("GRCh38-X-5-A-T"), ("GRCh38", "X-5-A-T"))
        self.assertEqual(strip_build("X-5-A-T"), (None, "X-5-A-T"))

    def test_parse_key_distinguishes_forms(self):
        self.assertEqual(parse_key("GRCh38-1-100-A-G"),
                         {"build": "GRCh38", "chrom": "1", "pos": 100, "ref": "A", "alt": "G"})
        self.assertIsNone(parse_key("1-100-A-G")["build"])
        with self.assertRaises(ValueError):
            parse_key("not-a-key-too-many-fields-here-x")

    def test_storage_compatible_format(self):
        # The canonical key is byte-compatible with storage.classifications.variant_key.
        self.assertEqual(Variant("1", 100, "A", "G").canonical_key(), "GRCh38-1-100-A-G")
        self.assertEqual(Variant("1", 100, "A", "G").provider_key(), "1-100-A-G")


# --------------------------------------------------------------------------- #
# Reference-aware normalization workflow (Part A, tasks 3, 8)                 #
# --------------------------------------------------------------------------- #
class TestNormalizeLocus(unittest.TestCase):
    def test_snv_no_reference_needed(self):
        r = normalize_locus("1", 100, "A", "G")
        self.assertTrue(r.ok)
        self.assertFalse(r.is_indel)
        self.assertEqual(r.method, "snv")
        self.assertEqual(r.key, "GRCh38-1-100-A-G")
        self.assertFalse(r.blocking)

    def test_indel_without_reference_is_advisory(self):
        r = normalize_locus("1", 5, "A", "AA")
        self.assertTrue(r.ok)
        self.assertTrue(r.is_indel)
        self.assertEqual(r.method, "reference_free_trim")
        self.assertIn("indel_not_left_aligned", r.warnings)
        self.assertFalse(r.blocking)  # advisory, not blocking

    def test_indel_with_reference_left_aligns(self):
        r = normalize_locus("1", 5, "A", "AA", reference=REF)
        self.assertTrue(r.ok)
        self.assertEqual(r.method, "reference_left_aligned")
        self.assertEqual((r.variant.pos, r.variant.ref, r.variant.alt), (1, "G", "GA"))
        self.assertEqual(r.key, "GRCh38-1-1-G-GA")

    def test_reference_mismatch_is_blocking_not_silent(self):
        r = normalize_locus("1", 1, "A", "AA", reference=REF)  # REF says A, reference has G
        self.assertFalse(r.ok)
        self.assertTrue(r.blocking)
        self.assertIn("reference_mismatch", r.warnings)

    def test_invalid_allele_is_blocking(self):
        r = normalize_locus("1", 1, "A", "X")
        self.assertFalse(r.ok)
        self.assertTrue(r.blocking)
        self.assertIn("invalid_allele", r.warnings)

    def test_cannot_left_align_past_contig_start_is_blocking(self):
        # Inserting into a homopolymer run that begins at position 1 forces a shift
        # past the contig start -> ReferenceLookupError -> blocking, never silent.
        r = normalize_locus("c", 1, "A", "AA", reference=InMemoryReference({"c": "AAAA"}))
        self.assertFalse(r.ok)
        self.assertTrue(r.blocking)
        self.assertIn("reference_lookup_failed", r.warnings)

    def test_normalize_case_missing_locus(self):
        r = normalize_case({"id": "x"})  # no locus block
        self.assertFalse(r.ok)
        self.assertIn("missing_locus", r.warnings)
        self.assertTrue(r.blocking)

    def test_normalize_case_from_fixture_locus(self):
        case = {"locus": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}}
        self.assertEqual(normalize_case(case).key, "GRCh38-1-100-A-G")


# --------------------------------------------------------------------------- #
# Identity audit: SNV/indel duplicate & mismatch rates (Part A, task 7)       #
# --------------------------------------------------------------------------- #
class TestAuditLoci(unittest.TestCase):
    def test_reference_backed_reveals_indel_duplicates(self):
        # Four repeat-shifted spellings of the same insertion + a duplicated SNV.
        loci = [("1", 2, "A", "AA"), ("1", 3, "A", "AA"), ("1", 4, "A", "AA"),
                ("1", 5, "A", "AA"), ("1", 100, "C", "T"), ("1", 100, "C", "T")]
        # No reference: indels stay distinct (not left-aligned), SNVs collide.
        a0 = audit_loci(loci)
        self.assertFalse(a0["reference_available"])
        self.assertEqual(a0["reference_free"]["indel"]["duplicated_loci"], 0)
        self.assertEqual(a0["reference_free"]["snv"]["duplicated_loci"], 2)
        self.assertEqual(a0["indel_not_left_aligned"], 4)
        # With reference: the four indels collapse to one canonical key.
        a1 = audit_loci(loci, reference=REF)
        self.assertTrue(a1["reference_available"])
        self.assertEqual(a1["reference_backed"]["indel"]["duplicated_loci"], 4)
        self.assertEqual(a1["reference_backed"]["indel_duplicates_revealed"], 4)

    def test_counts_snv_and_indel(self):
        loci = [("1", 100, "C", "T"), ("1", 5, "A", "AA")]
        a = audit_loci(loci)
        self.assertEqual(a["snv"], 1)
        self.assertEqual(a["indel"], 1)
        self.assertEqual(a["total_loci"], 2)

    def test_invalid_loci_are_counted_not_crashed(self):
        a = audit_loci([("1", 100, "C", "X"), "garbage", ("1", 100, "C", "T")])
        self.assertEqual(a["snv"], 1)
        self.assertGreaterEqual(a["invalid_loci"], 2)

    def test_reference_mismatch_counted(self):
        # An indel whose REF disagrees with the reference is a mismatch, not a dup.
        a = audit_loci([("1", 1, "A", "AA")], reference=REF)
        self.assertEqual(a["reference_backed"]["reference_mismatch"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
