#!/usr/bin/env python3
"""
Forum flow handlers for Lean proof audit and import graph analysis.

This module integrates lean_proof_audit.py with the Lean4-Automata forum
system, providing API endpoints and ticket creation.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from human_output import format_flow_result

logger = logging.getLogger(__name__)


class AuditFlowHandler:
    """Handler for Lean proof audit forum flows."""

    def __init__(self, project_root: str = None):
        """Initialize with optional project root."""
        if project_root is None:
            # Auto-detect project root
            project_root = self._find_project_root()
        self.project_root = str(Path(project_root).resolve())
        self.script_path = Path(__file__).resolve().with_name("lean_proof_audit.py")
        if not self.script_path.exists():
            raise FileNotFoundError(
                f"lean_proof_audit.py not found next to forum_handlers.py: {self.script_path}"
            )
        self.tensor_extract_path = Path(__file__).resolve().with_name(
            "torchlean_tensor_extract.py"
        )
        if not self.tensor_extract_path.exists():
            raise FileNotFoundError(
                "torchlean_tensor_extract.py not found next to forum_handlers.py: "
                f"{self.tensor_extract_path}"
            )
        self.proof_edit_guard_path = Path(__file__).resolve().with_name(
            "lean_proof_edit_guard.py"
        )
        if not self.proof_edit_guard_path.exists():
            raise FileNotFoundError(
                "lean_proof_edit_guard.py not found next to forum_handlers.py: "
                f"{self.proof_edit_guard_path}"
            )

    @staticmethod
    def _find_project_root() -> str:
        """Find project root by looking for Lean_Proof_Map.txt."""
        candidates = [
            Path(__file__).resolve().parent.parent,
            Path.cwd(),
        ]
        for base in candidates:
            current = base.resolve()
            while True:
                if (current / "Lean_Proof_Map.txt").exists():
                    return str(current)
                if current.parent == current:
                    break
                current = current.parent
        return str(Path.cwd())

    def run_command(self, command: str, *args: str) -> Dict[str, Any]:
        """Run audit command and return JSON result."""
        cmd: List[str] = [sys.executable, str(self.script_path), command, *args]
        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return {
                    "status": "error",
                    "command": command,
                    "args": list(args),
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }

            # Parse JSON output
            try:
                data = json.loads(result.stdout)
                return {
                    "status": "ok",
                    "command": command,
                    "args": list(args),
                    "data": data,
                }
            except json.JSONDecodeError:
                return {
                    "status": "error",
                    "command": command,
                    "args": list(args),
                    "error": "Invalid JSON output",
                    "output": result.stdout,
                    "stderr": result.stderr,
                }

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "command": command,
                "args": list(args),
                "error": "Command timeout",
            }
        except Exception as e:
            return {
                "status": "error",
                "command": command,
                "args": list(args),
                "error": str(e),
            }

    def get_audit_summary(self) -> Dict[str, Any]:
        """Get audit summary: file counts and mismatch count."""
        return self.run_command("audit-summary")

    def get_full_audit(self) -> Dict[str, Any]:
        """Get full audit: includes detailed mismatch info."""
        return self.run_command("audit-full")

    def get_millennium_paths(self, target: str = "Millennium.lean") -> Dict[str, Any]:
        """Get dependency paths to Millennium.lean."""
        return self.run_command("millennium-path", target)

    def get_cohesion_ranking(self, limit: int = 15) -> Dict[str, Any]:
        """Get module ranking by cohesion score."""
        return self.run_command("cohesion-ranking", str(limit))

    def get_torchlean_tensor_extract(
        self, max_artifacts: int = 50, artifact_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get static TorchLean tensor extraction readiness."""
        clamped_max_artifacts = max(1, min(int(max_artifacts), 500))
        cmd: List[str] = [
            sys.executable,
            str(self.tensor_extract_path),
            "--project-root",
            self.project_root,
            "--max-artifacts",
            str(clamped_max_artifacts),
        ]
        if artifact_root:
            cmd.extend(["--artifact-root", str(artifact_root)])

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return {
                    "status": "error",
                    "command": "torchlean_tensor_extract",
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }

            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                return {
                    "status": "error",
                    "command": "torchlean_tensor_extract",
                    "error": "Invalid JSON output",
                    "output": result.stdout,
                    "stderr": result.stderr,
                }

            return {"status": "ok", "command": "torchlean_tensor_extract", "data": data}
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "command": "torchlean_tensor_extract",
                "error": "Command timeout",
            }
        except Exception as exc:
            return {
                "status": "error",
                "command": "torchlean_tensor_extract",
                "error": str(exc),
            }

    def get_proof_edit_guard(
        self,
        diff_file: Optional[str] = None,
        build_log_file: Optional[str] = None,
        build_returncode: Optional[int] = None,
        run_build: bool = False,
        build_command: str = "lake build",
        backup_path: Optional[str] = None,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """Classify Lean proof-edit risk from git diff and build output."""
        cmd: List[str] = [
            sys.executable,
            str(self.proof_edit_guard_path),
            "--project-root",
            self.project_root,
            "--timeout",
            str(timeout),
        ]
        if diff_file:
            cmd.extend(["--diff-file", str(diff_file)])
        if build_log_file:
            cmd.extend(["--build-log-file", str(build_log_file)])
        if build_returncode is not None:
            cmd.extend(["--build-returncode", str(build_returncode)])
        if run_build:
            cmd.append("--run-build")
            cmd.extend(["--build-command", build_command])
        if backup_path:
            cmd.extend(["--backup-path", str(backup_path)])

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=max(30, int(timeout) + 30),
            )
            if result.returncode != 0:
                return {
                    "status": "error",
                    "command": "lean_proof_edit_guard",
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }

            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                return {
                    "status": "error",
                    "command": "lean_proof_edit_guard",
                    "error": "Invalid JSON output",
                    "output": result.stdout,
                    "stderr": result.stderr,
                }
            return {"status": "ok", "command": "lean_proof_edit_guard", "data": data}
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "command": "lean_proof_edit_guard",
                "error": "Command timeout",
            }
        except Exception as exc:
            return {
                "status": "error",
                "command": "lean_proof_edit_guard",
                "error": str(exc),
            }

    def create_ticket(
        self, title: str, description: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a forum ticket with audit data."""
        now = datetime.now(timezone.utc).isoformat()
        ticket = {
            "title": title,
            "description": description,
            "created_at": now,
            "type": "lean_audit",
            "data": data,
            "metadata": {
                "project_root": self.project_root,
                "timestamp": now,
            },
        }
        return ticket


def handler_lean_proof_audit(
    report_type: str = "summary", **kwargs
) -> Dict[str, Any]:
    """Forum flow handler: Lean proof audit."""
    audit = AuditFlowHandler()

    if report_type == "summary":
        result = audit.get_audit_summary()
    elif report_type == "full":
        result = audit.get_full_audit()
    else:
        return {
            "status": "error",
            "error": f"Unknown report_type: {report_type}",
        }

    if result.get("status") == "ok":
        data = result.get("data", {})
        ticket = audit.create_ticket(
            title=f"Lean Proof Map Audit ({report_type})",
            description=f"Audit report comparing actual imports vs documented entries in Lean_Proof_Map.txt",
            data=data,
        )
        return {"status": "ok", "ticket": ticket, "audit": data}
    else:
        return result


def handler_lean_millennium_paths(
    target: str = "Millennium.lean", **kwargs
) -> Dict[str, Any]:
    """Forum flow handler: Millennium.lean dependency paths."""
    audit = AuditFlowHandler()
    result = audit.get_millennium_paths(target)

    if result.get("status") == "ok":
        data = result.get("data", {})
        ticket = audit.create_ticket(
            title="Lean Millennium Path Analysis",
            description="Analysis of dependency paths to Millennium.lean, including modules off the path",
            data=data,
        )
        return {"status": "ok", "ticket": ticket, "paths": data}
    else:
        return result


def handler_lean_cohesion_ranking(limit: int = 15, **kwargs) -> Dict[str, Any]:
    """Forum flow handler: Module cohesion ranking."""
    audit = AuditFlowHandler()
    result = audit.get_cohesion_ranking(limit)

    if result.get("status") == "ok":
        data = result.get("data", [])

        ticket = audit.create_ticket(
            title=f"Lean Module Cohesion Ranking (top {len(data)})",
            description="Modules ranked by cohesion score: imports + dependents + documentation drift",
            data=data,
        )
        return {"status": "ok", "ticket": ticket, "ranking": data}
    else:
        return result


def handler_torchlean_tensor_extract(
    max_artifacts: int = 50, artifact_root: Optional[str] = None, **kwargs
) -> Dict[str, Any]:
    """Forum flow handler: static TorchLean tensor extraction readiness."""
    audit = AuditFlowHandler(project_root=kwargs.get("project_root"))
    result = audit.get_torchlean_tensor_extract(
        max_artifacts=max_artifacts,
        artifact_root=artifact_root,
    )

    if result.get("status") == "ok":
        data = result.get("data", {})
        ticket = audit.create_ticket(
            title="TorchLean Tensor Extract Static Readiness",
            description=(
                "Static audit of TorchLean, tensor artifact readiness, and the "
                "TensorExtractedBiasProfile Lean target"
            ),
            data=data,
        )
        return {"status": "ok", "ticket": ticket, "tensor_extract": data}
    else:
        return result


def handler_lean_proof_edit_guard(**kwargs) -> Dict[str, Any]:
    """Forum flow handler: Lean proof edit diff/build guard."""
    audit = AuditFlowHandler(project_root=kwargs.get("project_root"))
    result = audit.get_proof_edit_guard(
        diff_file=kwargs.get("diff_file"),
        build_log_file=kwargs.get("build_log_file"),
        build_returncode=kwargs.get("build_returncode"),
        run_build=bool(kwargs.get("run_build", False)),
        build_command=kwargs.get("build_command", "lake build"),
        backup_path=kwargs.get("backup_path"),
        timeout=int(kwargs.get("timeout", 300)),
    )

    if result.get("status") == "ok":
        data = result.get("data", {})
        guard_status = data.get("guard", {}).get("status", "unknown")
        ticket = audit.create_ticket(
            title=f"Lean Proof Edit Guard ({guard_status})",
            description=(
                "Diff and build-output guard for Lean proof edits, focused on "
                "tactic clipping and warning-cleanup regressions"
            ),
            data=data,
        )
        return {"status": "ok", "ticket": ticket, "proof_edit_guard": data}
    else:
        return result


# Handlers mapping
FLOW_HANDLERS = {
    "lean_proof_audit": handler_lean_proof_audit,
    "lean_millennium_paths": handler_lean_millennium_paths,
    "lean_cohesion_ranking": handler_lean_cohesion_ranking,
    "torchlean_tensor_extract": handler_torchlean_tensor_extract,
    "lean_proof_edit_guard": handler_lean_proof_edit_guard,
}


def execute_flow(flow_name: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    """Execute a flow by name with given parameters."""
    if params is None:
        params = {}

    handler = FLOW_HANDLERS.get(flow_name)
    if handler is None:
        return {"status": "error", "error": f"Unknown flow: {flow_name}"}

    try:
        return handler(**params)
    except Exception as e:
        logger.exception("Flow execution failed: %s", flow_name)
        return {
            "status": "error",
            "flow": flow_name,
            "error": str(e),
        }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python forum_handlers.py <flow_name> [json_params]")
        print(
            "       python forum_handlers.py <flow_name> "
            "[json_params] --format text"
        )
        print("\nAvailable flows:")
        for name in FLOW_HANDLERS.keys():
            print(f"  - {name}")
        sys.exit(0)

    selected_flow_name = sys.argv[1]
    flow_params = {}
    output_format = "json"
    cli_args = sys.argv[2:]

    if "--human" in cli_args:
        output_format = "text"
        cli_args.remove("--human")

    if "--format" in cli_args:
        format_index = cli_args.index("--format")
        try:
            output_format = cli_args[format_index + 1]
        except IndexError:
            print("Error: --format requires json or text")
            sys.exit(1)
        del cli_args[format_index: format_index + 2]

    if cli_args:
        try:
            flow_params = json.loads(cli_args[0])
        except json.JSONDecodeError as e:
            print(f"Error parsing params JSON: {e}")
            sys.exit(1)

    if output_format not in {"json", "text"}:
        print("Error: --format must be json or text")
        sys.exit(1)

    flow_result = execute_flow(selected_flow_name, flow_params)
    if output_format == "text":
        print(format_flow_result(selected_flow_name, flow_result))
    else:
        print(json.dumps(flow_result, indent=2))
