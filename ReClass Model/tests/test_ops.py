"""Tests for the operational reanalysis layer (ops/: scheduler, queue, run_report).

Two layers, mirroring tests/test_reanalysis.py:

  * ``TestSchedulerPure`` / ``TestQueuePure`` / ``TestRunReportPure`` — stdlib-only,
    DB-free unit tests of trigger detection, the in-memory queue + manifest loading,
    the run loop (including retry / skip / failure semantics), and run-report
    accounting. These always run.
  * ``TestOpsDB`` — PostgreSQL 16 integration tests proving the DB-backed queue
    de-dupes pending work, claims atomically, and that ``run_from_queue`` drives the
    real reanalysis core, persists a run report, and respects tenant RLS. These
    **skip cleanly** when psycopg or PostgreSQL is unavailable.

Run explicitly:
    export PATH="/usr/local/opt/postgresql@16/bin:$PATH"
    python -m unittest tests.test_ops -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAINTENANCE_DB = os.environ.get("RECLASS_MAINTENANCE_DB", "postgres")

# Pure-logic imports (no DB driver needed) -> these tests always run.
from engine.scoring import EvidenceEvent  # noqa: E402
from ops import queue as opsq  # noqa: E402
from ops import repo_guard as guard  # noqa: E402
from ops import run_report as opsr  # noqa: E402
from ops import scheduler as opss  # noqa: E402


def _result(*, changed, crossed=False, old_tier=None, new_tier="VUS",
            old_points=None, new_points=0.0):
    """A ReanalysisResult-shaped stand-in for the pure run-loop tests."""
    return SimpleNamespace(
        changed=changed, crossed=crossed, old_tier=old_tier, new_tier=new_tier,
        old_points=old_points, new_points=new_points,
        reanalysis_id=None, alert_id=None,
    )


class TestSchedulerPure(unittest.TestCase):
    def test_provider_version_changes_detects_changed_and_added(self):
        prev = {"gnomad": "4.0", "revel": "1.3"}
        cur = {"gnomad": "4.1", "revel": "1.3", "clingen": "2024-01"}
        changes = opss.provider_version_changes(prev, cur)
        self.assertEqual(changes["gnomad"], ("4.0", "4.1"))
        self.assertEqual(changes["clingen"], (None, "2024-01"))
        self.assertNotIn("revel", changes, "unchanged provider is not a trigger")

    def test_provider_version_changes_ignores_removed(self):
        self.assertEqual(opss.provider_version_changes({"x": "1"}, {}), {})

    def test_config_version_changed(self):
        self.assertTrue(opss.config_version_changed("0.9", "1.0.0"))
        self.assertFalse(opss.config_version_changed("1.0.0", "1.0.0"))

    def test_source_and_conflict_policy_changes_enqueue_with_manifest(self):
        snapshot_changes = opss.source_snapshot_changes(
            {"clinvar": "sha-old"},
            {"clinvar": "sha-new", "clingen": "sha-1"},
        )
        self.assertEqual(snapshot_changes["clinvar"], ("sha-old", "sha-new"))
        self.assertEqual(snapshot_changes["clingen"], (None, "sha-1"))
        self.assertTrue(opss.conflict_policy_changed("policy-v1", "policy-v2"))

        queue, manifest = opss.enqueue_affected_variants(
            ["variant-a", "variant-b"],
            trigger="source_snapshot",
            cause="ClinVar snapshot sha-old -> sha-new",
            run_id="run-123",
            tenant_id="tenant-1",
            priority=7,
        )
        self.assertEqual(len(queue), 2)
        self.assertEqual(manifest["run_id"], "run-123")
        self.assertEqual(manifest["trigger_cause"], "ClinVar snapshot sha-old -> sha-new")
        self.assertEqual([i["variant_id"] for i in manifest["items"]], ["variant-a", "variant-b"])
        self.assertTrue(all(i["trigger"] == "source_snapshot" for i in manifest["items"]))

        _, policy_manifest = opss.enqueue_affected_variants(
            ["variant-a"],
            trigger="conflict_policy",
            cause="BA1 curated-pathogenic exception policy changed",
            run_id="run-456",
        )
        self.assertEqual(policy_manifest["items"][0]["trigger"], "conflict_policy")

    def test_execute_run_buckets_outcomes(self):
        items = ["v_unchanged", "v_same", "v_crossed"]
        results = {
            "v_unchanged": _result(changed=False),
            "v_same": _result(changed=True, crossed=False),
            "v_crossed": _result(changed=True, crossed=True,
                                 old_tier="VUS", new_tier="Likely Pathogenic"),
        }
        report = opss.execute_run(
            items,
            resolve_events=lambda i: [],
            run_one=lambda i, e: results[i],
        )
        self.assertEqual(report.checked, 3)
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(report.same_tier, 1)
        self.assertEqual(report.crossed, 1)
        self.assertEqual(report.failed, 0)
        # Invariant: every variant lands in exactly one bucket.
        c = report.counts()
        self.assertEqual(
            c["checked"],
            c["unchanged"] + c["same_tier"] + c["crossed"] + c["failed"] + c["skipped"],
        )

    def test_execute_run_skip_is_not_a_failure(self):
        def resolve(_item):
            raise opss.SkipReanalysis(opss.NO_EVIDENCE, "no evidence in store")
        report = opss.execute_run(
            ["v1"], resolve_events=resolve, run_one=lambda i, e: _result(changed=True),
        )
        self.assertEqual(report.skipped, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.outcomes[0].reason_code, opss.NO_EVIDENCE)

    def test_execute_run_permanent_error_fails_without_retry(self):
        calls = {"n": 0}

        def resolve(_item):
            calls["n"] += 1
            raise opss.InvalidVariantIdentity("bad coords")

        report = opss.execute_run(
            ["v1"], resolve_events=resolve, run_one=lambda i, e: _result(changed=True),
            max_attempts=5,
        )
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.outcomes[0].reason_code, opss.INVALID_VARIANT_IDENTITY)
        self.assertEqual(calls["n"], 1, "a permanent error must not be retried")

    def test_execute_run_transient_error_is_retried_then_succeeds(self):
        calls = {"n": 0}

        def resolve(_item):
            calls["n"] += 1
            if calls["n"] < 3:
                raise opss.MissingProviderCache("cache warming up")
            return []

        report = opss.execute_run(
            ["v1"], resolve_events=resolve,
            run_one=lambda i, e: _result(changed=True, crossed=False),
            max_attempts=3,
        )
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.same_tier, 1)
        self.assertEqual(calls["n"], 3)

    def test_execute_run_transient_error_exhausts_attempts(self):
        def resolve(_item):
            raise opss.UnavailableReference("FASTA missing")
        report = opss.execute_run(
            ["v1"], resolve_events=resolve, run_one=lambda i, e: _result(changed=True),
            max_attempts=2,
        )
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.outcomes[0].reason_code, opss.UNAVAILABLE_REFERENCE)


class TestQueuePure(unittest.TestCase):
    def test_in_memory_queue_dedupes_pending(self):
        que = opsq.InMemoryQueue()
        self.assertTrue(que.enqueue(opsq.QueueItem(variant_id="v1")))
        self.assertFalse(que.enqueue(opsq.QueueItem(variant_id="v1")),
                         "identical pending item is deduped")
        self.assertEqual(len(que), 1)

    def test_in_memory_queue_priority_order(self):
        que = opsq.InMemoryQueue()
        que.enqueue(opsq.QueueItem(variant_id="low", priority=0))
        que.enqueue(opsq.QueueItem(variant_id="high", priority=10))
        que.enqueue(opsq.QueueItem(variant_id="mid", priority=5))
        order = [i.variant_id for i in que.claim_batch()]
        self.assertEqual(order, ["high", "mid", "low"])

    def test_manifest_round_trip(self):
        data = {"items": [
            {"variant_id": "a", "trigger": "provider_version", "reason": "gnomAD 4.1",
             "priority": 3},
            {"variant_id": "b"},
            {"variant_id": "c", "trigger": "conflict_policy"},
        ]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data, fh)
            path = fh.name
        try:
            items = opsq.load_manifest(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].trigger, "provider_version")
        self.assertEqual(items[1].trigger, "evidence")  # default
        self.assertEqual(items[2].trigger, "conflict_policy")

    def test_manifest_rejects_unknown_trigger(self):
        with self.assertRaises(ValueError):
            opsq.items_from_manifest([{"variant_id": "a", "trigger": "nope"}])


class TestRunReportPure(unittest.TestCase):
    def test_summary_and_to_dict(self):
        rep = opsr.RunReport(trigger="provider_version")
        rep.record_result("v1", _result(changed=False))
        rep.record_result("v2", _result(changed=True, crossed=True,
                                        old_tier="VUS", new_tier="Pathogenic"))
        rep.record_failure("v3", opss.INVALID_VARIANT_IDENTITY, "bad")
        rep.record_skip("v4", opss.NO_EVIDENCE)
        rep.finish()
        d = rep.to_dict()
        self.assertEqual(d["checked"], 4)
        self.assertEqual(d["crossed"], 1)
        self.assertEqual(len(d["detail"]), 4)
        self.assertIn("provider_version", rep.summary())
        self.assertEqual(len(rep.failures()), 1)


class TestRepoGuardPure(unittest.TestCase):
    """Commit guard (gap §6 task 3): prohibited files are flagged, allowed pass."""

    def test_flags_prohibited_paths(self):
        paths = [
            "ReClass Model/data/raw/revel_all.zip",
            "ReClass Model/data/raw/clinvar_GRCh38.vcf.gz",
            "ReClass Model/data/reference/GRCh38.fa",
            "ReClass Model/data/cache/providers/gnomad_cache.json",
            "ReClass Model/data/private/patients.csv",
            "export_mrn_dump.csv",
        ]
        flagged = dict(guard.check_paths(paths))
        self.assertEqual(flagged["ReClass Model/data/raw/revel_all.zip"], guard.RAW_ARCHIVE)
        self.assertEqual(
            flagged["ReClass Model/data/raw/clinvar_GRCh38.vcf.gz"], guard.RAW_ARCHIVE
        )
        self.assertEqual(
            flagged["ReClass Model/data/reference/GRCh38.fa"], guard.LARGE_FASTA
        )
        self.assertEqual(
            flagged["ReClass Model/data/cache/providers/gnomad_cache.json"],
            guard.PROVIDER_CACHE,
        )
        self.assertEqual(
            flagged["ReClass Model/data/private/patients.csv"], guard.PRIVATE_CLINICAL
        )
        self.assertEqual(flagged["export_mrn_dump.csv"], guard.PRIVATE_CLINICAL)

    def test_allows_committable_paths(self):
        ok = [
            "ReClass Model/ops/scheduler.py",
            "ReClass Model/validation/fixtures/clinvar_real_v1.json",
            "ReClass Model/data/raw/README.md",
            "ReClass Model/data/raw/clinvar_GRCh38.vcf.gz.md5",
            "ReClass Model/docs/data_governance.md",
            ".gitignore",
        ]
        self.assertEqual(guard.check_paths(ok), [])

    def test_committed_fixture_is_exempt_from_oversized(self):
        # A large file under validation/fixtures/ is committed on purpose and must
        # NOT be flagged, even when it exceeds the oversized threshold on disk.
        with tempfile.TemporaryDirectory() as root:
            fdir = os.path.join(root, "validation", "fixtures")
            os.makedirs(fdir)
            big = os.path.join(fdir, "clinvar_real_v1.json")
            with open(big, "wb") as fh:
                fh.write(b"\0" * 4096)
            flagged = guard.check_paths(
                ["validation/fixtures/clinvar_real_v1.json"],
                repo_root=root, size_limit=1024,
            )
            self.assertEqual(flagged, [])

    def test_oversized_catch_all(self):
        with tempfile.TemporaryDirectory() as root:
            big = os.path.join(root, "blob.dat")
            with open(big, "wb") as fh:
                fh.write(b"\0" * 2048)
            flagged = dict(guard.check_paths(["blob.dat"], repo_root=root, size_limit=1024))
            self.assertEqual(flagged["blob.dat"], guard.OVERSIZED)

    def test_hook_script_checks_staged_paths_from_repo_root(self):
        script = guard.hook_script()
        self.assertIn("repo_guard.py", script)
        self.assertIn("--staged --repo-root", script)
        self.assertIn('git rev-parse --show-toplevel', script)

    def test_install_pre_commit_hook(self):
        with tempfile.TemporaryDirectory() as root:
            hooks = os.path.join(root, ".git", "hooks")
            os.makedirs(hooks)
            path = guard.install_pre_commit_hook(root)
            self.assertEqual(path, os.path.join(root, ".git", "hooks", "pre-commit"))
            self.assertTrue(os.access(path, os.X_OK))
            with open(path, encoding="utf-8") as fh:
                script = fh.read()
            self.assertIn("ReClass Model/ops/repo_guard.py", script)
            self.assertIn("--staged --repo-root", script)


# --------------------------------------------------------------------------- #
# DB-backed ops integration tests                                             #
# --------------------------------------------------------------------------- #
_SKIP_REASON = ""
try:
    import psycopg
    from psycopg import sql

    import db.apply as applymod
    from storage import db as sdb
    from storage import classifications as crepo
    from monitoring import reanalysis as rean

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - exercised only without deps
    _IMPORTS_OK = False
    _SKIP_REASON = f"storage dependencies unavailable: {exc}"


def _pg_ready():
    if not _IMPORTS_OK:
        return False, _SKIP_REASON
    try:
        with psycopg.connect(dbname=MAINTENANCE_DB, connect_timeout=3):
            return True, ""
    except Exception as exc:  # pragma: no cover - exercised only without a server
        return False, f"PostgreSQL not available: {exc}"


PG_READY, PG_REASON = _pg_ready()


@unittest.skipUnless(PG_READY, PG_REASON)
class TestOpsDB(unittest.TestCase):
    """Operational queue + run orchestration against a throwaway database."""

    @classmethod
    def setUpClass(cls):
        cls.db = f"reclass_test_{uuid.uuid4().hex[:10]}"
        cls.role = f"reclass_app_{uuid.uuid4().hex[:8]}"
        applymod.recreate_database(cls.db)
        cls.conn = sdb.connect(cls.db)
        sdb.ensure_app_role(cls.conn, cls.role)
        sdb.grant_app_role(cls.conn, cls.role)
        with cls.conn.cursor() as cur:
            cls.tenant = crepo.insert_tenant(cur, "Tenant Ops")
            cls.tenant_b = crepo.insert_tenant(cur, "Tenant Ops B")
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            applymod.drop_database(cls.db)
            try:
                with psycopg.connect(dbname=MAINTENANCE_DB, autocommit=True) as c:
                    c.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(cls.role)
                        )
                    )
            except Exception:  # best-effort role cleanup
                pass

    def setUp(self):
        # Each test starts with an empty queue/run history so global per-run counts
        # are deterministic. Runs as the (superuser) owner conn -> clears all tenants.
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM clinical.reanalysis_queue")
            cur.execute("DELETE FROM clinical.reanalysis_run")
        self.conn.commit()

    def _session(self, tenant_id=None):
        return sdb.tenant_session(self.conn, tenant_id or self.tenant, role=self.role)

    def _new_variant(self, *, chrom, pos, ref="A", alt="G"):
        with self.conn.cursor() as cur:
            vid = crepo.upsert_variant(cur, chrom=chrom, pos=pos, ref=ref, alt=alt)
        self.conn.commit()
        return vid

    @staticmethod
    def _ev(criterion, strength, direction="pathogenic"):
        return EvidenceEvent(source="curated", acmg_criterion=criterion,
                             evidence_direction=direction, applied_strength=strength,
                             source_version="v1")

    def _seed(self, vid, events):
        with self._session() as cur:
            rean.reanalyze(cur, tenant_id=self.tenant, variant_id=vid,
                           new_events=events)

    def test_enqueue_dedupes_pending(self):
        vid = self._new_variant(chrom="1", pos=5001)
        with self._session() as cur:
            first = opsq.enqueue(cur, tenant_id=self.tenant, variant_id=vid,
                                 trigger="provider_version", reason="gnomAD 4.1")
            dup = opsq.enqueue(cur, tenant_id=self.tenant, variant_id=vid,
                               trigger="provider_version")
            pending = opsq.list_queue(cur, state="pending", variant_id=vid)
        self.assertIsNotNone(first)
        self.assertIsNone(dup, "a second pending item for the same trigger is deduped")
        self.assertEqual(len(pending), 1)

    def test_claim_batch_marks_running(self):
        vid = self._new_variant(chrom="2", pos=5002)
        with self._session() as cur:
            opsq.enqueue(cur, tenant_id=self.tenant, variant_id=vid)
            claimed = opsq.claim_batch(cur, limit=10)
            ids = {str(r["variant_id"]) for r in claimed}
            self.assertIn(vid, ids)
            row = next(r for r in claimed if str(r["variant_id"]) == vid)
            self.assertEqual(row["state"], "running")
            self.assertEqual(row["attempts"], 1)
            # Already-claimed (running) work is not re-claimed.
            again = opsq.claim_batch(cur, limit=10)
            self.assertNotIn(vid, {str(r["variant_id"]) for r in again})

    def test_run_from_queue_drives_crossing_and_persists_report(self):
        vid = self._new_variant(chrom="3", pos=5003)
        base = [self._ev("PM2", "supporting")]  # +1 -> VUS
        self._seed(vid, base)

        crossing = [self._ev("PM2", "supporting"), self._ev("PVS1", "very_strong")]

        def resolve(item):
            return crossing

        with self._session() as cur:
            opsq.enqueue(cur, tenant_id=self.tenant, variant_id=vid,
                         trigger="provider_version")
            report = opss.run_from_queue(
                cur, tenant_id=self.tenant, resolve_events=resolve,
                trigger="provider_version",
            )
            self.assertEqual(report.crossed, 1)
            self.assertEqual(report.checked, 1)
            run_id = opsr.list_runs(cur)[-1]["run_id"]
            run = opsr.get_run(cur, run_id)
            self.assertEqual(run["crossed"], 1)
            self.assertEqual(run["checked"], 1)
            self.assertEqual(len(run["detail"]), 1)
            self.assertEqual(run["detail"][0]["new_tier"], "Likely Pathogenic")
            # The queue item is now done and points at the run.
            item = opsq.list_queue(cur, variant_id=vid)[0]
            self.assertEqual(item["state"], "done")
            self.assertEqual(str(item["run_id"]), str(run_id))

    def test_run_from_queue_records_failures_with_reason(self):
        vid = self._new_variant(chrom="4", pos=5004)
        self._seed(vid, [self._ev("PM2", "supporting")])

        def resolve(item):
            raise opss.InvalidVariantIdentity("coords not resolvable")

        with self._session() as cur:
            opsq.enqueue(cur, tenant_id=self.tenant, variant_id=vid)
            report = opss.run_from_queue(
                cur, tenant_id=self.tenant, resolve_events=resolve,
            )
            self.assertEqual(report.failed, 1)
            item = opsq.list_queue(cur, variant_id=vid)[0]
            self.assertEqual(item["state"], "failed")
            self.assertEqual(item["last_reason_code"], opss.INVALID_VARIANT_IDENTITY)

    def test_run_from_queue_records_skips(self):
        vid = self._new_variant(chrom="5", pos=5005)
        self._seed(vid, [self._ev("PM2", "supporting")])

        def resolve(item):
            raise opss.SkipReanalysis(opss.NO_EVIDENCE, "no evidence for variant")

        with self._session() as cur:
            opsq.enqueue(cur, tenant_id=self.tenant, variant_id=vid)
            report = opss.run_from_queue(
                cur, tenant_id=self.tenant, resolve_events=resolve,
            )
            self.assertEqual(report.skipped, 1)
            item = opsq.list_queue(cur, variant_id=vid)[0]
            self.assertEqual(item["state"], "skipped")
            self.assertEqual(item["last_reason_code"], opss.NO_EVIDENCE)

    def test_queue_and_run_are_tenant_isolated(self):
        vid = self._new_variant(chrom="6", pos=5006)
        with self._session(self.tenant) as cur:
            opsq.enqueue(cur, tenant_id=self.tenant, variant_id=vid)
            opsr.start_run(cur, tenant_id=self.tenant, trigger="evidence")
        # Tenant B sees neither the queue item nor the run.
        with self._session(self.tenant_b) as cur:
            self.assertEqual(opsq.list_queue(cur), [])
            self.assertEqual(opsr.list_runs(cur), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
