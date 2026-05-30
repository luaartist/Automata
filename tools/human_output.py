#!/usr/bin/env python3
"""Plain-text summaries for Lean4-Automata flow results."""

from __future__ import annotations

import re
from typing import Any


_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def format_flow_result(flow_name: str, result: dict[str, Any]) -> str:
    """Render a forum flow result as readable plain text."""
    status = str(result.get("status", "unknown"))
    lines = ["Flow Result", f"Flow: {flow_name}", f"Status: {status}"]

    ticket = result.get("ticket")
    if isinstance(ticket, dict):
        lines.extend(
            [
                "",
                "Ticket",
                f"Title: {ticket.get('title', 'untitled')}",
                f"Type: {ticket.get('type', 'unknown')}",
                f"Created: {ticket.get('created_at', 'unknown')}",
            ]
        )

    if status != "ok":
        lines.extend(["", "Error", str(result.get("error", result))])
        return _render_lines(lines)

    if "audit" in result and isinstance(result["audit"], dict):
        lines.extend(_format_audit_summary(result["audit"]))
    if "paths" in result and isinstance(result["paths"], dict):
        lines.extend(_format_paths(result["paths"]))
    if "ranking" in result and isinstance(result["ranking"], list):
        lines.extend(_format_ranking(result["ranking"]))
    if "tensor_extract" in result and isinstance(
        result["tensor_extract"], dict
    ):
        lines.extend(_format_tensor_extract(result["tensor_extract"]))

    if len(lines) <= 3:
        lines.extend(
            [
                "",
                "Details",
                "No specialized formatter is available for this flow.",
            ]
        )

    return _render_lines(lines)


def _render_lines(lines: list[str]) -> str:
    return "\n".join(_plain_line(line) for line in lines)


def _plain_line(value: Any) -> str:
    text = _ANSI_ESCAPE_RE.sub("", str(value))
    return _CONTROL_RE.sub("", text)


def _format_audit_summary(audit: dict[str, Any]) -> list[str]:
    return [
        "",
        "Audit Summary",
        f"Lean files found: {audit.get('ActualSrcFiles', 'unknown')}",
        f"Documented entries: {audit.get('DocumentedEntries', 'unknown')}",
        f"Common files: {audit.get('CommonFiles', 'unknown')}",
        f"Missing in proof map: {audit.get('MissingInDoc', 'unknown')}",
        f"Import mismatches: {audit.get('MismatchCount', 'unknown')}",
    ]


def _format_paths(paths: dict[str, Any]) -> list[str]:
    off_path = paths.get("ModulesOffMillenniumPath") or []
    lines = [
        "",
        "Dependency Path Check",
        f"Target: {paths.get('Target', 'unknown')}",
        "Dependency closure size: "
        f"{paths.get('DependencyClosureSize', 'unknown')}",
        "Path count to target: "
        f"{paths.get('PathCountToMillennium', 'unknown')}",
        "All modules reach target: "
        f"{_yes_no(paths.get('AllModulesReachMillennium'))}",
        f"Modules off target path: {len(off_path)}",
    ]
    if off_path:
        preview = ", ".join(str(item) for item in off_path[:8])
        suffix = "" if len(off_path) <= 8 else f" and {len(off_path) - 8} more"
        lines.append(f"First off-path modules: {preview}{suffix}")
    return lines


def _format_ranking(ranking: list[Any]) -> list[str]:
    lines = ["", "Cohesion Ranking"]
    for index, item in enumerate(ranking[:10], start=1):
        if not isinstance(item, dict):
            continue
        lines.append(
            f"{index}. {item.get('File', 'unknown')} "
            f"score={item.get('Score', 'unknown')} "
            f"imports={item.get('Imports', 'unknown')} "
            f"dependents={item.get('Dependents', 'unknown')} "
            f"doc_drift={item.get('DocDrift', 'unknown')}"
        )
    if len(ranking) > 10:
        lines.append(f"Additional ranked modules: {len(ranking) - 10}")
    return lines


def _format_tensor_extract(data: dict[str, Any]) -> list[str]:
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    profile = (
        data.get("extracted_profile")
        if isinstance(data.get("extracted_profile"), dict)
        else {}
    )
    candidates = (
        data.get("candidate_modules")
        if isinstance(data.get("candidate_modules"), list)
        else []
    )
    lines = [
        "",
        "TorchLean Tensor Readiness",
        "Source status: "
        f"{source.get('status', data.get('status', 'unknown'))}",
        f"Extracted profile: {profile.get('status', 'unknown')}",
        f"Candidate modules: {len(candidates)}",
    ]
    for item in candidates[:8]:
        if isinstance(item, dict):
            module = item.get("module", item.get("file", "unknown"))
            lines.append(f"- {module}")
        else:
            lines.append(f"- {item}")
    if len(candidates) > 8:
        lines.append(f"Additional candidates: {len(candidates) - 8}")
    return lines


def _yes_no(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"
