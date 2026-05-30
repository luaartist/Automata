"""Tests for lean4_file_tools — no Serena deps required."""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap

import pytest

# Allow running directly from the tools/ directory
sys.path.insert(0, os.path.dirname(__file__))
from lean4_file_tools import FileToolsContext


@pytest.fixture()
def tmp_repo(tmp_path):
    """Create a small fake repo tree for testing."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Alpha.lean").write_text(
        textwrap.dedent(
            """\
            import Mathlib.Algebra.Group.Basic
            -- Alpha module
            def foo : Nat := 42
            def bar : Nat := foo + 1
            """
        )
    )
    (tmp_path / "src" / "Beta.lean").write_text(
        textwrap.dedent(
            """\
            import Mathlib.Data.List
            -- Beta module
            def baz : List Nat := [1, 2, 3]
            """
        )
    )
    (tmp_path / "src" / "notes.txt").write_text("not lean\n")
    (tmp_path / "README.md").write_text("# Project\n")
    return tmp_path


@pytest.fixture()
def ctx(tmp_repo):
    return FileToolsContext(root_path=str(tmp_repo))


# -----------------------------------------------------------------------
# list_dir
# -----------------------------------------------------------------------


def test_list_dir_root(ctx):
    result = ctx.list_dir()
    assert "README.md" in result
    assert "src" in result


def test_list_dir_recursive(ctx):
    result = ctx.list_dir(recursive=True)
    assert "Alpha.lean" in result


def test_list_dir_file_mask(ctx):
    result = ctx.list_dir(relative_path="src", file_mask="*.lean")
    assert "Alpha.lean" in result
    assert "Beta.lean" in result
    assert "notes.txt" not in result


def test_list_dir_max_entries(ctx):
    result = ctx.list_dir(recursive=True, max_entries=1)
    assert "entries shown" in result or result.count("\n") <= 2


# -----------------------------------------------------------------------
# find_file
# -----------------------------------------------------------------------


def test_find_file_exact(ctx):
    result = ctx.find_file("Alpha.lean")
    assert "Alpha.lean" in result


def test_find_file_glob(ctx):
    result = ctx.find_file("*.lean")
    assert "Alpha.lean" in result
    assert "Beta.lean" in result


def test_find_file_no_match(ctx):
    result = ctx.find_file("nonexistent.xyz")
    assert "No files" in result


# -----------------------------------------------------------------------
# read_file / copy_lines
# -----------------------------------------------------------------------


def test_read_file_full(ctx):
    content = ctx.read_file("src/Alpha.lean")
    assert "def foo" in content


def test_read_file_range(ctx):
    content = ctx.read_file("src/Alpha.lean", start_line=2, end_line=2)
    assert "def foo" in content
    assert "import" not in content


def test_copy_lines_with_numbers(ctx):
    result = ctx.copy_lines("src/Alpha.lean", start_line=0, end_line=1, include_line_numbers=True)
    # Line numbers should be present
    assert "1:" in result or "     1:" in result
    assert "import" in result


def test_copy_lines_without_numbers(ctx):
    result = ctx.copy_lines("src/Alpha.lean", start_line=0, end_line=0, include_line_numbers=False)
    # No leading digits
    assert result.strip().startswith("import")


def test_copy_lines_invalid_range(ctx):
    result = ctx.copy_lines("src/Alpha.lean", start_line=100, end_line=200)
    assert "Invalid range" in result


# -----------------------------------------------------------------------
# search_for_pattern
# -----------------------------------------------------------------------


def test_search_for_pattern_basic(ctx):
    result = ctx.search_for_pattern("def foo")
    assert "Alpha.lean" in result
    assert "def foo" in result


def test_search_for_pattern_include_glob(ctx):
    result = ctx.search_for_pattern("import", include_glob="*.lean")
    assert "import" in result
    # notes.txt should not appear
    assert "notes.txt" not in result


def test_search_for_pattern_context(ctx):
    result = ctx.search_for_pattern("def foo", context_after=1)
    assert "def bar" in result  # next line


def test_search_for_pattern_no_match(ctx):
    result = ctx.search_for_pattern("TOTALLY_ABSENT_xyz123")
    assert "No matches" in result


def test_search_for_pattern_max_matches(ctx):
    # "def" appears in both Alpha and Beta; cap at 1
    result = ctx.search_for_pattern("def ", max_matches=1)
    # Only one match block should be returned (one --- header)
    assert result.count("---") <= 2  # header open + 1 match


# -----------------------------------------------------------------------
# stacked_search
# -----------------------------------------------------------------------


def test_stacked_search_basic(ctx):
    # Seed: files that import Mathlib; follow-up: lines with "def"
    result = ctx.stacked_search(seed_pattern=r"import Mathlib", follow_up_pattern=r"def ")
    assert "def foo" in result or "def baz" in result
    assert "Seed" in result  # summary line


def test_stacked_search_no_seed_match(ctx):
    result = ctx.stacked_search(seed_pattern="NONEXISTENT_XYZ", follow_up_pattern="def")
    assert "matched no files" in result


def test_stacked_search_follow_up_no_match(ctx):
    result = ctx.stacked_search(seed_pattern=r"import Mathlib", follow_up_pattern="NONEXISTENT_XYZ")
    assert "matched nothing" in result


def test_stacked_search_max_seed_files(ctx):
    result = ctx.stacked_search(
        seed_pattern=r"import Mathlib",
        follow_up_pattern=r"def ",
        max_seed_files=1,
    )
    # Should still return something (1 file searched)
    assert "def" in result


# -----------------------------------------------------------------------
# truncation
# -----------------------------------------------------------------------


def test_truncation(tmp_path):
    big_file = tmp_path / "big.lean"
    big_file.write_text("x\n" * 5000)
    ctx = FileToolsContext(root_path=str(tmp_path), max_output_length=100)
    result = ctx.read_file("big.lean")
    assert "[truncated]" in result
    assert len(result) <= 130  # 100 + short truncation message
