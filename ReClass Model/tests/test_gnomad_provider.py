"""Unit tests for the gnomAD evidence provider (evidence/gnomad.py).

Fully offline: a fake fetcher and a fixed clock stand in for the network so no live
gnomAD call ever happens. Covers the popmax-FAF-with-AF-fallback mapping, the
absent-vs-failed distinction (and the "absence != AF 0" invariant), provenance in
source records, response caching + retry semantics, provider stats, and determinism
for a fixed cache snapshot.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import classify, classify_signals, derive_criteria_from_signals
from evidence.gnomad import (
    DATASET,
    PROVIDER_NAME,
    PROVIDER_VERSION,
    GnomadCache,
    GnomadProvider,
    variant_id_of,
)

CLOCK = lambda: "2026-01-01T00:00:00+00:00"  # noqa: E731 - fixed clock for determinism


def _matched(faf=None, pop=None, genome=None, exome=None):
    return {"status": "matched", "variant": {
        "joint": {"faf95": {"popmax": faf, "popmax_population": pop}},
        "genome": {"af": genome},
        "exome": {"af": exome},
    }}


# variant id -> fetcher result. Covers every branch of the AF resolution.
RAW = {
    "1-100-A-G": _matched(faf=0.2, pop="nfe", genome=0.18, exome=0.21),   # BA1 via faf95
    "1-200-A-G": _matched(faf=None, genome=0.02, exome=0.005),            # BS1 via fallback
    "1-300-A-G": _matched(faf=0.001, pop="afr"),                          # indeterminate band
    "1-400-A-G": _matched(faf=5e-6, pop="eas"),                           # PM2 (very rare)
    "1-500-A-G": _matched(faf=None),                                      # matched, no AF anywhere
    "1-600-A-G": {"status": "absent", "variant": None},                  # not in gnomAD
    "1-700-A-G": {"status": "failed", "variant": None},                  # transport/query error
}


class FakeFetcher:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def __call__(self, variant_id):
        self.calls.append(variant_id)
        return self.raw.get(variant_id, {"status": "absent", "variant": None})


def _provider(retry_failed=False):
    return GnomadProvider(GnomadCache(), FakeFetcher(RAW), retry_failed=retry_failed, clock=CLOCK)


def _vcase(vid):
    chrom, pos, ref, alt = vid.split("-")
    return {"locus": {"chrom": chrom, "pos": int(pos), "ref": ref, "alt": alt}}


class TestVariantId(unittest.TestCase):
    def test_from_case_bare_tuple_string(self):
        self.assertEqual(variant_id_of(_vcase("1-100-A-G")), "1-100-A-G")
        self.assertEqual(variant_id_of({"chrom": "X", "pos": 9, "ref": "A", "alt": "T"}), "X-9-A-T")
        self.assertEqual(variant_id_of(("1", 5, "A", "G")), "1-5-A-G")
        self.assertEqual(variant_id_of("1-5-A-G"), "1-5-A-G")

    def test_unresolvable_is_none(self):
        self.assertIsNone(variant_id_of({"locus": {"chrom": "1"}}))
        self.assertIsNone(variant_id_of("nope"))
        self.assertIsNone(variant_id_of(None))


class TestAfResolution(unittest.TestCase):
    def setUp(self):
        self.provider = _provider()

    def test_faf95_popmax_to_ba1(self):
        b = self.provider.fetch(_vcase("1-100-A-G"))
        self.assertEqual([e.acmg_criterion for e in b.events], ["BA1"])
        self.assertTrue(b.match["gnomad_match"])
        self.assertEqual(b.match["af"], 0.2)
        self.assertEqual(b.match["af_source"], "faf95_popmax")
        self.assertEqual(b.match["population"], "nfe")
        self.assertEqual(b.warnings, [])
        rec = b.source_records[0]
        self.assertEqual(rec["faf95_popmax"], 0.2)
        self.assertEqual(rec["genome_af"], 0.18)
        self.assertEqual(rec["exome_af"], 0.21)
        self.assertEqual(rec["dataset"], DATASET)

    def test_genome_exome_fallback_to_bs1_with_warning(self):
        b = self.provider.fetch(_vcase("1-200-A-G"))
        self.assertEqual([e.acmg_criterion for e in b.events], ["BS1"])
        self.assertEqual(b.match["af"], 0.02)  # max(0.02, 0.005)
        self.assertEqual(b.match["af_source"], "genome_exome_fallback")
        self.assertIn("gnomad_faf95_unavailable_used_af_fallback", b.warnings)

    def test_indeterminate_band_matched_but_eventless(self):
        b = self.provider.fetch(_vcase("1-300-A-G"))
        self.assertEqual(b.events, [])
        self.assertTrue(b.match["gnomad_match"])
        self.assertEqual(b.warnings, ["gnomad_af_indeterminate"])

    def test_very_rare_to_pm2(self):
        b = self.provider.fetch(_vcase("1-400-A-G"))
        self.assertEqual([e.acmg_criterion for e in b.events], ["PM2"])

    def test_matched_without_af_warns_unavailable(self):
        b = self.provider.fetch(_vcase("1-500-A-G"))
        self.assertEqual(b.events, [])
        self.assertIsNone(b.match["af"])
        self.assertEqual(b.warnings, ["gnomad_af_unavailable"])


class TestAbsentVsFailed(unittest.TestCase):
    def test_absence_is_unknown_evidence_not_af_zero(self):
        b = _provider().fetch(_vcase("1-600-A-G"))
        self.assertEqual(b.events, [])                       # no BA1/BS1/PM2 fabricated
        self.assertFalse(b.match["gnomad_match"])
        self.assertEqual(b.match["status"], "absent")
        self.assertNotIn("af", b.match)                      # never AF 0
        self.assertEqual(b.warnings, ["gnomad_absent"])
        self.assertEqual(b.source_records[0]["status"], "absent")

    def test_failure_is_distinct_from_absence(self):
        b = _provider().fetch(_vcase("1-700-A-G"))
        self.assertEqual(b.events, [])
        self.assertEqual(b.match["status"], "failed")
        self.assertEqual(b.warnings, ["gnomad_query_failed"])

    def test_missing_locus_warns(self):
        b = _provider().fetch({"locus": {"chrom": "1"}})
        self.assertEqual(b.warnings, ["missing_locus"])
        self.assertFalse(b.match["gnomad_match"])


class TestProvenanceFields(unittest.TestCase):
    def test_records_version_timestamp_query_id(self):
        rec = _provider().fetch(_vcase("1-100-A-G")).source_records[0]
        self.assertEqual(rec["version"], PROVIDER_VERSION)
        self.assertEqual(rec["timestamp"], "2026-01-01T00:00:00+00:00")
        self.assertTrue(rec["query_id"])
        self.assertEqual(rec["status"], "matched")


class TestCachingAndStats(unittest.TestCase):
    def test_second_fetch_served_from_cache(self):
        provider = _provider()
        provider.fetch(_vcase("1-100-A-G"))
        self.assertEqual(provider.fetcher.calls, ["1-100-A-G"])
        provider.fetch(_vcase("1-100-A-G"))  # cache hit -> no new network call
        self.assertEqual(provider.fetcher.calls, ["1-100-A-G"])
        self.assertEqual(provider.stats.cached, 1)

    def test_stats_partition_queried(self):
        provider = _provider()
        for vid in ("1-100-A-G", "1-600-A-G", "1-700-A-G"):
            provider.fetch(_vcase(vid))
        s = provider.stats.as_dict()
        self.assertEqual(s["queried"], 3)
        self.assertEqual(s["matched"] + s["absent"] + s["failed"], s["queried"])
        self.assertEqual((s["matched"], s["absent"], s["failed"]), (1, 1, 1))

    def test_failed_not_retried_by_default(self):
        provider = _provider(retry_failed=False)
        provider.fetch(_vcase("1-700-A-G"))             # caches the failure
        provider.fetch(_vcase("1-700-A-G"))             # default: serve cached failure
        self.assertEqual(provider.fetcher.calls, ["1-700-A-G"])
        self.assertEqual(provider.stats.cached, 1)

    def test_failed_retried_when_requested(self):
        provider = _provider(retry_failed=True)
        provider.fetch(_vcase("1-700-A-G"))
        provider.fetch(_vcase("1-700-A-G"))             # retry_failed -> re-query
        self.assertEqual(provider.fetcher.calls, ["1-700-A-G", "1-700-A-G"])

    def test_offline_cache_miss_is_deterministic_failure(self):
        provider = GnomadProvider(GnomadCache(), fetcher=None)  # offline
        b = provider.fetch(_vcase("1-100-A-G"))
        self.assertEqual(b.warnings, ["gnomad_not_cached"])
        self.assertEqual(provider.stats.failed, 1)
        self.assertNotIn("1-100-A-G", provider.cache)  # offline miss never poisons cache


class TestDeterminismAndPersistence(unittest.TestCase):
    def _populated_cache(self):
        provider = _provider()
        for vid in RAW:
            provider.fetch(_vcase(vid))
        return provider.cache

    def test_offline_fetch_deterministic_for_fixed_snapshot(self):
        cache = self._populated_cache()
        a = GnomadProvider(cache, fetcher=None).fetch(_vcase("1-100-A-G")).to_dict()
        b = GnomadProvider(cache, fetcher=None).fetch(_vcase("1-100-A-G")).to_dict()
        self.assertEqual(a, b)

    def test_cache_round_trip(self):
        cache = self._populated_cache()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "gnomad_cache.json")
            cache.to_cache(path)
            reloaded = GnomadCache.from_cache(path)
        provider = GnomadProvider(reloaded, fetcher=None)
        b = provider.fetch(_vcase("1-100-A-G"))
        self.assertEqual(b.match["af"], 0.2)
        self.assertEqual(provider.stats.cached, 1)

    def test_reproduces_engine_mapping(self):
        b = _provider().fetch(_vcase("1-100-A-G"))
        self.assertEqual([e.acmg_criterion for e in b.events],
                         [e.acmg_criterion for e in derive_criteria_from_signals({"gnomad_af": 0.2})])
        self.assertEqual(classify(b.events).tier, classify_signals({"gnomad_af": 0.2}).tier)

    def test_provider_versions_block(self):
        b = _provider().fetch(_vcase("1-100-A-G"))
        self.assertEqual(b.provider_versions, {PROVIDER_NAME: PROVIDER_VERSION})


if __name__ == "__main__":
    unittest.main()
