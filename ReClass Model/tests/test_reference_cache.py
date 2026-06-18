"""Tests for the GRCh38 reference-cache helper.

Pure stdlib. Every test that needs a FASTA writes a tiny temporary one, so nothing
is downloaded and no large genome file is touched. The ``RECLASS_GRCH38_FASTA``
environment variable is saved and restored around tests that mutate it.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import reference_cache as rc
from engine.reference_cache import (
    ENV_FASTA_PATH,
    ENV_FASTA_SHA256,
    ReferenceCacheConfig,
    default_config,
    default_reference_path,
    file_sha256,
    load_default_reference,
    load_reference,
    meta_path_for,
    read_metadata,
    record_metadata,
    reference_status,
)
from engine.reference import ReferenceLookupError

MINI_FASTA = ">1 desc\nGAAAAT\n>2\nACGT\nACGT\n"


def _write_fasta(path: str, content: str = MINI_FASTA) -> None:
    with open(path, "w") as f:
        f.write(content)


class _EnvGuard(unittest.TestCase):
    """Base class that snapshots and restores RECLASS_GRCH38_FASTA."""

    def setUp(self):
        self._saved_env = os.environ.get(ENV_FASTA_PATH)
        self._saved_sha = os.environ.get(ENV_FASTA_SHA256)
        os.environ.pop(ENV_FASTA_PATH, None)
        os.environ.pop(ENV_FASTA_SHA256, None)
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        for var, saved in ((ENV_FASTA_PATH, self._saved_env),
                           (ENV_FASTA_SHA256, self._saved_sha)):
            if saved is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = saved
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)


class TestDefaultPath(_EnvGuard):
    def test_default_path_selection(self):
        path = default_reference_path()
        self.assertTrue(path.endswith(os.path.join("data", "reference", "GRCh38.fa")))
        # Resolves under the ReClass Model project root (the package parent).
        self.assertTrue(os.path.isabs(path))

    def test_default_path_with_explicit_root(self):
        path = default_reference_path("/tmp/whatever")
        self.assertEqual(
            path, os.path.join("/tmp/whatever", "data", "reference", "GRCh38.fa")
        )

    def test_env_override(self):
        target = os.path.join(self.dir, "custom.fa")
        os.environ[ENV_FASTA_PATH] = target
        self.assertEqual(default_reference_path(), os.path.abspath(target))

    def test_env_override_expands_user_and_absolutizes(self):
        os.environ[ENV_FASTA_PATH] = "~/some/ref.fa"
        resolved = default_reference_path()
        self.assertTrue(os.path.isabs(resolved))
        self.assertFalse(resolved.startswith("~"))


class TestSha256(_EnvGuard):
    def test_sha256_matches_hashlib(self):
        import hashlib

        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        with open(path, "rb") as f:
            expected = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(file_sha256(path), expected)


class TestStatus(_EnvGuard):
    def test_missing_file_status(self):
        path = os.path.join(self.dir, "absent.fa")
        status = reference_status(ReferenceCacheConfig(path=path))
        self.assertFalse(status["exists"])
        self.assertFalse(status["fai_exists"])
        self.assertFalse(status["loadable"])
        self.assertIsNone(status["actual_sha256"])
        self.assertIsNone(status["checksum_match"])
        self.assertIn("not found", status["error"])

    def test_loadable_tiny_fasta_status(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        status = reference_status(ReferenceCacheConfig(path=path))
        self.assertTrue(status["exists"])
        self.assertTrue(status["loadable"])
        self.assertEqual(status["build"], "GRCh38")
        self.assertEqual(status["contigs"], 2)
        self.assertIsNone(status["error"])
        self.assertIsNone(status["checksum_match"])  # no expectation provided

    def test_fai_detected_when_present(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        with open(path + ".fai", "w") as f:
            f.write("1\t6\t8\t6\t7\n")
        status = reference_status(ReferenceCacheConfig(path=path))
        self.assertTrue(status["fai_exists"])

    def test_checksum_match(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        digest = file_sha256(path)
        status = reference_status(
            ReferenceCacheConfig(path=path, sha256=digest.upper())
        )
        self.assertTrue(status["checksum_match"])
        self.assertEqual(status["expected_sha256"], digest.upper())

    def test_checksum_mismatch(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        status = reference_status(
            ReferenceCacheConfig(path=path, sha256="deadbeef")
        )
        self.assertFalse(status["checksum_match"])


class TestLoadReference(_EnvGuard):
    def test_load_reference_returns_usable_provider(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        ref = load_reference(ReferenceCacheConfig(path=path))
        self.assertEqual(ref.sequence("1", 1, 6), "GAAAAT")
        self.assertEqual(ref.contig_length("2"), 8)

    def test_load_reference_verifies_checksum(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        digest = file_sha256(path)
        ref = load_reference(ReferenceCacheConfig(path=path, sha256=digest))
        self.assertEqual(ref.sequence("1", 1, 1), "G")

    def test_load_reference_raises_on_mismatch(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        with self.assertRaises(ValueError):
            load_reference(ReferenceCacheConfig(path=path, sha256="deadbeef"))


class TestLoadDefaultReference(_EnvGuard):
    def test_missing_returns_none_by_default(self):
        # No env override and (almost certainly) no default FASTA -> None.
        target = os.path.join(self.dir, "absent.fa")
        os.environ[ENV_FASTA_PATH] = target
        self.assertIsNone(load_default_reference())

    def test_missing_raises_when_required(self):
        os.environ[ENV_FASTA_PATH] = os.path.join(self.dir, "absent.fa")
        with self.assertRaises(ReferenceLookupError):
            load_default_reference(allow_missing=False)

    def test_discovers_and_loads_via_env(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        os.environ[ENV_FASTA_PATH] = path
        ref = load_default_reference()
        self.assertIsNotNone(ref)
        self.assertEqual(ref.sequence("1", 1, 6), "GAAAAT")

    def test_default_config_reads_expected_sha_from_env(self):
        os.environ[ENV_FASTA_SHA256] = "ABCDEF"
        cfg = default_config()
        self.assertEqual(cfg.sha256, "ABCDEF")
        self.assertEqual(cfg.build, "GRCh38")

    def test_checksum_mismatch_refuses_to_load(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        os.environ[ENV_FASTA_PATH] = path
        os.environ[ENV_FASTA_SHA256] = "deadbeef"  # wrong digest
        with self.assertRaises(ValueError):
            load_default_reference()


class TestCli(_EnvGuard):
    def test_cli_status_loadable(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rc.main(["--status", "--path", path])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("GRCh38 reference cache status", out)
        self.assertIn(path, out)
        self.assertIn("loadable        : yes", out)

    def test_cli_status_missing_exits_cleanly(self):
        path = os.path.join(self.dir, "absent.fa")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rc.main(["--status", "--path", path])
        self.assertEqual(code, 0)
        self.assertIn("file exists     : no", buf.getvalue())

    def test_cli_json_output(self):
        import json

        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc.main(["--status", "--path", path, "--json"])
        parsed = json.loads(buf.getvalue())
        self.assertTrue(parsed["loadable"])
        self.assertEqual(parsed["path"], path)

    def test_cli_uses_env_override(self):
        path = os.path.join(self.dir, "mini.fa")
        _write_fasta(path)
        os.environ[ENV_FASTA_PATH] = path
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc.main(["--status"])
        self.assertIn(path, buf.getvalue())


class TestMetadataRecord(_EnvGuard):
    """Recording the installed FASTA's source/version/checksum (job1 task 1)."""

    def test_record_writes_sidecar_with_checksum(self):
        path = os.path.join(self.dir, "GRCh38.fa")
        _write_fasta(path)
        meta = record_metadata(
            ReferenceCacheConfig(path=path),
            source="Ensembl", source_url="https://example/GRCh38.fa",
            version="release-110", notes="test",
        )
        self.assertEqual(meta["sha256"], file_sha256(path))
        self.assertEqual(meta["source"], "Ensembl")
        self.assertEqual(meta["version"], "release-110")
        self.assertEqual(meta["build"], "GRCh38")
        self.assertTrue(meta["recorded_utc"])
        # Round-trips through the sidecar file.
        on_disk = read_metadata(meta_path_for(path))
        self.assertEqual(on_disk["sha256"], meta["sha256"])

    def test_record_raises_when_fasta_absent(self):
        with self.assertRaises(ReferenceLookupError):
            record_metadata(ReferenceCacheConfig(path=os.path.join(self.dir, "nope.fa")))

    def test_status_surfaces_recorded_metadata_and_match(self):
        path = os.path.join(self.dir, "GRCh38.fa")
        _write_fasta(path)
        record_metadata(ReferenceCacheConfig(path=path), source="Ensembl", version="r110")
        status = reference_status(ReferenceCacheConfig(path=path))
        self.assertEqual(status["metadata"]["source"], "Ensembl")
        self.assertTrue(status["metadata_sha256_match"])

    def test_status_metadata_mismatch_detected(self):
        path = os.path.join(self.dir, "GRCh38.fa")
        _write_fasta(path)
        record_metadata(ReferenceCacheConfig(path=path), source="Ensembl")
        # Tamper with the FASTA after recording -> recorded checksum no longer matches.
        with open(path, "a") as f:
            f.write("ACGT\n")
        status = reference_status(ReferenceCacheConfig(path=path))
        self.assertFalse(status["metadata_sha256_match"])

    def test_status_no_metadata_is_clean(self):
        path = os.path.join(self.dir, "GRCh38.fa")
        _write_fasta(path)
        status = reference_status(ReferenceCacheConfig(path=path))
        self.assertIsNone(status["metadata"])
        self.assertIsNone(status["metadata_sha256_match"])

    def test_cli_record_then_status(self):
        path = os.path.join(self.dir, "GRCh38.fa")
        _write_fasta(path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rc.main(["--record", "--path", path, "--source", "Ensembl",
                            "--source-version", "release-110"])
        self.assertEqual(code, 0)
        self.assertIn("Recorded provenance", buf.getvalue())
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc.main(["--status", "--path", path])
        out = buf2.getvalue()
        self.assertIn("recorded source : Ensembl", out)
        self.assertIn("meta matches    : yes", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
