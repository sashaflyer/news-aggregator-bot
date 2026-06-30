"""Merge new topics from config.example.toml into config.toml.

Reads both files, finds topics present in the example but missing from the
live config, and appends them as raw TOML text. Existing topics and all
non-topic settings are preserved unchanged.

Usage:
    python scripts/merge_config.py              # default paths
    python scripts/merge_config.py /path/to/config.toml
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.toml"


def _topic_header(name: str) -> str:
    return f"[topics.{name}]"


def _extract_topic_block(text: str, topic: str) -> str:
    """Return the raw TOML block for ``[topics.<topic>]`` from *text*.

    Captures everything from the header line to the next ``[`` at column 0
    (or end-of-file). Nested ``[[topics.<topic>....]]`` arrays are included.
    """
    header = _topic_header(topic)
    # Find header at start of line to avoid false-matching prefix names
    # (e.g., crypto_general matching inside crypto_general_extra).
    pattern = re.compile(rf"^{re.escape(header)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    # Start from the beginning of the matched line
    start = match.start()

    # Find the next top-level section header after this block.
    after_header = text[start + len(header):]
    # A top-level header is a ``[`` at the start of a line that is NOT
    # an array-of-tables ``[[`` for this topic.
    m = re.search(r"^\[(?!\[)", after_header, re.MULTILINE)
    end = start + len(header) + m.start() if m else len(text)
    return text[start:end].rstrip() + "\n"


def merge_topics(live_path: Path) -> list[str]:
    """Add missing topics from the example into *live_path*.

    Returns a list of topic names that were added.
    """
    # Read once and reuse for both text operations and TOML parsing.
    example_bytes = EXAMPLE.read_bytes()
    live_bytes = live_path.read_bytes()
    example_text = example_bytes.decode("utf-8")
    live_text = live_bytes.decode("utf-8")
    example_cfg = tomllib.loads(example_text)
    live_cfg = tomllib.loads(live_text)

    example_topics = set(example_cfg.get("topics", {}))
    live_topics = set(live_cfg.get("topics", {}))
    missing = sorted(example_topics - live_topics)

    if not missing:
        return []

    blocks = []
    for topic in missing:
        block = _extract_topic_block(example_text, topic)
        if block:
            blocks.append(block)

    if not blocks:
        return []

    # Append to live config, ensuring a blank line separator.
    appended = "\n".join(blocks)
    if not live_text.endswith("\n"):
        live_text += "\n"
    live_path.write_text(live_text + "\n" + appended, encoding="utf-8")
    return missing


def main() -> None:
    live = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "config.toml"
    if not live.exists():
        print(f"config not found: {live}")
        sys.exit(1)
    if not EXAMPLE.exists():
        print(f"example config not found: {EXAMPLE}")
        sys.exit(1)

    added = merge_topics(live)
    if added:
        print(f"added {len(added)} topic(s) to {live.name}: {', '.join(added)}")
    else:
        print("no new topics to add")


if __name__ == "__main__":
    main()
