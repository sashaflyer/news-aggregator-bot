# Upstream provenance

Source: https://github.com/mvanhorn/last30days-skill
Commit: 1e03af19e0ad435ee6d227a3593b0c6e5d2ecbe8
Path: skills/last30days/scripts/lib/

## Vendored modules

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

## Modifications

- Import paths adjusted: any intra-package import within these modules
  (e.g., `from .http import ...`) continues to resolve because we kept the
  package layout. Imports of upstream-only modules NOT vendored here will
  fail and must be surgically removed or stubbed when first encountered.
- No logic changes to any vendored file.

## Deviations from initial vendor spec

- `store.py` was in the task spec but does not exist at the upstream path
  (404). Removed from `MODULES` in `scripts/vendor_last30days.py`.
- `query.py` and `providers.py` were added to `MODULES` after the smoke
  import test surfaced `ModuleNotFoundError` from `reddit.py` (imports
  `.query`) and `rerank.py` (imports `.providers` and `.query`).
