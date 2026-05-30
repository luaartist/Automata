#!/usr/bin/env python3
"""Backfill helpers for the mapping/audit context schema.

The functions in this module intentionally use synchronous psycopg, matching
the parser scanner and worker code paths. They are safe to call as a one-shot
CLI or from live ingestion code after local import edges are rebuilt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

DEFAULT_DATABASE_URL = "postgresql://lean4:lean4_dev_password@localhost:5432/lean4_automata"
DEFAULT_PROJECT_ID = 1

_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DEFAULT_LAKE_PACKAGE_PREFIXES = frozenset(
    {
        "Aesop",
        "Batteries",
        "Cli",
        "ImportGraph",
        "Lean",
        "LeanSearchClient",
        "ProofWidgets",
        "Qq",
        "Std",
    }
)


@dataclass(frozen=True, slots=True)
class ImportSource:
    """Minimal source row needed to rebuild external import references."""

    file_id: int
    module_name: str
    relative_path: str
    imports: tuple[str, ...]


def normalise_psycopg_dsn(url: str) -> str:
    """Return a psycopg3-compatible DSN from common SQLAlchemy URL variants."""
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://") :]
    return url


def generate_ticket_id() -> str:
    """Generate a ULID-shaped ticket id without adding a runtime dependency."""
    timestamp_ms = int(time.time() * 1000)
    random_bits = random.getrandbits(80)
    value = (timestamp_ms << 80) | random_bits
    chars: list[str] = []
    for shift in range(125, -1, -5):
        chars.append(_CROCKFORD32[(value >> shift) & 0b11111])
    return "tk_" + "".join(chars)


def collect_lake_package_prefixes(project_root: Path) -> set[str]:
    """Discover import prefixes from nearby Lake package directories.

    The development checkout has the Lean source root and package caches in
    different places in different environments, so this scans the project root,
    its parent, and common Lake cache directories. It returns top-level Lean
    module directories/files that can be treated as known non-project imports.
    """
    root = Path(project_root).resolve()
    candidates = [
        root / "lake-packages",
        root / ".lake" / "packages",
        root.parent / "lake-packages",
        root.parent / ".lake" / "packages",
    ]

    prefixes: set[str] = set(_DEFAULT_LAKE_PACKAGE_PREFIXES)
    for package_dir in candidates:
        if not package_dir.exists() or not package_dir.is_dir():
            continue
        for package_root in package_dir.iterdir():
            if not package_root.is_dir():
                continue
            prefixes.update(_lean_prefixes_in_package(package_root))
    prefixes.discard("Mathlib")
    return prefixes


def _lean_prefixes_in_package(package_root: Path) -> set[str]:
    prefixes: set[str] = set()
    for child in package_root.iterdir():
        name = child.stem if child.is_file() and child.suffix == ".lean" else child.name
        if not name or name.startswith(".") or name in {"lake-packages", "build", "test", "tests"}:
            continue
        if child.is_dir() and any(child.rglob("*.lean")):
            prefixes.add(name)
        elif child.is_file() and child.suffix == ".lean":
            prefixes.add(name)
    return prefixes


def classify_import(import_name: str, package_prefixes: Iterable[str]) -> tuple[str, str | None]:
    """Classify an unresolved Lean import for ``lean_import_references``."""
    if import_name == "Mathlib" or import_name.startswith("Mathlib."):
        return "mathlib", "mathlib"

    top_level = import_name.split(".", 1)[0]
    if top_level in package_prefixes:
        return "lake_package", top_level

    return "unknown", None


def replace_external_import_references(
    cur: psycopg.Cursor,
    *,
    project_id: int,
    import_sources: Sequence[ImportSource],
    module_to_file_id: Mapping[str, int],
    package_prefixes: Iterable[str],
    provenance: str,
) -> int:
    """Replace external/non-project import references for the given source files."""
    if not import_sources:
        return 0

    source_file_ids = [source.file_id for source in import_sources]
    cur.execute(
        """
        DELETE FROM lean_import_references
         WHERE project_id = %s AND source_file_id = ANY(%s)
        """,
        (project_id, source_file_ids),
    )

    package_prefix_set = set(package_prefixes)
    rows: list[tuple[int, int, str, str, str | None, str, str]] = []
    seen: set[tuple[int, str]] = set()
    for source in import_sources:
        for import_name in source.imports:
            if module_to_file_id.get(import_name) is not None:
                continue
            key = (source.file_id, import_name)
            if key in seen:
                continue
            seen.add(key)
            target_scope, package_name = classify_import(import_name, package_prefix_set)
            metadata = {
                "source_module": source.module_name,
                "source_relative_path": source.relative_path,
            }
            rows.append(
                (
                    project_id,
                    source.file_id,
                    import_name,
                    target_scope,
                    package_name,
                    provenance,
                    json.dumps(metadata),
                )
            )

    if not rows:
        return 0

    cur.executemany(
        """
        INSERT INTO lean_import_references
            (project_id, source_file_id, import_name, target_scope,
             package_name, is_resolved, provenance, metadata, indexed_at)
        VALUES (%s, %s, %s, %s, %s, false, %s, %s::jsonb, now())
        ON CONFLICT (project_id, source_file_id, import_name) DO UPDATE
           SET target_scope = EXCLUDED.target_scope,
               package_name = EXCLUDED.package_name,
               is_resolved = false,
               provenance = EXCLUDED.provenance,
               metadata = EXCLUDED.metadata,
               indexed_at = now()
        """,
        rows,
    )
    return len(rows)


def backfill_external_import_references(
    conn: psycopg.Connection,
    *,
    project_id: int,
    project_root: Path,
) -> int:
    """Backfill ``lean_import_references`` from existing ``files.imports`` rows."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, module_name, relative_path, imports
              FROM files
             WHERE project_id = %s
             ORDER BY id
            """,
            (project_id,),
        )
        file_rows = cur.fetchall()
        cur.execute("SELECT module_name, id FROM files WHERE project_id = %s", (project_id,))
        module_to_file_id = {
            str(row["module_name"]): int(row["id"])
            for row in cur.fetchall()
        }

    import_sources = [_import_source_from_file_row(row) for row in file_rows]
    with conn.cursor() as cur:
        inserted = replace_external_import_references(
            cur,
            project_id=project_id,
            import_sources=import_sources,
            module_to_file_id=module_to_file_id,
            package_prefixes=collect_lake_package_prefixes(project_root),
            provenance="backfill:files.imports",
        )
    conn.commit()
    return inserted


def _import_source_from_file_row(row: Mapping[str, Any]) -> ImportSource:
    imports = row["imports"]
    if isinstance(imports, str):
        imports = json.loads(imports)
    return ImportSource(
        file_id=int(row["id"]),
        module_name=str(row["module_name"]),
        relative_path=str(row["relative_path"]),
        imports=tuple(str(item) for item in imports or []),
    )


def backfill_wolfram_companion_maps(
    conn: psycopg.Connection,
    *,
    project_id: int,
    project_root: Path,
    wolfram_dir: Path,
) -> int:
    """Backfill ``wolfram_companion_maps`` from ``*.wl`` companion files."""
    maps_dir = Path(wolfram_dir).resolve()
    if not maps_dir.exists():
        return 0

    map_paths = sorted(maps_dir.glob("*.wl"))
    if not map_paths:
        return 0

    module_names = [path.stem for path in map_paths]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT module_name, id
              FROM files
             WHERE project_id = %s AND module_name = ANY(%s)
            """,
            (project_id, module_names),
        )
        module_to_file_id = {str(module): int(file_id) for module, file_id in cur.fetchall()}

    rows: list[tuple[int, int | None, str, str, str, str, str, str, datetime]] = []
    for path in map_paths:
        raw = path.read_bytes()
        raw_text = raw.decode("utf-8", errors="replace")
        module_name = path.stem
        metadata = {
            "source": "backfill:wolfram_lean_maps",
            "size_bytes": len(raw),
            "line_count": raw_text.count("\n") + (1 if raw_text else 0),
        }
        rows.append(
            (
                project_id,
                module_to_file_id.get(module_name),
                module_name,
                _relative_display_path(path, project_root),
                hashlib.sha256(raw).hexdigest(),
                raw_text,
                json.dumps(metadata),
                json.dumps({}),
                datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
            )
        )

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO wolfram_companion_maps
                (project_id, file_id, module_name, wolfram_path, file_hash,
                 raw_text, metadata, parsed_map, llm_synthesis, indexed_at, last_modified)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, '{}'::jsonb, now(), %s)
            ON CONFLICT (project_id, module_name, wolfram_path) DO UPDATE
               SET file_id = EXCLUDED.file_id,
                   file_hash = EXCLUDED.file_hash,
                   raw_text = EXCLUDED.raw_text,
                   metadata = EXCLUDED.metadata,
                   parsed_map = EXCLUDED.parsed_map,
                   indexed_at = now(),
                   last_modified = EXCLUDED.last_modified
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def _relative_display_path(path: Path, base: Path) -> str:
    return os.path.relpath(path.resolve(), Path(base).resolve()).replace(os.sep, "/")


def seed_proof_obligations(conn: psycopg.Connection, *, project_id: int) -> dict[str, int]:
    """Seed proof obligations from current mapping/audit state."""
    with conn.cursor() as cur:
        resolved_stale = _resolve_stale_proof_obligations(cur, project_id)
        unresolved_imports = _seed_unresolved_import_obligations(cur, project_id)
        missing_wolfram_maps = _seed_missing_wolfram_map_obligations(cur, project_id)
        failed_audit_tickets = _seed_failed_audit_ticket_obligations(cur, project_id)
    conn.commit()
    return {
        "resolved_stale": resolved_stale,
        "unresolved_imports": unresolved_imports,
        "missing_wolfram_maps": missing_wolfram_maps,
        "failed_audit_tickets": failed_audit_tickets,
    }


def _resolve_stale_proof_obligations(cur: psycopg.Cursor, project_id: int) -> int:
    total = 0
    cur.execute(
        """
        UPDATE proof_obligations po
           SET status = 'resolved', resolved_at = now()
         WHERE po.project_id = %s
           AND po.obligation_type = 'unresolved_import'
           AND po.status IN ('open', 'acknowledged')
           AND NOT EXISTS (
               SELECT 1
                 FROM lean_import_references lir
                WHERE lir.project_id = po.project_id
                  AND lir.source_file_id = po.file_id
                  AND lir.import_name = po.evidence->>'import_name'
                  AND lir.is_resolved = false
                  AND lir.target_scope = 'unknown'
           )
        """,
        (project_id,),
    )
    total += cur.rowcount if cur.rowcount is not None else 0

    cur.execute(
        """
        UPDATE proof_obligations po
           SET status = 'resolved', resolved_at = now()
         WHERE po.project_id = %s
           AND po.obligation_type = 'missing_wolfram_map'
           AND po.status IN ('open', 'acknowledged')
           AND EXISTS (
               SELECT 1
                 FROM wolfram_companion_maps wcm
                WHERE wcm.project_id = po.project_id
                  AND (
                      wcm.file_id = po.file_id
                      OR wcm.module_name = po.module_name
                      OR wcm.module_name = po.evidence->>'module_name'
                  )
           )
        """,
        (project_id,),
    )
    total += cur.rowcount if cur.rowcount is not None else 0

    cur.execute(
        """
        UPDATE proof_obligations po
           SET status = 'resolved', resolved_at = now()
         WHERE po.project_id = %s
           AND po.obligation_type = 'failed_audit_ticket'
           AND po.status IN ('open', 'acknowledged')
           AND EXISTS (
               SELECT 1
                 FROM audit_tickets at
                WHERE at.ticket_id = po.ticket_id
                  AND at.status NOT IN ('error', 'timeout')
           )
        """,
        (project_id,),
    )
    total += cur.rowcount if cur.rowcount is not None else 0
    return total


def _seed_unresolved_import_obligations(cur: psycopg.Cursor, project_id: int) -> int:
    cur.execute(
        """
        INSERT INTO proof_obligations
            (project_id, file_id, module_name, obligation_type, severity,
             status, title, details, evidence)
        SELECT
            lir.project_id,
            lir.source_file_id,
            f.module_name,
            'unresolved_import',
            CASE WHEN lir.target_scope = 'unknown' THEN 'high' ELSE 'medium' END,
            'open',
            'Unresolved Lean import: ' || lir.import_name,
            'Import does not resolve to a project-local file row.',
            jsonb_build_object(
                'import_reference_id', lir.id,
                'import_name', lir.import_name,
                'target_scope', lir.target_scope,
                'source_module', f.module_name,
                'source_relative_path', f.relative_path
            )
        FROM lean_import_references lir
        JOIN files f ON f.id = lir.source_file_id
        WHERE lir.project_id = %s
          AND lir.is_resolved = false
          AND lir.target_scope = 'unknown'
          AND NOT EXISTS (
              SELECT 1
                FROM proof_obligations po
               WHERE po.project_id = lir.project_id
                 AND po.file_id = lir.source_file_id
                 AND po.obligation_type = 'unresolved_import'
                 AND po.status IN ('open', 'acknowledged')
                 AND po.evidence->>'import_name' = lir.import_name
          )
        """,
        (project_id,),
    )
    return cur.rowcount if cur.rowcount is not None else 0


def _seed_missing_wolfram_map_obligations(cur: psycopg.Cursor, project_id: int) -> int:
    cur.execute(
        """
        INSERT INTO proof_obligations
            (project_id, file_id, module_name, obligation_type, severity,
             status, title, details, evidence)
        SELECT
            f.project_id,
            f.id,
            f.module_name,
            'missing_wolfram_map',
            'medium',
            'open',
            'Missing Wolfram companion map for ' || f.module_name,
            'No wolfram_companion_maps row is linked to this Lean module.',
            jsonb_build_object(
                'module_name', f.module_name,
                'relative_path', f.relative_path,
                'file_id', f.id
            )
        FROM files f
        WHERE f.project_id = %s
          AND NOT EXISTS (
              SELECT 1
                FROM wolfram_companion_maps wcm
               WHERE wcm.project_id = f.project_id
                 AND (wcm.file_id = f.id OR wcm.module_name = f.module_name)
          )
          AND NOT EXISTS (
              SELECT 1
                FROM proof_obligations po
               WHERE po.project_id = f.project_id
                 AND po.file_id = f.id
                 AND po.obligation_type = 'missing_wolfram_map'
                 AND po.status IN ('open', 'acknowledged')
          )
        """,
        (project_id,),
    )
    return cur.rowcount if cur.rowcount is not None else 0


def _seed_failed_audit_ticket_obligations(cur: psycopg.Cursor, project_id: int) -> int:
    cur.execute(
        """
        INSERT INTO proof_obligations
            (project_id, ticket_id, obligation_type, severity,
             status, title, details, evidence)
        SELECT
            at.project_id,
            at.ticket_id,
            'failed_audit_ticket',
            'high',
            'open',
            'Failed audit flow: ' || at.flow_name,
            COALESCE(at.error_message, 'Audit flow did not complete successfully.'),
            jsonb_build_object(
                'ticket_id', at.ticket_id,
                'flow_name', at.flow_name,
                'status', at.status,
                'output_json', at.output_json
            )
        FROM audit_tickets at
        WHERE at.project_id = %s
          AND at.status IN ('error', 'timeout')
          AND NOT EXISTS (
              SELECT 1
                FROM proof_obligations po
               WHERE po.ticket_id = at.ticket_id
                 AND po.obligation_type = 'failed_audit_ticket'
                 AND po.status IN ('open', 'acknowledged')
          )
        """,
        (project_id,),
    )
    return cur.rowcount if cur.rowcount is not None else 0


def persist_audit_ticket_result(
    *,
    database_url: str,
    flow_name: str,
    params: Mapping[str, Any],
    result: dict[str, Any],
    duration_ms: int | None = None,
    project_id: int | None = None,
    origin: str = "fastapi",
) -> str | None:
    """Persist an audit flow result into ``audit_tickets``.

    The function is intentionally non-fatal for callers: missing database URLs
    return ``None`` and database errors are left to the caller to log/handle.
    """
    if not database_url:
        return None

    ticket = result.get("ticket") if isinstance(result.get("ticket"), dict) else {}
    metadata = dict(ticket.get("metadata") or {})
    ticket_id = str(ticket.get("ticket_id") or metadata.get("ticket_id") or generate_ticket_id())
    metadata["ticket_id"] = ticket_id
    metadata["origin"] = origin
    if ticket:
        ticket["metadata"] = metadata
        ticket.setdefault("ticket_id", ticket_id)
        result["ticket"] = ticket

    status = _audit_status(result)
    ticket_type = str(ticket.get("type") or "lean_audit")
    title = ticket.get("title") or f"Audit flow {flow_name}"
    description = ticket.get("description") or result.get("error")
    tags = [origin, flow_name, ticket_type]

    with psycopg.connect(normalise_psycopg_dsn(database_url), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_tickets
                    (ticket_id, project_id, flow_name, ticket_type, title,
                     description, input_json, output_json, status, error_message,
                     duration_ms, tags, metadata, completed_at)
                VALUES (%s, %s, %s, %s, %s,
                        %s, %s::jsonb, %s::jsonb, %s, %s,
                        %s, %s::jsonb, %s::jsonb, now())
                ON CONFLICT (ticket_id) DO UPDATE
                   SET project_id = EXCLUDED.project_id,
                       flow_name = EXCLUDED.flow_name,
                       ticket_type = EXCLUDED.ticket_type,
                       title = EXCLUDED.title,
                       description = EXCLUDED.description,
                       input_json = EXCLUDED.input_json,
                       output_json = EXCLUDED.output_json,
                       status = EXCLUDED.status,
                       error_message = EXCLUDED.error_message,
                       duration_ms = EXCLUDED.duration_ms,
                       tags = EXCLUDED.tags,
                       metadata = EXCLUDED.metadata,
                       completed_at = EXCLUDED.completed_at
                """,
                (
                    ticket_id,
                    project_id,
                    flow_name,
                    ticket_type,
                    title,
                    description,
                    json.dumps(dict(params)),
                    json.dumps(result),
                    status,
                    result.get("error") if status != "success" else None,
                    duration_ms,
                    json.dumps(tags),
                    json.dumps(metadata),
                ),
            )
    return ticket_id


def _audit_status(result: Mapping[str, Any]) -> str:
    status = str(result.get("status") or "error")
    if status == "ok":
        return "success"
    if "timeout" in str(result.get("error") or "").lower():
        return "timeout"
    return "error"


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    """Return row counts for the mapping/audit context tables."""
    tables = (
        "lean_import_references",
        "wolfram_companion_maps",
        "audit_tickets",
        "proof_obligations",
        "ots_stamps",
        "l2bl4_badges",
    )
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            row = cur.fetchone()
            counts[table] = int(row[0]) if row is not None else 0
    return counts


def _assert_project_exists(conn: psycopg.Connection, project_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,))
        if cur.fetchone() is None:
            raise RuntimeError(f"project_id={project_id} does not exist in projects")


def run_backfill(
    *,
    database_url: str,
    project_id: int,
    project_root: Path,
    wolfram_dir: Path,
    include_external_imports: bool = True,
    include_wolfram_maps: bool = True,
    include_proof_obligations: bool = True,
) -> dict[str, Any]:
    """Run the selected backfill steps and return a JSON-serializable summary."""
    with psycopg.connect(normalise_psycopg_dsn(database_url), autocommit=False) as conn:
        _assert_project_exists(conn, project_id)
        result: dict[str, Any] = {"project_id": project_id, "steps": {}}
        if include_external_imports:
            result["steps"]["lean_import_references"] = backfill_external_import_references(
                conn,
                project_id=project_id,
                project_root=project_root,
            )
        if include_wolfram_maps:
            result["steps"]["wolfram_companion_maps"] = backfill_wolfram_companion_maps(
                conn,
                project_id=project_id,
                project_root=project_root,
                wolfram_dir=wolfram_dir,
            )
        if include_proof_obligations:
            result["steps"]["proof_obligations"] = seed_proof_obligations(
                conn,
                project_id=project_id,
            )
        result["table_counts"] = table_counts(conn)
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="Postgres URL. Defaults to DATABASE_URL or local dev Postgres.",
    )
    parser.add_argument(
        "--project-id",
        type=int,
        default=int(os.getenv("LEAN_PROJECT_ID", str(DEFAULT_PROJECT_ID))),
        help="Integer projects.id to backfill.",
    )
    parser.add_argument("--project-root", type=Path, default=default_root)
    parser.add_argument("--wolfram-dir", type=Path, default=default_root.parent / "wolfram_lean_maps")
    parser.add_argument(
        "--only",
        action="append",
        choices=("external-imports", "wolfram-maps", "proof-obligations"),
        help="Run only the selected step. May be passed multiple times.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    only = set(args.only or [])
    result = run_backfill(
        database_url=args.database_url,
        project_id=args.project_id,
        project_root=args.project_root,
        wolfram_dir=args.wolfram_dir,
        include_external_imports=not only or "external-imports" in only,
        include_wolfram_maps=not only or "wolfram-maps" in only,
        include_proof_obligations=not only or "proof-obligations" in only,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()