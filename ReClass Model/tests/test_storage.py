"""PostgreSQL integration tests for the persistence layer.

These tests prove, against a real PostgreSQL 16 server:

  * **RLS / tenant isolation** — a session scoped to tenant A cannot read tenant
    B's ``patient`` / ``classification`` / ``alert`` rows.
  * **Clinical/research boundary** — ``research.*`` tables carry no patient/tenant
    identifier columns and have no foreign key back into the clinical schema.
  * **Receipts reconstruct** — a stored classification produced by the real engine
    re-derives byte-for-byte (tier + reconstruction hash) via ``storage.verify``.
  * **Crossing-only alerts** — a tier crossing writes an alert (serious flips are
    flagged); a same-tier rescoring writes none, and the schema CHECK rejects an
    ``old_tier == new_tier`` row.

The whole module **skips cleanly** when psycopg or PostgreSQL is unavailable, so a
shared ``python -m unittest discover -s tests`` never fails for another agent.

Run explicitly:
    export PATH="/usr/local/opt/postgresql@16/bin:$PATH"
    python -m unittest tests.test_storage -v
"""
from __future__ import annotations

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAINTENANCE_DB = os.environ.get("RECLASS_MAINTENANCE_DB", "postgres")

# All psycopg-dependent imports live here so importing this test module never
# fails when the driver (or its transitive deps) is missing -> clean skip.
_SKIP_REASON = ""
try:
    import psycopg
    from psycopg import sql

    import db.apply as applymod
    from storage import db as sdb
    from storage import classifications as crepo
    from storage import evidence as erepo
    from storage import alerts as arepo
    from storage import verify as vrepo
    from engine.scoring import EvidenceEvent, classify
    from evidence.model import EvidenceBundle
    from evidence.revel import RevelProvider

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
class TestStorage(unittest.TestCase):
    """Integration tests against a throwaway database."""

    @classmethod
    def setUpClass(cls):
        cls.db = f"reclass_test_{uuid.uuid4().hex[:10]}"
        cls.role = f"reclass_app_{uuid.uuid4().hex[:8]}"

        # Fresh schema in a throwaway database.
        applymod.recreate_database(cls.db)

        cls.conn = sdb.connect(cls.db)
        # Non-superuser role for which RLS is actually enforced.
        sdb.ensure_app_role(cls.conn, cls.role)
        sdb.grant_app_role(cls.conn, cls.role)

        # Tenants and a shared variant are not RLS-protected; seed as superuser.
        with cls.conn.cursor() as cur:
            cls.tenant_a = crepo.insert_tenant(cur, "Tenant A")
            cls.tenant_b = crepo.insert_tenant(cur, "Tenant B")
            cls.variant_id = crepo.upsert_variant(
                cur, chrom="17", pos=43044295, ref="A", alt="G"
            )
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
                        sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(cls.role))
                    )
            except Exception:  # best-effort role cleanup
                pass

    def _session(self, tenant_id):
        """Tenant-scoped, RLS-enforced session (SET LOCAL ROLE to the app role)."""
        return sdb.tenant_session(self.conn, tenant_id, role=self.role)

    @staticmethod
    def _simple_classification(criterion="PVS1", strength="very_strong"):
        return classify(
            [
                EvidenceEvent(
                    source="curated",
                    acmg_criterion=criterion,
                    evidence_direction="pathogenic",
                    applied_strength=strength,
                    source_version="vcep-1",
                )
            ]
        )

    # ------------------------------------------------------------------ #
    # RLS: tenant isolation                                              #
    # ------------------------------------------------------------------ #
    def test_rls_patient_isolation(self):
        with self._session(self.tenant_a) as cur:
            pa = crepo.insert_patient(cur, tenant_id=self.tenant_a, mrn="MRN-A-1")
        with self._session(self.tenant_b) as cur:
            pb = crepo.insert_patient(cur, tenant_id=self.tenant_b, mrn="MRN-B-1")

        with self._session(self.tenant_a) as cur:
            cur.execute("SELECT patient_id, tenant_id FROM clinical.patient")
            rows = cur.fetchall()
            ids = {str(r["patient_id"]) for r in rows}
            self.assertIn(pa, ids)
            self.assertNotIn(pb, ids)
            self.assertTrue(all(str(r["tenant_id"]) == self.tenant_a for r in rows))
            cur.execute(
                "SELECT 1 FROM clinical.patient WHERE patient_id = %s", (pb,)
            )
            self.assertIsNone(cur.fetchone(), "tenant A must not read tenant B patient")

    def test_rls_classification_isolation(self):
        clf = self._simple_classification()
        with self._session(self.tenant_a) as cur:
            ca = crepo.insert_classification(
                cur, tenant_id=self.tenant_a, variant_id=self.variant_id,
                classification=clf,
            )
        with self._session(self.tenant_b) as cur:
            cb = crepo.insert_classification(
                cur, tenant_id=self.tenant_b, variant_id=self.variant_id,
                classification=clf,
            )

        with self._session(self.tenant_a) as cur:
            visible = {str(r["classification_id"]) for r in crepo.list_classifications(cur)}
            self.assertIn(ca, visible)
            self.assertNotIn(cb, visible)
            self.assertIsNone(
                crepo.get_classification(cur, cb),
                "tenant A must not read tenant B classification",
            )

    def test_rls_alert_isolation(self):
        with self._session(self.tenant_a) as cur:
            aa = arepo.record_rescoring(
                cur, tenant_id=self.tenant_a, variant_id=self.variant_id,
                old_tier="VUS", new_tier="Likely Pathogenic",
            )
        with self._session(self.tenant_b) as cur:
            ab = arepo.record_rescoring(
                cur, tenant_id=self.tenant_b, variant_id=self.variant_id,
                old_tier="VUS", new_tier="Likely Pathogenic",
            )
        self.assertIsNotNone(aa)
        self.assertIsNotNone(ab)

        with self._session(self.tenant_a) as cur:
            visible = {str(r["alert_id"]) for r in arepo.list_alerts(cur)}
            self.assertIn(aa, visible)
            self.assertNotIn(ab, visible)
            self.assertIsNone(
                arepo.get_alert(cur, ab), "tenant A must not read tenant B alert"
            )

    # ------------------------------------------------------------------ #
    # Clinical / research boundary                                       #
    # ------------------------------------------------------------------ #
    def test_research_has_no_identifiers(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'research'"
            )
            columns = [(r["table_name"], r["column_name"]) for r in cur.fetchall()]

        self.assertTrue(columns, "research schema should expose columns")
        for table, column in columns:
            lowered = column.lower()
            self.assertNotIn("patient", lowered, f"{table}.{column} leaks patient id")
            self.assertNotIn("tenant", lowered, f"{table}.{column} leaks tenant id")
            self.assertNotIn("mrn", lowered, f"{table}.{column} leaks identifier")

    def test_research_cannot_join_back_to_clinical(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS n
                  FROM pg_constraint con
                  JOIN pg_class src        ON con.conrelid = src.oid
                  JOIN pg_namespace src_ns ON src.relnamespace = src_ns.oid
                  JOIN pg_class tgt        ON con.confrelid = tgt.oid
                  JOIN pg_namespace tgt_ns ON tgt.relnamespace = tgt_ns.oid
                 WHERE con.contype = 'f'
                   AND src_ns.nspname = 'research'
                   AND tgt_ns.nspname = 'clinical'
                """
            )
            self.assertEqual(
                cur.fetchone()["n"], 0,
                "research tables must not reference the clinical schema",
            )

    # ------------------------------------------------------------------ #
    # Receipts: byte-for-byte reconstruction                             #
    # ------------------------------------------------------------------ #
    def test_receipt_reconstructs(self):
        events = [
            EvidenceEvent(source="curated", acmg_criterion="PVS1",
                          evidence_direction="pathogenic", applied_strength="very_strong",
                          source_version="vcep-1"),
            EvidenceEvent(source="gnomad", acmg_criterion="PM2",
                          evidence_direction="pathogenic", applied_strength="supporting",
                          source_version="gnomAD"),
            EvidenceEvent(source="revel", acmg_criterion="PP3",
                          evidence_direction="pathogenic", applied_strength="moderate",
                          source_version="REVEL"),
        ]
        clf = classify(events)
        self.assertEqual(clf.tier, "Pathogenic")  # 8 + 1 + 2 = 11

        key = crepo.variant_key("13", 32340000, "C", "T")
        with self.conn.cursor() as cur:
            erepo.upsert_research_variant(
                cur, variant_key=key, chrom="13", pos=32340000, ref="C", alt="T"
            )
            erepo.insert_evidence_events(cur, variant_key=key, events=events)
            variant_id = crepo.upsert_variant(cur, chrom="13", pos=32340000, ref="C", alt="T")
        self.conn.commit()

        with self._session(self.tenant_a) as cur:
            cid = crepo.insert_classification(
                cur, tenant_id=self.tenant_a, variant_id=variant_id, classification=clf,
            )

        with self._session(self.tenant_a) as cur:
            result = vrepo.verify_classification(cur, cid)

        self.assertTrue(result.ok, msg=f"mismatches: {result.mismatches}")
        self.assertEqual(result.recomputed_tier, clf.tier)
        self.assertEqual(result.recomputed_hash, clf.reconstruction_hash)

    def test_receipt_tamper_is_detected(self):
        """A receipt whose stored hash was altered must fail verification."""
        events = [
            EvidenceEvent(source="curated", acmg_criterion="PS1",
                          evidence_direction="pathogenic", applied_strength="strong",
                          source_version="vcep-1"),
        ]
        clf = classify(events)
        key = crepo.variant_key("2", 47800000, "G", "A")
        with self.conn.cursor() as cur:
            erepo.upsert_research_variant(
                cur, variant_key=key, chrom="2", pos=47800000, ref="G", alt="A"
            )
            erepo.insert_evidence_events(cur, variant_key=key, events=events)
            variant_id = crepo.upsert_variant(cur, chrom="2", pos=47800000, ref="G", alt="A")
        self.conn.commit()

        with self._session(self.tenant_a) as cur:
            cid = crepo.insert_classification(
                cur, tenant_id=self.tenant_a, variant_id=variant_id, classification=clf,
            )
            cur.execute(
                "UPDATE clinical.classification SET reconstruction_hash = %s "
                "WHERE classification_id = %s",
                ("0" * 64, cid),
            )

        with self._session(self.tenant_a) as cur:
            result = vrepo.verify_classification(cur, cid)
        self.assertFalse(result.ok)
        self.assertTrue(any("reconstruction_hash" in m for m in result.mismatches))

    # ------------------------------------------------------------------ #
    # Persisted evidence bundles (gap §3)                                #
    # ------------------------------------------------------------------ #
    def _persisted_bundle(self, key, *, chrom, pos, ref, alt):
        """Build + persist a provenance-rich EvidenceBundle; return (bundle, id, clf)."""
        events = [
            EvidenceEvent(source="curated", acmg_criterion="PVS1",
                          evidence_direction="pathogenic", applied_strength="very_strong",
                          source_version="vcep-1"),
            EvidenceEvent(source="gnomad", acmg_criterion="PM2",
                          evidence_direction="pathogenic", applied_strength="supporting",
                          source_version="gnomAD-4.1"),
        ]
        bundle = EvidenceBundle(
            variant_key=key,
            events=events,
            provider_versions={"gnomad": "gnomAD-4.1", "vcep": "vcep-1"},
            source_records=[
                {"source": "gnomad", "payload_ref": "gnomad:1-100-A-G",
                 "popmax_af": 0.0},
                {"source": "clinvar", "payload_ref": "VCV000001", "stars": 3},
            ],
            warnings=["label disagreement between submitters"],
            match={"strategy": "variation_id", "matched_on": "VCV000001"},
        )
        clf = classify(events)
        with self.conn.cursor() as cur:
            erepo.upsert_research_variant(
                cur, variant_key=key, chrom=chrom, pos=pos, ref=ref, alt=alt
            )
            bundle_id = erepo.insert_evidence_bundle(cur, bundle)
            variant_id = crepo.upsert_variant(cur, chrom=chrom, pos=pos, ref=ref, alt=alt)
        self.conn.commit()
        return bundle, bundle_id, clf, variant_id

    def test_bundle_round_trips_from_storage(self):
        key = crepo.variant_key("11", 108200000, "A", "G")
        bundle, bundle_id, _clf, _vid = self._persisted_bundle(
            key, chrom="11", pos=108200000, ref="A", alt="G"
        )
        with self.conn.cursor() as cur:
            loaded = erepo.get_evidence_bundle(cur, bundle_id)
        self.assertEqual(loaded.to_dict(), bundle.to_dict(),
                         "persisted bundle must reconstruct byte-for-byte")
        self.assertEqual(loaded.reconstruction_hash(), bundle.reconstruction_hash())

    def test_receipt_reconstructs_from_bundle_provenance(self):
        key = crepo.variant_key("11", 108210000, "C", "T")
        _bundle, _bid, clf, variant_id = self._persisted_bundle(
            key, chrom="11", pos=108210000, ref="C", alt="T"
        )
        with self._session(self.tenant_a) as cur:
            cid = crepo.insert_classification(
                cur, tenant_id=self.tenant_a, variant_id=variant_id, classification=clf,
            )
        with self._session(self.tenant_a) as cur:
            result = vrepo.verify_classification(cur, cid)

        self.assertTrue(result.ok, msg=f"mismatches: {result.mismatches}")
        self.assertIsNotNone(result.bundle_id, "bundle should be auto-discovered")
        self.assertTrue(result.provenance_ok,
                        msg=f"provenance: {result.provenance_mismatches}")
        self.assertEqual(result.recomputed_hash, clf.reconstruction_hash)

    def test_provider_bundle_key_is_verified_against_clinical_variant(self):
        """Provider bundles can use chrom-pos-ref-alt; storage can verify them."""
        provider = RevelProvider.from_scores({"7-140453136-A-T": 0.95})
        bundle = provider.fetch({
            "locus": {"chrom": "7", "pos": 140453136, "ref": "A", "alt": "T"}
        })
        self.assertEqual(bundle.variant_key, "7-140453136-A-T")
        clf = classify(bundle.events)

        with self.conn.cursor() as cur:
            bundle_id = erepo.insert_evidence_bundle(cur, bundle)
            variant_id = crepo.upsert_variant(
                cur, chrom="7", pos=140453136, ref="A", alt="T"
            )
        self.conn.commit()

        with self._session(self.tenant_a) as cur:
            cid = crepo.insert_classification(
                cur, tenant_id=self.tenant_a, variant_id=variant_id,
                classification=clf,
            )
            result = vrepo.verify_classification(cur, cid)

        self.assertTrue(result.ok, msg=f"mismatches: {result.mismatches}")
        self.assertEqual(result.bundle_id, bundle_id)
        self.assertTrue(result.provenance_ok)
        self.assertEqual(result.recomputed_hash, clf.reconstruction_hash)

    def test_bundle_provenance_tamper_is_detected(self):
        key = crepo.variant_key("11", 108220000, "G", "A")
        _bundle, bundle_id, clf, variant_id = self._persisted_bundle(
            key, chrom="11", pos=108220000, ref="G", alt="A"
        )
        # Silently delete a scored event from the bundle: the events no longer hash
        # to the recorded bundle_hash, so provenance verification must fail.
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM research.evidence_events WHERE bundle_id = %s "
                "AND acmg_criterion = 'PM2'",
                (bundle_id,),
            )
        self.conn.commit()
        with self._session(self.tenant_a) as cur:
            cid = crepo.insert_classification(
                cur, tenant_id=self.tenant_a, variant_id=variant_id, classification=clf,
            )
            result = vrepo.verify_classification(cur, cid, bundle_id=bundle_id)
        self.assertFalse(result.ok)
        self.assertFalse(result.provenance_ok)
        self.assertTrue(any("bundle_hash" in m for m in result.provenance_mismatches))

    def test_raw_payload_pruning_keeps_reconstruction(self):
        key = crepo.variant_key("11", 108230000, "T", "C")
        bundle, bundle_id, clf, variant_id = self._persisted_bundle(
            key, chrom="11", pos=108230000, ref="T", alt="C"
        )
        with self.conn.cursor() as cur:
            pruned = erepo.prune_raw_payloads(cur, bundle_id)
        self.conn.commit()
        self.assertEqual(pruned, len(bundle.source_records))

        # Compact provenance refs survive; reconstruction still verifies.
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT payload_ref, payload FROM research.source_record "
                "WHERE bundle_id = %s ORDER BY ordinal",
                (bundle_id,),
            )
            rows = cur.fetchall()
        self.assertTrue(all(r["payload"] is None for r in rows))
        self.assertTrue(all(r["payload_ref"] for r in rows))

        with self._session(self.tenant_a) as cur:
            cid = crepo.insert_classification(
                cur, tenant_id=self.tenant_a, variant_id=variant_id, classification=clf,
            )
            result = vrepo.verify_classification(cur, cid, bundle_id=bundle_id)
        self.assertTrue(result.ok, msg=f"mismatches: {result.mismatches}")
        self.assertTrue(result.provenance_ok)

    def test_no_clinical_identifiers_leak_into_research(self):
        """Identified clinical values must never appear in any research table."""
        mrn = f"MRN-SECRET-{uuid.uuid4().hex}"
        with self._session(self.tenant_a) as cur:
            patient_id = crepo.insert_patient(cur, tenant_id=self.tenant_a, mrn=mrn)

        # Persist a bundle (de-identified) for the same biological variant.
        key = crepo.variant_key("11", 108240000, "A", "C")
        self._persisted_bundle(key, chrom="11", pos=108240000, ref="A", alt="C")

        # Enumerate every research table and scan all text/jsonb data for the
        # tenant id, patient id, and MRN. None may appear anywhere in research.
        secrets = [self.tenant_a, patient_id, mrn]
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'research'"
            )
            tables = [r["table_name"] for r in cur.fetchall()]
            self.assertTrue(tables)
            for table in tables:
                cur.execute(
                    sql.SQL("SELECT * FROM research.{}").format(sql.Identifier(table))
                )
                for row in cur.fetchall():
                    blob = repr(row)
                    for secret in secrets:
                        self.assertNotIn(
                            secret, blob,
                            f"{secret!r} leaked into research.{table}: {blob}",
                        )

    # ------------------------------------------------------------------ #
    # Alerts: crossings only                                             #
    # ------------------------------------------------------------------ #
    def test_serious_crossing_is_flagged(self):
        with self._session(self.tenant_a) as cur:
            aid = arepo.record_rescoring(
                cur, tenant_id=self.tenant_a, variant_id=self.variant_id,
                old_tier="Likely Benign", new_tier="Pathogenic",
            )
            self.assertIsNotNone(aid)
            row = arepo.get_alert(cur, aid)
            self.assertTrue(row["serious"], "benign<->pathogenic flip must be serious")

    def test_non_crossing_writes_no_alert(self):
        with self._session(self.tenant_a) as cur:
            before = len(arepo.list_alerts(cur))
            res = arepo.record_rescoring(
                cur, tenant_id=self.tenant_a, variant_id=self.variant_id,
                old_tier="VUS", new_tier="VUS",
            )
            self.assertIsNone(res)
            after = len(arepo.list_alerts(cur))
            self.assertEqual(before, after, "a non-crossing must write no alert row")

    def test_schema_check_rejects_non_crossing(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self._session(self.tenant_a) as cur:
                cur.execute(
                    "INSERT INTO clinical.alert "
                    "(tenant_id, variant_id, old_tier, new_tier, serious) "
                    "VALUES (%s, %s, 'VUS', 'VUS', false)",
                    (self.tenant_a, self.variant_id),
                )

    def test_app_guard_rejects_non_crossing(self):
        with self._session(self.tenant_a) as cur:
            with self.assertRaises(ValueError):
                arepo.insert_alert(
                    cur, tenant_id=self.tenant_a, variant_id=self.variant_id,
                    old_tier="VUS", new_tier="VUS",
                )

    def test_alert_state_lifecycle(self):
        with self._session(self.tenant_a) as cur:
            aid = arepo.record_rescoring(
                cur, tenant_id=self.tenant_a, variant_id=self.variant_id,
                old_tier="VUS", new_tier="Likely Pathogenic",
            )
            self.assertEqual(arepo.get_alert(cur, aid)["state"], "open")

            row = arepo.update_alert_state(cur, aid, state="acknowledged")
            self.assertEqual(row["state"], "acknowledged")
            row = arepo.update_alert_state(cur, aid, state="in_review")
            self.assertEqual(row["state"], "in_review")
            row = arepo.update_alert_state(cur, aid, state="resolved")
            self.assertEqual(row["state"], "resolved")
            self.assertIsNotNone(row["resolved_at"], "resolving stamps resolved_at")

            # Terminal: a resolved alert cannot be reopened.
            with self.assertRaises(ValueError):
                arepo.update_alert_state(cur, aid, state="open")


if __name__ == "__main__":
    unittest.main(verbosity=2)
