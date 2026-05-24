"""
Vendor selected modules from mvanhorn/last30days-skill.

Run: python scripts/vendor_last30days.py [<commit-sha>]
If sha is omitted, defaults to 'main' (records actual resolved SHA in UPSTREAM.md).
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "mvanhorn/last30days-skill"
BASE_PATH = "skills/last30days/scripts/lib"
DEST = Path(__file__).resolve().parent.parent / "aggregator" / "vendor" / "last30days"

MODULES = [
    "__init__.py",
    "reddit.py",
    "reddit_public.py",
    "reddit_enrich.py",
    "polymarket.py",
    "dedupe.py",
    "cluster.py",
    "rerank.py",
    "signals.py",
    "relevance.py",
    "normalize.py",
    "schema.py",
    "http.py",
    "dates.py",
    "env.py",
    "log.py",
    "query.py",
    "providers.py",
]


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()


def resolve_sha(ref: str) -> str:
    import json
    data = json.loads(fetch(f"https://api.github.com/repos/{REPO}/commits/{ref}").decode())
    return data["sha"]


def main() -> None:
    ref = sys.argv[1] if len(sys.argv) > 1 else "main"
    sha = resolve_sha(ref)
    print(f"Vendoring {REPO}@{sha}")
    DEST.mkdir(parents=True, exist_ok=True)

    missing: list[str] = []
    for mod in MODULES:
        url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{BASE_PATH}/{mod}"
        print(f"  fetching {mod}")
        try:
            (DEST / mod).write_bytes(fetch(url))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"    SKIP: {mod} not found at upstream (404)")
                missing.append(mod)
            else:
                raise

    license_url = f"https://raw.githubusercontent.com/{REPO}/{sha}/LICENSE"
    (DEST / "LICENSE").write_bytes(fetch(license_url))

    (DEST / "UPSTREAM.md").write_text(
        f"# Upstream provenance\n\n"
        f"Source: https://github.com/{REPO}\n"
        f"Commit: {sha}\n"
        f"Path: {BASE_PATH}/\n\n"
        f"## Vendored modules\n\n"
        + "\n".join(f"- `{m}`" for m in MODULES)
        + "\n\n## Modifications\n\n"
        f"- Import paths adjusted: any intra-package import within these modules\n"
        f"  (e.g., `from .http import ...`) continues to resolve because we kept the\n"
        f"  package layout. Imports of upstream-only modules NOT vendored here will\n"
        f"  fail and must be surgically removed or stubbed when first encountered.\n"
        f"- No logic changes.\n",
        encoding="utf-8",
    )
    if missing:
        print(f"WARNING: {len(missing)} modules missing upstream: {missing}")
    print(f"Done. Wrote {len(MODULES) - len(missing)} modules + LICENSE + UPSTREAM.md to {DEST}")


if __name__ == "__main__":
    main()
