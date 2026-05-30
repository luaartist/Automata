"""
lean4_file_tools.py — Standalone file/search utilities for Lean4-Automata.

No Serena, MCP, LSP, sensai, joblib, or bs4 dependencies.
Pure Python stdlib + pathspec (optional; gitignore-aware listing uses it if available).

Public API
----------
ctx = FileToolsContext(root_path="/path/to/repo")

ctx.list_dir(relative_path, recursive, file_mask, max_entries)
ctx.find_file(filename_pattern, search_dir)
ctx.search_for_pattern(pattern, relative_path, context_before, context_after,
                        include_glob, exclude_glob, max_matches, max_files)
ctx.copy_lines(relative_path, start_line, end_line, include_line_numbers)
ctx.stacked_search(seed_pattern, follow_up_pattern, search_dir,
                   include_glob, context_before, context_after,
                   max_seed_files, max_results)
ctx.read_file(relative_path, start_line, end_line)
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, NamedTuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ENCODING = "utf-8"
SUCCESS = "OK"


# ---------------------------------------------------------------------------
# Text line / match types  (extracted from serena/util/text_utils.py)
# ---------------------------------------------------------------------------


class LineType(str, Enum):
    MATCH = "match"
    BEFORE_MATCH = "prefix"
    AFTER_MATCH = "postfix"


@dataclass(kw_only=True)
class TextLine:
    line_number: int
    line_content: str
    match_type: LineType

    def format_line(self, include_line_numbers: bool = True) -> str:
        prefix = "  >" if self.match_type == LineType.MATCH else "..."
        if include_line_numbers:
            prefix = f"{prefix}{str(self.line_number).rjust(4)}"
        return f"{prefix}:{self.line_content}"


@dataclass(kw_only=True)
class MatchedLines:
    lines: list[TextLine]
    source_file_path: str | None = None

    def to_display_string(self, include_line_numbers: bool = True) -> str:
        return "\n".join(ln.format_line(include_line_numbers) for ln in self.lines)

    @property
    def matched_line_numbers(self) -> list[int]:
        return [ln.line_number for ln in self.lines if ln.match_type == LineType.MATCH]


# ---------------------------------------------------------------------------
# Glob helpers  (extracted from serena/util/text_utils.py)
# ---------------------------------------------------------------------------


def _expand_braces(pattern: str) -> list[str]:
    """Expand {a,b,c} brace patterns into a list of patterns."""
    patterns = [pattern]
    while any("{" in p for p in patterns):
        new_patterns: list[str] = []
        for p in patterns:
            m = re.search(r"\{([^{}]+)\}", p)
            if m:
                prefix, suffix = p[: m.start()], p[m.end() :]
                for opt in m.group(1).split(","):
                    new_patterns.append(f"{prefix}{opt}{suffix}")
            else:
                new_patterns.append(p)
        patterns = new_patterns
    return patterns


def _glob_match(pattern: str, path: str) -> bool:
    """Match path against a glob pattern including ** semantics."""
    pattern = pattern.replace("\\", "/")
    path = path.replace("\\", "/")

    if "**" not in pattern:
        return fnmatch.fnmatch(path, pattern)

    # ** — one-or-more dirs
    if re.match(fnmatch.translate(pattern), path):
        return True
    # ** — zero dirs via /**/ → /
    if "/**/" in pattern:
        if re.match(fnmatch.translate(pattern.replace("/**/", "/")), path):
            return True
    # ** — zero dirs via leading **/
    if pattern.startswith("**/"):
        if re.match(fnmatch.translate(pattern[3:]), path):
            return True
    return False


# ---------------------------------------------------------------------------
# Text search  (extracted / simplified from serena/util/text_utils.py)
# ---------------------------------------------------------------------------


def _search_text(
    pattern: str,
    content: str,
    source_file_path: str | None = None,
    context_before: int = 0,
    context_after: int = 0,
) -> list[MatchedLines]:
    """Search *content* for *pattern* (regex). Returns list of MatchedLines."""
    compiled = re.compile(pattern, re.DOTALL)
    lines = content.splitlines()
    total = len(lines)
    results: list[MatchedLines] = []

    for m in compiled.finditer(content):
        start_line = content[: m.start()].count("\n")
        end_line = content[: m.end()].count("\n")
        ctx_start = max(0, start_line - context_before)
        ctx_end = min(total - 1, end_line + context_after)

        text_lines: list[TextLine] = []
        for ln in range(ctx_start, ctx_end + 1):
            if ln < start_line:
                ltype = LineType.BEFORE_MATCH
            elif ln > end_line:
                ltype = LineType.AFTER_MATCH
            else:
                ltype = LineType.MATCH
            text_lines.append(TextLine(line_number=ln, line_content=lines[ln], match_type=ltype))

        results.append(MatchedLines(lines=text_lines, source_file_path=source_file_path))

    return results


# ---------------------------------------------------------------------------
# Directory scan  (extracted / simplified from serena/util/file_system.py)
# ---------------------------------------------------------------------------


class ScanResult(NamedTuple):
    directories: list[str]
    files: list[str]


def _scan_directory(
    path: str,
    recursive: bool = False,
    relative_to: str | None = None,
    is_ignored_dir: Callable[[str], bool] | None = None,
    is_ignored_file: Callable[[str], bool] | None = None,
) -> ScanResult:
    if is_ignored_file is None:
        is_ignored_file = lambda _: False
    if is_ignored_dir is None:
        is_ignored_dir = lambda _: False

    files: list[str] = []
    directories: list[str] = []
    abs_path = os.path.abspath(path)
    rel_base = os.path.abspath(relative_to) if relative_to else None

    try:
        with os.scandir(abs_path) as entries:
            for entry in entries:
                try:
                    ep = entry.path
                    result_path = os.path.relpath(ep, rel_base) if rel_base else ep
                    if entry.is_file():
                        if not is_ignored_file(ep):
                            files.append(result_path)
                    elif entry.is_dir():
                        if not is_ignored_dir(ep):
                            directories.append(result_path)
                            if recursive:
                                sub = _scan_directory(
                                    ep,
                                    recursive=True,
                                    relative_to=relative_to,
                                    is_ignored_dir=is_ignored_dir,
                                    is_ignored_file=is_ignored_file,
                                )
                                files.extend(sub.files)
                                directories.extend(sub.directories)
                except PermissionError:
                    continue
    except PermissionError:
        return ScanResult([], [])

    return ScanResult(directories, files)


# ---------------------------------------------------------------------------
# FileToolsContext — the main API class
# ---------------------------------------------------------------------------


class FileToolsContext:
    """
    Wraps a *root_path* and provides file / search utilities that are fully
    independent of Serena/MCP/LSP infrastructure.

    Parameters
    ----------
    root_path:
        Absolute path to the project root.  All relative paths are resolved
        against this directory.
    max_output_length:
        Truncate any returned string to at most this many characters.
        Set to 0 or None to disable truncation.
    """

    def __init__(self, root_path: str, max_output_length: int = 200_000) -> None:
        self.root_path = os.path.abspath(root_path)
        self.max_output_length = max_output_length or 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _abs(self, relative_path: str) -> str:
        return os.path.normpath(os.path.join(self.root_path, relative_path))

    def _rel(self, abs_path: str) -> str:
        return os.path.relpath(abs_path, self.root_path)

    def _limit(self, text: str) -> str:
        if self.max_output_length and len(text) > self.max_output_length:
            return text[: self.max_output_length] + "\n... [truncated]"
        return text

    def _read_raw(self, abs_path: str) -> str:
        with open(abs_path, encoding=DEFAULT_ENCODING, errors="replace") as fh:
            return fh.read()

    def _all_files(self, search_dir: str = "") -> list[str]:
        """Return all relative file paths under *search_dir* (relative to root)."""
        scan_root = self._abs(search_dir) if search_dir else self.root_path
        result = _scan_directory(scan_root, recursive=True, relative_to=self.root_path)
        return result.files

    # ------------------------------------------------------------------
    # Public: read_file
    # ------------------------------------------------------------------

    def read_file(
        self,
        relative_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """
        Read *relative_path* (relative to root).  Optionally restrict to a
        0-based line range [start_line, end_line] inclusive.

        Returns the file content as a string.
        """
        abs_path = self._abs(relative_path)
        content = self._read_raw(abs_path)
        if start_line is None and end_line is None:
            return self._limit(content)
        lines = content.splitlines(keepends=True)
        s = start_line or 0
        e = (end_line + 1) if end_line is not None else len(lines)
        return self._limit("".join(lines[s:e]))

    # ------------------------------------------------------------------
    # Public: copy_lines
    # ------------------------------------------------------------------

    def copy_lines(
        self,
        relative_path: str,
        start_line: int,
        end_line: int,
        include_line_numbers: bool = True,
    ) -> str:
        """
        Extract lines [start_line, end_line] (0-based, inclusive) from
        *relative_path* and return them as a formatted string.

        Parameters
        ----------
        include_line_numbers:
            Prefix each line with its 1-based line number (``lineno: content``).
        """
        abs_path = self._abs(relative_path)
        content = self._read_raw(abs_path)
        all_lines = content.splitlines()

        if start_line < 0 or end_line >= len(all_lines) or start_line > end_line:
            return (
                f"Invalid range [{start_line}, {end_line}] for file with "
                f"{len(all_lines)} lines."
            )

        output_lines: list[str] = []
        for i, line in enumerate(all_lines[start_line : end_line + 1], start=start_line):
            if include_line_numbers:
                output_lines.append(f"{i + 1:>6}: {line}")
            else:
                output_lines.append(line)

        return self._limit("\n".join(output_lines))

    # ------------------------------------------------------------------
    # Public: list_dir
    # ------------------------------------------------------------------

    def list_dir(
        self,
        relative_path: str = "",
        recursive: bool = False,
        file_mask: str | None = None,
        max_entries: int | None = None,
    ) -> str:
        """
        List the contents of *relative_path* (defaults to project root).

        Parameters
        ----------
        file_mask:
            Glob pattern to filter files, e.g. ``"*.lean"`` or ``"**/*.py"``.
        max_entries:
            Cap the number of returned entries (files + dirs combined).
        """
        abs_dir = self._abs(relative_path) if relative_path else self.root_path

        if not os.path.isdir(abs_dir):
            return f"Not a directory: {relative_path}"

        scan = _scan_directory(abs_dir, recursive=recursive, relative_to=abs_dir)
        dirs = sorted(scan.directories)
        files = sorted(scan.files)

        if file_mask:
            patterns = _expand_braces(file_mask)
            files = [f for f in files if any(_glob_match(p, f) for p in patterns)]

        entries: list[str] = [f"[dir]  {d}" for d in dirs] + [f"[file] {f}" for f in files]

        if max_entries and len(entries) > max_entries:
            entries = entries[:max_entries]
            entries.append(f"... [{max_entries} entries shown, use max_entries to adjust]")

        header = f"Contents of '{relative_path or '.'}'"
        return self._limit(header + "\n" + "\n".join(entries))

    # ------------------------------------------------------------------
    # Public: find_file
    # ------------------------------------------------------------------

    def find_file(
        self,
        filename_pattern: str,
        search_dir: str = "",
        max_results: int = 50,
    ) -> str:
        """
        Recursively find files whose *name* (not full path) matches
        *filename_pattern* (glob).

        Returns a newline-separated list of relative paths.
        """
        files = self._all_files(search_dir)
        matched = [
            f
            for f in files
            if fnmatch.fnmatch(os.path.basename(f), filename_pattern)
        ]
        if not matched:
            return f"No files matching '{filename_pattern}' found."
        if len(matched) > max_results:
            matched = matched[:max_results]
            matched.append(f"... (capped at {max_results})")
        return self._limit("\n".join(matched))

    # ------------------------------------------------------------------
    # Public: search_for_pattern
    # ------------------------------------------------------------------

    def search_for_pattern(
        self,
        pattern: str,
        relative_path: str = "",
        context_before: int = 0,
        context_after: int = 0,
        include_glob: str | None = None,
        exclude_glob: str | None = None,
        max_matches: int | None = None,
        max_files: int | None = None,
    ) -> str:
        """
        Search for a regex *pattern* across files under *relative_path*.

        Parameters
        ----------
        context_before / context_after:
            Lines of context around each match.
        include_glob / exclude_glob:
            Glob patterns to filter which files are searched.
        max_matches:
            Stop after this many total matches.
        max_files:
            Search at most this many files.
        """
        all_files = self._all_files(relative_path)

        # Filter by glob
        inc_pats = _expand_braces(include_glob) if include_glob else None
        exc_pats = _expand_braces(exclude_glob) if exclude_glob else None
        filtered: list[str] = []
        for f in all_files:
            if inc_pats and not any(_glob_match(p, f) for p in inc_pats):
                continue
            if exc_pats and any(_glob_match(p, f) for p in exc_pats):
                continue
            filtered.append(f)

        if max_files:
            filtered = filtered[:max_files]

        all_matches: list[MatchedLines] = []
        for rel_file in filtered:
            abs_file = os.path.join(self.root_path, rel_file)
            try:
                content = self._read_raw(abs_file)
            except OSError:
                continue
            hits = _search_text(
                pattern,
                content,
                source_file_path=rel_file,
                context_before=context_before,
                context_after=context_after,
            )
            all_matches.extend(hits)
            if max_matches and len(all_matches) >= max_matches:
                all_matches = all_matches[:max_matches]
                break

        if not all_matches:
            return f"No matches for '{pattern}'."

        sections: list[str] = []
        for m in all_matches:
            header = f"--- {m.source_file_path} ---"
            body = m.to_display_string(include_line_numbers=True)
            sections.append(f"{header}\n{body}")

        return self._limit("\n\n".join(sections))

    # ------------------------------------------------------------------
    # Public: stacked_search
    # ------------------------------------------------------------------

    def stacked_search(
        self,
        seed_pattern: str,
        follow_up_pattern: str,
        search_dir: str = "",
        include_glob: str | None = None,
        context_before: int = 0,
        context_after: int = 2,
        max_seed_files: int | None = None,
        max_results: int | None = None,
    ) -> str:
        """
        Two-phase search:

        1. **Seed pass** — find all files containing *seed_pattern*.
        2. **Follow-up pass** — search only those files for *follow_up_pattern*,
           returning detailed matches with context.

        Useful for: find all files that import a module, then show how they
        use a specific function within those files.

        Parameters
        ----------
        max_seed_files:
            Cap on how many files pass phase 1 into phase 2.
        max_results:
            Cap on total match blocks returned.
        """
        all_files = self._all_files(search_dir)

        inc_pats = _expand_braces(include_glob) if include_glob else None
        filtered: list[str] = []
        for f in all_files:
            if inc_pats and not any(_glob_match(p, f) for p in inc_pats):
                continue
            filtered.append(f)

        # Phase 1: seed
        seed_files: list[str] = []
        try:
            seed_re = re.compile(seed_pattern, re.DOTALL)
        except re.error as exc:
            return f"Invalid seed pattern: {exc}"

        for rel_file in filtered:
            abs_file = os.path.join(self.root_path, rel_file)
            try:
                content = self._read_raw(abs_file)
            except OSError:
                continue
            if seed_re.search(content):
                seed_files.append(rel_file)
            if max_seed_files and len(seed_files) >= max_seed_files:
                break

        if not seed_files:
            return f"Seed pattern '{seed_pattern}' matched no files."

        # Phase 2: follow-up
        all_matches: list[MatchedLines] = []
        try:
            _ = re.compile(follow_up_pattern)
        except re.error as exc:
            return f"Invalid follow-up pattern: {exc}"

        for rel_file in seed_files:
            abs_file = os.path.join(self.root_path, rel_file)
            try:
                content = self._read_raw(abs_file)
            except OSError:
                continue
            hits = _search_text(
                follow_up_pattern,
                content,
                source_file_path=rel_file,
                context_before=context_before,
                context_after=context_after,
            )
            all_matches.extend(hits)
            if max_results and len(all_matches) >= max_results:
                all_matches = all_matches[:max_results]
                break

        if not all_matches:
            return (
                f"Follow-up pattern '{follow_up_pattern}' matched nothing in the "
                f"{len(seed_files)} files that contained '{seed_pattern}'."
            )

        summary = (
            f"Seed '{seed_pattern}' → {len(seed_files)} files. "
            f"Follow-up '{follow_up_pattern}' → {len(all_matches)} matches.\n\n"
        )
        sections: list[str] = []
        for m in all_matches:
            sections.append(f"--- {m.source_file_path} ---\n{m.to_display_string()}")

        return self._limit(summary + "\n\n".join(sections))
