"""Migration ledger and restore rehearsal tests.

The module skips cleanly when PostgreSQL or client tools are unavailable. When
available, it exercises the operational path this repository documents:

* apply schema plus ordered migrations through ``db/apply.py``;
* reject an applied migration whose file checksum changed;
* backup with ``deploy/backup.sh``;
* restore with ``deploy/restore.sh`` into a fresh database;
* verify representative restored clinical/research/audit rows under RLS.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RECLASS_DIR = Path(__file__).resolve().parents[1]
MAINTENANCE_DB = os.environ.get("RECLASS_MAINTENANCE_DB", "postgres")

_SKIP_REASON = ""
try:
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb

    import db.apply as applymod
    from engine.scoring import EvidenceEvent, classify
    from evidence.model import EvidenceBundle
    from storage import alerts as arepo
    from storage import classifications as crepo
    from storage import db as sdb
    from storage import evidence as erepo
    from storage import verify as vrepo

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - exercised only without deps
    _IMPORTS_OK = False
    _SKIP_REASON = f"database test dependencies unavailable: {exc}"


def _pg_ready():
    if not _IMPORTS_OK:
        return False, _SKIP_REASON
    try:
        with psycopg.connect(dbname=MAINTENANCE_DB, connect_timeout=3):
            return True, ""
    except Exception as exc:  # pragma: no cover - exercised only without a server
        return False, f"PostgreSQL not available: {exc}"


def _tools_ready():
    missing = [
        tool
        for tool in ("pg_dump", "psql", "createdb", "dropdb", "gzip")
        if shutil.which(tool) is None
    ]
    if missing:
        return False, f"PostgreSQL client tools unavailable: {', '.join(missing)}"
    return True, ""


PG_READY, PG_REASON = _pg_ready()
TOOLS_READY, TOOLS_REASON = _tools_ready()


def _drop_database(name: str) -> None:
    if not _IMPORTS_OK:
        return
    try:
        applymod.drop_database(name)
    except Exception:
        pass


def _drop_role(role: str) -> None:
    if not _IMPORTS_OK:
        return
    try:
        with psycopg.connect(dbname=MAINTENANCE_DB, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
    except Exception:
        pass


@unittest.skipUnless(PG_READY, PG_REASON)
class TestMigrationLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.db = f"reclass_migration_{uuid.uuid4().hex[:10]}"

    def tearDown(self) -> None:
        _drop_database(self.db)

    def test_migrations_are_ordered_idempotent_and_checksum_guarded(self):
        applymod.recreate_database(self.db)

        with sdb.connect(self.db) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT migration_id, filename, checksum_sha256, status "
                    "FROM public.reclass_schema_migrations ORDER BY migration_id"
                )
                ledger_rows = cur.fetchall()
                cur.execute("SELECT to_regclass('clinical.audit_log') AS audit_table")
                audit_table = cur.fetchone()["audit_table"]
                # FORCE RLS must hold on schema-defined (patient/classification) and
                # migration-defined (webhook_delivery/worklist_case) tenant tables.
                cur.execute(
                    """
                    SELECT n.nspname || '.' || c.relname AS tbl,
                           c.relrowsecurity AS enabled,
                           c.relforcerowsecurity AS forced
                      FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'clinical'
                       AND c.relname IN
                           ('patient', 'classification', 'webhook_delivery', 'worklist_case')
                    """
                )
                rls_rows = cur.fetchall()

        expected = applymod.discover_migrations()
        self.assertEqual(
            [row["migration_id"] for row in ledger_rows],
            [migration.migration_id for migration in expected],
        )
        self.assertTrue(all(row["status"] == "applied" for row in ledger_rows))
        self.assertTrue(all(len(row["checksum_sha256"]) == 64 for row in ledger_rows))
        self.assertEqual(audit_table, "clinical.audit_log")

        self.assertEqual(len(rls_rows), 4, "expected all four tenant tables present")
        for row in rls_rows:
            self.assertTrue(row["enabled"], f"RLS not enabled on {row['tbl']}")
            self.assertTrue(row["forced"], f"RLS not FORCEd on {row['tbl']}")

        self.assertEqual(
            applymod.apply(self.db),
            [],
            "second apply should skip applied migrations",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_migrations = Path(tmpdir)
            for migration in expected:
                shutil.copy2(migration.path, tmp_migrations / migration.filename)
            first = tmp_migrations / expected[0].filename
            first.write_text(
                first.read_text(encoding="utf-8") + "\n-- checksum drift\n",
                encoding="utf-8",
            )

            with self.assertRaises(applymod.MigrationChecksumError):
                applymod.apply(self.db, migrations_path=tmp_migrations)


@unittest.skipUnless(PG_READY and TOOLS_READY, PG_REASON or TOOLS_REASON)
class TestRestoreRehearsal(unittest.TestCase):
    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.source_db = f"reclass_restore_src_{suffix}"
        self.restore_db = f"reclass_restore_dst_{suffix}"
        self.role = f"reclass_restore_role_{suffix[:8]}"

    def tearDown(self) -> None:
        _drop_database(self.restore_db)
        _drop_database(self.source_db)
        _drop_role(self.role)

    def _session(self, conn, tenant_id):
        return sdb.tenant_session(conn, tenant_id, role=self.role)

    def _seed_source_database(self):
        applymod.recreate_database(self.source_db)
        conn = sdb.connect(self.source_db)
        sdb.ensure_app_role(conn, self.role)
        sdb.grant_app_role(conn, self.role)

        events = [
            EvidenceEvent(
                source="curated",
                acmg_criterion="PVS1",
                evidence_direction="pathogenic",
                applied_strength="very_strong",
                source_version="vcep-restore",
            ),
            EvidenceEvent(
                source="gnomad",
                acmg_criterion="PM2",
                evidence_direction="pathogenic",
                applied_strength="supporting",
                source_version="gnomAD-restore",
            ),
        ]
        bundle = EvidenceBundle(
            variant_key=crepo.variant_key("3", 12345, "A", "G"),
            events=events,
            provider_versions={"curated": "vcep-restore", "gnomad": "gnomAD-restore"},
            source_records=[{"source": "curated", "payload_ref": "restore:case-1"}],
            warnings=["restore rehearsal seed"],
            match={"strategy": "restore_rehearsal"},
        )
        classification = classify(events)

        with conn.cursor() as cur:
            tenant_a = crepo.insert_tenant(cur, "Restore Tenant A")
            tenant_b = crepo.insert_tenant(cur, "Restore Tenant B")
            bundle_id = erepo.insert_evidence_bundle(cur, bundle)
            variant_id = crepo.upsert_variant(cur, chrom="3", pos=12345, ref="A", alt="G")
        conn.commit()

        with self._session(conn, tenant_a) as cur:
            patient_a = crepo.insert_patient(cur, tenant_id=tenant_a, mrn="RESTORE-A")
            classification_id = crepo.insert_classification(
                cur,
                tenant_id=tenant_a,
                patient_id=patient_a,
                variant_id=variant_id,
                classification=classification,
            )
            alert_id = arepo.record_rescoring(
                cur,
                tenant_id=tenant_a,
                variant_id=variant_id,
                old_tier="VUS",
                new_tier="Likely Pathogenic",
            )
            cur.execute(
                """
                INSERT INTO clinical.audit_log
                    (tenant_id, actor_id, action, resource_type, resource_id, detail)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_a,
                    "restore-test",
                    "restore.seed",
                    "classification",
                    classification_id,
                    Jsonb({"alert_id": alert_id}),
                ),
            )

        with self._session(conn, tenant_b) as cur:
            patient_b = crepo.insert_patient(cur, tenant_id=tenant_b, mrn="RESTORE-B")

        conn.close()
        return {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "patient_a": patient_a,
            "patient_b": patient_b,
            "classification_id": classification_id,
            "bundle_id": bundle_id,
            "alert_id": alert_id,
        }

    def _backup_source(self, backup_dir: Path) -> Path:
        env = os.environ.copy()
        env.update({"RECLASS_DB": self.source_db, "RECLASS_BACKUP_DIR": str(backup_dir)})
        subprocess.run(
            [str(RECLASS_DIR / "deploy" / "backup.sh")],
            cwd=RECLASS_DIR,
            env=env,
            check=True,
        )
        backups = sorted(backup_dir.glob(f"{self.source_db}_*.sql.gz"))
        self.assertEqual(len(backups), 1)
        return backups[0]

    def _restore_backup(self, backup_path: Path) -> None:
        env = os.environ.copy()
        env.update({
            "RECLASS_RESTORE_SOURCE": str(backup_path),
            "RECLASS_RESTORE_TARGET_DB": self.restore_db,
        })
        subprocess.run(
            [str(RECLASS_DIR / "deploy" / "restore.sh")],
            cwd=RECLASS_DIR,
            env=env,
            check=True,
        )

    def test_backup_restore_reconstructs_data_and_rls(self):
        ids = self._seed_source_database()
        with tempfile.TemporaryDirectory() as tmpdir:
            backup_path = self._backup_source(Path(tmpdir))
            self._restore_backup(backup_path)

        conn = sdb.connect(self.restore_db)
        sdb.grant_app_role(conn, self.role)

        with self._session(conn, ids["tenant_a"]) as cur:
            cur.execute("SELECT patient_id FROM clinical.patient ORDER BY mrn")
            patient_ids = {str(row["patient_id"]) for row in cur.fetchall()}
            self.assertIn(ids["patient_a"], patient_ids)
            self.assertNotIn(ids["patient_b"], patient_ids)

            verification = vrepo.verify_classification(
                cur,
                ids["classification_id"],
                bundle_id=ids["bundle_id"],
            )
            self.assertTrue(verification.ok, msg=f"mismatches: {verification.mismatches}")
            self.assertTrue(
                verification.provenance_ok,
                msg=f"provenance: {verification.provenance_mismatches}",
            )

            self.assertIsNotNone(arepo.get_alert(cur, ids["alert_id"]))
            cur.execute("SELECT count(*) AS n FROM clinical.audit_log")
            self.assertEqual(cur.fetchone()["n"], 1)

        with self._session(conn, ids["tenant_b"]) as cur:
            cur.execute(
                "SELECT 1 FROM clinical.classification WHERE classification_id = %s",
                (ids["classification_id"],),
            )
            self.assertIsNone(cur.fetchone())
            cur.execute("SELECT count(*) AS n FROM clinical.audit_log")
            self.assertEqual(cur.fetchone()["n"], 0)

        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
