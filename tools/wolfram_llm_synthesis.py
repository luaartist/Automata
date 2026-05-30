#!/usr/bin/env python3
"""Optional Wolfram LLMSynthesize enrichment for companion maps.

This utility is intentionally opt-in. It is not imported by the FastAPI audit
routes or the forum flow dispatcher, and it never makes Wolfram required for
core Lean audit flows. When ``wolframscript`` is unavailable, it returns a JSON
summary and exits without opening the database.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

DEFAULT_DATABASE_URL = "postgresql://lean4:lean4_dev_password@localhost:5432/lean4_automata"
DEFAULT_PROJECT_ID = 1
DEFAULT_LIMIT = 10
DEFAULT_MAX_RAW_CHARS = 12_000
DEFAULT_TIMEOUT_SECONDS = 60
PROMPT_VERSION = "wolfram-llm-synthesis-v1"
SOURCE_NAME = "tools/wolfram_llm_synthesis.py"


@dataclass(frozen=True, slots=True)
class CompanionMapRow:
    id: int
    module_name: str
    wolfram_path: str
    file_hash: str | None
    raw_text: str


def normalise_psycopg_dsn(url: str) -> str:
    """Return a psycopg3-compatible DSN from common SQLAlchemy URL variants."""
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://") :]
    return url


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_wolframscript(command: str) -> str | None:
    """Resolve a wolframscript executable name or explicit path."""
    expanded = os.path.expanduser(command)
    looks_like_path = os.path.isabs(expanded) or os.sep in expanded
    if looks_like_path:
        return expanded if os.path.isfile(expanded) and os.access(expanded, os.X_OK) else None
    return shutil.which(expanded)


def build_prompt_payload(row: CompanionMapRow, *, max_raw_chars: int, prompt_version: str) -> dict[str, Any]:
    raw_text = row.raw_text or ""
    return {
        "map_id": row.id,
        "module_name": row.module_name,
        "wolfram_path": row.wolfram_path,
        "file_hash": row.file_hash,
        "raw_text": raw_text[:max_raw_chars],
        "raw_text_chars": len(raw_text),
        "raw_text_truncated": len(raw_text) > max_raw_chars,
        "prompt_version": prompt_version,
    }


def build_wolfram_code(payload: Mapping[str, Any]) -> str:
    """Build Wolfram code that runs LLMSynthesize and prints RawJSON."""
    payload_json = json.dumps(dict(payload), ensure_ascii=True, sort_keys=True)
    byte_values = ",".join(str(byte) for byte in payload_json.encode("ascii"))
    template = r'''
inputJson = FromCharacterCode[{__BYTE_VALUES__}];
input = Quiet[Check[ImportString[inputJson, "RawJSON"], <||>]];
prompt = StringRiffle[
  {
    "You are assisting a Lean/Wolfram proof-map audit. Synthesize the companion map for audit triage.",
    "Return concise plain text with: Lean module role, Wolfram objects, likely proof obligations, drift risks, and next verification checks.",
    "Module: " <> ToString[Lookup[input, "module_name", ""]],
    "Wolfram path: " <> ToString[Lookup[input, "wolfram_path", ""]],
    "File hash: " <> ToString[Lookup[input, "file_hash", ""]],
    "Raw Wolfram companion map:",
    ToString[Lookup[input, "raw_text", ""]]
  },
  "\n\n"
];
result = If[
  !NameQ["System`LLMSynthesize"],
  <|
    "status" -> "llm_unavailable",
    "error" -> "LLMSynthesize is not available in this Wolfram kernel.",
    "prompt_version" -> Lookup[input, "prompt_version", "unknown"]
  |>,
  Module[{synthesis},
    synthesis = Quiet[Check[LLMSynthesize[prompt], $Failed]];
    If[
      synthesis === $Failed,
      <|
        "status" -> "llm_error",
        "error" -> "LLMSynthesize returned $Failed.",
        "prompt_version" -> Lookup[input, "prompt_version", "unknown"]
      |>,
      <|
        "status" -> "ok",
        "text" -> ToString[synthesis],
        "prompt_version" -> Lookup[input, "prompt_version", "unknown"]
      |>
    ]
  ]
];
Print[ExportString[result, "RawJSON"]];
'''
    return template.replace("__BYTE_VALUES__", byte_values).strip()


def parse_wolfram_stdout(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse RawJSON from wolframscript stdout, tolerating leading messages."""
    text = stdout.strip()
    if not text:
        return None, "wolframscript stdout was empty"

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None, None if isinstance(parsed, dict) else "stdout JSON was not an object"
    except json.JSONDecodeError:
        pass

    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else None, None if isinstance(parsed, dict) else "stdout JSON was not an object"

    return None, "wolframscript stdout did not contain a JSON object"


def clip_output(value: str | bytes | None, *, limit: int = 4_000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def synthesize_row(
    row: CompanionMapRow,
    *,
    wolframscript_path: str,
    timeout_seconds: int,
    max_raw_chars: int,
    prompt_version: str,
) -> dict[str, Any]:
    payload = build_prompt_payload(row, max_raw_chars=max_raw_chars, prompt_version=prompt_version)
    started_at = time.perf_counter()
    base = {
        "source": SOURCE_NAME,
        "status": "pending",
        "synthesized_at": utc_now_iso(),
        "prompt_version": prompt_version,
        "input": {
            "map_id": row.id,
            "module_name": row.module_name,
            "wolfram_path": row.wolfram_path,
            "file_hash": row.file_hash,
            "raw_text_chars": payload["raw_text_chars"],
            "raw_text_truncated": payload["raw_text_truncated"],
        },
        "wolfram": {
            "executable": wolframscript_path,
            "timeout_seconds": timeout_seconds,
        },
    }

    try:
        completed = subprocess.run(
            [wolframscript_path, "-code", build_wolfram_code(payload)],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return {
            **base,
            "status": "wolfram_unavailable",
            "error": str(exc),
            "wolfram": {
                **base["wolfram"],
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
            },
        }
    except subprocess.TimeoutExpired as exc:
        return {
            **base,
            "status": "timeout",
            "error": f"wolframscript exceeded {timeout_seconds}s timeout",
            "stdout_snippet": clip_output(exc.stdout),
            "stderr_snippet": clip_output(exc.stderr),
            "wolfram": {
                **base["wolfram"],
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
            },
        }

    duration_ms = int((time.perf_counter() - started_at) * 1000)
    wolfram_metadata = {
        **base["wolfram"],
        "duration_ms": duration_ms,
        "returncode": completed.returncode,
    }
    if completed.returncode != 0:
        return {
            **base,
            "status": "wolfram_error",
            "stdout_snippet": clip_output(completed.stdout),
            "stderr_snippet": clip_output(completed.stderr),
            "wolfram": wolfram_metadata,
        }

    parsed, parse_error = parse_wolfram_stdout(completed.stdout)
    if parsed is None:
        return {
            **base,
            "status": "parse_error",
            "error": parse_error,
            "stdout_snippet": clip_output(completed.stdout),
            "stderr_snippet": clip_output(completed.stderr),
            "wolfram": wolfram_metadata,
        }

    wolfram_status = str(parsed.get("status") or "unknown")
    return {
        **base,
        "status": "ok" if wolfram_status == "ok" else wolfram_status,
        "text": parsed.get("text"),
        "wolfram_result": parsed,
        "stderr_snippet": clip_output(completed.stderr),
        "wolfram": wolfram_metadata,
    }


def fetch_companion_maps(
    conn: Any,
    *,
    project_id: int,
    modules: Sequence[str],
    limit: int | None,
    force: bool,
) -> list[CompanionMapRow]:
    from psycopg.rows import dict_row

    conditions = ["project_id = %s"]
    params: list[Any] = [project_id]
    if not force:
        conditions.append("llm_synthesis = '{}'::jsonb")
    if modules:
        conditions.append("module_name = ANY(%s)")
        params.append(list(modules))

    limit_clause = ""
    if limit is not None:
        limit_clause = " LIMIT %s"
        params.append(limit)

    query = f"""
        SELECT id, module_name, wolfram_path, file_hash, COALESCE(raw_text, '') AS raw_text
          FROM wolfram_companion_maps
         WHERE {' AND '.join(conditions)}
         ORDER BY module_name, wolfram_path
        {limit_clause}
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return [
            CompanionMapRow(
                id=int(row["id"]),
                module_name=str(row["module_name"]),
                wolfram_path=str(row["wolfram_path"]),
                file_hash=str(row["file_hash"]) if row["file_hash"] is not None else None,
                raw_text=str(row["raw_text"] or ""),
            )
            for row in cur.fetchall()
        ]


def persist_synthesis(conn: Any, *, map_id: int, synthesis: Mapping[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE wolfram_companion_maps
               SET llm_synthesis = %s::jsonb,
                   indexed_at = now()
             WHERE id = %s
            """,
            (json.dumps(dict(synthesis), sort_keys=True), map_id),
        )


def connect_psycopg(database_url: str) -> Any:
    import psycopg

    return psycopg.connect(normalise_psycopg_dsn(database_url), autocommit=False)


def unavailable_summary(*, project_id: int, wolframscript: str, resolved_wolframscript: str | None) -> dict[str, Any]:
    return {
        "source": SOURCE_NAME,
        "status": "wolfram_unavailable",
        "project_id": project_id,
        "wolframscript": wolframscript,
        "resolved_wolframscript": resolved_wolframscript,
        "attempted": 0,
        "updated": 0,
        "succeeded": 0,
        "message": "wolframscript executable was not found; no database connection was opened.",
    }


def run_synthesis(
    *,
    database_url: str,
    project_id: int,
    wolframscript: str,
    modules: Sequence[str] = (),
    limit: int | None = DEFAULT_LIMIT,
    force: bool = False,
    dry_run: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_raw_chars: int = DEFAULT_MAX_RAW_CHARS,
    prompt_version: str = PROMPT_VERSION,
) -> dict[str, Any]:
    resolved_wolframscript = None if dry_run else resolve_wolframscript(wolframscript)
    if not dry_run and resolved_wolframscript is None:
        return unavailable_summary(
            project_id=project_id,
            wolframscript=wolframscript,
            resolved_wolframscript=resolved_wolframscript,
        )

    with connect_psycopg(database_url) as conn:
        rows = fetch_companion_maps(conn, project_id=project_id, modules=modules, limit=limit, force=force)
        if dry_run:
            return {
                "source": SOURCE_NAME,
                "status": "dry_run",
                "project_id": project_id,
                "would_attempt": len(rows),
                "force": force,
                "rows": [
                    {
                        "id": row.id,
                        "module_name": row.module_name,
                        "wolfram_path": row.wolfram_path,
                        "raw_text_chars": len(row.raw_text or ""),
                    }
                    for row in rows
                ],
            }

        results: list[dict[str, Any]] = []
        for row in rows:
            synthesis = synthesize_row(
                row,
                wolframscript_path=str(resolved_wolframscript),
                timeout_seconds=timeout_seconds,
                max_raw_chars=max_raw_chars,
                prompt_version=prompt_version,
            )
            persist_synthesis(conn, map_id=row.id, synthesis=synthesis)
            results.append(
                {
                    "id": row.id,
                    "module_name": row.module_name,
                    "wolfram_path": row.wolfram_path,
                    "status": synthesis.get("status"),
                }
            )
        conn.commit()

    succeeded = sum(1 for result in results if result.get("status") == "ok")
    status = "no_rows" if not rows else "ok" if succeeded == len(results) else "completed_with_errors"
    return {
        "source": SOURCE_NAME,
        "status": status,
        "project_id": project_id,
        "wolframscript": wolframscript,
        "resolved_wolframscript": resolved_wolframscript,
        "attempted": len(results),
        "updated": len(results),
        "succeeded": succeeded,
        "force": force,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="Postgres URL. Defaults to DATABASE_URL or local dev Postgres.",
    )
    parser.add_argument(
        "--project-id",
        type=int,
        default=int(os.getenv("LEAN_PROJECT_ID", str(DEFAULT_PROJECT_ID))),
        help="Integer projects.id to enrich.",
    )
    parser.add_argument(
        "--wolframscript",
        default=os.getenv("WOLFRAMSCRIPT", "wolframscript"),
        help="wolframscript executable name or absolute path.",
    )
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        help="Restrict to a module_name. May be passed multiple times.",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum rows to process. Use 0 for no limit.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="wolframscript timeout in seconds.")
    parser.add_argument("--max-raw-chars", type=int, default=DEFAULT_MAX_RAW_CHARS, help="Raw map text chars sent to LLMSynthesize.")
    parser.add_argument("--force", action="store_true", help="Reprocess rows even when llm_synthesis is already populated.")
    parser.add_argument("--dry-run", action="store_true", help="List rows that would be processed without invoking Wolfram.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_synthesis(
            database_url=args.database_url,
            project_id=args.project_id,
            wolframscript=args.wolframscript,
            modules=tuple(args.module),
            limit=None if args.limit == 0 else args.limit,
            force=args.force,
            dry_run=args.dry_run,
            timeout_seconds=args.timeout,
            max_raw_chars=args.max_raw_chars,
        )
    except Exception as exc:  # pragma: no cover - CLI guardrail
        result = {
            "source": SOURCE_NAME,
            "status": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())