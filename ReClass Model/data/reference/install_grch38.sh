#!/usr/bin/env bash
# Install the local production GRCh38 FASTA reference cache (job1 task 1).
#
# Downloads a pinned GRCh38 primary-assembly FASTA, places it at the engine's
# default cache path, optionally builds a samtools .fai index, and records its
# source/version/checksum provenance via `engine.reference_cache --record`.
#
# The whole-genome FASTA is a LOCAL-ONLY cache and is gitignored; only the small
# provenance sidecar (GRCh38.fa.meta.json) and this script are tracked.
#
# Usage (from the ReClass Model/ directory):
#   bash data/reference/install_grch38.sh
#
# Override the source by exporting these before running:
#   GRCH38_URL      download URL (default: Ensembl release-110 primary assembly)
#   GRCH38_SOURCE   human-readable source label
#   GRCH38_VERSION  source release/version label
#   RECLASS_GRCH38_FASTA  install to a non-default path
set -euo pipefail

# Pinned default source: Ensembl release-110 primary assembly, bare contig names
# (1, 2, ..., X, Y, MT) matching the convention used across this project.
GRCH38_URL="${GRCH38_URL:-https://ftp.ensembl.org/pub/release-110/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz}"
GRCH38_SOURCE="${GRCH38_SOURCE:-Ensembl GRCh38 primary assembly}"
GRCH38_VERSION="${GRCH38_VERSION:-Ensembl release-110}"

# Resolve paths relative to this script -> ReClass Model/ project root.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PY:-$ROOT/../.venv/bin/python}"
DEST="${RECLASS_GRCH38_FASTA:-$HERE/GRCh38.fa}"

echo ">> Installing GRCh38 FASTA"
echo "   source : $GRCH38_SOURCE ($GRCH38_VERSION)"
echo "   url    : $GRCH38_URL"
echo "   dest   : $DEST"

tmp_gz="$(mktemp "${TMPDIR:-/tmp}/grch38.XXXXXX.fa.gz")"
trap 'rm -f "$tmp_gz"' EXIT

echo ">> Downloading (this is multi-GB; it will take a while)..."
if command -v curl >/dev/null 2>&1; then
  curl -fL --retry 3 -o "$tmp_gz" "$GRCH38_URL"
elif command -v wget >/dev/null 2>&1; then
  wget -O "$tmp_gz" "$GRCH38_URL"
else
  echo "!! Neither curl nor wget is available; cannot download." >&2
  exit 1
fi

echo ">> Decompressing -> $DEST"
gunzip -c "$tmp_gz" > "$DEST"

if command -v samtools >/dev/null 2>&1; then
  echo ">> Building samtools .fai index"
  samtools faidx "$DEST"
else
  echo ">> samtools not found; FastaReference will build an in-memory index on first load."
fi

echo ">> Recording provenance (source, version, SHA-256) to ${DEST}.meta.json"
( cd "$ROOT" && "$PY" -m engine.reference_cache --record \
    --path "$DEST" \
    --source "$GRCH38_SOURCE" \
    --source-url "$GRCH38_URL" \
    --source-version "$GRCH38_VERSION" )

echo ">> Done. Verify with:"
echo "   cd \"$ROOT\" && $PY -m engine.reference_cache --status"
