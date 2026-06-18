"""Unit tests for the evidence model layer (EvidenceBundle + serialization).

Run from the ``ReClass Model/`` directory:

    ../.venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import EvidenceEvent, classify
from evidence.model import (
    SCHEMA_VERSION,
    CohortCounts,
    EvidenceBundle,
    TranscriptIdentity,
    event_from_dict,
    event_to_dict,
)
from evidence.providers import EvidenceProvider


def _events():
    return [
        EvidenceEvent(
            source="clingen",
            acmg_criterion="PVS1",
            evidence_direction="pathogenic",
            applied_strength="very_strong",
            source_version="ERepo",
            raw={"clingen_case_id": "CG-1"},
        ),
        EvidenceEvent(
            source="gnomad",
            acmg_criterion="PM2",
            evidence_direction="pathogenic",
            applied_strength="supporting",
            source_version="gnomAD",
            raw={"popmax_af": 1e-6},
        ),
    ]


class TestEventSerialization(unittest.TestCase):
    def test_event_round_trip_preserves_all_fields(self):
        for e in _events():
            back = event_from_dict(event_to_dict(e))
            self.assertEqual(back, e)

    def test_event_to_dict_has_stable_keys(self):
        d = event_to_dict(_events()[0])
        self.assertEqual(
            set(d),
            {
                "source",
                "acmg_criterion",
                "evidence_direction",
                "applied_strength",
                "points",
                "source_version",
                "raw",
            },
        )
        # raw is copied, not aliased.
        d["raw"]["mutated"] = True
        self.assertNotIn("mutated", _events()[0].raw)


class TestEvidenceBundle(unittest.TestCase):
    def _bundle(self):
        return EvidenceBundle(
            variant_key="clinvar_variation_id:586",
            events=_events(),
            provider_versions={"clingen_erepo": "ERepo"},
            source_records=[{"id": "CG-1"}],
            warnings=["multiple_clingen_matches"],
            match={"clingen_variation_id_match": True, "clingen_case_id": "CG-1"},
        )

    def test_defaults_are_independent(self):
        a, b = EvidenceBundle(), EvidenceBundle()
        a.events.append(_events()[0])
        a.warnings.append("x")
        a.provider_versions["k"] = "v"
        self.assertEqual(b.events, [])
        self.assertEqual(b.warnings, [])
        self.assertEqual(b.provider_versions, {})

    def test_to_dict_shape(self):
        d = self._bundle().to_dict()
        self.assertEqual(d["schema_version"], SCHEMA_VERSION)
        self.assertEqual(d["variant_key"], "clinvar_variation_id:586")
        self.assertEqual(len(d["events"]), 2)
        self.assertEqual(d["match"]["clingen_case_id"], "CG-1")

    def test_json_round_trip_preserves_events_and_provenance(self):
        original = self._bundle()
        restored = EvidenceBundle.from_json(original.to_json())
        self.assertEqual(restored.events, original.events)
        self.assertEqual(restored.warnings, original.warnings)
        self.assertEqual(restored.provider_versions, original.provider_versions)
        self.assertEqual(restored.match, original.match)
        self.assertEqual(restored.source_records, original.source_records)
        # Provenance in event.raw survives the round trip.
        self.assertEqual(restored.events[0].raw["clingen_case_id"], "CG-1")

    def test_round_trip_reproduces_engine_hash(self):
        original = self._bundle()
        restored = EvidenceBundle.from_dict(original.to_dict())
        self.assertEqual(
            restored.reconstruction_hash(), original.reconstruction_hash()
        )
        # ...and the same classification tier + hash as scoring the events directly.
        direct = classify(original.events)
        self.assertEqual(direct.reconstruction_hash, original.reconstruction_hash())

    def test_to_json_is_sorted_and_stable(self):
        a = self._bundle().to_json()
        b = self._bundle().to_json()
        self.assertEqual(a, b)

    def test_none_match_round_trips(self):
        bundle = EvidenceBundle(match=None)
        self.assertIsNone(EvidenceBundle.from_json(bundle.to_json()).match)


class TestTranscriptAndCohortFields(unittest.TestCase):
    """Additive transcript identity + PS4 cohort-count fields (job1 tasks 4-5)."""

    def test_defaults_are_none_and_back_compatible(self):
        # A bundle written without the new fields deserializes unchanged.
        d = EvidenceBundle(events=_events()).to_dict()
        self.assertIsNone(d["transcript"])
        self.assertIsNone(d["cohort_counts"])
        d.pop("transcript")
        d.pop("cohort_counts")  # simulate an older serialized dict (no new keys)
        restored = EvidenceBundle.from_dict(d)
        self.assertIsNone(restored.transcript)
        self.assertIsNone(restored.cohort_counts)

    def test_transcript_round_trips(self):
        bundle = EvidenceBundle(
            transcript=TranscriptIdentity(mane_select="NM_1.3", gene="G", hgvs_c="c.1A>G"))
        restored = EvidenceBundle.from_json(bundle.to_json())
        self.assertEqual(restored.transcript.mane_select, "NM_1.3")
        self.assertTrue(restored.transcript.is_mane_select)
        self.assertEqual(restored.transcript.hgvs_c, "c.1A>G")

    def test_cohort_counts_round_trips_with_denominator(self):
        bundle = EvidenceBundle(cohort_counts=CohortCounts(
            case_count=40, case_total=100, control_count=5, control_total=100,
            odds_ratio=8.0, ci_low=3.0))
        d = bundle.to_dict()
        self.assertEqual(d["cohort_counts"]["denominator"], 200)
        restored = EvidenceBundle.from_json(bundle.to_json())
        self.assertEqual(restored.cohort_counts.case_count, 40)
        self.assertEqual(restored.cohort_counts.denominator, 200)
        self.assertAlmostEqual(restored.cohort_counts.odds_ratio, 8.0)

    def test_new_fields_do_not_change_reconstruction_hash(self):
        # transcript / cohort_counts are provenance, outside the engine event hash.
        plain = EvidenceBundle(events=_events())
        annotated = EvidenceBundle(
            events=_events(),
            transcript=TranscriptIdentity(mane_select="NM_1.3"),
            cohort_counts=CohortCounts(case_count=1, case_total=2))
        self.assertEqual(plain.reconstruction_hash(), annotated.reconstruction_hash())


class TestProviderInterface(unittest.TestCase):
    def test_base_fetch_raises(self):
        with self.assertRaises(NotImplementedError):
            EvidenceProvider().fetch({"x": 1})

    def test_subclass_can_return_bundle(self):
        class Dummy(EvidenceProvider):
            name = "dummy"
            version = "1"

            def fetch(self, case_or_variant):
                return EvidenceBundle(provider_versions={self.name: self.version})

        bundle = Dummy().fetch(None)
        self.assertEqual(bundle.provider_versions, {"dummy": "1"})


if __name__ == "__main__":
    unittest.main()
