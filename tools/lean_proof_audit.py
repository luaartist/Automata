#!/usr/bin/env python3
"""
Lean Proof Map Audit and Import Graph Analysis

Ports Wolfram Language logic:
- LeanProofMapAudit.wl — JSON audit summary (file counts, mismatches)
- LeanImportGraphLib.wl — Import graph and dependency closures
- LeanMillenniumPathChart.wl — Dependency closure to Millennium.lean
- LeanCohesionSeams.wl — Module ranking by drift/priority
"""

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Optional


class LeanImportGraph:
    """Build and analyze Lean import graphs."""

    def __init__(self, project_root: str):
        """Initialize with project root directory."""
        self.project_root = Path(project_root)
        self.src_dir = self.project_root / "src"
        self.proof_map_path = self.project_root / "Lean_Proof_Map.txt"

        self._lean_files_cache: Optional[Dict[str, Path]] = None
        self._import_assoc_cache: Optional[Dict[str, List[str]]] = None
        self._reverse_import_cache: Optional[Dict[str, List[str]]] = None
        self._proof_map_cache: Optional[Dict[str, List[str]]] = None

    # ─ File discovery ────────────────────────────────────────────────────────

    def get_lean_file_paths(self) -> List[Path]:
        """Get sorted list of .lean file paths from src directory."""
        if not self.src_dir.exists():
            return []
        return sorted(self.src_dir.glob("**/*.lean"))

    def get_lean_file_names(self) -> List[str]:
        """Get sorted list of .lean file names."""
        return sorted([p.name for p in self.get_lean_file_paths()])

    def get_known_files(self) -> Dict[str, Path]:
        """Build mapping of filename -> full path."""
        if self._lean_files_cache is not None:
            return self._lean_files_cache

        mapping = {}
        for fpath in self.get_lean_file_paths():
            mapping[fpath.name] = fpath

        self._lean_files_cache = mapping
        return mapping

    # ─ Import analysis ───────────────────────────────────────────────────────

    @staticmethod
    def extract_import_targets(
        text: str, known_files: List[str]
    ) -> List[str]:
        """Extract Lean import targets from file text."""
        lines = text.splitlines()
        import_lines = [
            line.strip() for line in lines if line.strip().startswith("import ")
        ]

        raw_targets = []
        for line in import_lines:
            # Strip "import " prefix and split by whitespace
            targets = line[7:].strip().split()
            raw_targets.extend(targets)

        # Normalize: convert dot notation to .lean filename
        normalized = set()
        for target in raw_targets:
            # Last component + .lean suffix
            filename = target.split(".")[-1] + ".lean"
            if filename in known_files:
                normalized.add(filename)

        return sorted(list(normalized))

    def get_import_association(self) -> Dict[str, List[str]]:
        """Get mapping of file -> list of imported files."""
        if self._import_assoc_cache is not None:
            return self._import_assoc_cache

        known = self.get_known_files()
        known_names = list(known.keys())
        imports = {}

        for fname in known_names:
            fpath = known[fname]
            try:
                text = fpath.read_text(encoding="utf-8")
                imports[fname] = self.extract_import_targets(text, known_names)
            except Exception:
                imports[fname] = []

        self._import_assoc_cache = imports
        return imports

    def get_reverse_import_association(self) -> Dict[str, List[str]]:
        """Get mapping of file -> list of files that import it."""
        if self._reverse_import_cache is not None:
            return self._reverse_import_cache

        imports = self.get_import_association()
        reverse: Dict[str, Set[str]] = defaultdict(set)

        for source, targets in imports.items():
            for target in targets:
                reverse[target].add(source)

        # Convert sets to sorted lists
        result = {k: sorted(list(v)) for k, v in reverse.items()}
        self._reverse_import_cache = result
        return result

    # ─ Proof map parsing ─────────────────────────────────────────────────────

    def get_proof_map_association(self) -> Dict[str, List[str]]:
        """Parse Lean_Proof_Map.txt and extract documented imports."""
        if self._proof_map_cache is not None:
            return self._proof_map_cache

        if not self.proof_map_path.exists():
            return {}

        try:
            text = self.proof_map_path.read_text(encoding="utf-8")
        except Exception:
            return {}

        lines = text.splitlines()
        doc: Dict[str, List[str]] = {}

        index = 0
        while index < len(lines):
            line = lines[index].strip()

            # Check if line matches file heading pattern: "Filename.lean" or "Filename.lean (description)"
            if re.match(r"[A-Za-z0-9_]+\.lean(\s*\(.*\))?", line):
                # Extract filename
                match = re.search(r"([A-Za-z0-9_]+\.lean)", line)
                if match:
                    heading_name = match.group(1)
                    imports = []
                    cursor = index + 1

                    # Parse content until next heading or special marker
                    while cursor < len(lines):
                        content_line = lines[cursor].strip()

                        # Stop conditions
                        if re.match(r"[A-Za-z0-9_]+\.lean(\s*\(.*\))?", content_line):
                            break
                        if content_line.startswith("Level "):
                            break
                        if content_line.startswith("#"):
                            break

                        # Look for Imports: line
                        if content_line.startswith("Imports:"):
                            payload = content_line[len("Imports:") :].strip()
                            if payload:
                                # Split by comma and clean up
                                parts = [p.strip() for p in payload.split(",")]
                                imports = [
                                    (
                                        p
                                        if p.endswith(".lean")
                                        else p + ".lean"
                                    )
                                    for p in parts
                                ]
                            break

                        cursor += 1

                    doc[heading_name] = sorted(list(set(imports)))
                    index = cursor
            else:
                index += 1

        self._proof_map_cache = doc
        return doc

    # ─ Audit logic ───────────────────────────────────────────────────────────

    def get_audit_association(self) -> Dict[str, Any]:
        """Perform full audit: compare actual vs documented imports."""
        actual = self.get_import_association()
        doc = self.get_proof_map_association()

        actual_names = sorted(actual.keys())
        doc_names = sorted(doc.keys())

        common = sorted(set(actual_names) & set(doc_names))
        missing_doc = sorted(set(actual_names) - set(doc_names))
        missing_src = sorted(set(doc_names) - set(actual_names))

        # Find mismatches in common files
        mismatches = {}
        for name in common:
            actual_imports = set(actual.get(name, []))
            doc_imports = set(doc.get(name, []))

            if actual_imports != doc_imports:
                mismatches[name] = {
                    "Actual": sorted(list(actual_imports)),
                    "Documented": sorted(list(doc_imports)),
                    "OnlyInActual": sorted(
                        list(actual_imports - doc_imports)
                    ),
                    "OnlyInDoc": sorted(list(doc_imports - actual_imports)),
                }

        return {
            "ActualFiles": actual_names,
            "DocumentedFiles": doc_names,
            "CommonFiles": common,
            "MissingInDoc": missing_doc,
            "MissingInSrc": missing_src,
            "Mismatches": mismatches,
        }

    def get_audit_summary(self) -> Dict[str, Any]:
        """Generate audit summary with counts."""
        audit = self.get_audit_association()
        return {
            "ActualSrcFiles": len(audit["ActualFiles"]),
            "DocumentedEntries": len(audit["DocumentedFiles"]),
            "CommonFiles": len(audit["CommonFiles"]),
            "MissingInDoc": len(audit["MissingInDoc"]),
            "MissingInSrc": len(audit["MissingInSrc"]),
            "MismatchCount": len(audit["Mismatches"]),
        }

    # ─ Dependency closure ────────────────────────────────────────────────────

    def get_dependency_closure(
        self, target: str = "Millennium.lean"
    ) -> List[str]:
        """Compute transitive closure of dependencies for target."""
        imports = self.get_import_association()

        closure = {target}
        to_process = [target]

        while to_process:
            current = to_process.pop(0)
            for dep in imports.get(current, []):
                if dep not in closure:
                    closure.add(dep)
                    to_process.append(dep)

        return sorted(list(closure))

    def get_dependents_closure(
        self, target: str = "Millennium.lean"
    ) -> List[str]:
        """Compute reverse closure: all files that depend on target."""
        reverse = self.get_reverse_import_association()

        closure = {target}
        to_process = [target]

        while to_process:
            current = to_process.pop(0)
            for dependent in reverse.get(current, []):
                if dependent not in closure:
                    closure.add(dependent)
                    to_process.append(dependent)

        return sorted(list(closure))

    def get_modules_off_target_path(
        self, target: str = "Millennium.lean"
    ) -> List[str]:
        """Get files NOT in dependency closure of target."""
        all_files = set(self.get_lean_file_names())
        closure = set(self.get_dependency_closure(target))
        return sorted(list(all_files - closure))

    def get_shortest_paths_to_target(
        self, target: str = "Millennium.lean"
    ) -> Dict[str, Optional[List[str]]]:
        """Get shortest path from each file to target using BFS."""
        imports = self.get_import_association()
        all_files = self.get_lean_file_names()

        paths = {}
        for start in all_files:
            # BFS from start to target
            queue = [(start, [start])]
            visited = {start}
            found_path = None

            while queue and not found_path:
                current, path = queue.pop(0)
                if current == target:
                    found_path = path
                    break

                for next_file in imports.get(current, []):
                    if next_file not in visited:
                        visited.add(next_file)
                        queue.append((next_file, path + [next_file]))

            paths[start] = found_path

        return paths

    # ─ Millennium path analysis ──────────────────────────────────────────────

    def get_millennium_path_report(
        self, target: str = "Millennium.lean", core: str = "MillenniumCore.lean"
    ) -> Dict[str, Any]:
        """Generate report on Millennium.lean dependencies."""
        dep_closure = self.get_dependency_closure(target)
        core_closure = self.get_dependency_closure(core)
        off_path = self.get_modules_off_target_path(target)
        paths = self.get_shortest_paths_to_target(target)
        paths_with_routes = {k: v for k, v in paths.items() if v is not None}

        return {
            "Target": target,
            "DependencyClosureSize": len(dep_closure),
            "DependencyClosure": dep_closure,
            "MillenniumCoreClosureSize": len(core_closure),
            "MillenniumCoreClosure": core_closure,
            "AllModulesReachMillennium": len(off_path) == 0,
            "ModulesOffMillenniumPath": off_path,
            "PathCountToMillennium": len(paths_with_routes),
        }

    # ─ Cohesion ranking ──────────────────────────────────────────────────────

    def get_aggregator_ranking(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Rank modules by cohesion score (imports + dependents + drift)."""
        imports = self.get_import_association()
        reverse = self.get_reverse_import_association()
        audit = self.get_audit_association()
        mismatches = audit["Mismatches"]

        def drift_size(name: str) -> int:
            if name not in mismatches:
                return 0
            mismatch = mismatches[name]
            return len(mismatch.get("OnlyInActual", [])) + len(
                mismatch.get("OnlyInDoc", [])
            )

        ranking = []
        for name in imports.keys():
            import_count = len(imports.get(name, []))
            dependent_count = len(reverse.get(name, []))
            drift = drift_size(name)
            score = import_count + dependent_count + drift

            ranking.append(
                {
                    "File": name,
                    "Imports": import_count,
                    "Dependents": dependent_count,
                    "DocDrift": drift,
                    "Score": score,
                }
            )

        # Sort by score descending
        ranking.sort(key=lambda x: -x["Score"])
        return ranking[:limit]


def main():
    """CLI entry point for testing."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python lean_proof_audit.py <command> [args]")
        print("\nCommands:")
        print("  audit-summary                 - Print audit summary JSON")
        print(
            "  audit-full                    - Print full audit JSON"
        )
        print(
            "  millennium-path               - Print Millennium dependency paths JSON"
        )
        print(
            "  cohesion-ranking              - Print module ranking JSON"
        )
        sys.exit(1)

    # Determine project root (look for Lean_Proof_Map.txt)
    project_root = os.getcwd()
    while project_root != "/":
        if Path(project_root, "Lean_Proof_Map.txt").exists():
            break
        project_root = str(Path(project_root).parent)
    else:
        project_root = os.getcwd()

    graph = LeanImportGraph(project_root)

    command = sys.argv[1]

    if command == "audit-summary":
        result = graph.get_audit_summary()
        print(json.dumps(result, indent=2))

    elif command == "audit-full":
        result = graph.get_audit_association()
        print(json.dumps(result, indent=2))

    elif command == "millennium-path":
        target = sys.argv[2] if len(sys.argv) > 2 else "Millennium.lean"
        result = graph.get_millennium_path_report(target=target)
        print(json.dumps(result, indent=2))

    elif command == "cohesion-ranking":
        limit = 15
        if len(sys.argv) > 2:
            try:
                limit = int(sys.argv[2])
            except ValueError:
                print(
                    f"Invalid limit value: {sys.argv[2]}",
                    file=sys.stderr,
                )
                sys.exit(2)
        result = graph.get_aggregator_ranking(limit=limit)
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
