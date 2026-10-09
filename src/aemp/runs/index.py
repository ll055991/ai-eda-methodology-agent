"""SQLite index over run directories (stdlib ``sqlite3`` only).

The index is a query cache ONLY. The filesystem (``runs/<run_id>/``) is
the source of truth: deleting or corrupting the index never loses run
data, and :meth:`RunIndex.rebuild` reconstructs the index from the run
directories.

Consistency discipline: run code writes the filesystem FIRST and updates
this index afterwards, so the index can never claim a run whose directory
does not exist.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aemp.core.errors import InvalidRequestError, SchemaValidationError, StorageError
from aemp.core.logging import get_logger
from aemp.runs.models import RUN_STATUSES, FileRecord
from aemp.schema.result import validate_result

logger = get_logger(__name__)

INDEX_FILENAME = "index.sqlite3"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    operation TEXT NOT NULL,
    engineering_status TEXT NOT NULL,
    run_dir TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);
CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL,
    role TEXT NOT NULL,
    path TEXT NOT NULL,
    "exists" INTEGER NOT NULL,
    bytes INTEGER,
    sha256 TEXT,
    PRIMARY KEY (run_id, role, path)
);
"""

_INSERT_RUN_SQL = """
INSERT OR REPLACE INTO runs
    (run_id, status, created_at, started_at, finished_at, operation,
     engineering_status, run_dir)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_ARTIFACT_SQL = """
INSERT OR REPLACE INTO artifacts (run_id, role, path, "exists", bytes, sha256)
VALUES (?, ?, ?, ?, ?, ?)
"""


class RunIndex:
    """Minimal SQLite index: two tables (``runs``, ``artifacts``), WAL mode."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # -- internal helpers ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
        except (OSError, sqlite3.Error) as exc:
            msg = f"Cannot open run index {self.db_path}: {exc}"
            raise StorageError(msg) from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        try:
            conn.executescript(_SCHEMA_SQL)
        except sqlite3.Error as exc:
            msg = f"Cannot initialize run index {self.db_path}: {exc}"
            raise StorageError(msg) from exc

    def _transact(self, statements: Sequence[tuple[str, tuple[Any, ...]]]) -> None:
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            with conn:
                for sql, params in statements:
                    conn.execute(sql, params)
        except sqlite3.Error as exc:
            msg = f"Run index write failed ({self.db_path}): {exc}"
            raise StorageError(msg) from exc
        finally:
            conn.close()

    # -- write API -------------------------------------------------------

    def upsert_run(
        self,
        *,
        run_id: str,
        status: str,
        created_at: str,
        started_at: str | None,
        finished_at: str | None,
        operation: str,
        engineering_status: str,
        run_dir: str,
    ) -> None:
        """Insert or replace one run row."""
        if status not in RUN_STATUSES:
            msg = f"Invalid run status: {status!r}"
            raise InvalidRequestError(msg)
        self._transact(
            [
                (
                    _INSERT_RUN_SQL,
                    (
                        run_id,
                        status,
                        created_at,
                        started_at,
                        finished_at,
                        operation,
                        engineering_status,
                        run_dir,
                    ),
                )
            ]
        )

    def record_artifact(self, run_id: str, record: FileRecord) -> None:
        """Insert or replace one artifact/input row for *run_id*."""
        self._transact(
            [
                (
                    _INSERT_ARTIFACT_SQL,
                    (
                        run_id,
                        record.role,
                        record.path,
                        int(record.exists),
                        record.bytes,
                        record.sha256,
                    ),
                )
            ]
        )

    def rebuild(self, runs_dir: Path) -> int:
        """Rebuild the entire index from ``runs/*/run.json`` (repair tool).

        Corrupt or schema-invalid run directories are skipped with a
        warning; they are never deleted. Returns the number of indexed
        runs.
        """
        statements: list[tuple[str, tuple[Any, ...]]] = [
            ("DELETE FROM artifacts", ()),
            ("DELETE FROM runs", ()),
        ]
        indexed = 0
        for run_json in sorted(runs_dir.glob("*/run.json")):
            envelope = self._load_valid_envelope(run_json)
            if envelope is None:
                continue
            provenance = envelope["provenance"]
            statements.append(
                (
                    _INSERT_RUN_SQL,
                    (
                        envelope["run_id"],
                        self._lifecycle_status(envelope),
                        provenance["created_at"],
                        provenance["created_at"],
                        provenance.get("finished_at"),
                        envelope["operation"],
                        envelope["engineering"]["status"],
                        f"runs/{envelope['run_id']}",
                    ),
                )
            )
            for record in [*envelope["inputs"], *envelope["artifacts"]]:
                statements.append(
                    (
                        _INSERT_ARTIFACT_SQL,
                        (
                            envelope["run_id"],
                            record["role"],
                            record["path"],
                            int(record["exists"]),
                            record.get("bytes"),
                            record.get("sha256"),
                        ),
                    )
                )
            indexed += 1
        self._transact(statements)
        return indexed

    # -- read API --------------------------------------------------------

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent runs (newest first), capped at *limit*."""
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            cursor = conn.execute(
                "SELECT run_id, status, operation, created_at FROM runs "
                "ORDER BY created_at DESC, run_id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            msg = f"Run index read failed ({self.db_path}): {exc}"
            raise StorageError(msg) from exc
        finally:
            conn.close()

    def query_runs(
        self, *, limit: int = 20, offset: int = 0, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Return one filtered page of runs (newest first).

        Read-only extension for the Platform API history endpoint (M6.0):
        same ordering as :meth:`list_runs`, plus optional lifecycle-status
        filtering and pagination. Existing callers are unaffected.

        Raises:
            InvalidRequestError: On a bad limit/offset or unknown status.
        """
        if limit < 1 or offset < 0:
            msg = f"limit must be >= 1 and offset >= 0, got limit={limit}, offset={offset}"
            raise InvalidRequestError(msg)
        if status is not None and status not in RUN_STATUSES:
            msg = f"Invalid run status: {status!r}"
            raise InvalidRequestError(msg)
        where = "WHERE status = ?" if status is not None else ""
        params: list[Any] = [status] if status is not None else []
        params.extend((limit, offset))
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            cursor = conn.execute(
                "SELECT run_id, status, operation, created_at, engineering_status "
                f"FROM runs {where} ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?",
                params,
            )
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            msg = f"Run index read failed ({self.db_path}): {exc}"
            raise StorageError(msg) from exc
        finally:
            conn.close()

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _load_valid_envelope(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            validate_result(data)
        except (OSError, ValueError, SchemaValidationError) as exc:
            logger.warning("Skipping %s during index rebuild: %s", path, exc)
            return None
        return data

    @staticmethod
    def _lifecycle_status(envelope: dict[str, Any]) -> str:
        """Run lifecycle state from provenance, with a documented fallback."""
        recorded = envelope["provenance"].get("run_status")
        if recorded in RUN_STATUSES:
            return recorded
        # Older/manual envelopes without run_status: map from execution
        # status; an unexecuted run maps to 'created', never to a guess.
        return {"success": "success", "failed": "failed"}.get(
            envelope["execution"]["status"], "created"
        )
