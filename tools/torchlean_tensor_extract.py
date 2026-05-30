#!/usr/bin/env python3
"""Static TorchLean tensor extraction readiness report.

This tool intentionally does not run GPU inference or synthesize tensor values.
It inspects the Lean project and nearby artifact paths, then reports whether the
source surface is ready for a future tensor reader to populate
SU3QuantumMeshBiasProbe.TensorExtractedBiasProfile.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FLOW_NAME = "torchlean_tensor_extract"
ARTIFACT_SUFFIXES = {".npy", ".npz", ".onnx", ".safetensors", ".pt", ".pth"}
SKIP_DIR_NAMES = {
    ".git",
    ".lake",
    ".mypy_cache",
    ".pytest_cache",
    ".serena",
    ".shipping_backups",
    ".venv",
    ".worktree-dirties",
    "Lean4-Automata",
    "Lean4-Automata-Development-Only",
    "__pycache__",
    "external_repos",
    "lake-packages",
    "node_modules",
    "serena",
}
EXPECTED_TENSOR_FILES = {
    "gate/bias.npy": "gateFlavor",
    "gate/weight.npy": "gateEnergy",
    "mixer/weight.npy": "mixerInput",
}
SOURCE_TERMS = (
    "TensorExtractedBiasProfile",
    "quantumMeshLayer0NpyTensorFileCount",
    "TorchleanDiagonal",
    "gate/bias.npy",
    "gate/weight.npy",
    "mixer/weight.npy",
)


@dataclass(frozen=True, slots=True)
class ProjectRoots:
    project_root: Path
    warnings: list[str]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_project_root(path: Path) -> bool:
    return ((path / "lakefile.lean").is_file() or (path / "lakefile.toml").is_file()) and (path / "src").is_dir()


def ancestors(path: Path) -> Iterable[Path]:
    current = path.resolve()
    yield current
    for parent in current.parents:
        yield parent


def find_project_root(explicit_root: str | None = None) -> ProjectRoots:
    warnings: list[str] = []
    if explicit_root:
        root = Path(explicit_root).expanduser().resolve()
        if is_project_root(root):
            return ProjectRoots(root, warnings)
        warnings.append(f"explicit project root is missing lakefile.lean/toml or src/: {root}")
        return ProjectRoots(root, warnings)

    seeds: list[Path] = []
    for env_key in ("TORCHLEAN4PHYSICS_ROOT", "LEAN4A_AUDIT_ROOT", "LEAN4A_PROOF_ROOT"):
        if raw_value := os.getenv(env_key):
            seeds.append(Path(raw_value).expanduser())
    seeds.extend([Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent])

    seen: set[Path] = set()
    for seed in seeds:
        for candidate in ancestors(seed):
            if candidate in seen:
                continue
            seen.add(candidate)
            if is_project_root(candidate):
                return ProjectRoots(candidate, warnings)

    fallback = Path.cwd().resolve()
    warnings.append(f"could not locate Lean project root, falling back to cwd: {fallback}")
    return ProjectRoots(fallback, warnings)


def path_text(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_text(path: Path, *, limit_bytes: int = 1_000_000) -> str:
    if not path.is_file():
        return ""
    data = path.read_bytes()[:limit_bytes]
    return data.decode("utf-8", errors="replace")


def nested_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from nested_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_dicts(item)


def collect_torchlean_paths(project_root: Path) -> list[dict[str, Any]]:
    candidates: list[tuple[Path, str]] = [(project_root / "lake-packages" / "torchlean", "common_lake_path")]

    lakefile_lean = project_root / "lakefile.lean"
    lakefile_toml = project_root / "lakefile.toml"

    if lakefile_lean.is_file():
        lakefile_text = read_text(lakefile_lean)
        for match in re.finditer(r'require\s+torchlean\s+from\s+"([^"]+)"', lakefile_text):
            candidates.append(((project_root / match.group(1)).resolve(), "lakefile_require"))
    elif lakefile_toml.is_file():
        lakefile_text = read_text(lakefile_toml)
        # For TOML we typically use git requirements which end up in lake-packages, 
        # but we check if any local path is specified.
        pass

    manifest_path = project_root / "lake-manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(read_text(manifest_path))
        except json.JSONDecodeError:
            manifest = None
        if manifest is not None:
            for package in nested_dicts(manifest):
                if package.get("name") == "torchlean" and package.get("dir"):
                    candidates.append(((project_root / str(package["dir"])).resolve(), "lake_manifest"))

    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for candidate_path, source in candidates:
        resolved = candidate_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        torchlean_root_file = resolved / "TorchLean.lean"
        tensor_file = resolved / "TorchLean" / "Frontend" / "Tensor.lean"
        layers_file = resolved / "TorchLean" / "Frontend" / "Layers.lean"
        lean_file_count = 0
        if resolved.is_dir():
            lean_file_count = sum(1 for lean_file in resolved.rglob("*.lean") if ".lake" not in lean_file.parts)
        records.append(
            {
                "path": path_text(resolved, project_root),
                "source": source,
                "exists": resolved.exists(),
                "is_dir": resolved.is_dir(),
                "lean_file_count": lean_file_count,
                "root_module_exists": torchlean_root_file.is_file(),
                "frontend_tensor_exists": tensor_file.is_file(),
                "frontend_layers_exists": layers_file.is_file(),
            }
        )
    return records


def parse_imports(text: str) -> list[str]:
    imports: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            imports.append(stripped.removeprefix("import ").strip())
    return imports


def parse_declarations(text: str, *, limit: int = 30) -> list[dict[str, str]]:
    declarations: list[dict[str, str]] = []
    pattern = re.compile(r"^(structure|def|theorem|lemma|abbrev|inductive)\s+([A-Za-z0-9_'.]+)", re.MULTILINE)
    for match in pattern.finditer(text):
        declarations.append({"kind": match.group(1), "name": match.group(2)})
        if len(declarations) >= limit:
            break
    return declarations


def parse_structure_fields(text: str, structure_name: str) -> list[dict[str, str]]:
    pattern = re.compile(
        rf"structure\s+{re.escape(structure_name)}\b.*?where\n(?P<body>.*?)(?=\n/(?:-|--)|\nstructure\s|\ndef\s|\ntheorem\s|\nlemma\s|\nabbrev\s|\nend\s|\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return []
    fields: list[dict[str, str]] = []
    for line in match.group("body").splitlines():
        field_match = re.match(r"\s+([A-Za-z0-9_']+)\s*:\s*(.+?)\s*$", line)
        if field_match:
            fields.append({"name": field_match.group(1), "type": field_match.group(2)})
    return fields


def parse_nat_defs(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for match in re.finditer(r"^def\s+([A-Za-z0-9_']+)\s*:\s*Nat\s*:=\s*([0-9]+)\s*$", text, re.MULTILINE):
        values[match.group(1)] = int(match.group(2))
    return values


def lean_module_name(path: Path, project_root: Path) -> str:
    if "TorchLean" in path.parts:
        start_index = path.parts.index("TorchLean")
        return Path(*path.parts[start_index:]).with_suffix("").as_posix().replace("/", ".")
    try:
        relative = path.relative_to(project_root / "src")
    except ValueError:
        relative = path.name
    if isinstance(relative, Path):
        return relative.with_suffix("").as_posix().replace("/", ".")
    return str(relative).removesuffix(".lean")


def module_record(
    path: Path,
    project_root: Path,
    *,
    role: str,
    focus_terms: Iterable[str] = SOURCE_TERMS,
    structures: Iterable[str] = (),
) -> dict[str, Any]:
    text = read_text(path)
    structure_fields = {
        structure_name: parse_structure_fields(text, structure_name) for structure_name in structures
    }
    return {
        "module": lean_module_name(path, project_root),
        "path": path_text(path, project_root),
        "exists": path.is_file(),
        "role": role,
        "imports": parse_imports(text),
        "focus_hits": [term for term in focus_terms if term in text],
        "declarations": parse_declarations(text),
        "structure_fields": {key: value for key, value in structure_fields.items() if value},
    }


def collect_candidate_modules(project_root: Path, torchlean_paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()

    def add_if_file(path: Path, *, role: str, structures: Iterable[str] = ()) -> None:
        resolved = path.resolve()
        if resolved in seen_paths or not path.is_file():
            return
        seen_paths.add(resolved)
        candidates.append(module_record(path, project_root, role=role, structures=structures))

    add_if_file(
        project_root / "src" / "SU3QuantumMeshBiasProbe.lean",
        role="tensor_extracted_bias_profile_target",
        structures=("TensorExtractedBiasProfile",),
    )
    add_if_file(project_root / "src" / "SU3FlavorLatticeMap.lean", role="729_flavor_lattice_adapter")
    add_if_file(project_root / "src" / "ModelWitnessCertificates.lean", role="static_tensor_witness_constants")
    add_if_file(project_root / "src" / "QuantumMeshStructural.lean", role="finite_mesh_whitebox_surface")
    add_if_file(project_root / "src" / "SU3Bias.lean", role="bias_profile_type_surface", structures=("BiasProfile",))

    for torchlean_record in torchlean_paths:
        if not torchlean_record.get("exists"):
            continue
        torchlean_root = (project_root / torchlean_record["path"]).resolve()
        if not torchlean_root.exists():
            torchlean_root = Path(str(torchlean_record["path"])).resolve()
        add_if_file(
            torchlean_root / "TorchLean" / "Frontend" / "Tensor.lean",
            role="torchlean_tensor_api",
            structures=("Tensor",),
        )
        add_if_file(
            torchlean_root / "TorchLean" / "Frontend" / "Layers.lean",
            role="torchlean_layer_api",
            structures=("LinearLayer", "Conv2dLayer"),
        )

    src_dir = project_root / "src"
    if src_dir.is_dir():
        for path in sorted(src_dir.glob("*.lean")):
            if path.resolve() in seen_paths:
                continue
            text = read_text(path, limit_bytes=250_000)
            if any(term in text for term in SOURCE_TERMS):
                seen_paths.add(path.resolve())
                candidates.append(module_record(path, project_root, role="related_static_source"))
            if len(candidates) >= 12:
                break

    model_witness_path = project_root / "src" / "ModelWitnessCertificates.lean"
    for candidate in candidates:
        if candidate["path"] == path_text(model_witness_path, project_root):
            candidate["nat_witnesses"] = parse_nat_defs(read_text(model_witness_path))
    return candidates


def artifact_roots(project_root: Path, explicit_artifact_root: str | None) -> list[Path]:
    if explicit_artifact_root:
        return [Path(explicit_artifact_root).expanduser().resolve()]
    roots = [project_root / name for name in ("local_data", "papers", "proof_logs", "Studyformulae")]
    return [root for root in roots if root.exists()]


def scan_artifacts(project_root: Path, explicit_artifact_root: str | None, max_artifacts: int) -> dict[str, Any]:
    roots = artifact_roots(project_root, explicit_artifact_root)
    artifacts: list[dict[str, Any]] = []
    expected_found = {expected_path: False for expected_path in EXPECTED_TENSOR_FILES}

    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES]
            current_dir = Path(dirpath)
            for filename in sorted(filenames):
                path = current_dir / filename
                suffix = path.suffix.lower()
                relative = path_text(path, project_root)
                normalized_relative = relative.replace(os.sep, "/")
                for expected_path in expected_found:
                    if normalized_relative.endswith(expected_path):
                        expected_found[expected_path] = True
                if suffix not in ARTIFACT_SUFFIXES:
                    continue
                try:
                    size_bytes = path.stat().st_size
                except OSError:
                    size_bytes = None
                artifacts.append({"path": relative, "suffix": suffix, "size_bytes": size_bytes})
                if len(artifacts) >= max_artifacts:
                    return {
                        "roots_scanned": [path_text(root_path, project_root) for root_path in roots],
                        "artifacts": artifacts,
                        "artifact_count": len(artifacts),
                        "truncated": True,
                        "expected_tensor_files_found": expected_found,
                    }

    return {
        "roots_scanned": [path_text(root_path, project_root) for root_path in roots],
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "truncated": False,
        "expected_tensor_files_found": expected_found,
    }


def default_profile_fields() -> dict[str, Any]:
    return {
        "layer": {"lean_type": "Nat", "source_hint": "selected tensor layer index"},
        "gateFlavor": {
            "lean_type": "SU3Bias.FlavorBias",
            "shape_hint": "Fin 729 -> Real",
            "source_hint": "gate/bias.npy vector",
        },
        "gateEnergy": {
            "lean_type": "SU3Bias.FlavorBias",
            "shape_hint": "Fin 729 -> Real",
            "source_hint": "row-norm-squared data from gate/weight.npy",
        },
        "mixerInput": {
            "lean_type": "SU3Bias.FlavorBias",
            "shape_hint": "Fin 729 -> Real",
            "source_hint": "column-norm-squared data from mixer/weight.npy",
        },
        "adjointFold": {
            "lean_type": "SU3Bias.AdjointBias",
            "shape_hint": "Fin 8 -> Real",
            "source_hint": "eight-channel fold of a chosen 729-state profile",
        },
        "offset": {"lean_type": "Real", "source_hint": "reader-supplied scalar offset"},
    }


def build_next_targets(profile_module_exists: bool) -> list[dict[str, str]]:
    targets = [
        {
            "module": "SU3QuantumMeshBiasProbe",
            "target": "TensorExtractedBiasProfile",
            "next_input": "Provide audited gate/bias.npy, gate/weight.npy, mixer/weight.npy paths or a manifest.",
        },
        {
            "module": "SU3FlavorLatticeMap",
            "target": "gateFlavorOnLattice, gateEnergyOnLattice, mixerInputOnLattice",
            "next_input": "Feed a populated TensorExtractedBiasProfile through the 729 flavor lattice adapters.",
        },
        {
            "module": "ModelWitnessCertificates",
            "target": "quantumMeshLayer0NpyTensorFileCount and byte-total witnesses",
            "next_input": "Compare discovered artifact counts and byte totals against checked Lean constants.",
        },
        {
            "module": "forum_flow_chain",
            "target": "lean_float_scan -> torchlean_tensor_extract -> lean_cohesion_ranking",
            "next_input": "Route this static report into audit tickets, then rank modules after the profile target is populated.",
        },
    ]
    if not profile_module_exists:
        targets.insert(
            0,
            {
                "module": "source_tree",
                "target": "src/SU3QuantumMeshBiasProbe.lean",
                "next_input": "Restore or add the Lean target module before tensor profile extraction can land.",
            },
        )
    return targets


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    roots = find_project_root(args.project_root)
    project_root = roots.project_root
    warnings = list(roots.warnings)

    torchlean_paths = collect_torchlean_paths(project_root)
    candidate_modules = collect_candidate_modules(project_root, torchlean_paths)
    artifact_summary = scan_artifacts(project_root, args.artifact_root, args.max_artifacts)
    profile_fields = default_profile_fields()

    su3_probe_path = project_root / "src" / "SU3QuantumMeshBiasProbe.lean"
    profile_module_exists = su3_probe_path.is_file()
    torchlean_package_exists = any(record.get("exists") for record in torchlean_paths)
    expected_files_found = artifact_summary["expected_tensor_files_found"]
    all_expected_files_found = all(expected_files_found.values())

    if not torchlean_package_exists:
        warnings.append("TorchLean package path was not found; checked lakefile, lake-manifest, and lake-packages/torchlean.")
    if not profile_module_exists:
        warnings.append("SU3QuantumMeshBiasProbe.lean was not found, so no TensorExtractedBiasProfile target is available.")
    if not all_expected_files_found:
        missing = [path for path, found in expected_files_found.items() if not found]
        warnings.append(
            "No complete tensor input set was found for static extraction; missing " + ", ".join(missing) + "."
        )
    warnings.append("No GPU inference, Wolfram, or LLMSynthesize step was run or required by this flow.")

    source_status = {
        "status": "static_ready" if profile_module_exists else "missing_profile_module",
        "checked_at": utc_now_iso(),
        "project_root": str(project_root),
        "lean_src_exists": (project_root / "src").is_dir(),
        "su3_probe_exists": profile_module_exists,
        "torchlean_package_exists": torchlean_package_exists,
        "gpu_required": False,
        "wolfram_required": False,
        "tensor_extraction_attempted": False,
        "artifact_summary": artifact_summary,
    }

    extracted_profile = {
        "status": "not_extracted",
        "reason": "Exact tensor extraction requires audited tensor files and a domain mapping from arrays to Lean values.",
        "expected_inputs": EXPECTED_TENSOR_FILES,
        "available_static_contract": "SU3QuantumMeshBiasProbe.TensorExtractedBiasProfile" if profile_module_exists else None,
        "profile_fields": profile_fields,
    }

    return {
        "status": "ok",
        "flow": FLOW_NAME,
        "source_status": source_status,
        "torchlean_paths_found": torchlean_paths,
        "candidate_modules": candidate_modules,
        "extracted_profile": extracted_profile,
        "profile_fields": profile_fields,
        "next_lean_targets": build_next_targets(profile_module_exists),
        "warnings": warnings,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static TorchLean tensor extraction readiness report")
    parser.add_argument("--project-root", help="Lean project root to inspect")
    parser.add_argument("--artifact-root", help="Optional artifact root to scan for tensor files")
    parser.add_argument("--max-artifacts", type=int, default=50, help="Maximum artifact records to include")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.max_artifacts < 1:
        args.max_artifacts = 1
    report = build_report(args)
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())