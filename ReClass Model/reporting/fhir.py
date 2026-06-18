"""FHIR Genomics / HL7 result export for a signed classification (gap.md C5).

Renders an auditable :class:`engine.scoring.Classification` (plus its variant
identity and sign-off metadata) into a FHIR R4 Genomics-Reporting bundle so a
released result can be exchanged with an EHR. This module is a *serializer only*:
it builds plain Python dicts (no third-party FHIR/pydantic dependency) that
``json.dumps`` to spec-shaped FHIR resources. There is no live endpoint here.

The contract that matters is **traceability + determinism**, mirroring the
engine's own ``reconstruction_hash`` discipline:

  * Every resource id is derived deterministically from the variant key and the
    classification's ``reconstruction_hash`` -- the same signed input always
    yields the same bundle, byte-for-byte (no wall-clock; an ``issued`` /
    ``effective`` timestamp is an explicit argument, never ``datetime.now()``).
  * The genomic coordinates carried in the ``Observation`` / ``MolecularSequence``
    round-trip through :func:`engine.normalize.parse_key`, so a consumer recovers
    exactly the chrom/pos/ref/alt/build that produced the result.
  * The ``engine_version`` and ``reconstruction_hash`` travel as identifiers /
    derivation so the FHIR record points back at the deterministic classification
    it was rendered from.

Profile conventions follow the HL7 Clinical Genomics "variant" / "genomic
implication" guidance: the variant-interpretation ``Observation`` uses LOINC
``53037-8`` ("Genetic variation's clinical significance") with the SVI answer
list, plus per-criterion components. The LOINC code/answer mappings below are
**reviewable clinical mappings** -- see the provenance comments on each constant.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional

from engine.normalize import DEFAULT_BUILD, parse_key

# --------------------------------------------------------------------------- #
# Reviewable clinical code mappings (LOINC)                                    #
# --------------------------------------------------------------------------- #
# These map the engine's ACMG/AMP tiers onto the LOINC "Genetic variation's
# clinical significance" answer list. They are a CLINICAL mapping a reviewer
# should confirm, not a code-level constant to silently tune -- the engine tier
# vocabulary ("VUS", "Likely Pathogenic", ...) is its own enum and is bridged to
# the standardized LOINC answer codes here, in one documented place.
#
# Source: LOINC 53037-8 "Genetic variation's clinical significance" and its
# normative answer list (ACMG/AMP 5-tier), as used by the HL7 Genomics
# Reporting IG:
#   Pathogenic                 LA6668-3
#   Likely pathogenic          LA26332-9
#   Uncertain significance     LA26333-7
#   Likely benign              LA26334-5
#   Benign                     LA6675-8
LOINC_SYSTEM = "http://loinc.org"

#: LOINC code for the variant clinical-significance Observation itself.
CLIN_SIG_LOINC = "53037-8"
CLIN_SIG_DISPLAY = "Genetic variation's clinical significance"

#: Engine tier -> (LOINC answer code, human display). Reviewable clinical mapping.
TIER_TO_LOINC: Dict[str, Dict[str, str]] = {
    "Pathogenic": {"code": "LA6668-3", "display": "Pathogenic"},
    "Likely Pathogenic": {"code": "LA26332-9", "display": "Likely pathogenic"},
    # The engine names the middle tier "VUS"; LOINC's answer is "Uncertain significance".
    "VUS": {"code": "LA26333-7", "display": "Uncertain significance"},
    "Likely Benign": {"code": "LA26334-5", "display": "Likely benign"},
    "Benign": {"code": "LA6675-8", "display": "Benign"},
}

#: LOINC code for the "Gene studied" Observation component (HGNC name in the value).
GENE_STUDIED_LOINC = "48018-6"
GENE_STUDIED_DISPLAY = "Gene studied [ID]"

#: LOINC code for the "Genomic ref allele [ID]" / "Genomic alt allele [ID]" and the
#: cytogenetic/coordinate components used to carry the variant's coordinates.
GENOMIC_REF_ALLELE_LOINC = "69547-8"   # Genomic ref allele [ID]
GENOMIC_REF_ALLELE_DISPLAY = "Genomic ref allele [ID]"
GENOMIC_ALT_ALLELE_LOINC = "69551-0"   # Genomic alt allele [ID]
GENOMIC_ALT_ALLELE_DISPLAY = "Genomic alt allele [ID]"
GENOMIC_COORD_LOINC = "81254-5"        # Genomic allele start-end (1-based)
GENOMIC_COORD_DISPLAY = "Genomic allele start-end"
GENOMIC_HGVS_LOINC = "48004-6"         # DNA change (c.HGVS)
GENOMIC_HGVS_DISPLAY = "DNA change (c.HGVS)"
AMINO_ACID_HGVS_LOINC = "48005-3"      # Amino acid change (p.HGVS)
AMINO_ACID_HGVS_DISPLAY = "Amino acid change (p.HGVS)"
CHROMOSOME_LOINC = "48000-4"           # Chromosome
CHROMOSOME_DISPLAY = "Chromosome"

#: LOINC code for "Genetic variant assessment" (an applied ACMG criterion component).
CRITERION_LOINC = "53037-8"            # reuse clinical-significance scale per-criterion
ACMG_CRITERION_DISPLAY = "ACMG criterion applied"

#: Identifier systems for the engine-traceability values. These are local URNs (no
#: registered OID is implied); they exist so a consumer can find the deterministic
#: source classification a FHIR record was rendered from.
ENGINE_VERSION_SYSTEM = "urn:reclass:engine-version"
RECONSTRUCTION_HASH_SYSTEM = "urn:reclass:reconstruction-hash"
VARIANT_KEY_SYSTEM = "urn:reclass:variant-key"

#: FHIR observation status mapped from the release/sign-off state. A signed result
#: is "final"; an unsigned/draft one is "preliminary" (it is not released for
#: clinical use until a credentialed reviewer signs it -- see reporting.common).
STATUS_SIGNED = "final"
STATUS_DRAFT = "preliminary"

# Outbound integration state. These are deliberately smaller than FHIR's status
# vocabulary: they describe the LIS/EHR exchange lifecycle around a rendered
# DiagnosticReport payload.
REPORT_STATE_DRAFT = "draft"
REPORT_STATE_FINAL = "final"
REPORT_STATE_AMENDED = "amended"
REPORT_STATES = (REPORT_STATE_DRAFT, REPORT_STATE_FINAL, REPORT_STATE_AMENDED)
REPORT_STATE_TO_FHIR_STATUS = {
    REPORT_STATE_DRAFT: STATUS_DRAFT,
    REPORT_STATE_FINAL: STATUS_SIGNED,
    REPORT_STATE_AMENDED: "amended",
}
REPORT_STATE_TRANSITIONS = {
    REPORT_STATE_DRAFT: {REPORT_STATE_FINAL},
    REPORT_STATE_FINAL: {REPORT_STATE_AMENDED},
    REPORT_STATE_AMENDED: {REPORT_STATE_AMENDED},
}


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #
def _classification_dict(classification: Any) -> Dict[str, Any]:
    """Accept a ``Classification`` dataclass OR its dict form -> plain dict."""
    if hasattr(classification, "to_dict"):
        return classification.to_dict()
    if isinstance(classification, Mapping):
        return dict(classification)
    raise TypeError(
        "classification must be an engine.scoring.Classification or a receipt dict"
    )


def _coords(variant_key: str) -> Dict[str, Any]:
    """Parse a provider/canonical variant key into FHIR-ready coordinate fields.

    Returns ``{build, chrom, pos, ref, alt}`` exactly as
    :func:`engine.normalize.parse_key` reports them (``build`` defaulted to
    :data:`engine.normalize.DEFAULT_BUILD` when the key is a bare provider key),
    so the values round-trip back through ``parse_key`` to the same identity.
    """
    p = parse_key(variant_key)
    return {
        "build": p["build"] or DEFAULT_BUILD,
        "chrom": p["chrom"],
        "pos": int(p["pos"]),
        "ref": p["ref"],
        "alt": p["alt"],
    }


def _short_hash(reconstruction_hash: Optional[str]) -> str:
    """A stable, id-safe slice of the reconstruction hash (or a fixed sentinel)."""
    return (reconstruction_hash or "nohash")[:16]


def _key_slug(variant_key: str) -> str:
    """Turn a variant key into an id-safe slug (FHIR ids allow ``A-Za-z0-9-.``)."""
    return "".join(c if (c.isalnum() or c in "-.") else "-" for c in str(variant_key))


def _release_status(
    *, signer: Optional[str], signed: Optional[bool]
) -> str:
    """Resolve the FHIR observation status from the sign-off state.

    A result is "final" only when it carries a signer (or ``signed=True`` is
    passed explicitly); otherwise it is a "preliminary" draft. This mirrors the
    "not released until a credentialed human signs it off" rule in
    :mod:`reporting.common`.
    """
    if signed is not None:
        return STATUS_SIGNED if signed else STATUS_DRAFT
    return STATUS_SIGNED if signer not in (None, "") else STATUS_DRAFT


def _laboratory_category() -> Dict[str, Any]:
    return {
        "coding": [
            {
                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": "laboratory",
                "display": "Laboratory",
            }
        ]
    }


def _criterion_component(contribution: Mapping[str, Any]) -> Dict[str, Any]:
    """One Observation component recording an applied ACMG criterion + its points.

    The criterion code (e.g. ``PP3``), its evidence direction, applied strength,
    signed points, and source/version travel as a coded value plus a text note so
    a reviewer can audit each contribution without leaving the FHIR record.
    """
    criterion = contribution.get("acmg_criterion")
    direction = contribution.get("evidence_direction")
    strength = contribution.get("applied_strength")
    points = contribution.get("points")
    source = contribution.get("source")
    version = contribution.get("source_version")
    note = (
        f"{criterion} ({direction}"
        + (f", {strength}" if strength else "")
        + f"): {points} pts from {source}"
        + (f" [{version}]" if version else "")
    )
    return {
        "code": {
            "coding": [
                {
                    "system": LOINC_SYSTEM,
                    "code": CRITERION_LOINC,
                    "display": ACMG_CRITERION_DISPLAY,
                }
            ],
            "text": "ACMG criterion applied",
        },
        "valueCodeableConcept": {
            "coding": [
                {
                    # Local code system for ACMG/AMP criteria (PVS1, PS1, ..., BP7).
                    "system": "urn:reclass:acmg-criterion",
                    "code": str(criterion),
                    "display": note,
                }
            ],
            "text": note,
        },
    }


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #
def variant_observation(
    classification: Any,
    *,
    variant_key: str,
    gene: Optional[str] = None,
    transcript: Optional[str] = None,
    hgvs_c: Optional[str] = None,
    hgvs_p: Optional[str] = None,
    issued: Optional[str] = None,
    effective: Optional[str] = None,
    signer: Optional[str] = None,
    signed: Optional[bool] = None,
    report_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a FHIR R4 ``Observation`` for the variant's clinical significance.

    The result uses LOINC ``53037-8`` with the SVI answer list
    (:data:`TIER_TO_LOINC`) as its ``valueCodeableConcept``, carries one component
    per applied ACMG criterion, and embeds the engine version + reconstruction
    hash as identifiers so the record is traceable back to the deterministic
    classification.

    Determinism: no wall-clock is read. ``issued`` and ``effective`` are optional
    ISO-8601 strings the caller supplies (e.g. the sign-off time); when omitted
    they are simply absent from the resource, so the same input always serializes
    to the same bytes. ``signer`` (or an explicit ``signed`` flag) drives the
    ``status`` ("final" when signed, else "preliminary").
    """
    clf = _classification_dict(classification)
    coords = _coords(variant_key)
    tier = clf.get("tier")
    status = _release_status(signer=signer, signed=signed)
    short = _short_hash(clf.get("reconstruction_hash"))
    obs_id = f"reclass-obs-{_key_slug(variant_key)}-{short}"

    tier_answer = TIER_TO_LOINC.get(
        tier or "",
        # An unmapped/absent tier is recorded as a data-absent value rather than
        # silently coerced to "Uncertain significance".
        {"code": "LA4489-6", "display": "Unknown"},
    )

    components: List[Dict[str, Any]] = []

    if gene is not None:
        components.append({
            "code": {
                "coding": [{
                    "system": LOINC_SYSTEM,
                    "code": GENE_STUDIED_LOINC,
                    "display": GENE_STUDIED_DISPLAY,
                }],
                "text": "Gene studied",
            },
            "valueCodeableConcept": {
                # HGNC gene symbol carried as text + a coded value under the HGNC system.
                "coding": [{"system": "http://www.genenames.org", "code": gene}],
                "text": gene,
            },
        })

    # Genomic coordinate components -- carried so they round-trip via parse_key.
    components.append({
        "code": {
            "coding": [{
                "system": LOINC_SYSTEM,
                "code": CHROMOSOME_LOINC,
                "display": CHROMOSOME_DISPLAY,
            }],
            "text": "Chromosome",
        },
        "valueString": coords["chrom"],
    })
    components.append({
        "code": {
            "coding": [{
                "system": LOINC_SYSTEM,
                "code": GENOMIC_COORD_LOINC,
                "display": GENOMIC_COORD_DISPLAY,
            }],
            "text": "Genomic allele start (1-based)",
        },
        "valueInteger": coords["pos"],
    })
    components.append({
        "code": {
            "coding": [{
                "system": LOINC_SYSTEM,
                "code": GENOMIC_REF_ALLELE_LOINC,
                "display": GENOMIC_REF_ALLELE_DISPLAY,
            }],
            "text": "Genomic ref allele",
        },
        "valueString": coords["ref"],
    })
    components.append({
        "code": {
            "coding": [{
                "system": LOINC_SYSTEM,
                "code": GENOMIC_ALT_ALLELE_LOINC,
                "display": GENOMIC_ALT_ALLELE_DISPLAY,
            }],
            "text": "Genomic alt allele",
        },
        "valueString": coords["alt"],
    })

    if hgvs_c is not None:
        components.append({
            "code": {
                "coding": [{
                    "system": LOINC_SYSTEM,
                    "code": GENOMIC_HGVS_LOINC,
                    "display": GENOMIC_HGVS_DISPLAY,
                }],
                "text": "DNA change (c.HGVS)",
            },
            "valueString": hgvs_c,
        })
    if hgvs_p is not None:
        components.append({
            "code": {
                "coding": [{
                    "system": LOINC_SYSTEM,
                    "code": AMINO_ACID_HGVS_LOINC,
                    "display": AMINO_ACID_HGVS_DISPLAY,
                }],
                "text": "Amino acid change (p.HGVS)",
            },
            "valueString": hgvs_p,
        })
    if transcript is not None:
        components.append({
            "code": {
                "coding": [{
                    "system": "urn:reclass:fhir-component",
                    "code": "mane-transcript",
                    "display": "MANE Select transcript",
                }],
                "text": "MANE Select transcript",
            },
            "valueString": transcript,
        })

    # One component per ACMG criterion contribution (the per-point audit trail).
    for contribution in clf.get("contributions") or []:
        components.append(_criterion_component(contribution))

    identifiers = [
        {"system": VARIANT_KEY_SYSTEM, "value": str(variant_key)},
        {"system": ENGINE_VERSION_SYSTEM, "value": str(clf.get("engine_version"))},
        {
            "system": RECONSTRUCTION_HASH_SYSTEM,
            "value": str(clf.get("reconstruction_hash")),
        },
    ]
    if report_id is not None:
        identifiers.append({"system": "urn:reclass:report-id", "value": str(report_id)})

    observation: Dict[str, Any] = {
        "resourceType": "Observation",
        "id": obs_id,
        "identifier": identifiers,
        "status": status,
        "category": [_laboratory_category()],
        "code": {
            "coding": [{
                "system": LOINC_SYSTEM,
                "code": CLIN_SIG_LOINC,
                "display": CLIN_SIG_DISPLAY,
            }],
            "text": CLIN_SIG_DISPLAY,
        },
        "valueCodeableConcept": {
            "coding": [{
                "system": LOINC_SYSTEM,
                "code": tier_answer["code"],
                "display": tier_answer["display"],
            }],
            # Keep the engine's own tier label as the human text for traceability.
            "text": tier or tier_answer["display"],
        },
        "component": components,
        # `method` captures the deterministic engine so the FHIR record names the
        # exact derivation it came from (engine version + reconstruction hash).
        "method": {
            "coding": [{
                "system": "urn:reclass:method",
                "code": "acmg-bayesian-points",
                "display": (
                    f"ReClass ACMG/AMP engine {clf.get('engine_version')} "
                    f"(reconstruction {clf.get('reconstruction_hash')})"
                ),
            }],
            "text": "Standardized ACMG/AMP Bayesian-points classification engine",
        },
    }

    if effective is not None:
        observation["effectiveDateTime"] = effective
    if issued is not None:
        observation["issued"] = issued
    if signer not in (None, ""):
        observation["performer"] = [{"display": str(signer)}]
    overrides = clf.get("overrides") or []
    if overrides:
        observation["note"] = [{"text": o} for o in overrides]

    return observation


def molecular_sequence(
    variant_key: str,
    *,
    build: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a FHIR R4 ``MolecularSequence``-style resource for the variant.

    Carries the genome build + chromosome in ``referenceSeq`` and the variant in a
    ``variant`` block (1-based ``start`` plus observed/reference alleles), keeping
    the spec shape but minimal. ``build`` overrides the build token parsed from the
    key; otherwise the key's build (or :data:`engine.normalize.DEFAULT_BUILD`) is
    used. The coordinates round-trip back through :func:`engine.normalize.parse_key`.
    """
    coords = _coords(variant_key)
    used_build = build or coords["build"]
    short = _key_slug(variant_key)
    return {
        "resourceType": "MolecularSequence",
        "id": f"reclass-seq-{short}",
        "type": "dna",
        "coordinateSystem": 1,  # 1-based, matching the variant key's POS convention.
        "referenceSeq": {
            "genomeBuild": used_build,
            "orientation": "sense",
            "referenceSeqId": {
                "coding": [{
                    # NCBI chromosome reference per the genome build.
                    "system": "http://www.ncbi.nlm.nih.gov/nuccore",
                    "code": coords["chrom"],
                    "display": f"chromosome {coords['chrom']} ({used_build})",
                }],
                "text": coords["chrom"],
            },
            "strand": "watson",
            "windowStart": coords["pos"],
            "windowEnd": coords["pos"] + max(len(coords["ref"]), 1) - 1,
        },
        "variant": [{
            "start": coords["pos"],
            "end": coords["pos"] + max(len(coords["ref"]), 1) - 1,
            "observedAllele": coords["alt"],
            "referenceAllele": coords["ref"],
        }],
    }


def diagnostic_report(
    observation_full_url: str,
    *,
    variant_key: str,
    status: str = STATUS_DRAFT,
    issued: Optional[str] = None,
    effective: Optional[str] = None,
    signer: Optional[str] = None,
    report_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a FHIR R4 ``DiagnosticReport`` wrapping the variant Observation.

    A thin genetics report whose ``result`` references the variant-interpretation
    Observation (by its bundle ``fullUrl``). ``status`` should match the
    Observation's release state (``final`` when signed, else ``preliminary``).
    """
    report: Dict[str, Any] = {
        "resourceType": "DiagnosticReport",
        "id": f"reclass-dr-{_key_slug(variant_key)}",
        "status": status,
        "category": [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                "code": "GE",
                "display": "Genetics",
            }],
        }],
        "code": {
            "coding": [{
                "system": LOINC_SYSTEM,
                "code": "51969-4",
                "display": "Genetic analysis master panel",
            }],
            "text": "Genetic variant interpretation",
        },
        "result": [{"reference": observation_full_url}],
    }
    if report_id is not None:
        report["identifier"] = [{"system": "urn:reclass:report-id", "value": str(report_id)}]
    if effective is not None:
        report["effectiveDateTime"] = effective
    if issued is not None:
        report["issued"] = issued
    if signer not in (None, ""):
        report["performer"] = [{"display": str(signer)}]
    return report


def genomics_report_bundle(
    classification: Any,
    *,
    variant_key: str,
    gene: Optional[str] = None,
    transcript: Optional[str] = None,
    hgvs_c: Optional[str] = None,
    hgvs_p: Optional[str] = None,
    build: Optional[str] = None,
    issued: Optional[str] = None,
    effective: Optional[str] = None,
    signer: Optional[str] = None,
    signed: Optional[bool] = None,
    report_id: Optional[str] = None,
    include_diagnostic_report: bool = True,
) -> Dict[str, Any]:
    """Build a FHIR ``Bundle`` (type ``collection``) wrapping the genomics result.

    Entries (each with a deterministic ``fullUrl``):

      * a :func:`molecular_sequence` for the variant,
      * the :func:`variant_observation` (clinical-significance interpretation),
      * optionally a :func:`diagnostic_report` referencing the Observation.

    Fully deterministic: no wall-clock is read; timestamps are the caller's
    ``issued`` / ``effective`` arguments. The same signed classification + identity
    always yields the same bundle (use :func:`to_json` for the canonical bytes).
    """
    clf = _classification_dict(classification)
    seq = molecular_sequence(variant_key, build=build)
    obs = variant_observation(
        clf,
        variant_key=variant_key,
        gene=gene,
        transcript=transcript,
        hgvs_c=hgvs_c,
        hgvs_p=hgvs_p,
        issued=issued,
        effective=effective,
        signer=signer,
        signed=signed,
        report_id=report_id,
    )

    seq_url = f"urn:uuid:{seq['id']}"
    obs_url = f"urn:uuid:{obs['id']}"
    entries: List[Dict[str, Any]] = [
        {"fullUrl": seq_url, "resource": seq},
        {"fullUrl": obs_url, "resource": obs},
    ]

    if include_diagnostic_report:
        dr = diagnostic_report(
            obs_url,
            variant_key=variant_key,
            status=obs["status"],
            issued=issued,
            effective=effective,
            signer=signer,
            report_id=report_id,
        )
        entries.append({"fullUrl": f"urn:uuid:{dr['id']}", "resource": dr})

    bundle: Dict[str, Any] = {
        "resourceType": "Bundle",
        "id": f"reclass-bundle-{_key_slug(variant_key)}-{_short_hash(clf.get('reconstruction_hash'))}",
        "identifier": {"system": VARIANT_KEY_SYSTEM, "value": str(variant_key)},
        "type": "collection",
        "entry": entries,
    }
    if issued is not None:
        # FHIR Bundle.timestamp is "when the bundle was assembled" -- caller-supplied
        # for determinism; never read from the wall-clock here.
        bundle["timestamp"] = issued
    return bundle


def to_json(resource: Dict[str, Any], *, indent: int = 2) -> str:
    """Canonical JSON for a FHIR resource/bundle: sorted keys, stable bytes."""
    return json.dumps(resource, sort_keys=True, indent=indent)


# --------------------------------------------------------------------------- #
# LIS/EHR outbound payload scaffolding                                         #
# --------------------------------------------------------------------------- #
def transition_report_state(current_state: str, next_state: str) -> str:
    """Validate an amended-report state transition.

    Draft reports may become final; final reports may be amended; amended reports
    may be superseded by another amendment. Invalid transitions raise a clear
    ``ValueError`` with the named states so startup/jobs can log a useful failure.
    """
    current = str(current_state).strip().lower()
    target = str(next_state).strip().lower()
    if current not in REPORT_STATES:
        raise ValueError(f"unknown report state {current_state!r}; expected one of {REPORT_STATES}")
    if target not in REPORT_STATES:
        raise ValueError(f"unknown report state {next_state!r}; expected one of {REPORT_STATES}")
    if target == current:
        return target
    if target not in REPORT_STATE_TRANSITIONS[current]:
        raise ValueError(f"illegal report state transition {current!r} -> {target!r}")
    return target


def _diagnostic_report_resources(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        entry["resource"]
        for entry in bundle.get("entry", [])
        if entry.get("resource", {}).get("resourceType") == "DiagnosticReport"
    ]


def _apply_outbound_report_state(
    bundle: Dict[str, Any],
    *,
    state: str,
    previous_report_id: Optional[str],
    amendment_reason: Optional[str],
) -> None:
    """Mutate ``bundle`` with the outbound state metadata."""
    for report in _diagnostic_report_resources(bundle):
        report["status"] = REPORT_STATE_TO_FHIR_STATUS[state]
        if state == REPORT_STATE_AMENDED:
            report["extension"] = [
                {
                    "url": "urn:reclass:fhir-extension:amends-report",
                    "valueIdentifier": {
                        "system": "urn:reclass:report-id",
                        "value": str(previous_report_id),
                    },
                },
                {
                    "url": "urn:reclass:fhir-extension:amendment-reason",
                    "valueString": str(amendment_reason),
                },
            ]


def _bundle_from_render_request(render_request: Dict[str, Any]) -> Dict[str, Any]:
    state = render_request["state"]
    bundle = genomics_report_bundle(
        render_request["classification"],
        variant_key=render_request["variant_key"],
        gene=render_request.get("gene"),
        transcript=render_request.get("transcript"),
        hgvs_c=render_request.get("hgvs_c"),
        hgvs_p=render_request.get("hgvs_p"),
        build=render_request.get("build"),
        issued=render_request.get("issued"),
        effective=render_request.get("effective"),
        signer=render_request.get("signer"),
        signed=state in (REPORT_STATE_FINAL, REPORT_STATE_AMENDED),
        report_id=render_request.get("report_id"),
        include_diagnostic_report=True,
    )
    _apply_outbound_report_state(
        bundle,
        state=state,
        previous_report_id=render_request.get("previous_report_id"),
        amendment_reason=render_request.get("amendment_reason"),
    )
    return bundle


def build_outbound_payload(
    classification: Any,
    *,
    variant_key: str,
    report_id: str,
    state: str = REPORT_STATE_FINAL,
    previous_report_id: Optional[str] = None,
    amendment_reason: Optional[str] = None,
    gene: Optional[str] = None,
    transcript: Optional[str] = None,
    hgvs_c: Optional[str] = None,
    hgvs_p: Optional[str] = None,
    build: Optional[str] = None,
    issued: Optional[str] = None,
    effective: Optional[str] = None,
    signer: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a replayable outbound FHIR payload for LIS/EHR exchange.

    The returned envelope stores both canonical FHIR JSON bytes and the exact
    render request needed to recreate those bytes. ``replay_outbound_payload`` can
    therefore re-render and compare ``payload_sha256`` before transmission or
    during an audit.
    """
    state = str(state).strip().lower()
    if state not in REPORT_STATES:
        raise ValueError(f"unknown report state {state!r}; expected one of {REPORT_STATES}")
    if state == REPORT_STATE_AMENDED and not previous_report_id:
        raise ValueError("amended outbound payloads require previous_report_id")
    if state == REPORT_STATE_AMENDED and not amendment_reason:
        raise ValueError("amended outbound payloads require amendment_reason")
    render_request = {
        "classification": _classification_dict(classification),
        "variant_key": variant_key,
        "report_id": report_id,
        "state": state,
        "previous_report_id": previous_report_id,
        "amendment_reason": amendment_reason,
        "gene": gene,
        "transcript": transcript,
        "hgvs_c": hgvs_c,
        "hgvs_p": hgvs_p,
        "build": build,
        "issued": issued,
        "effective": effective,
        "signer": signer,
    }
    bundle = _bundle_from_render_request(render_request)
    payload = to_json(bundle)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "payload_id": f"reclass-outbound-{report_id}-{state}-{digest[:16]}",
        "report_id": report_id,
        "state": state,
        "content_type": "application/fhir+json",
        "payload": payload,
        "payload_sha256": digest,
        "render_request": render_request,
    }


def replay_outbound_payload(outbound_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Replay an outbound envelope to prove byte-identical FHIR JSON output."""
    request = dict(outbound_payload["render_request"])
    classification = request.pop("classification")
    return build_outbound_payload(classification, **request)


def amend_outbound_payload(
    previous_payload: Mapping[str, Any],
    classification: Any,
    *,
    report_id: str,
    amendment_reason: str,
    issued: Optional[str] = None,
    effective: Optional[str] = None,
    signer: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an amended outbound payload from a previously final payload."""
    transition_report_state(str(previous_payload["state"]), REPORT_STATE_AMENDED)
    request = dict(previous_payload["render_request"])
    return build_outbound_payload(
        classification,
        variant_key=request["variant_key"],
        report_id=report_id,
        state=REPORT_STATE_AMENDED,
        previous_report_id=str(previous_payload["report_id"]),
        amendment_reason=amendment_reason,
        gene=request.get("gene"),
        transcript=request.get("transcript"),
        hgvs_c=request.get("hgvs_c"),
        hgvs_p=request.get("hgvs_p"),
        build=request.get("build"),
        issued=issued if issued is not None else request.get("issued"),
        effective=effective if effective is not None else request.get("effective"),
        signer=signer if signer is not None else request.get("signer"),
    )
