"""Append-only, hash-chained audit log for tamper evidence.

Each TraceRecord is written as one JSON line. Before writing, we set
`prev_hash` to the previous record's `record_hash` and compute this record's
`record_hash` over its canonical payload (which includes prev_hash). Any edit to
a past record breaks every subsequent hash, so `verify_chain()` detects
tampering, reordering, or truncation-in-the-middle.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.models import TraceRecord

GENESIS_HASH = "0" * 64


def _hash_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_record_hash(record: TraceRecord) -> str:
    return _hash_payload(record.payload_for_hash())


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS_HASH
        last = GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = json.loads(line)["record_hash"]
        return last

    def write_trace_record(self, record: TraceRecord) -> TraceRecord:
        """Chain and append a record. Returns the record with hashes populated."""
        record.prev_hash = self._last_hash()
        record.record_hash = compute_record_hash(record)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
        return record

    def read_all(self) -> list[TraceRecord]:
        if not self.path.exists():
            return []
        records: list[TraceRecord] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(TraceRecord.model_validate_json(line))
        return records

    def verify_chain(self) -> bool:
        """Return True iff every record's hash matches and links its predecessor."""
        prev = GENESIS_HASH
        for rec in self.read_all():
            if rec.prev_hash != prev:
                return False
            if compute_record_hash(rec) != rec.record_hash:
                return False
            prev = rec.record_hash
        return True


def verify_chain(path: str | Path) -> bool:
    return AuditLog(path).verify_chain()
