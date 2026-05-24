# Upstream provenance

Source: https://github.com/mvanhorn/last30days-skill
Commit: 1e03af19e0ad435ee6d227a3593b0c6e5d2ecbe8
Path: skills/last30days/scripts/lib/

To reproduce this exact vendor: `python scripts/vendor_last30days.py 1e03af19e0ad435ee6d227a3593b0c6e5d2ecbe8`

## Vendored modules (from `skills/last30days/scripts/lib/`)

- `__init__.py`
- `reddit.py`
- `reddit_public.py`
- `reddit_enrich.py`
- `polymarket.py`
- `dedupe.py`
- `cluster.py`
- `rerank.py`
- `signals.py`
- `relevance.py`
- `normalize.py`
- `schema.py`
- `http.py`
- `dates.py`
- `env.py`
- `log.py`
- `query.py`
- `providers.py`

## Vendored modules (from other paths)

- `store.py` (from `skills/last30days/scripts/store.py`)

## Modifications

- Import paths adjusted: any intra-package import within these modules
  (e.g., `from .http import ...`) continues to resolve because we kept the
  package layout. Imports of upstream-only modules NOT vendored here will
  fail and must be surgically removed or stubbed when first encountered.
- `store.py` was at `scripts/` upstream (parent of `lib/`) and used
  `sys.path.insert(SCRIPT_DIR); from lib import schema`. The vendor
  script rewrites this to `from . import schema` so the import resolves
  in our flat package layout. The orphan `sys.path.insert` lines are
  left in place as harmless no-ops to minimize the patch surface.
- No other logic changes.

## Deviations from initial vendor spec

- `store.py` does not exist at `skills/last30days/scripts/lib/` (404). It lives at
  `skills/last30days/scripts/store.py` (parent dir of `lib/`) and is
  fetched via `EXTRA_MODULES` in `scripts/vendor_last30days.py`.
- `query.py` and `providers.py` were added to `MODULES` after the
  smoke import test surfaced `ModuleNotFoundError` from `reddit.py`
  (imports `.query`) and `rerank.py` (imports `.providers` and
  `.query`).
