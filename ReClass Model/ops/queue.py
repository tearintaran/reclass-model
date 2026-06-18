"""Reanalysis work queues and manifests (gap §5 task 2).

A reanalysis run consumes *work items* — each a ``(tenant, variant, trigger)`` tuple
saying "this variant needs re-scoring because evidence / a provider version / the
config version changed". Two interchangeable backends are provided:

  * :class:`InMemoryQueue` / :func:`load_manifest` — stdlib-only, DB-free input for
    dry runs, fixtures, and unit tests. A *manifest* is just a JSON list of items.
  * The ``clinical.reanalysis_queue`` DB functions (:func:`enqueue`,
    :func:`claim_batch`, :func:`mark`, ...) — durable, tenant-scoped (RLS) work
    storage with at-most-one-pending-per-(variant, trigger) de-duplication and
    ``FOR UPDATE SKIP LOCKED`` claiming so concurrent workers never double-process.

Both yield items with the same shape (``variant_id`` / ``trigger`` / ``reason`` /
``priority``), so :func:`ops.scheduler.execute_run` consumes either one.
"""

from __future__ import annotations

import heapq
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Triggers that may enqueue work (kept in sync with ops.scheduler.TRIGGERS).
_VALID_TRIGGERS = (
    "evidence",
    "source_snapshot",
    "provider_version",
    "config_version",
    "conflict_policy",
)


@dataclass
class QueueItem:
    """A single unit of reanalysis work (backend-independent)."""

    variant_id: str
    trigger: str = "evidence"
    reason: Optional[str] = None
    priority: int = 0
    tenant_id: Optional[str] = None
    queue_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "trigger": self.trigger,
            "reason": self.reason,
            "priority": self.priority,
            "tenant_id": self.tenant_id,
            "queue_id": self.queue_id,
        }


# --------------------------------------------------------------------------- #
# In-memory queue + manifest loading (DB-free)                                 #
# --------------------------------------------------------------------------- #
class InMemoryQueue:
    """A simple priority queue of :class:`QueueItem` (higher priority first).

    Ordering is deterministic: ``(-priority, insertion_order)`` so equal-priority
    items keep FIFO order. De-duplicates on ``(variant_id, trigger)`` so re-enqueuing
    pending work is a no-op, mirroring the DB backend's partial-unique constraint.
    """

    def __init__(self) -> None:
        self._heap: List[Any] = []
        self._counter = itertools.count()
        self._pending_keys: set = set()

    def __len__(self) -> int:
        return len(self._heap)

    def enqueue(self, item: QueueItem) -> bool:
        """Add an item; returns ``False`` if an identical pending item exists."""
        key = (item.variant_id, item.trigger)
        if key in self._pending_keys:
            return False
        self._pending_keys.add(key)
        heapq.heappush(self._heap, (-item.priority, next(self._counter), item))
        return True

    def claim_next(self) -> Optional[QueueItem]:
        if not self._heap:
            return None
        _, _, item = heapq.heappop(self._heap)
        self._pending_keys.discard((item.variant_id, item.trigger))
        return item

    def claim_batch(self, limit: Optional[int] = None) -> List[QueueItem]:
        items: List[QueueItem] = []
        while self._heap and (limit is None or len(items) < limit):
            items.append(self.claim_next())
        return items

    def pending(self) -> List[QueueItem]:
        return [entry[2] for entry in sorted(self._heap)]


def items_from_manifest(data: Union[List[Dict[str, Any]], Dict[str, Any]]) -> List[QueueItem]:
    """Parse a manifest (a list of item dicts, or ``{"items": [...]}``)."""
    rows = data["items"] if isinstance(data, dict) else data
    items: List[QueueItem] = []
    for row in rows:
        trigger = row.get("trigger", "evidence")
        if trigger not in _VALID_TRIGGERS:
            raise ValueError(
                f"invalid trigger {trigger!r}; expected one of {_VALID_TRIGGERS}"
            )
        items.append(QueueItem(
            variant_id=str(row["variant_id"]),
            trigger=trigger,
            reason=row.get("reason"),
            priority=int(row.get("priority", 0)),
            tenant_id=row.get("tenant_id"),
        ))
    return items


def load_manifest(path: Union[str, Path]) -> List[QueueItem]:
    """Load reanalysis work items from a JSON manifest file."""
    text = Path(path).read_text(encoding="utf-8")
    return items_from_manifest(json.loads(text))


def queue_from_manifest(path: Union[str, Path]) -> InMemoryQueue:
    q = InMemoryQueue()
    for item in load_manifest(path):
        q.enqueue(item)
    return q


def build_run_manifest(
    items: List[QueueItem],
    *,
    run_id: str,
    trigger_cause: str,
) -> Dict[str, Any]:
    """Build an auditable run manifest for a reanalysis enqueue wave."""
    if not run_id:
        raise ValueError("run_id is required")
    if not trigger_cause:
        raise ValueError("trigger_cause is required")
    return {
        "run_id": run_id,
        "trigger_cause": trigger_cause,
        "items": [item.to_dict() for item in items],
    }


# --------------------------------------------------------------------------- #
# DB-backed queue (clinical.reanalysis_queue); requires a tenant-scoped cursor  #
# --------------------------------------------------------------------------- #
def enqueue(cur, *, tenant_id: str, variant_id: str, trigger: str = "evidence",
            reason: Optional[str] = None, priority: int = 0) -> Optional[str]:
    """Enqueue a work item; returns its ``queue_id`` or ``None`` if already pending.

    Re-enqueuing the same ``(tenant, variant, trigger)`` while a previous item is
    still pending is a no-op (deduped by the partial unique index), so a chatty
    trigger cannot flood the queue.
    """
    if trigger not in _VALID_TRIGGERS:
        raise ValueError(
            f"invalid trigger {trigger!r}; expected one of {_VALID_TRIGGERS}"
        )
    cur.execute(
        """
        INSERT INTO clinical.reanalysis_queue (tenant_id, variant_id, trigger, reason, priority)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, variant_id, trigger) WHERE state = 'pending'
        DO NOTHING
        RETURNING queue_id
        """,
        (tenant_id, variant_id, trigger, reason, priority),
    )
    row = cur.fetchone()
    return str(row["queue_id"]) if row else None


def claim_batch(cur, *, trigger: Optional[str] = None, limit: Optional[int] = None,
                run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Atomically claim pending items: mark them ``running`` and return the rows.

    Uses ``FOR UPDATE SKIP LOCKED`` so two concurrent workers claim disjoint sets.
    Highest ``priority`` first, then oldest. Each claimed row's ``attempts`` is
    incremented and ``run_id`` stamped so the run that processed it is auditable.
    """
    params: List[Any] = []
    where = "state = 'pending'"
    if trigger is not None:
        where += " AND trigger = %s"
        params.append(trigger)
    limit_sql = ""
    if limit is not None:
        limit_sql = " LIMIT %s"
        params_tail = [limit]
    else:
        params_tail = []
    cur.execute(
        f"""
        UPDATE clinical.reanalysis_queue q
           SET state = 'running', started_at = now(),
               attempts = q.attempts + 1, run_id = %s
          FROM (
              SELECT queue_id FROM clinical.reanalysis_queue
               WHERE {where}
               ORDER BY priority DESC, enqueued_at
               {limit_sql}
               FOR UPDATE SKIP LOCKED
          ) picked
         WHERE q.queue_id = picked.queue_id
        RETURNING q.*
        """,
        [run_id] + params + params_tail,
    )
    return cur.fetchall()


def mark(cur, queue_id: str, *, state: str, error: Optional[str] = None,
         reason_code: Optional[str] = None) -> Dict[str, Any]:
    """Move a claimed item to a terminal state (done / failed / skipped).

    ``done`` clears any stale error; ``failed`` / ``skipped`` record the
    deterministic ``reason_code`` (and human message) for the operator.
    """
    if state not in ("pending", "running", "done", "failed", "skipped"):
        raise ValueError(f"invalid queue state {state!r}")
    cur.execute(
        """
        UPDATE clinical.reanalysis_queue
           SET state = %s, last_error = %s, last_reason_code = %s,
               finished_at = CASE WHEN %s IN ('done','failed','skipped')
                                  THEN now() ELSE finished_at END
         WHERE queue_id = %s
        RETURNING *
        """,
        (state, error, reason_code, state, queue_id),
    )
    return cur.fetchone()


def requeue(cur, queue_id: str) -> Dict[str, Any]:
    """Reset an item back to ``pending`` (operator-driven retry of a failed item)."""
    cur.execute(
        "UPDATE clinical.reanalysis_queue "
        "SET state = 'pending', started_at = NULL, finished_at = NULL, "
        "    last_error = NULL, last_reason_code = NULL, run_id = NULL "
        "WHERE queue_id = %s RETURNING *",
        (queue_id,),
    )
    return cur.fetchone()


def get_item(cur, queue_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        "SELECT * FROM clinical.reanalysis_queue WHERE queue_id = %s", (queue_id,)
    )
    return cur.fetchone()


def list_queue(cur, *, state: Optional[str] = None,
               variant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List queue items visible to this session (subject to RLS)."""
    clauses: List[str] = []
    params: List[Any] = []
    if state is not None:
        clauses.append("state = %s")
        params.append(state)
    if variant_id is not None:
        clauses.append("variant_id = %s")
        params.append(variant_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cur.execute(
        f"SELECT * FROM clinical.reanalysis_queue{where} "
        "ORDER BY priority DESC, enqueued_at",
        params,
    )
    return cur.fetchall()
