#!/usr/bin/env python3
"""Lean connector triage for Millennium/Quantum chains.

Dual tracking:
1) Intent track: files that are expected connector candidates (by keyword groups).
2) Reachability track: actual dependency chains to target modules using import graph.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from lean_proof_audit import LeanImportGraph


DEFAULT_TARGETS = ["Millennium.lean", "MillenniumCore.lean", "Quantum.lean"]
DEFAULT_SOURCE_KEYWORDS = [
    "Torus",
    "SU3",
    "MassGap",
    "Clay",
    "Gauge",
    "Continuum",
]
DEFAULT_CONNECTOR_KEYWORDS = [
    "Bridge",
    "Transport",
    "Packaging",
    "Core",
    "Route",
    "Program",
]


def shortest_path(imports: dict[str, list[str]], start: str, target: str) -> list[str] | None:
    """BFS shortest path from start to target along import edges."""
    queue: list[tuple[str, list[str]]] = [(start, [start])]
    visited = {start}
    while queue:
        node, path = queue.pop(0)
        if node == target:
            return path
        for nxt in imports.get(node, []):
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, path + [nxt]))
    return None


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(k.lower() in text.lower() for k in keywords)


def build_triage_report(project_root: Path, targets: list[str]) -> dict[str, Any]:
    graph = LeanImportGraph(str(project_root))
    imports = graph.get_import_association()
    files = sorted(imports.keys())

    source_candidates = [f for f in files if contains_any(f, DEFAULT_SOURCE_KEYWORDS)]
    connector_candidates = [f for f in files if contains_any(f, DEFAULT_CONNECTOR_KEYWORDS)]

    reachable: dict[str, dict[str, list[str]]] = {}
    unreachable: dict[str, list[str]] = {}

    for source in source_candidates:
        reachable[source] = {}
        missing: list[str] = []
        for target in targets:
            if target not in imports:
                missing.append(target)
                continue
            path = shortest_path(imports, source, target)
            if path is None:
                missing.append(target)
            else:
                reachable[source][target] = path
        if missing:
            unreachable[source] = missing

    # Connector centrality proxy: appears in many reachable paths.
    path_nodes: Counter[str] = Counter()
    for source_map in reachable.values():
        for path in source_map.values():
            # Count only internal nodes for connector signal.
            for node in path[1:-1]:
                path_nodes[node] += 1

    connector_rank = sorted(
        [
            {
                "file": node,
                "path_frequency": freq,
                "is_named_connector_candidate": node in connector_candidates,
            }
            for node, freq in path_nodes.items()
        ],
        key=lambda x: (-x["path_frequency"], x["file"]),
    )

    triage_rows: list[dict[str, Any]] = []
    for source in source_candidates:
        row = {
            "source": source,
            "intended_role": "source_cluster_candidate",
            "has_any_chain": bool(reachable.get(source)),
            "reachable_targets": sorted(list(reachable.get(source, {}).keys())),
            "missing_targets": sorted(unreachable.get(source, [])),
            "next_action": "promote_bridge" if unreachable.get(source) else "stabilize_api",
        }
        triage_rows.append(row)

    summary = {
        "total_files": len(files),
        "source_candidates": len(source_candidates),
        "connector_candidates": len(connector_candidates),
        "sources_with_any_chain": sum(1 for s in source_candidates if reachable.get(s)),
        "sources_missing_some_targets": sum(1 for s in source_candidates if unreachable.get(s)),
        "targets": targets,
    }

    return {
        "summary": summary,
        "triage_rows": triage_rows,
        "reachable_paths": reachable,
        "unreachable_targets": unreachable,
        "connector_rank": connector_rank[:30],
        "source_candidates": source_candidates,
        "connector_candidates": connector_candidates,
    }


def to_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines: list[str] = []
    lines.append("# Lean Connector Dual Tracking Report")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Total files: {summary['total_files']}")
    lines.append(f"- Source candidates: {summary['source_candidates']}")
    lines.append(f"- Connector candidates: {summary['connector_candidates']}")
    lines.append(f"- Sources with any chain: {summary['sources_with_any_chain']}")
    lines.append(f"- Sources missing some targets: {summary['sources_missing_some_targets']}")
    lines.append(f"- Targets: {', '.join(summary['targets'])}")
    lines.append("")
    lines.append("## Dual Tracking Rows")
    for row in report["triage_rows"]:
        lines.append(
            f"- {row['source']}: has_chain={row['has_any_chain']}, "
            f"reachable={row['reachable_targets']}, missing={row['missing_targets']}, "
            f"next={row['next_action']}"
        )
    lines.append("")
    lines.append("## Connector Rank (Top 15)")
    for item in report["connector_rank"][:15]:
        lines.append(
            f"- {item['file']}: path_frequency={item['path_frequency']}, "
            f"named_connector={item['is_named_connector_candidate']}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Dual-track connector triage for Lean chains.")
    parser.add_argument("--project-root", default=None, help="Root containing src and Lean_Proof_Map.txt")
    parser.add_argument(
        "--targets",
        default=",".join(DEFAULT_TARGETS),
        help="Comma-separated target modules",
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--write", default=None, help="Optional path to write output")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve() if args.project_root else Path.cwd().resolve()
    targets = [x.strip() for x in args.targets.split(",") if x.strip()]

    report = build_triage_report(project_root, targets)
    payload = to_markdown(report) if args.format == "markdown" else json.dumps(report, indent=2)

    if args.write:
        path = Path(args.write)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
