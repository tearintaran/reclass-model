"""Parse GRCh38 genomic HGVS expressions into ``(chrom, pos, ref, alt)`` loci.

The ClinGen ERepo export carries genomic HGVS in its "HGVS Expressions" column,
e.g. ``NC_000012.12:g.102917130T>C``. That `NC_0000NN.VV` accession is the RefSeq
chromosome accession; its *exact version* pins the build (``.12`` of chr12 is
GRCh38, ``.11`` is GRCh37, ``.10`` is GRCh36). To recover usable loci for the
canonical-key fallback matcher (job1 task 3) we therefore accept ONLY the pinned
GRCh38 accessions, so a GRCh37/36 coordinate is never mistaken for a GRCh38 one.

Two parsing levels live here:

  * The **substitution** form (``g.POS<ref>><alt>``, i.e. SNV/MNV) parses with NO
    reference genome -- it already carries the exact ref/alt bases, which is all the
    reference-free canonical SNV-key fallback needs (:func:`parse_genomic_hgvs`,
    :func:`locus_from_hgvs_list`).
  * The **indel** forms (``del``/``dup``/``ins``/``delins``) only name positions
    (and, for ins/delins, the inserted bases); the deleted/duplicated bases and the
    VCF anchor base must be read from the GRCh38 reference. :func:`parse_genomic_indel`
    resolves them into a VCF-style ``(chrom, pos, ref, alt)`` given a reference, which
    powers the job1 *HGVS-genomic* (``hgvs_g``) indel fallback tier. Without a
    reference these stay unparsed -- never guessed.

:func:`pick_grch38_genomic_hgvs` selects the GRCh38 genomic token (substitution or
indel) from a full ERepo cell *without* a reference, so ingest can record the token
and defer the reference-backed indel resolution to the matching layer.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

#: Exact GRCh38 RefSeq chromosome accessions -> bare contig name. Version-pinned so
#: only GRCh38 coordinates are accepted (the ERepo HGVS list also carries 37/36).
GRCH38_ACCESSIONS: Dict[str, str] = {
    "NC_000001.11": "1", "NC_000002.12": "2", "NC_000003.12": "3",
    "NC_000004.12": "4", "NC_000005.10": "5", "NC_000006.12": "6",
    "NC_000007.14": "7", "NC_000008.11": "8", "NC_000009.12": "9",
    "NC_000010.11": "10", "NC_000011.10": "11", "NC_000012.12": "12",
    "NC_000013.11": "13", "NC_000014.9": "14", "NC_000015.10": "15",
    "NC_000016.10": "16", "NC_000017.11": "17", "NC_000018.10": "18",
    "NC_000019.10": "19", "NC_000020.11": "20", "NC_000021.9": "21",
    "NC_000022.11": "22", "NC_000023.11": "X", "NC_000024.10": "Y",
    "NC_012920.1": "MT",
}

# A genomic substitution HGVS: <accession>:g.<pos><ref>><alt>, ref/alt = A/C/G/T(/N).
_SUB_RE = re.compile(
    r"^\s*(NC_\d+\.\d+):g\.(\d+)([ACGTN]+)>([ACGTN]+)\s*$", re.IGNORECASE
)

# Genomic indel HGVS forms. Each names positions (1-based, inclusive); ins/delins
# also carry the inserted bases. The reference supplies the deleted/duplicated bases
# and the VCF anchor. ``delins`` is matched before ``del`` (it contains "del").
_DELINS_RE = re.compile(
    r"^\s*(NC_\d+\.\d+):g\.(\d+)(?:_(\d+))?delins([ACGTN]+)\s*$", re.IGNORECASE
)
_DEL_RE = re.compile(r"^\s*(NC_\d+\.\d+):g\.(\d+)(?:_(\d+))?del\s*$", re.IGNORECASE)
_DUP_RE = re.compile(r"^\s*(NC_\d+\.\d+):g\.(\d+)(?:_(\d+))?dup\s*$", re.IGNORECASE)
_INS_RE = re.compile(
    r"^\s*(NC_\d+\.\d+):g\.(\d+)_(\d+)ins([ACGTN]+)\s*$", re.IGNORECASE
)
# Reference-free recognizer for the *form* of a GRCh38 genomic indel token (used by
# the picker, which selects a token without resolving its bases).
_INDEL_FORM_RE = re.compile(
    r"^\s*(NC_\d+\.\d+):g\.\d+(?:_\d+)?(?:delins|del|dup|ins)", re.IGNORECASE
)

# NCBI SPDI ``SEQ:POS:DEL:INS`` (job1 task 3). POS is a 0-based interbase coordinate;
# DEL is either the deleted sequence OR a deleted-length integer; INS is the inserted
# sequence (possibly empty). Only version-pinned GRCh38 genomic accessions are
# accepted, so a GRCh37/36 SPDI is never resolved as GRCh38.
_SPDI_RE = re.compile(
    r"^\s*(NC_\d+\.\d+):(\d+):([ACGTN]*|\d+):([ACGTN]*)\s*$", re.IGNORECASE
)


def _contig_for(accession: str) -> Optional[str]:
    """Bare GRCh38 contig name for a RefSeq accession, or None if not GRCh38."""
    return GRCH38_ACCESSIONS.get(accession) or GRCH38_ACCESSIONS.get(accession.upper())


def parse_genomic_hgvs(expr: str) -> Optional[Tuple[str, int, str, str]]:
    """Parse one GRCh38 genomic *substitution* HGVS -> ``(chrom, pos, ref, alt)``.

    Returns None for a non-substitution form, a non-GRCh38 accession, or anything
    unparseable -- never a guess.
    """
    m = _SUB_RE.match(str(expr or ""))
    if not m:
        return None
    accession, pos, ref, alt = m.group(1), m.group(2), m.group(3), m.group(4)
    chrom = _contig_for(accession)
    if chrom is None:
        return None
    return (chrom, int(pos), ref.upper(), alt.upper())


def parse_genomic_indel(expr: str, reference) -> Optional[Tuple[str, int, str, str]]:
    """Parse one GRCh38 genomic *indel* HGVS into a VCF-style ``(chrom, pos, ref, alt)``.

    Handles ``del`` / ``dup`` / ``ins`` / ``delins``. Requires a ``reference`` (a
    :class:`engine.reference.ReferenceProvider`): the deleted/duplicated bases and the
    VCF anchor base are read from the genome. The result is a valid biallelic locus
    -- NOT necessarily left-aligned; callers should left-align it via
    ``engine.normalize`` so the canonical key matches the ClinVar side.

    Returns None for a non-indel form, a non-GRCh38 accession, an out-of-range or
    unknown-contig coordinate, or anything unparseable -- never a guess. A
    ``ReferenceLookupError`` (out-of-range/unknown contig) is treated as unparseable.
    """
    if reference is None:
        return None
    s = str(expr or "")

    # delins START[_END]delins<ins>: replace ref[start..end] with the inserted bases.
    # Both sides are non-empty, so no VCF anchor base is needed.
    m = _DELINS_RE.match(s)
    if m:
        chrom = _contig_for(m.group(1))
        if chrom is None:
            return None
        start = int(m.group(2))
        end = int(m.group(3)) if m.group(3) else start
        try:
            ref_seq = reference.sequence(chrom, start, end)
        except LookupError:
            return None
        return (chrom, start, ref_seq.upper(), m.group(4).upper())

    # del START[_END]: delete ref[start..end], VCF-anchored on the base before start.
    m = _DEL_RE.match(s)
    if m:
        chrom = _contig_for(m.group(1))
        start = int(m.group(2))
        if chrom is None or start <= 1:
            return None
        end = int(m.group(3)) if m.group(3) else start
        try:
            anchor = reference.base_at(chrom, start - 1)
            deleted = reference.sequence(chrom, start, end)
        except LookupError:
            return None
        return (chrom, start - 1, (anchor + deleted).upper(), anchor.upper())

    # dup START[_END]: duplicate ref[start..end]; modeled as an insertion of the
    # duplicated segment immediately after `end`, anchored on the base at `end`.
    m = _DUP_RE.match(s)
    if m:
        chrom = _contig_for(m.group(1))
        if chrom is None:
            return None
        start = int(m.group(2))
        end = int(m.group(3)) if m.group(3) else start
        try:
            dup = reference.sequence(chrom, start, end)
            anchor = reference.base_at(chrom, end)
        except LookupError:
            return None
        return (chrom, end, anchor.upper(), (anchor + dup).upper())

    # ins START_END ins<ins>: insert between adjacent positions, anchored on `start`.
    m = _INS_RE.match(s)
    if m:
        chrom = _contig_for(m.group(1))
        if chrom is None:
            return None
        start = int(m.group(2))
        try:
            anchor = reference.base_at(chrom, start)
        except LookupError:
            return None
        return (chrom, start, anchor.upper(), (anchor + m.group(4)).upper())

    return None


def parse_spdi(expr: str, reference=None) -> Optional[Tuple[str, int, str, str]]:
    """Parse a GRCh38 genomic SPDI into a VCF-style ``(chrom, pos, ref, alt)`` (task 3).

    SPDI (``SEQ:POS:DEL:INS``) positions are 0-based interbase, so ``NC_000001.11:1:A:T``
    is the SNV at 1-based position 2. ``DEL`` may be the deleted sequence OR a deleted
    length; ``INS`` is the inserted sequence (possibly empty).

      * A substitution / MNV / delins (both DEL and INS are non-empty sequences) needs
        no reference -- it already carries both alleles.
      * A pure deletion (INS empty) or pure insertion (DEL empty), and any form whose
        DEL is given as a *length*, requires a ``reference`` to read the deleted bases
        and/or the VCF anchor base. Without one, these return None -- never guessed.

    Returns None for a non-GRCh38 accession, an out-of-range/unknown-contig coordinate,
    an identity (empty DEL and INS), or anything unparseable.
    """
    m = _SPDI_RE.match(str(expr or ""))
    if not m:
        return None
    chrom = _contig_for(m.group(1))
    if chrom is None:
        return None
    pos0 = int(m.group(2))          # 0-based interbase
    start = pos0 + 1                 # 1-based start of the affected span
    del_field, ins_seq = m.group(3), m.group(4).upper()

    # Resolve the deleted sequence (given as bases, or as a length read from the genome).
    if del_field.isdigit():
        del_len = int(del_field)
        if del_len == 0:
            del_seq = ""
        else:
            if reference is None:
                return None
            try:
                del_seq = reference.sequence(chrom, start, start + del_len - 1).upper()
            except LookupError:
                return None
    else:
        del_seq = del_field.upper()

    if del_seq and ins_seq:
        # Substitution / MNV / delins: both sides explicit, no anchor needed.
        return (chrom, start, del_seq, ins_seq)
    if del_seq and not ins_seq:
        # Pure deletion: VCF-anchor on the base before the deletion.
        if reference is None or start <= 1:
            return None
        try:
            anchor = reference.base_at(chrom, start - 1).upper()
        except LookupError:
            return None
        return (chrom, start - 1, anchor + del_seq, anchor)
    if ins_seq and not del_seq:
        # Pure insertion between pos0 and pos0+1: VCF-anchor on the base at pos0.
        if reference is None or pos0 < 1:
            return None
        try:
            anchor = reference.base_at(chrom, pos0).upper()
        except LookupError:
            return None
        return (chrom, pos0, anchor, anchor + ins_seq)
    return None  # both empty -> identity / invalid


def locus_from_genomic_hgvs(expr: str, reference=None) -> Optional[Tuple[str, int, str, str]]:
    """Resolve ONE GRCh38 genomic HGVS token to a ``(chrom, pos, ref, alt)`` locus.

    Substitutions need no reference; indels (``del``/``dup``/``ins``/``delins``)
    require one. Returns None for a non-GRCh38 / unparseable token, or for an indel
    form when no reference is supplied.
    """
    sub = parse_genomic_hgvs(expr)
    if sub is not None:
        return sub
    return parse_genomic_indel(expr, reference)


def pick_grch38_genomic_hgvs(cell: str) -> Optional[str]:
    """Select the GRCh38 genomic HGVS token (substitution OR indel) from an ERepo cell.

    Reference-free: a token is chosen by its version-pinned GRCh38 accession and a
    recognized genomic form, so ingest can record it and defer reference-backed indel
    resolution to the matching layer. A substitution is preferred (it pins a complete
    reference-free SNV/MNV locus); otherwise the first GRCh38 ``del``/``dup``/``ins``/
    ``delins`` token is returned. None when the cell carries neither.
    """
    indel_token: Optional[str] = None
    for token in str(cell or "").split(","):
        token = token.strip()
        if parse_genomic_hgvs(token) is not None:
            return token
        if indel_token is None:
            m = _INDEL_FORM_RE.match(token)
            if m is not None and _contig_for(m.group(1)) is not None:
                indel_token = token
    return indel_token


# A coding HGVS token: <transcript>:c.<...> (RefSeq NM_/NR_/XM_ or Ensembl ENST).
_CODING_HGVS_RE = re.compile(
    r"^\s*((?:NM_|NR_|XM_|XR_|ENST)\d+(?:\.\d+)?):(c\.\S+)\s*$", re.IGNORECASE
)


def parse_coding_hgvs(expr: str) -> Optional[Tuple[str, str]]:
    """Parse one coding HGVS token into ``(transcript, c_hgvs)`` (job1 task 4).

    ``NM_000277.3:c.1A>G`` -> ``("NM_000277.3", "c.1A>G")``. Returns None for a
    non-coding token (genomic ``g.`` HGVS, SPDI, free text) -- the transcript identity
    is recorded verbatim; resolving the c.HGVS to coordinates needs a transcript model
    and is intentionally out of scope here.
    """
    m = _CODING_HGVS_RE.match(str(expr or ""))
    if not m:
        return None
    return (m.group(1), m.group(2))


def pick_coding_hgvs(cell: str) -> Optional[Tuple[str, str]]:
    """Select the first coding HGVS ``(transcript, c_hgvs)`` from an HGVS cell (task 4).

    Prefers a RefSeq ``NM_`` transcript (the MANE Select / RefSeq convention) over
    other coding accessions; returns None when the cell carries no coding HGVS. The
    transcript identity is carried into the evidence bundle so a reviewer can name the
    transcript the evidence was interpreted against.
    """
    fallback: Optional[Tuple[str, str]] = None
    for token in str(cell or "").split(","):
        parsed = parse_coding_hgvs(token)
        if parsed is None:
            continue
        if parsed[0].upper().startswith("NM_"):
            return parsed
        if fallback is None:
            fallback = parsed
    return fallback


def locus_from_hgvs_list(cell: str) -> Optional[Dict[str, object]]:
    """Pull a GRCh38 SNV/MNV ``locus`` dict from a comma-separated HGVS cell.

    Scans the ERepo "HGVS Expressions" cell for the GRCh38 genomic substitution and
    returns ``{chrom, pos, ref, alt, snv, source_hgvs}`` (``snv`` True only for a
    single-base substitution), or None when no GRCh38 substitution is present.
    """
    for token in str(cell or "").split(","):
        loc = parse_genomic_hgvs(token)
        if loc is not None:
            chrom, pos, ref, alt = loc
            return {
                "chrom": chrom, "pos": pos, "ref": ref, "alt": alt,
                "snv": len(ref) == 1 and len(alt) == 1,
                "source_hgvs": token.strip(),
            }
    return None
