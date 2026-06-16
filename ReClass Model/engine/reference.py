"""GRCh38 reference sequence providers (spec 03 / AC-3.4 support).

Reference-anchored variant operations (notably indel left-alignment in
`engine.normalize`) need to read the genome around a locus. This module provides a
small, deterministic provider abstraction so the rest of the engine never embeds a
specific genome backend.

Coordinate convention (matches VCF / samtools faidx):
  * positions are **1-based** and **inclusive** on both ends.
  * `sequence(chrom, start, end)` returns bases for positions `start..end` inclusive.
  * `base_at(chrom, pos)` returns the single base at `pos`.

Error semantics: an unknown contig, or any request outside `[1, contig_length]`,
raises `ReferenceLookupError`. A provider must NEVER return wrong/padded bases for
an out-of-range request — silent wrong bases would corrupt classification.

Providers:
  * `InMemoryReference` — tiny dict-backed reference for tests and small fixtures.
  * `FastaReference`    — random-access reader over a local FASTA (+ optional .fai),
                          stdlib only. Keep large FASTAs under data/reference/ and
                          out of source control.
"""

from __future__ import annotations

import os
from typing import Dict, Tuple


class ReferenceLookupError(LookupError):
    """Raised for an unknown contig or an out-of-range position request."""


class ReferenceProvider:
    """Abstract reference provider. Subclasses implement `sequence`."""

    def sequence(self, chrom: str, start: int, end: int) -> str:
        raise NotImplementedError

    def base_at(self, chrom: str, pos: int) -> str:
        return self.sequence(chrom, pos, pos)

    def contig_length(self, chrom: str) -> int:
        raise NotImplementedError


class InMemoryReference(ReferenceProvider):
    """Dict-backed reference: ``{contig: sequence}`` (1-based positions).

    Sequences are stored uppercased. Intended for fast, offline tests and small
    fixtures — not for whole-genome use.
    """

    def __init__(self, sequences: Dict[str, str]):
        self._seq: Dict[str, str] = {c: s.upper() for c, s in sequences.items()}

    def contig_length(self, chrom: str) -> int:
        if chrom not in self._seq:
            raise ReferenceLookupError(f"unknown contig: {chrom!r}")
        return len(self._seq[chrom])

    def sequence(self, chrom: str, start: int, end: int) -> str:
        if chrom not in self._seq:
            raise ReferenceLookupError(f"unknown contig: {chrom!r}")
        s = self._seq[chrom]
        if start < 1 or end < start or end > len(s):
            raise ReferenceLookupError(
                f"out-of-range request {chrom}:{start}-{end} "
                f"(contig length {len(s)})"
            )
        return s[start - 1:end]


class FastaReference(ReferenceProvider):
    """Random-access FASTA reader (stdlib only).

    Uses a samtools-style faidx model: per contig it records
    ``(length, byte_offset_of_first_base, bases_per_line, bytes_per_line)`` and
    seeks directly to any position. If a sibling ``<path>.fai`` exists it is used;
    otherwise the index is built by scanning the file once on construction.

    Assumes (per the FASTA spec) a uniform line width within each contig.
    """

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            raise ReferenceLookupError(f"FASTA not found: {path}")
        # contig -> (length, offset, linebases, linewidth)
        self._index: Dict[str, Tuple[int, int, int, int]] = {}
        self._load_or_build_index()

    def _load_or_build_index(self) -> None:
        fai = self.path + ".fai"
        if os.path.exists(fai):
            with open(fai) as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 5:
                        continue
                    name, length, offset, linebases, linewidth = parts[:5]
                    self._index[name] = (int(length), int(offset),
                                         int(linebases), int(linewidth))
            return

        with open(self.path, "rb") as f:
            byte_pos = 0
            cur = None
            seq_offset = 0
            length = 0
            linebases = None
            linewidth = None
            for raw in f:
                line_len = len(raw)
                stripped = raw.rstrip(b"\r\n")
                if raw.startswith(b">"):
                    if cur is not None:
                        self._index[cur] = (length, seq_offset,
                                            linebases or 0, linewidth or 0)
                    cur = stripped[1:].split()[0].decode() if len(stripped) > 1 else ""
                    seq_offset = byte_pos + line_len
                    length = 0
                    linebases = None
                    linewidth = None
                else:
                    if linebases is None:
                        linebases = len(stripped)
                        linewidth = line_len
                    length += len(stripped)
                byte_pos += line_len
            if cur is not None:
                self._index[cur] = (length, seq_offset,
                                    linebases or 0, linewidth or 0)

    def contig_length(self, chrom: str) -> int:
        if chrom not in self._index:
            raise ReferenceLookupError(f"unknown contig: {chrom!r}")
        return self._index[chrom][0]

    def sequence(self, chrom: str, start: int, end: int) -> str:
        if chrom not in self._index:
            raise ReferenceLookupError(f"unknown contig: {chrom!r}")
        length, offset, linebases, linewidth = self._index[chrom]
        if linebases <= 0:
            raise ReferenceLookupError(f"empty contig index for {chrom!r}")
        if start < 1 or end < start or end > length:
            raise ReferenceLookupError(
                f"out-of-range request {chrom}:{start}-{end} "
                f"(contig length {length})"
            )
        s = start - 1
        e = end - 1
        start_byte = offset + (s // linebases) * linewidth + (s % linebases)
        end_byte = offset + (e // linebases) * linewidth + (e % linebases)
        with open(self.path, "rb") as f:
            f.seek(start_byte)
            raw = f.read(end_byte - start_byte + 1)
        return raw.replace(b"\n", b"").replace(b"\r", b"").decode("ascii").upper()
