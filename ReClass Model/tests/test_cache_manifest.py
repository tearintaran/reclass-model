"""Tests for the provider source-cache generators + manifests (job1 task 2).

Covers the shared manifest helper and each cache builder (AlphaMissense, conservation,
gene constraint, and the validated functional/phenotype source). Every builder must:

  * write a manifest recording source version, checksum, and access date,
  * be byte-stable -- rebuilding from the same inputs yields an identical cache file
    (and therefore the recorded checksum re-verifies).

All caches are written to a temporary directory, so nothing touches the committed
``data/cache/providers/`` tree and no network or large source files are needed.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_cache_manifest -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import cache_manifest
from evidence.alphamissense import AlphaMissenseIndex
from evidence.computational import ConservationProvider, GeneConstraintProvider
from evidence.upstream import FunctionalPhenotypeCache


class _TmpDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def _path(self, name):
        return os.path.join(self.dir, name)

    @staticmethod
    def _read_bytes(path):
        with open(path, "rb") as f:
            return f.read()


class TestManifestHelper(_TmpDir):
    def test_write_cache_records_provenance_and_checksum(self):
        path = self._path("c.json")
        manifest = cache_manifest.write_cache(
            {"provider": "x", "version": "1", "scores": {"b": 2, "a": 1}}, path,
            provider="x", source="Some Source", source_version="v9",
            access_date="2026-06-17", record_count=2, source_url="https://e/x")
        self.assertEqual(manifest["source"], "Some Source")
        self.assertEqual(manifest["source_version"], "v9")
        self.assertEqual(manifest["access_date"], "2026-06-17")
        self.assertEqual(manifest["record_count"], 2)
        self.assertEqual(manifest["checksum_algorithm"], "sha256")
        # The recorded checksum is the checksum of the bytes actually on disk.
        self.assertEqual(manifest["checksum"], cache_manifest.file_sha256(path))

    def test_cache_is_byte_stable(self):
        payload = {"provider": "x", "version": "1", "scores": {"b": 2, "a": 1}}
        p1, p2 = self._path("a.json"), self._path("b.json")
        cache_manifest.write_cache(payload, p1, provider="x", source="s",
                                   source_version="v1", access_date="2026-06-17")
        cache_manifest.write_cache(dict(payload), p2, provider="x", source="s",
                                   source_version="v1", access_date="2026-06-17")
        self.assertEqual(self._read_bytes(p1), self._read_bytes(p2))

    def test_verify_cache_detects_match_and_tamper(self):
        path = self._path("c.json")
        cache_manifest.write_cache({"a": 1}, path, provider="x", source="s",
                                   source_version="v1", access_date="2026-06-17")
        self.assertTrue(cache_manifest.verify_cache(path)["checksum_match"])
        with open(path, "a", encoding="utf-8") as f:
            f.write("tampered\n")
        self.assertFalse(cache_manifest.verify_cache(path)["checksum_match"])

    def test_verify_cache_no_manifest_is_no_expectation(self):
        path = self._path("plain.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}\n")
        v = cache_manifest.verify_cache(path)
        self.assertIsNone(v["checksum_match"])      # no manifest -> no false pass
        self.assertFalse(v["manifest_exists"])


class TestAlphaMissenseCache(_TmpDir):
    def test_builder_writes_manifest_and_is_byte_stable(self):
        idx = AlphaMissenseIndex.from_scores({"1-100-A-G": 0.9, "1-50-C-T": 0.2})
        path = self._path("am.json")
        manifest = idx.to_cache_with_manifest(path, access_date="2026-06-17")
        self.assertEqual(manifest["provider"], "alphamissense")
        self.assertEqual(manifest["source_version"], "AlphaMissense_v1")
        self.assertEqual(manifest["access_date"], "2026-06-17")
        self.assertEqual(manifest["record_count"], 2)
        self.assertEqual(manifest["checksum"], cache_manifest.file_sha256(path))
        first = self._read_bytes(path)
        idx.to_cache_with_manifest(path, access_date="2026-06-17")
        self.assertEqual(first, self._read_bytes(path))
        # Cache round-trips back into an equivalent index.
        self.assertEqual(AlphaMissenseIndex.from_cache(path).lookup("1", 100, "A", "G"), 0.9)


class TestConservationCache(_TmpDir):
    def test_builder_writes_manifest(self):
        cp = ConservationProvider.from_scores({"1-100": 2.5, "1-50": -1.0})
        path = self._path("cons.json")
        manifest = cp.to_cache_with_manifest(path, access_date="2026-06-17")
        self.assertEqual(manifest["provider"], "conservation")
        self.assertEqual(manifest["record_count"], 2)
        self.assertTrue(cache_manifest.verify_cache(path)["checksum_match"])


class TestGeneConstraintCache(_TmpDir):
    def test_builder_writes_manifest(self):
        gp = GeneConstraintProvider.from_metrics({"NF1": {"loeuf": 0.1, "pli": 0.99}})
        path = self._path("gc.json")
        manifest = gp.to_cache_with_manifest(path, access_date="2026-06-17")
        self.assertEqual(manifest["provider"], "gene_constraint")
        self.assertEqual(manifest["record_count"], 1)
        self.assertTrue(cache_manifest.verify_cache(path)["checksum_match"])


class TestFunctionalPhenotypeCache(_TmpDir):
    ROWS = [
        {"variant_key": "1-100-A-G", "functional": {"result": "damaging", "oddspath": 20.0}},
        {"variant_key": "1-100-A-G", "phenotype": {"specificity": "high"}},
        {"variant_key": "1-50-C-T", "functional": {"result": "normal"}},
        {"no_key": True},  # skipped -- never keyed under a bogus default
    ]

    def test_build_from_rows_merges_and_skips_keyless(self):
        cache = FunctionalPhenotypeCache.build_from_rows(self.ROWS)
        self.assertEqual(len(cache), 2)
        rec = cache.lookup("1-100-A-G")
        self.assertEqual(rec["functional"]["result"], "damaging")
        self.assertEqual(rec["phenotype"]["specificity"], "high")

    def test_target_key_filter(self):
        cache = FunctionalPhenotypeCache.build_from_rows(self.ROWS, target_keys={"1-50-C-T"})
        self.assertEqual(len(cache), 1)
        self.assertIsNone(cache.lookup("1-100-A-G"))

    def test_builder_writes_manifest_and_is_byte_stable(self):
        cache = FunctionalPhenotypeCache.build_from_rows(self.ROWS)
        path = self._path("fp.json")
        manifest = cache.to_cache_with_manifest(path, access_date="2026-06-17")
        self.assertEqual(manifest["provider"], "functional_phenotype")
        self.assertEqual(manifest["record_count"], 2)
        self.assertEqual(manifest["access_date"], "2026-06-17")
        first = self._read_bytes(path)
        FunctionalPhenotypeCache.build_from_rows(self.ROWS).to_cache_with_manifest(
            path, access_date="2026-06-17")
        self.assertEqual(first, self._read_bytes(path))
        self.assertEqual(FunctionalPhenotypeCache.from_cache(path).lookup(
            "1-50-C-T")["functional"]["result"], "normal")


if __name__ == "__main__":
    unittest.main()
