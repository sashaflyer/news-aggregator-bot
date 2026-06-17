"""Quick smoke test for merge_config."""
import tempfile
import tomllib
from pathlib import Path

from scripts.merge_config import merge_topics

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.toml"


def test_merge_adds_missing_topic():
    example_text = EXAMPLE.read_text(encoding="utf-8")
    lines = example_text.split("\n")
    # Find where github_trending block starts
    start = next(i for i, l in enumerate(lines) if "[topics.github_trending]" in l)
    # Remove it to simulate a live config missing that topic
    live_text = "\n".join(lines[:start]).rstrip() + "\n"

    tmp = Path(tempfile.mktemp(suffix=".toml"))
    try:
        tmp.write_text(live_text)
        added = merge_topics(tmp)
        assert added == ["github_trending"], f"expected [github_trending], got {added}"

        with open(tmp, "rb") as f:
            cfg = tomllib.load(f)
        assert "github_trending" in cfg["topics"]
        assert cfg["topics"]["github_trending"]["schedule"] == "20 5,17 * * *"
    finally:
        tmp.unlink()


def test_merge_noop_when_all_present():
    tmp = Path(tempfile.mktemp(suffix=".toml"))
    try:
        tmp.write_text(EXAMPLE.read_text(encoding="utf-8"))
        added = merge_topics(tmp)
        assert added == [], f"expected empty, got {added}"
    finally:
        tmp.unlink()
