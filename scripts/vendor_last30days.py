"""
Vendor selected modules from mvanhorn/last30days-skill.

Run: python scripts/vendor_last30days.py [<commit-sha>]

Resolution order for the ref:
  1. Explicit argv[1], if given.
  2. The `Commit:` line of an existing DEST/UPSTREAM.md (re-pin to last vendor).
  3. Otherwise: exit with an error. Floating to upstream `main` is a
     reproducibility footgun — first-time bootstrap on different days would
     yield different code with no audit trail.

Each run is a clean re-vendor: DEST is removed and recreated before fetching,
so stale files left over from a previous MODULES list cannot linger.
"""
from __future__ import annotations

import re
import shutil
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
    "snippet.py",
    "hackernews.py",
]

# Modules vendored from paths OTHER than BASE_PATH.
# Each entry is (upstream_relative_path, local_filename).
EXTRA_MODULES = [
    ("skills/last30days/scripts/store.py", "store.py"),
]

# Files (by local_name) that need the `from lib import X` -> `from . import X`
# rewrite after fetch. Add one entry per file; no code changes required.
PATCH_FROM_LIB_IMPORT = {"store.py"}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()


def resolve_sha(ref: str) -> str:
    import json
    data = json.loads(fetch(f"https://api.github.com/repos/{REPO}/commits/{ref}").decode())
    return data["sha"]


def _read_pinned_sha_from(upstream_md_path: Path) -> str | None:
    """Return the SHA recorded in upstream_md_path, or None if missing/unparseable."""
    if not upstream_md_path.exists():
        return None
    for line in upstream_md_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Commit:"):
            return line.split(":", 1)[1].strip()
    return None


def _read_pinned_sha() -> str | None:
    """Return the SHA recorded in DEST/UPSTREAM.md, or None if not present."""
    return _read_pinned_sha_from(DEST / "UPSTREAM.md")


def resolve_ref(argv_sha: str | None, upstream_md_path: Path) -> str:
    """Resolve a vendor ref or exit. No silent fallback to upstream main."""
    if argv_sha:
        return argv_sha
    pinned = _read_pinned_sha_from(upstream_md_path)
    if pinned:
        return pinned
    sys.exit(
        "no SHA provided and UPSTREAM.md is missing or unparseable. "
        "Pass an explicit SHA: python scripts/vendor_last30days.py <sha>"
    )


def _patch_from_lib_import(path: Path) -> None:
    """Rewrite `from lib import X` -> `from . import X` in `path`. Loud on no-op."""
    text = path.read_text(encoding="utf-8")
    patched, n = re.subn(r"^from lib\s+import\s+", "from . import ", text, flags=re.MULTILINE)
    if n == 0:
        raise RuntimeError(
            f"Expected to rewrite at least one 'from lib import ...' in {path.name}, "
            f"but found none. Upstream layout may have changed; re-pin and re-check."
        )
    path.write_text(patched, encoding="utf-8")
    print(f"  patched {path.name}: rewrote {n} `from lib import ...` -> `from . import ...`")


def main() -> None:
    # Resolve the ref BEFORE wiping DEST, so a previously-pinned SHA can be read.
    argv_sha = sys.argv[1] if len(sys.argv) > 1 else None
    ref = resolve_ref(argv_sha, DEST / "UPSTREAM.md")
    if argv_sha:
        print(f"Using explicit ref from argv: {ref}")
    else:
        print(f"Re-using previously pinned SHA from UPSTREAM.md: {ref}")

    sha = resolve_sha(ref)
    print(f"Vendoring {REPO}@{sha}")

    # Clean re-vendor: wipe DEST so files removed from MODULES don't linger.
    if DEST.exists():
        shutil.rmtree(DEST)
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

    for upstream_rel_path, local_name in EXTRA_MODULES:
        url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{upstream_rel_path}"
        print(f"  fetching {upstream_rel_path}")
        try:
            (DEST / local_name).write_bytes(fetch(url))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"    SKIP: {upstream_rel_path} not found at upstream (404)")
                missing.append(upstream_rel_path)
            else:
                raise

    # Surgical patch: rewrite `from lib import X` -> `from . import X` in
    # files that need it (currently just store.py, which lived at scripts/
    # upstream and imported lib/ submodules via sys.path manipulation).
    for local_name in PATCH_FROM_LIB_IMPORT:
        target = DEST / local_name
        if target.exists():
            _patch_from_lib_import(target)

    license_url = f"https://raw.githubusercontent.com/{REPO}/{sha}/LICENSE"
    (DEST / "LICENSE").write_bytes(fetch(license_url))

    extra_lines = "\n".join(
        f"- `{local}` (from `{path}`)" for path, local in EXTRA_MODULES
    )
    (DEST / "UPSTREAM.md").write_text(
        f"# Upstream provenance\n\n"
        f"Source: https://github.com/{REPO}\n"
        f"Commit: {sha}\n"
        f"Path: {BASE_PATH}/\n\n"
        f"To reproduce this exact vendor: `python scripts/vendor_last30days.py {sha}`\n\n"
        f"## Vendored modules (from `{BASE_PATH}/`)\n\n"
        + "\n".join(f"- `{m}`" for m in MODULES)
        + "\n\n## Vendored modules (from other paths)\n\n"
        + extra_lines
        + "\n\n## Modifications\n\n"
        f"- Import paths adjusted: any intra-package import within these modules\n"
        f"  (e.g., `from .http import ...`) continues to resolve because we kept the\n"
        f"  package layout. Imports of upstream-only modules NOT vendored here will\n"
        f"  fail and must be surgically removed or stubbed when first encountered.\n"
        f"- `store.py` was at `scripts/` upstream (parent of `lib/`) and used\n"
        f"  `sys.path.insert(SCRIPT_DIR); from lib import schema`. The vendor\n"
        f"  script rewrites this to `from . import schema` so the import resolves\n"
        f"  in our flat package layout. The orphan `sys.path.insert` lines are\n"
        f"  left in place as harmless no-ops to minimize the patch surface.\n"
        f"- No other logic changes.\n\n"
        f"## Deviations from initial vendor spec\n\n"
        f"- `store.py` does not exist at `{BASE_PATH}/` (404). It lives at\n"
        f"  `skills/last30days/scripts/store.py` (parent dir of `lib/`) and is\n"
        f"  fetched via `EXTRA_MODULES` in `scripts/vendor_last30days.py`.\n"
        f"- `query.py` and `providers.py` were added to `MODULES` after the\n"
        f"  smoke import test surfaced `ModuleNotFoundError` from `reddit.py`\n"
        f"  (imports `.query`) and `rerank.py` (imports `.providers` and\n"
        f"  `.query`).\n",
        encoding="utf-8",
    )
    total = len(MODULES) + len(EXTRA_MODULES) - len(missing)
    if missing:
        print(f"WARNING: {len(missing)} modules missing upstream: {missing}")
    print(f"Done. Wrote {total} modules + LICENSE + UPSTREAM.md to {DEST}")


if __name__ == "__main__":
    main()
