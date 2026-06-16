"""Pydantic request/response models for the API.

These models validate input at the edge (invalid variant identity, malformed
evidence) and shape responses so every clinical result carries its provenance:
evidence events, provider versions, warnings, the engine version, and the
reconstruction hash. They are plain DTOs — no business logic lives here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# Variant identity                                                            #
# --------------------------------------------------------------------------- #
class VariantRef(BaseModel):
    """A variant identified by genomic locus and/or a ClinVar Variation ID.

    At least one usable identity must be present: a complete ``(chrom, pos, ref,
    alt)`` locus and/or a ``variation_id``. An empty/partial reference is an
    *invalid variant identity* and is rejected with HTTP 422.
    """

    chrom: Optional[str] = None
    pos: Optional[int] = None
    ref: Optional[str] = None
    alt: Optional[str] = None
    build: str = "GRCh38"
    variation_id: Optional[str] = None

    @model_validator(mode="after")
    def _require_identity(self) -> "VariantRef":
        has_locus = all(
            v is not None and str(v) != ""
            for v in (self.chrom, self.pos, self.ref, self.alt)
        )
        has_vid = self.variation_id is not None and str(self.variation_id).strip() != ""
        if not (has_locus or has_vid):
            raise ValueError(
                "invalid variant identity: provide a full (chrom,pos,ref,alt) "
                "locus and/or a variation_id"
            )
        return self

    @property
    def has_locus(self) -> bool:
        return all(v is not None for v in (self.chrom, self.pos, self.ref, self.alt))

    def variant_key(self) -> Optional[str]:
        """Canonical de-identified key ``build-chrom-pos-ref-alt`` (or None)."""
        if not self.has_locus:
            return None
        return f"{self.build}-{self.chrom}-{self.pos}-{self.ref}-{self.alt}"

    def to_provider_input(self) -> Dict[str, Any]:
        """A case-like dict accepted by every evidence provider's ``fetch``."""
        out: Dict[str, Any] = {}
        if self.has_locus:
            out["locus"] = {
                "chrom": str(self.chrom),
                "pos": int(self.pos),  # type: ignore[arg-type]
                "ref": self.ref,
                "alt": self.alt,
                "build": self.build,
            }
            out.update(out["locus"])
        if self.variation_id:
            out["provenance"] = {
                "variation_id": str(self.variation_id),
                "clinvar_id": str(self.variation_id),
            }
        return out


# --------------------------------------------------------------------------- #
# Evidence input                                                              #
# --------------------------------------------------------------------------- #
class EvidenceEventModel(BaseModel):
    """One standardized evidence event (mirrors ``engine.scoring.EvidenceEvent``)."""

    source: str
    acmg_criterion: str
    evidence_direction: str = Field(pattern="^(pathogenic|benign|neutral)$")
    applied_strength: Optional[str] = None
    points: Optional[float] = None
    source_version: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class ResolveRequest(BaseModel):
    """Resolve evidence for a variant through configured providers."""

    variant: VariantRef
    providers: Optional[List[str]] = None


class EvidenceInput(BaseModel):
    """Evidence for classify/persist/reanalysis, given one of three ways.

    Exactly one (or none) of ``events`` / ``signals`` / ``resolve`` selects how
    evidence is obtained; an entirely empty input is allowed and yields a
    no-evidence (VUS) draft with an explicit warning rather than an error.
    """

    events: Optional[List[EvidenceEventModel]] = None
    signals: Optional[Dict[str, Any]] = None
    resolve: Optional[ResolveRequest] = None

    @model_validator(mode="after")
    def _at_most_one_source(self) -> "EvidenceInput":
        provided = [x for x in (self.events, self.signals, self.resolve) if x is not None]
        if len(provided) > 1:
            raise ValueError(
                "provide at most one of: events, signals, resolve"
            )
        return self


# --------------------------------------------------------------------------- #
# Persist / reanalysis request bodies                                         #
# --------------------------------------------------------------------------- #
class PersistRequest(BaseModel):
    """Persist a classification receipt for a (tenant, optional patient, variant)."""

    variant: VariantRef
    evidence: EvidenceInput = Field(default_factory=EvidenceInput)
    patient_mrn: Optional[str] = None


class ClassifyRequest(BaseModel):
    """Stateless classification preview (no persistence, no sign-off)."""

    variant: Optional[VariantRef] = None
    evidence: EvidenceInput = Field(default_factory=EvidenceInput)


class ReanalysisRequest(BaseModel):
    """Recompute a variant from current evidence; persist/alert only on change."""

    variant: VariantRef
    evidence: EvidenceInput = Field(default_factory=EvidenceInput)
    patient_mrn: Optional[str] = None
    trigger: str = "evidence"


class SignOffRequest(BaseModel):
    """Credentialed human sign-off releasing a draft for clinical use."""

    signed_off_by: str = Field(min_length=1)
    credential: Optional[str] = None


class AlertStateRequest(BaseModel):
    state: str


# --------------------------------------------------------------------------- #
# Response shapes (kept permissive: assembled from dataclasses/dicts)         #
# --------------------------------------------------------------------------- #
class EvidenceBundleResponse(BaseModel):
    variant_key: Optional[str] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)
    provider_versions: Dict[str, str] = Field(default_factory=dict)
    source_records: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    match: Optional[Dict[str, Any]] = None
    per_provider: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
