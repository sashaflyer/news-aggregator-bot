import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

_INCLUDE_RE = re.compile(r"\{include:([A-Za-z0-9_\-]+)\}")


def load(name: str) -> str:
    """Load a prompt template and expand `{include:partial}` directives.

    A directive like ``{include:_rules_telegram_html}`` is replaced with the
    contents of ``_rules_telegram_html.md`` in the same directory. One level of
    expansion only — partials may not themselves include other partials.
    """
    target = (PROMPTS_DIR / name).resolve()
    if not target.is_relative_to(PROMPTS_DIR.resolve()):
        raise ValueError(f"prompt template escapes PROMPTS_DIR: {name!r}")
    if target.suffix != ".md":
        raise ValueError(f"prompt template must be a .md file: {name!r}")
    raw = target.read_text(encoding="utf-8")

    def _expand(m: re.Match[str]) -> str:
        partial_path = (PROMPTS_DIR / f"{m.group(1)}.md").resolve()
        if not partial_path.is_relative_to(PROMPTS_DIR.resolve()):
            raise ValueError(f"include partial escapes PROMPTS_DIR: {m.group(1)!r}")
        return partial_path.read_text(encoding="utf-8").rstrip()

    return _INCLUDE_RE.sub(_expand, raw)
