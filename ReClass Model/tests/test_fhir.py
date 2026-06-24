"""Tests for the FHIR Genomics / HL7 result export (gap.md C5).

Pure, offline unit tests: they feed a real ``classify(...)`` result plus a variant
key into the FHIR serializer and assert the acceptance criteria —

  * each ACMG tier maps to the correct LOINC answer code,
  * the variant coordinates round-trip back through ``engine.normalize.parse_key``
    to the same chrom/pos/ref/alt/build that went in (the "round-trip against the
    spec structure"),
  * the engine version + reconstruction hash travel in the bundle and match the
    classification (auditability/traceability),
  * the serializer is deterministic (same input -> identical JSON), and
  * a signed classification yields ``status: "final"`` while a draft yields
    ``status: "preliminary"``.

No network, no DB, no third-party FHIR library.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.normalize import parse_key  # noqa: E402
from engine.scoring import EvidenceEvent, classify  # noqa: E402
from reporting.fhir import (  # noqa: E402
    CLIN_SIG_LOINC,
    GENE_STUDIED_LOINC,
    TIER_TO_LOINC,
    amend_outbound_payload,
    amended_report_record,
    build_outbound_payload,
    clinician_notification_record,
    diagnostic_report,
    genomics_report_bundle,
    lis_ehr_lifecycle_adapter,
    molecular_sequence,
    replay_outbound_payload,
    to_json,
    transition_report_state,
    variant_observation,
)

_KEY = "GRCh38-1-100-A-G"


def _events_for_tier(tier: str):
    """A small evidence set whose ``classify`` result lands in ``tier``.

    Uses explicit-point curated criteria so the test is independent of the REVEL /
    gnomAD thresholds and only depends on the engine's tier cutoffs
    (Pathogenic >= 10, Likely Pathogenic >= 6, VUS >= 0, Likely Benign >= -6,
    Benign below that; BA1 stand-alone forces Benign).
    """
    def patho(points):
        return [EvidenceEvent(source="curated", acmg_criterion="PM1",
                              evidence_direction="pathogenic", points=points,
                              source_version="curated_v1")]

    if tier == "Pathogenic":
        return patho(12)
    if tier == "Likely Pathogenic":
        return patho(7)
    if tier == "VUS":
        return patho(2)
    if tier == "Likely Benign":
        return [EvidenceEvent(source="curated", acmg_criterion="BP1",
                              evidence_direction="benign", points=4,
                              source_version="curated_v1")]
    if tier == "Benign":
        return [EvidenceEvent(source="gnomad", acmg_criterion="BA1",
                              evidence_direction="benign", applied_strength="stand_alone",
                              source_version="gnomAD", raw={"popmax_af": 0.2})]
    raise AssertionError(tier)


class TierToLoincTests(unittest.TestCase):
    def test_each_tier_maps_to_expected_loinc_answer(self):
        # The expected LOINC SVI answer list (gap.md C5), keyed by engine tier.
        expected = {
            "Pathogenic": "LA6668-3",
            "Likely Pathogenic": "LA26332-9",
            "VUS": "LA26333-7",
            "Likely Benign": "LA26334-5",
            "Benign": "LA6675-8",
        }
        for tier, code in expected.items():
            clf = classify(_events_for_tier(tier))
            self.assertEqual(clf.tier, tier, f"fixture for {tier} produced {clf.tier}")
            obs = variant_observation(clf, variant_key=_KEY, signer="Dr. Reviewer")
            coding = obs["valueCodeableConcept"]["coding"][0]
            self.assertEqual(coding["system"], "http://loinc.org")
            self.assertEqual(coding["code"], code, f"tier {tier} -> wrong LOINC answer")
            # The constant table agrees with the asserted mapping.
            self.assertEqual(TIER_TO_LOINC[tier]["code"], code)

    def test_observation_code_is_clinical_significance_loinc(self):
        clf = classify(_events_for_tier("Pathogenic"))
        obs = variant_observation(clf, variant_key=_KEY, signer="x")
        self.assertEqual(obs["code"]["coding"][0]["code"], CLIN_SIG_LOINC)
        self.assertEqual(obs["code"]["coding"][0]["code"], "53037-8")

    def test_unmapped_tier_is_unknown_not_silently_vus(self):
        # A receipt with a tier the LOINC table does not cover must not be coerced
        # to "Uncertain significance" -- it is recorded as Unknown / data-absent.
        receipt = {
            "tier": "Established risk allele",
            "total_points": 0.0,
            "contributions": [],
            "overrides": [],
            "engine_version": "test",
            "reconstruction_hash": "deadbeef",
        }
        obs = variant_observation(receipt, variant_key=_KEY, signer="x")
        self.assertNotEqual(
            obs["valueCodeableConcept"]["coding"][0]["code"], "LA26333-7"
        )
        self.assertEqual(obs["valueCodeableConcept"]["coding"][0]["code"], "LA4489-6")


class RoundTripTests(unittest.TestCase):
    def test_observation_coordinates_round_trip_via_parse_key(self):
        clf = classify(_events_for_tier("Pathogenic"))
        obs = variant_observation(clf, variant_key=_KEY, signer="x")
        comps = {c["code"]["coding"][0]["code"]: c for c in obs["component"]}
        chrom = comps["48000-4"]["valueString"]
        pos = comps["81254-5"]["valueInteger"]
        ref = comps["69547-8"]["valueString"]
        alt = comps["69551-0"]["valueString"]
        # Reassemble the key from the FHIR coordinates and parse it back: it must
        # recover the SAME identity that was serialized.
        rebuilt = f"GRCh38-{chrom}-{pos}-{ref}-{alt}"
        self.assertEqual(rebuilt, _KEY)
        parsed = parse_key(rebuilt)
        original = parse_key(_KEY)
        self.assertEqual(parsed, original)

    def test_molecular_sequence_coordinates_round_trip(self):
        seq = molecular_sequence(_KEY)
        v = seq["variant"][0]
        chrom = seq["referenceSeq"]["referenceSeqId"]["text"]
        build = seq["referenceSeq"]["genomeBuild"]
        rebuilt = f"{build}-{chrom}-{v['start']}-{v['referenceAllele']}-{v['observedAllele']}"
        self.assertEqual(parse_key(rebuilt), parse_key(_KEY))

    def test_provider_key_without_build_defaults_to_grch38(self):
        # A bare provider key (no build token) still round-trips to a GRCh38 default.
        seq = molecular_sequence("1-100-A-G")
        self.assertEqual(seq["referenceSeq"]["genomeBuild"], "GRCh38")
        v = seq["variant"][0]
        self.assertEqual(v["start"], 100)
        self.assertEqual(v["referenceAllele"], "A")
        self.assertEqual(v["observedAllele"], "G")

    def test_indel_window_end_accounts_for_ref_length(self):
        seq = molecular_sequence("GRCh38-1-100-ATG-A")
        # A 3-base REF spans positions 100..102.
        self.assertEqual(seq["referenceSeq"]["windowStart"], 100)
        self.assertEqual(seq["referenceSeq"]["windowEnd"], 102)
        self.assertEqual(seq["variant"][0]["end"], 102)


class TraceabilityTests(unittest.TestCase):
    def test_engine_version_and_hash_in_observation_identifiers(self):
        clf = classify(_events_for_tier("Pathogenic"))
        obs = variant_observation(clf, variant_key=_KEY, signer="x")
        ids = {i["system"]: i["value"] for i in obs["identifier"]}
        self.assertEqual(ids["urn:reclass:engine-version"], clf.engine_version)
        self.assertEqual(ids["urn:reclass:reconstruction-hash"], clf.reconstruction_hash)
        self.assertEqual(ids["urn:reclass:variant-key"], _KEY)
        # The method (derivation) also names the engine + hash.
        method = obs["method"]["coding"][0]["display"]
        self.assertIn(clf.engine_version, method)
        self.assertIn(clf.reconstruction_hash, method)

    def test_hash_and_version_present_in_bundle(self):
        clf = classify(_events_for_tier("Likely Pathogenic"))
        bundle = genomics_report_bundle(clf, variant_key=_KEY, signer="x")
        text = to_json(bundle)
        self.assertIn(clf.reconstruction_hash, text)
        self.assertIn(clf.engine_version, text)

    def test_criteria_components_one_per_contribution(self):
        events = [
            EvidenceEvent(source="revel", acmg_criterion="PP3",
                          evidence_direction="pathogenic", applied_strength="strong",
                          source_version="REVEL"),
            EvidenceEvent(source="gnomad", acmg_criterion="PM2",
                          evidence_direction="pathogenic", applied_strength="supporting",
                          source_version="gnomAD"),
        ]
        clf = classify(events)
        obs = variant_observation(clf, variant_key=_KEY, gene="BRCA1", signer="x")
        criterion_comps = [
            c for c in obs["component"]
            if c.get("valueCodeableConcept", {}).get("coding", [{}])[0].get("system")
            == "urn:reclass:acmg-criterion"
        ]
        self.assertEqual(len(criterion_comps), len(clf.contributions))
        codes = {c["valueCodeableConcept"]["coding"][0]["code"] for c in criterion_comps}
        self.assertEqual(codes, {"PP3", "PM2"})

    def test_gene_component_present_when_supplied(self):
        clf = classify(_events_for_tier("VUS"))
        obs = variant_observation(clf, variant_key=_KEY, gene="TP53", signer="x")
        gene_comps = [
            c for c in obs["component"]
            if c["code"]["coding"][0]["code"] == GENE_STUDIED_LOINC
        ]
        self.assertEqual(len(gene_comps), 1)
        self.assertEqual(gene_comps[0]["valueCodeableConcept"]["text"], "TP53")

    def test_overrides_surface_as_notes(self):
        clf = classify(_events_for_tier("Benign"))  # BA1 stand-alone -> override
        self.assertTrue(clf.overrides)
        obs = variant_observation(clf, variant_key=_KEY, signer="x")
        notes = [n["text"] for n in obs.get("note", [])]
        self.assertEqual(notes, clf.overrides)


class StatusTests(unittest.TestCase):
    def test_signed_is_final(self):
        clf = classify(_events_for_tier("Pathogenic"))
        obs = variant_observation(clf, variant_key=_KEY, signer="Dr. Reviewer")
        self.assertEqual(obs["status"], "final")
        self.assertEqual(obs["performer"][0]["display"], "Dr. Reviewer")

    def test_unsigned_is_preliminary(self):
        clf = classify(_events_for_tier("Pathogenic"))
        obs = variant_observation(clf, variant_key=_KEY)
        self.assertEqual(obs["status"], "preliminary")
        self.assertNotIn("performer", obs)

    def test_explicit_signed_flag_overrides_signer_absence(self):
        clf = classify(_events_for_tier("VUS"))
        obs = variant_observation(clf, variant_key=_KEY, signed=True)
        self.assertEqual(obs["status"], "final")
        obs2 = variant_observation(clf, variant_key=_KEY, signer="x", signed=False)
        self.assertEqual(obs2["status"], "preliminary")

    def test_bundle_diagnostic_report_status_matches_observation(self):
        clf = classify(_events_for_tier("Pathogenic"))
        bundle = genomics_report_bundle(clf, variant_key=_KEY, signer="x")
        types = {e["resource"]["resourceType"]: e["resource"] for e in bundle["entry"]}
        self.assertEqual(types["DiagnosticReport"]["status"], "final")
        self.assertEqual(types["Observation"]["status"], "final")


class DeterminismTests(unittest.TestCase):
    def test_same_input_yields_identical_json(self):
        clf = classify(_events_for_tier("Likely Pathogenic"))
        a = to_json(genomics_report_bundle(
            clf, variant_key=_KEY, gene="BRCA1", hgvs_c="c.100A>G",
            issued="2026-06-16T00:00:00+00:00", signer="Dr. R"))
        b = to_json(genomics_report_bundle(
            clf, variant_key=_KEY, gene="BRCA1", hgvs_c="c.100A>G",
            issued="2026-06-16T00:00:00+00:00", signer="Dr. R"))
        self.assertEqual(a, b)

    def test_no_wall_clock_timestamp_without_issued_argument(self):
        # Omitting issued/effective must leave NO timestamp in the output, proving
        # the serializer never reads the wall clock.
        clf = classify(_events_for_tier("VUS"))
        bundle = genomics_report_bundle(clf, variant_key=_KEY, signer="x")
        self.assertNotIn("timestamp", bundle)
        obs = bundle["entry"][1]["resource"]
        self.assertNotIn("issued", obs)
        self.assertNotIn("effectiveDateTime", obs)

    def test_supplied_timestamps_appear_verbatim(self):
        clf = classify(_events_for_tier("VUS"))
        obs = variant_observation(
            clf, variant_key=_KEY, signer="x",
            issued="2026-06-16T12:00:00+00:00",
            effective="2026-06-15T00:00:00+00:00")
        self.assertEqual(obs["issued"], "2026-06-16T12:00:00+00:00")
        self.assertEqual(obs["effectiveDateTime"], "2026-06-15T00:00:00+00:00")


class OutboundPayloadTests(unittest.TestCase):
    def test_amended_report_transition_and_replay_are_deterministic(self):
        clf = classify(_events_for_tier("Likely Pathogenic"))
        final = build_outbound_payload(
            clf,
            variant_key=_KEY,
            report_id="report-1",
            state="final",
            transcript="NM_000059.4",
            issued="2026-06-16T12:00:00+00:00",
            signer="Dr. Reviewer",
        )
        replayed = replay_outbound_payload(final)
        self.assertEqual(replayed["payload"], final["payload"])
        self.assertEqual(replayed["payload_sha256"], final["payload_sha256"])

        amended = amend_outbound_payload(
            final,
            classify(_events_for_tier("Pathogenic")),
            report_id="report-2",
            amendment_reason="Updated curated evidence",
            issued="2026-06-17T12:00:00+00:00",
            signer="Dr. Reviewer",
        )
        self.assertEqual(amended["state"], "amended")
        self.assertEqual(transition_report_state("final", "amended"), "amended")
        payload = json.loads(amended["payload"])
        resources = {e["resource"]["resourceType"]: e["resource"] for e in payload["entry"]}
        self.assertEqual(resources["DiagnosticReport"]["status"], "amended")
        extensions = resources["DiagnosticReport"]["extension"]
        self.assertEqual(extensions[0]["valueIdentifier"]["value"], "report-1")
        self.assertEqual(extensions[1]["valueString"], "Updated curated evidence")
        transcript_values = [
            component.get("valueString")
            for component in resources["Observation"]["component"]
            if component["code"]["coding"][0].get("code") == "mane-transcript"
        ]
        self.assertEqual(transcript_values, ["NM_000059.4"])
        self.assertEqual(replay_outbound_payload(amended)["payload"], amended["payload"])

    def test_invalid_amendment_transition_is_named(self):
        with self.assertRaisesRegex(ValueError, "illegal report state transition"):
            transition_report_state("draft", "amended")

    def test_lis_ehr_lifecycle_adapter_tracks_amended_report_and_notifications(self):
        clf = classify(_events_for_tier("Pathogenic"))
        outbound = build_outbound_payload(
            clf,
            variant_key=_KEY,
            report_id="report-2",
            state="amended",
            previous_report_id="report-1",
            amendment_reason="new curated evidence",
            signer="Dr. Reviewer",
        )
        report = amended_report_record(outbound, classification_id="c1")
        self.assertEqual(report["state"], "amended")
        self.assertEqual(report["previous_report_id"], "report-1")

        notification = clinician_notification_record(
            classification_id="c1",
            report_id="report-2",
            recipient="clinician@example.test",
        )
        self.assertEqual(notification["notification_state"], "pending")

        lifecycle = lis_ehr_lifecycle_adapter(
            outbound,
            classification_id="c1",
            recipients=["clinician@example.test"],
        )
        self.assertEqual(lifecycle["report"]["report_id"], "report-2")
        self.assertEqual(len(lifecycle["notifications"]), 1)


class BundleStructureTests(unittest.TestCase):
    def test_bundle_is_valid_json_collection_with_expected_entries(self):
        clf = classify(_events_for_tier("Pathogenic"))
        bundle = genomics_report_bundle(clf, variant_key=_KEY, signer="x")
        # Valid JSON round-trips.
        reparsed = json.loads(to_json(bundle))
        self.assertEqual(reparsed["resourceType"], "Bundle")
        self.assertEqual(reparsed["type"], "collection")
        resource_types = [e["resource"]["resourceType"] for e in reparsed["entry"]]
        self.assertIn("MolecularSequence", resource_types)
        self.assertIn("Observation", resource_types)
        self.assertIn("DiagnosticReport", resource_types)
        # Every entry has a fullUrl.
        for entry in reparsed["entry"]:
            self.assertTrue(entry["fullUrl"].startswith("urn:uuid:"))

    def test_bundle_without_diagnostic_report(self):
        clf = classify(_events_for_tier("VUS"))
        bundle = genomics_report_bundle(
            clf, variant_key=_KEY, signer="x", include_diagnostic_report=False)
        resource_types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        self.assertNotIn("DiagnosticReport", resource_types)
        self.assertEqual(resource_types, ["MolecularSequence", "Observation"])

    def test_diagnostic_report_references_observation_full_url(self):
        clf = classify(_events_for_tier("Pathogenic"))
        bundle = genomics_report_bundle(clf, variant_key=_KEY, signer="x")
        obs_entry = next(e for e in bundle["entry"]
                         if e["resource"]["resourceType"] == "Observation")
        dr_entry = next(e for e in bundle["entry"]
                        if e["resource"]["resourceType"] == "DiagnosticReport")
        self.assertEqual(
            dr_entry["resource"]["result"][0]["reference"], obs_entry["fullUrl"]
        )

    def test_standalone_diagnostic_report_builder(self):
        dr = diagnostic_report("urn:uuid:obs-1", variant_key=_KEY,
                               status="final", signer="x")
        self.assertEqual(dr["resourceType"], "DiagnosticReport")
        self.assertEqual(dr["status"], "final")
        self.assertEqual(dr["result"][0]["reference"], "urn:uuid:obs-1")

    def test_accepts_receipt_dict_as_well_as_dataclass(self):
        clf = classify(_events_for_tier("Pathogenic"))
        from_obj = to_json(variant_observation(clf, variant_key=_KEY, signer="x"))
        from_dict = to_json(variant_observation(clf.to_dict(), variant_key=_KEY, signer="x"))
        self.assertEqual(from_obj, from_dict)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
