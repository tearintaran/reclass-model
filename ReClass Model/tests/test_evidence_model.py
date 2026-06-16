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
    EvidenceBundle,
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
