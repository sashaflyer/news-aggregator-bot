"""Tests for the vendor pin-or-die behavior in scripts/vendor_last30days.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ isn't on sys.path by default; add the repo root so we can import.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.vendor_last30days import resolve_ref  # noqa: E402


def test_vendor_script_refuses_without_pin_or_argv(tmp_path):
    upstream_md = tmp_path / "UPSTREAM.md"  # doesn't exist
    with pytest.raises(SystemExit):
        resolve_ref(argv_sha=None, upstream_md_path=upstream_md)


def test_vendor_script_uses_argv_sha(tmp_path):
    sha = resolve_ref(argv_sha="abc123", upstream_md_path=tmp_path / "missing.md")
    assert sha == "abc123"


def test_vendor_script_reads_pinned_sha(tmp_path):
    upstream_md = tmp_path / "UPSTREAM.md"
    upstream_md.write_text("Source: https://github.com/x/y\nCommit: deadbeef\n",
                           encoding="utf-8")
    sha = resolve_ref(argv_sha=None, upstream_md_path=upstream_md)
    assert sha == "deadbeef"


def test_vendor_script_argv_takes_precedence_over_pinned(tmp_path):
    upstream_md = tmp_path / "UPSTREAM.md"
    upstream_md.write_text("Commit: deadbeef\n", encoding="utf-8")
    sha = resolve_ref(argv_sha="explicit", upstream_md_path=upstream_md)
    assert sha == "explicit"
