#!/usr/bin/env python3
"""Lean proof edit guard.

Detects risky Lean proof-edit diffs and classifies `lake build` diagnostics so
warning-cleanup edits that clip proof tactics are surfaced as audit tickets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROOF_CLOSER_PATTERN = re.compile(
    r"\b("
    r"simp(?:_all)?|simp only|simpa|ring(?:_nf)?|field_simp|norm_num|omega|linarith|"
    r"nlinarith|positivity|aesop|exact|refine|apply|rw|rewrite|calc|tauto|constructor|"
    r"ext|fin_cases|all_goals|any_goals|try|repeat|first|solve_by_elim"
    r")\b|<;>",
)

CRITICAL_SIMPLIFIER_PATTERN = re.compile(
    r"Complex\.I_sq|Matrix\.mul_apply|Fin\.sum_univ|Submodule\.coe_smul_of_tower|"
    r"LieRing\.of_associative_ring_bracket|SU3\.gellMannMatrix",
)

RISKY_OPTION_PATTERN = re.compile(
    r"set_option\s+linter\.(unusedTactic|unreachableTactic|unusedSimpArgs)\s+false|"
    r"set_option\s+autoImplicit\s+true",
)

BUILD_PATTERNS: list[tuple[str, str, str]] = [
    ("unsolved_goals", "error", r"unsolved goals"),
    ("no_goals_to_be_solved", "error", r"No goals to be solved"),
    ("metavariables", "error", r"declaration has metavariables|has metavariables"),
    ("unused_tactic", "warning", r"tactic does nothing|tactic is never executed"),
    ("unused_simp_args", "warning", r"simp argument is unused|unusedSimpArgs"),
    ("sorry", "error", r"declaration uses 'sorry'|contains sorry"),
]


@dataclass
class DiffSignal:
    code: str
    severity: str
    file: str
    line: str
    evidence: str


@dataclass
class BuildSignal:
    code: str
    severity: str
    count: int
    examples: list[str] = field(default_factory=list)


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path:
    """Find the Lean project root by walking up to Lean_Proof_Map.txt or lakefile.lean/toml."""
    current = Path(start or os.getcwd()).resolve()
    while True:
        if (current / "Lean_Proof_Map.txt").exists() or (current / "lakefile.lean").exists() or (current / "lakefile.toml").exists():
            return current
        if current.parent == current:
            return Path(start or os.getcwd()).resolve()
        current = current.parent


def run_command(args: list[str], cwd: Path, timeout: int = 120) -> dict[str, Any]:
    """Run a command and capture output without raising."""
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "args": args,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "args": args,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timed_out": True,
        }


def read_diff(project_root: Path, diff_file: str | None = None) -> str:
    """Read a diff file or collect the current tracked Lean working-tree diff."""
    if diff_file:
        return Path(diff_file).read_text(encoding="utf-8")

    result = run_command(["git", "diff", "--", ":(glob)**/*.lean"], project_root)
    return str(result.get("stdout") or "")


def parse_changed_files(diff_text: str) -> list[str]:
    """Extract changed files from a unified git diff."""
    files: list[str] = []
    for match in re.finditer(r"^diff --git a/(.*?) b/(.*?)$", diff_text, flags=re.MULTILINE):
        candidate = match.group(2)
        if candidate.endswith(".lean") and candidate not in files:
            files.append(candidate)
    return files


def analyze_diff(diff_text: str) -> dict[str, Any]:
    """Classify Lean proof-edit risk from a unified diff."""
    signals: list[DiffSignal] = []
    removed_closers = 0
    added_closers = 0
    removed_critical_simplifiers = 0
    added_risky_options = 0
    current_file = ""

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.*?) b/(.*?)$", raw_line)
            current_file = match.group(2) if match else ""
            continue
        if raw_line.startswith("--- ") or raw_line.startswith("+++ "):
            continue
        if not current_file.endswith(".lean"):
            continue

        if raw_line.startswith("-"):
            line = raw_line[1:]
            if PROOF_CLOSER_PATTERN.search(line):
                removed_closers += 1
                signals.append(
                    DiffSignal(
                        code="removed_proof_closer",
                        severity="high",
                        file=current_file,
                        line=line.strip(),
                        evidence="Removed proof-closing tactic or tactic combinator.",
                    )
                )
            if CRITICAL_SIMPLIFIER_PATTERN.search(line):
                removed_critical_simplifiers += 1
                signals.append(
                    DiffSignal(
                        code="removed_critical_simplifier",
                        severity="high",
                        file=current_file,
                        line=line.strip(),
                        evidence="Removed a simplifier known to close arithmetic/matrix goals.",
                    )
                )
        elif raw_line.startswith("+"):
            line = raw_line[1:]
            if PROOF_CLOSER_PATTERN.search(line):
                added_closers += 1
            if RISKY_OPTION_PATTERN.search(line):
                added_risky_options += 1
                signals.append(
                    DiffSignal(
                        code="added_linter_suppression",
                        severity="medium",
                        file=current_file,
                        line=line.strip(),
                        evidence="Added a linter suppression or permissive option.",
                    )
                )

    risk_score = removed_closers * 3 + removed_critical_simplifiers * 4 + added_risky_options * 2
    if removed_closers > added_closers:
        risk_score += 3
        signals.append(
            DiffSignal(
                code="proof_closer_count_regression",
                severity="high",
                file="*",
                line=f"removed={removed_closers}, added={added_closers}",
                evidence="Proof-closing tactic removals exceed additions.",
            )
        )

    changed_files = parse_changed_files(diff_text)
    return {
        "changed_files": changed_files,
        "metrics": {
            "removed_proof_closers": removed_closers,
            "added_proof_closers": added_closers,
            "removed_critical_simplifiers": removed_critical_simplifiers,
            "added_risky_options": added_risky_options,
            "risk_score": risk_score,
        },
        "signals": [signal.__dict__ for signal in signals],
    }


def analyze_build_output(output: str, returncode: int | None = None) -> dict[str, Any]:
    """Classify Lean build output."""
    signals: list[BuildSignal] = []
    for code, severity, pattern in BUILD_PATTERNS:
        regex = re.compile(pattern, flags=re.IGNORECASE)
        matches = list(regex.finditer(output))
        if not matches:
            continue
        examples: list[str] = []
        lines = output.splitlines()
        for line in lines:
            if regex.search(line):
                examples.append(line.strip())
            if len(examples) >= 3:
                break
        signals.append(BuildSignal(code=code, severity=severity, count=len(matches), examples=examples))

    error_count = sum(signal.count for signal in signals if signal.severity == "error")
    warning_count = sum(signal.count for signal in signals if signal.severity == "warning")
    if returncode not in (None, 0) and error_count == 0:
        error_count = 1
        signals.append(
            BuildSignal(
                code="nonzero_build_exit",
                severity="error",
                count=1,
                examples=[f"build command exited with {returncode}"],
            )
        )

    return {
        "returncode": returncode,
        "error_count": error_count,
        "warning_count": warning_count,
        "signals": [signal.__dict__ for signal in signals],
    }


def classify_guard(diff_analysis: dict[str, Any], build_analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Assign guard status and recommendations."""
    risk_score = int(diff_analysis.get("metrics", {}).get("risk_score", 0))
    build_errors = int((build_analysis or {}).get("error_count", 0))
    changed_files = diff_analysis.get("changed_files", [])

    if build_errors > 0:
        status = "red"
        action = "rollback_or_repair_before_next_edit"
        summary = "Lean build errors detected after proof-edit risk. Do not continue cleanup edits."
    elif risk_score >= 8:
        status = "amber"
        action = "require_lake_build_and_symbol_review"
        summary = "High-risk proof tactic diff detected. Require `lake build` before accepting."
    elif risk_score > 0:
        status = "yellow"
        action = "review_diff_and_run_targeted_build"
        summary = "Proof tactic changes detected. Review symbol-local intent and run a targeted build."
    else:
        status = "green"
        action = "no_proof_edit_risk_detected"
        summary = "No proof-edit risk signals detected in the Lean diff."

    if not changed_files and risk_score == 0 and build_errors == 0:
        summary = "No tracked Lean diff was found."

    return {
        "status": status,
        "action": action,
        "summary": summary,
        "requires_build": risk_score > 0 and build_analysis is None,
        "requires_rollback_consideration": build_errors > 0,
    }


def build_report(
    project_root: Path,
    diff_text: str,
    build_output: str | None = None,
    build_returncode: int | None = None,
    backup_path: str | None = None,
    run_build: bool = False,
    build_command: str = "lake build",
    timeout: int = 300,
) -> dict[str, Any]:
    """Build a full guard report."""
    diff_analysis = analyze_diff(diff_text)
    build_analysis = None
    build_run = None

    if run_build:
        build_args = shlex.split(build_command)
        build_run = run_command(build_args, project_root, timeout=timeout)
        combined_output = f"{build_run.get('stdout', '')}\n{build_run.get('stderr', '')}"
        build_analysis = analyze_build_output(combined_output, build_run.get("returncode"))
    elif build_output is not None:
        build_analysis = analyze_build_output(build_output, build_returncode)

    guard = classify_guard(diff_analysis, build_analysis)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "tool": "lean_proof_edit_guard",
        "created_at": now,
        "project_root": str(project_root),
        "guard": guard,
        "diff": diff_analysis,
        "build": build_analysis,
        "build_run": {
            "args": build_run.get("args"),
            "returncode": build_run.get("returncode"),
            "timed_out": build_run.get("timed_out"),
        }
        if build_run
        else None,
        "backup_path": backup_path,
        "policy": {
            "proof_edit_protocol": [
                "Use symbol-aware navigation before proof edits.",
                "Record diff and build result for every proof-touch.",
                "Treat warning cleanup as unsafe until `lake build` passes.",
            ]
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Guard Lean proof edits with diff/build diagnostics.")
    parser.add_argument("--project-root", default=None, help="Lean project root.")
    parser.add_argument("--diff-file", default=None, help="Read unified diff from this file.")
    parser.add_argument("--build-log-file", default=None, help="Read build output from this file.")
    parser.add_argument("--build-returncode", type=int, default=None, help="Return code for provided build log.")
    parser.add_argument("--run-build", action="store_true", help="Run the build command and classify output.")
    parser.add_argument("--build-command", default="lake build", help="Build command to run when --run-build is set.")
    parser.add_argument("--timeout", type=int, default=300, help="Build timeout in seconds.")
    parser.add_argument("--backup-path", default=None, help="Backup archive or directory associated with the edit.")
    args = parser.parse_args()

    project_root = find_project_root(args.project_root)
    diff_text = read_diff(project_root, args.diff_file)
    build_output = None
    if args.build_log_file:
        build_output = Path(args.build_log_file).read_text(encoding="utf-8")

    report = build_report(
        project_root=project_root,
        diff_text=diff_text,
        build_output=build_output,
        build_returncode=args.build_returncode,
        backup_path=args.backup_path,
        run_build=args.run_build,
        build_command=args.build_command,
        timeout=args.timeout,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
