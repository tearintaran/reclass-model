"""Standard-library unit tests for the engine, normalization, monitoring, and gate.

Run from the project root (the `ReClass Model/` folder):

    python3 -m unittest discover -s tests -v
"""

import os
import json
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import config as C
from engine.config_registry import (
    BASE_CONFIG,
    BASE_CONFIG_VERSION,
    get_config,
)
from engine.scoring import (
    EvidenceEvent,
    classify,
    classify_signals,
    derive_criteria_from_signals,
    reconstruction_hash,
)
from engine.normalize import Variant, normalize, split_multiallelic, trim, left_align
from monitoring.diff import Alert, diff, is_serious_crossing


# --------------------------------------------------------------------------- #
# Config: tier cutoffs and strength points                                    #
# --------------------------------------------------------------------------- #
class TestConfig(unittest.TestCase):
    def test_strength_points_values(self):
        self.assertEqual(C.STRENGTH_POINTS["supporting"], 1)
        self.assertEqual(C.STRENGTH_POINTS["moderate"], 2)
        self.assertEqual(C.STRENGTH_POINTS["strong"], 4)
        self.assertEqual(C.STRENGTH_POINTS["very_strong"], 8)

    def test_pathogenic_cutoff(self):
        self.assertEqual(C.points_to_tier(10), "Pathogenic")
        self.assertEqual(C.points_to_tier(13), "Pathogenic")

    def test_likely_pathogenic_band(self):
        self.assertEqual(C.points_to_tier(6), "Likely Pathogenic")
        self.assertEqual(C.points_to_tier(9), "Likely Pathogenic")

    def test_vus_band(self):
        self.assertEqual(C.points_to_tier(0), "VUS")
        self.assertEqual(C.points_to_tier(5), "VUS")

    def test_likely_benign_band(self):
        self.assertEqual(C.points_to_tier(-1), "Likely Benign")
        self.assertEqual(C.points_to_tier(-6), "Likely Benign")

    def test_benign_cutoff(self):
        self.assertEqual(C.points_to_tier(-7), "Benign")
        self.assertEqual(C.points_to_tier(-8), "Benign")


# --------------------------------------------------------------------------- #
# EvidenceEvent signed points                                                 #
# --------------------------------------------------------------------------- #
class TestEvidenceEvent(unittest.TestCase):
    def test_pathogenic_strength_points(self):
        e = EvidenceEvent("curated", "PVS1", "pathogenic", applied_strength="very_strong")
        self.assertEqual(e.signed_points(), 8.0)

    def test_benign_strength_points(self):
        e = EvidenceEvent("curated", "BS1", "benign", applied_strength="strong")
        self.assertEqual(e.signed_points(), -4.0)

    def test_neutral_is_zero(self):
        e = EvidenceEvent("curated", "PP3", "neutral", applied_strength="strong")
        self.assertEqual(e.signed_points(), 0.0)

    def test_explicit_points_override_strength(self):
        e = EvidenceEvent("curated", "PS1", "pathogenic", points=3.5)
        self.assertEqual(e.signed_points(), 3.5)

    def test_explicit_benign_points_signed(self):
        e = EvidenceEvent("curated", "BS1", "benign", points=4)
        self.assertEqual(e.signed_points(), -4.0)

    def test_missing_strength_and_points_raises(self):
        e = EvidenceEvent("curated", "PS1", "pathogenic")
        with self.assertRaises(ValueError):
            e.signed_points()


# --------------------------------------------------------------------------- #
# classify()                                                                  #
# --------------------------------------------------------------------------- #
class TestClassify(unittest.TestCase):
    def test_pathogenic_sum(self):
        ev = [
            EvidenceEvent("curated", "PVS1", "pathogenic", applied_strength="very_strong"),
            EvidenceEvent("curated", "PS1", "pathogenic", applied_strength="strong"),
            EvidenceEvent("curated", "PM2", "pathogenic", applied_strength="supporting"),
        ]
        r = classify(ev)
        self.assertEqual(r.total_points, 13.0)
        self.assertEqual(r.tier, "Pathogenic")

    def test_vus_empty_evidence(self):
        r = classify([])
        self.assertEqual(r.total_points, 0.0)
        self.assertEqual(r.tier, "VUS")

    def test_ba1_standalone_override(self):
        ev = [
            EvidenceEvent("gnomad", "BA1", "benign", applied_strength="stand_alone"),
            EvidenceEvent("curated", "PVS1", "pathogenic", applied_strength="very_strong"),
        ]
        r = classify(ev)
        self.assertEqual(r.tier, "Benign")
        self.assertTrue(any("BA1" in o for o in r.overrides))

    def test_contributions_recorded(self):
        ev = [EvidenceEvent("curated", "PS1", "pathogenic", applied_strength="strong")]
        r = classify(ev)
        self.assertEqual(len(r.contributions), 1)
        self.assertEqual(r.contributions[0].acmg_criterion, "PS1")
        self.assertEqual(r.contributions[0].points, 4.0)

    def test_engine_version_recorded(self):
        r = classify([], engine_version="9.9.9")
        self.assertEqual(r.engine_version, "9.9.9")


# --------------------------------------------------------------------------- #
# ACMG single-application (each criterion scored at most once)                 #
# --------------------------------------------------------------------------- #
class TestSingleApplication(unittest.TestCase):
    def test_computational_duplicate_keeps_strongest(self):
        # Same criterion (PP3) from two computational mappers must not stack;
        # the strongest single contribution is kept.
        ev = [
            EvidenceEvent("revel", "PP3", "pathogenic", applied_strength="strong"),
            EvidenceEvent("conservation", "PP3", "pathogenic", applied_strength="supporting"),
        ]
        r = classify(ev)
        self.assertEqual([c.acmg_criterion for c in r.contributions], ["PP3"])
        self.assertEqual(r.total_points, 4.0)  # strong only, not strong+supporting
        self.assertTrue(any("single-application" in o for o in r.overrides))

    def test_expert_curation_preferred_over_computational(self):
        # An expert (ClinGen) strength wins over the engine's own computational
        # derivation of the same criterion, even when the computational one is stronger.
        ev = [
            EvidenceEvent("revel", "BP4", "benign", applied_strength="moderate"),
            EvidenceEvent("clingen", "BP4", "benign", applied_strength="supporting"),
        ]
        r = classify(ev)
        self.assertEqual(len(r.contributions), 1)
        self.assertEqual(r.contributions[0].source, "clingen")
        self.assertEqual(r.total_points, -1.0)  # clingen supporting, not revel moderate

    def test_distinct_criteria_are_not_collapsed(self):
        ev = [
            EvidenceEvent("revel", "PP3", "pathogenic", applied_strength="strong"),
            EvidenceEvent("gnomad", "PM2", "pathogenic", applied_strength="supporting"),
        ]
        r = classify(ev)
        self.assertEqual(
            sorted(c.acmg_criterion for c in r.contributions), ["PM2", "PP3"]
        )
        self.assertEqual(r.total_points, 5.0)
        self.assertEqual(r.overrides, [])

    def test_no_tier_flip_from_double_count(self):
        # Regression: gnomAD PM2 + REVEL PP3 + conservation PP3 must stay VUS (5 pts),
        # not be pushed to Likely Pathogenic (6 pts) by a double-counted PP3.
        r = classify_signals({"gnomad_af": 1e-6, "revel": 0.95, "conservation": 3.0})
        self.assertEqual(r.tier, "VUS")
        self.assertEqual(r.total_points, 5.0)

    def test_reconstruction_hash_over_original_evidence(self):
        # The hash is taken over the original (pre-collapse) inputs, so a stored
        # classification still re-derives from exactly what was supplied.
        ev = [
            EvidenceEvent("revel", "PP3", "pathogenic", applied_strength="strong"),
            EvidenceEvent("conservation", "PP3", "pathogenic", applied_strength="supporting"),
        ]
        r = classify(ev, engine_version="1.0.0")
        self.assertEqual(r.reconstruction_hash, reconstruction_hash(ev, "1.0.0"))


# --------------------------------------------------------------------------- #
# Determinism / reconstruction hash                                           #
# --------------------------------------------------------------------------- #
class TestReconstructionHash(unittest.TestCase):
    def setUp(self):
        self.ev = [
            EvidenceEvent("curated", "PVS1", "pathogenic", applied_strength="very_strong"),
            EvidenceEvent("curated", "PM2", "pathogenic", applied_strength="supporting"),
        ]

    def test_stable_across_calls(self):
        self.assertEqual(
            reconstruction_hash(self.ev, "1.0.0"),
            reconstruction_hash(self.ev, "1.0.0"),
        )

    def test_order_independent(self):
        self.assertEqual(
            reconstruction_hash(self.ev, "1.0.0"),
            reconstruction_hash(list(reversed(self.ev)), "1.0.0"),
        )

    def test_changes_with_engine_version(self):
        self.assertNotEqual(
            reconstruction_hash(self.ev, "1.0.0"),
            reconstruction_hash(self.ev, "2.0.0"),
        )

    def test_classify_hash_matches_helper(self):
        r = classify(self.ev, engine_version="1.0.0")
        self.assertEqual(r.reconstruction_hash, reconstruction_hash(self.ev, "1.0.0"))


# --------------------------------------------------------------------------- #
# Signal derivation (REVEL / gnomAD)                                          #
# --------------------------------------------------------------------------- #
class TestSignalDerivation(unittest.TestCase):
    def test_revel_high_is_pp3_strong(self):
        ev = derive_criteria_from_signals({"revel": 0.95})
        self.assertEqual(ev[0].acmg_criterion, "PP3")
        self.assertEqual(ev[0].applied_strength, "strong")

    def test_revel_mid_is_pp3_supporting(self):
        ev = derive_criteria_from_signals({"revel": 0.70})
        self.assertEqual(ev[0].acmg_criterion, "PP3")
        self.assertEqual(ev[0].applied_strength, "supporting")

    def test_revel_low_is_bp4(self):
        ev = derive_criteria_from_signals({"revel": 0.05})
        self.assertEqual(ev[0].acmg_criterion, "BP4")
        self.assertEqual(ev[0].evidence_direction, "benign")

    def test_revel_indeterminate_yields_nothing(self):
        ev = derive_criteria_from_signals({"revel": 0.45})
        self.assertEqual(ev, [])

    def test_gnomad_common_is_ba1(self):
        ev = derive_criteria_from_signals({"gnomad_af": 0.08})
        self.assertEqual(ev[0].acmg_criterion, "BA1")
        self.assertEqual(ev[0].applied_strength, "stand_alone")

    def test_gnomad_uncommon_is_bs1(self):
        ev = derive_criteria_from_signals({"gnomad_af": 0.02})
        self.assertEqual(ev[0].acmg_criterion, "BS1")

    def test_gnomad_rare_is_pm2(self):
        ev = derive_criteria_from_signals({"gnomad_af": 1e-6})
        self.assertEqual(ev[0].acmg_criterion, "PM2")
        self.assertEqual(ev[0].evidence_direction, "pathogenic")

    def test_unknown_strength_raises(self):
        with self.assertRaises(ValueError):
            derive_criteria_from_signals(
                {"criteria": [{"criterion": "PS1", "direction": "pathogenic", "strength": "ultra"}]}
            )

    def test_classify_signals_integration(self):
        r = classify_signals({
            "gnomad_af": 1e-6,
            "criteria": [
                {"criterion": "PVS1", "direction": "pathogenic", "strength": "very_strong"},
                {"criterion": "PS1", "direction": "pathogenic", "strength": "strong"},
            ],
        })
        self.assertEqual(r.total_points, 13.0)
        self.assertEqual(r.tier, "Pathogenic")


# --------------------------------------------------------------------------- #
# Normalization                                                               #
# --------------------------------------------------------------------------- #
class TestNormalize(unittest.TestCase):
    def test_split_multiallelic(self):
        out = split_multiallelic(Variant("1", 100, "A", "G,T"))
        self.assertEqual([v.alt for v in out], ["G", "T"])

    def test_snp_unchanged_by_trim(self):
        v = trim(Variant("1", 100, "A", "G"))
        self.assertEqual((v.pos, v.ref, v.alt), (100, "A", "G"))

    def test_trim_shared_suffix(self):
        # AGG -> AG deletion written as (CAGG -> CAG) trims to (G -> "")? keep >=1 base
        v = trim(Variant("1", 100, "CT", "GT"))
        self.assertEqual((v.pos, v.ref, v.alt), (100, "C", "G"))

    def test_trim_shared_prefix_advances_pos(self):
        v = trim(Variant("1", 100, "GCA", "GCT"))
        self.assertEqual((v.pos, v.ref, v.alt), (102, "A", "T"))

    def test_trim_insertion(self):
        v = trim(Variant("1", 100, "A", "AT"))
        self.assertEqual((v.pos, v.ref, v.alt), (100, "A", "AT"))

    def test_normalize_splits_and_trims(self):
        out = normalize(Variant("1", 100, "GCA", "GCT,GCG"))
        self.assertEqual([(v.pos, v.ref, v.alt) for v in out], [(102, "A", "T"), (102, "A", "G")])

    def test_invalid_allele_raises(self):
        with self.assertRaises(ValueError):
            split_multiallelic(Variant("1", 100, "A", "X"))

    def test_left_align_is_loud_hook(self):
        with self.assertRaises(NotImplementedError):
            left_align(Variant("1", 100, "A", "AT"))


# --------------------------------------------------------------------------- #
# Monitoring / tier-crossing alerts                                           #
# --------------------------------------------------------------------------- #
class TestMonitoring(unittest.TestCase):
    def test_no_change_no_alert(self):
        self.assertIsNone(diff("VUS", "VUS"))

    def test_upgrade(self):
        a = diff("VUS", "Likely Pathogenic")
        self.assertIsInstance(a, Alert)
        self.assertEqual(a.direction, "upgrade")
        self.assertEqual(a.steps, 1)

    def test_downgrade(self):
        a = diff("Pathogenic", "Likely Pathogenic")
        self.assertEqual(a.direction, "downgrade")
        self.assertFalse(a.serious)

    def test_serious_crossing_path_to_benign(self):
        a = diff("Pathogenic", "Benign")
        self.assertTrue(a.serious)

    def test_serious_crossing_benign_to_path(self):
        self.assertTrue(is_serious_crossing("Likely Benign", "Likely Pathogenic"))

    def test_vus_crossing_not_serious(self):
        a = diff("VUS", "Likely Benign")
        self.assertFalse(a.serious)

    def test_unknown_tier_raises(self):
        with self.assertRaises(ValueError):
            diff("VUS", "Nonsense")


# --------------------------------------------------------------------------- #
# Validation gate logic (no I/O)                                              #
# --------------------------------------------------------------------------- #
class TestGate(unittest.TestCase):
    def test_gate_passes_on_benchmark(self):
        from validation.build_fixtures import build
        from validation.harness import evaluate, compute_metrics, gate_passes

        results = evaluate(build())
        m = compute_metrics(results)
        self.assertTrue(gate_passes(m))
        self.assertGreaterEqual(m["definitive_concordance"], 0.85)
        self.assertEqual(m["serious_count"], 0)

    def test_african_subgroup_is_weaker(self):
        from validation.build_fixtures import build
        from validation.harness import evaluate, compute_metrics

        m = compute_metrics(evaluate(build()))
        self.assertLess(m["by_ancestry"]["African"]["concordance"], 1.0)


# --------------------------------------------------------------------------- #
# Versioned config registry (Part B)                                          #
# --------------------------------------------------------------------------- #
class TestConfigRegistry(unittest.TestCase):
    def test_base_config_matches_module_constants(self):
        # engine.config re-exports the base versioned config byte-for-byte.
        self.assertEqual(BASE_CONFIG.version, C.ENGINE_VERSION)
        self.assertEqual(dict(BASE_CONFIG.strength_points), C.STRENGTH_POINTS)
        self.assertEqual(BASE_CONFIG.ba1_af, C.BA1_AF)
        self.assertEqual(BASE_CONFIG.bs1_af, C.BS1_AF)
        self.assertEqual(BASE_CONFIG.pm2_af, C.PM2_AF)
        self.assertTrue(BASE_CONFIG.is_base)
        self.assertEqual(BASE_CONFIG.engine_version, BASE_CONFIG_VERSION)

    def test_points_to_tier_matches(self):
        for p in (13, 10, 9, 6, 5, 0, -1, -6, -7, -8):
            self.assertEqual(BASE_CONFIG.points_to_tier(p), C.points_to_tier(p))

    def test_config_hash_is_deterministic(self):
        self.assertEqual(get_config().config_hash, BASE_CONFIG.config_hash)
        self.assertEqual(len(BASE_CONFIG.config_hash), 64)

    def test_fingerprint_exposes_overrides(self):
        fp = C.config_fingerprint()
        self.assertEqual(fp["version"], C.ENGINE_VERSION)
        self.assertIn("pku_vcep_ba1", fp["override_ids"])
        self.assertIn("founder_variant_frequency_exception_template", fp["override_ids"])
        self.assertEqual(fp["config_hash"], C.CONFIG_HASH)

    def test_config_carries_review_status_annotation(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "engine", "configs", "base_v1.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(
            data["clinical_review"]["review_status"],
            "governance_reviewed_pending_credentialed_signoff",
        )
        self.assertEqual(
            data["clinical_review"]["clinical_release"],
            "blocked_until_credentialed_human_signoff",
        )

    def test_resolve_applies_vcep_override(self):
        r = BASE_CONFIG.resolve(vcep="Phenylketonuria VCEP")
        self.assertIn("pku_vcep_ba1", r.applied_override_ids)
        self.assertEqual(r.config.ba1_af, 0.015)
        self.assertEqual(r.config.bs1_af, 0.002)
        self.assertFalse(r.config.is_base)
        # A scoring-relevant deviation gets a distinct, fingerprinted version.
        self.assertNotEqual(r.engine_version, BASE_CONFIG_VERSION)

    def test_resolve_no_match_returns_base_unchanged(self):
        r = BASE_CONFIG.resolve(vcep="No Such VCEP")
        self.assertEqual(r.applied_override_ids, [])
        self.assertEqual(r.config.engine_version, BASE_CONFIG_VERSION)

    def test_gene_specific_override_does_not_fire_on_bare_vcep(self):
        # The GJB2 hearing-loss rule names a gene+disease; a bare VCEP query must
        # not trigger it.
        self.assertEqual(BASE_CONFIG.matching_overrides(vcep="Phenylketonuria VCEP"),
                         [ov for ov in BASE_CONFIG.overrides
                          if ov.get("id") == "pku_vcep_ba1"])
        self.assertEqual(
            [ov["id"] for ov in BASE_CONFIG.matching_overrides(
                gene="GJB2", disease="Nonsyndromic Hearing Loss")],
            ["hearing_loss_gjb2_35delg"],
        )

    def test_hearing_loss_override_matches_current_cspec_values(self):
        r = BASE_CONFIG.resolve(gene="GJB2", disease="Nonsyndromic Hearing Loss")
        self.assertIn("hearing_loss_gjb2_35delg", r.applied_override_ids)
        self.assertEqual(r.config.ba1_af, 0.005)
        self.assertEqual(r.config.bs1_af, 0.003)

    def test_founder_variant_override_by_key(self):
        ovs = BASE_CONFIG.matching_overrides(
            variant_key="REPLACE_WITH_CURATED_FOUNDER_VARIANT_KEY"
        )
        self.assertEqual([o["id"] for o in ovs],
                         ["founder_variant_frequency_exception_template"])
        self.assertEqual(ovs[0]["set"], {}, "founder exception is a non-scoring template")

    def test_perturb_changes_version_and_hash(self):
        p = BASE_CONFIG.perturb(pm2_af=BASE_CONFIG.pm2_af / 10.0, version_suffix="-x")
        self.assertFalse(p.is_base)
        self.assertNotEqual(p.config_hash, BASE_CONFIG.config_hash)
        self.assertNotEqual(p.engine_version, BASE_CONFIG.engine_version)


class TestConfigAwareScoring(unittest.TestCase):
    def test_default_path_is_unchanged(self):
        a = classify_signals({"gnomad_af": 0.02})
        b = classify_signals({"gnomad_af": 0.02}, config=None)
        self.assertEqual(a.tier, b.tier)
        self.assertEqual(a.tier, "Likely Benign")  # base BS1 strong -> -4 pts

    def test_vcep_override_changes_tier(self):
        pku = get_config().resolve(vcep="Phenylketonuria VCEP").config
        # af 0.02 >= the lowered BA1 (0.015) -> stand-alone Benign under the override.
        self.assertEqual(classify_signals({"gnomad_af": 0.02}, config=pku).tier, "Benign")

    def test_config_relevant_change_alters_reconstruction_hash(self):
        ev = [EvidenceEvent("curated", "PVS1", "pathogenic", applied_strength="very_strong")]
        perturbed = get_config().perturb(
            tier_cutoffs=((12, "Pathogenic"), (7, "Likely Pathogenic"),
                          (0, "VUS"), (-6, "Likely Benign")),
            version_suffix="-sens")
        base_hash = classify(ev).reconstruction_hash
        self.assertNotEqual(base_hash, classify(ev, config=perturbed).reconstruction_hash)
        # The recorded engine_version reflects the config fingerprint.
        self.assertEqual(classify(ev, config=perturbed).engine_version,
                         perturbed.engine_version)

    def test_explicit_engine_version_still_wins(self):
        r = classify([], engine_version="9.9.9", config=get_config())
        self.assertEqual(r.engine_version, "9.9.9")

    def test_revel_bins_respect_config(self):
        # Default REVEL 0.70 -> PP3 supporting; a config that raises the supporting
        # threshold above 0.70 drops it into the indeterminate band.
        base = get_config()
        strict = base.perturb(revel_pp3=((0.95, "strong"), (0.85, "moderate"),
                                         (0.80, "supporting")), version_suffix="-revel")
        self.assertEqual(len(derive_criteria_from_signals({"revel": 0.70})), 1)
        self.assertEqual(derive_criteria_from_signals({"revel": 0.70}, config=strict), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
