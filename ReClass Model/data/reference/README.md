# GRCh38 reference cache

This directory is a **local cache** for the GRCh38 genome FASTA used by
`engine.reference.FastaReference` (for reference-anchored indel left-alignment) and
located/validated by `engine.reference_cache`.

## Do not commit large FASTA files

Whole-genome FASTA files are multi-gigabyte. They must **not** be committed to
source control. Treat everything here except this README as a local-only cache.

A `.gitignore` policy should exclude `*.fa`, `*.fasta`, and `*.fai` in this folder.

## Expected default filename

By default the engine looks for:

```text
ReClass Model/data/reference/GRCh38.fa
```

A samtools-style index sibling (`GRCh38.fa.fai`) is optional. When present it is
used directly; otherwise `FastaReference` builds an in-memory index by scanning the
file once on load.

## Pointing at a different file

Set the `RECLASS_GRCH38_FASTA` environment variable to an absolute path to override
the default location (for example, a shared genome already on disk):

```bash
export RECLASS_GRCH38_FASTA=/path/to/GRCh38.fa
```

`engine.reference_cache.load_default_reference()` resolves this same order
(`RECLASS_GRCH38_FASTA` → default cache path) and returns a ready `FastaReference`
when the file is present, or `None` when it is absent so normalization can fall back
to reference-free behavior and explicitly flag indels.

## Accepted GRCh38 FASTA source

Use a **GRCh38 primary-assembly** nucleotide FASTA whose contig names match the bare
convention used everywhere else in this project (`1`, `2`, …, `X`, `Y`, `MT`) — the
same names the ClinVar VCF and the REVEL index use. A leading `chr` is tolerated by
`engine.normalize.normalize_chrom` (it is stripped), but a bare-name analysis set is
preferred. Any of the following are acceptable, provided the build is GRCh38:

- Ensembl `Homo_sapiens.GRCh38.dna.primary_assembly.fa` (bare contig names), or
- the GRCh38 **no-alt analysis set** (`GCA_000001405.15_GRCh38_no_alt_analysis_set`),
  with `chr`-prefixed names normalized on lookup.

Reference-backed left-alignment only reads short windows around a locus, so any
faithful GRCh38 primary assembly yields identical normalization; the choice of
distributor does not change results as long as the **build is GRCh38**.

## Expected checksum policy

The reference is a local-only cache, so the repo pins **no** checksum by default
(`expected sha256 : n/a`). To make a site's genome build explicit and tamper-evident,
pin the expected lowercase hex SHA-256 via the `RECLASS_GRCH38_SHA256` environment
variable (or `--sha256` on the CLI):

```bash
export RECLASS_GRCH38_SHA256=<lowercase-hex-sha256-of-your-GRCh38.fa>
```

When set, `engine.reference_cache`:

- reports `checksum match : yes/no` in `--status`, and
- **refuses to load** a FASTA whose digest does not match (`load_reference` /
  `load_default_reference` raise `ValueError`), so the engine never silently
  normalizes against the wrong genome.

When unset, status simply reports the file's actual digest with no expectation.

## `.fai` handling

A samtools-style index sibling (`GRCh38.fa.fai`) is **optional**:

- if `GRCh38.fa.fai` exists, `FastaReference` uses it directly for O(1) seeks;
- otherwise `FastaReference` builds an equivalent in-memory index by scanning the
  file once on load (assuming a uniform line width per contig, per the FASTA spec).

Generate one with `samtools faidx GRCh38.fa` if you want to avoid the one-time scan.
Like the FASTA itself, `*.fai` files are local-cache artifacts and must not be
committed.

## Checking cache status

The status helper never downloads anything and exits cleanly even when the FASTA is
absent:

```bash
cd "ReClass Model"
../.venv/bin/python -m engine.reference_cache --status
```

It reports the configured path, build, whether the file and `.fai` exist, an
optional checksum comparison, and whether `FastaReference` can load the file.
