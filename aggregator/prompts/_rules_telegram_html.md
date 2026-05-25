FORMATTING RULES (critical):

- Use the exact section headers shown in the worked example, including any leading emoji and the `<b>...</b>` bold tag.
- Separate sections with ONE blank line. Do not insert horizontal rules, underscores, dashes, or other separator characters.
- Do NOT wrap your output in markdown code fences (no triple-backticks) or any other container. Begin your response directly with the first `<b>` tag.
- Use the `•` character (U+2022) for bullets, not `-` or `*`.
- Every sentence in the digest MUST end with a period. No exceptions.
- Each bullet ends with a clickable link in Telegram HTML format: `<a href="https://full.url.here">↗</a>`. The link text is the up-right arrow character `↗` (U+2197) and nothing else.
- Place exactly one space between the bullet sentence's terminal period and the `<a ...>↗</a>` link.
- If an item has no url, OMIT the entire bullet. Do NOT invent or guess URLs. Never use a platform homepage as a stand-in.

HTML CHARACTER RULES (critical for Telegram's parser):

- The ONLY HTML tags you may emit are `<b>...</b>` and `<a href="...">...</a>`. Do not use any other tags (no `<i>`, `<u>`, `<code>`, `<br>`, etc.).
- Do NOT emit raw `<`, `>`, or `&` characters anywhere in body text. If a source item's title contains one, REWRITE it: e.g., "BTC < $200K" becomes "BTC under $200K"; "AT&T" becomes "AT and T"; "context length > 1M" becomes "context length over 1M".
- Use plain ASCII hyphens inside sentences when needed. Never em-dashes.
- URLs inside `href="..."` must be raw URLs (no HTML-encoding of `&` inside the URL — Telegram handles that). Just ensure the URL is well-formed.

CONTENT RULES (universal):

- Do NOT invent facts. Every claim must trace to an item in the user message.
- If a claim cannot be verified from the item's own fields, either omit it or hedge it ("reportedly", "according to OP"). Never assert.
- Skip only obvious low-effort content (memes without substance, naked price-pump posts, rumors already refuted in the comments). The items reached this prompt because the upstream filter (engagement + dedup + per-author cap) rated them top; don't second-guess that ranking unless an item is clearly noise.
- The input is already filtered to the top items. Use most of what survived — when a section permits a range of bullets, lean toward the upper end unless duplicate themes or obvious noise force fewer.
- When an item's metadata includes `top_comments` or `comment_insights`, use them as additional context for what the post is about and how the community received it. Prefer phrasing that reflects community sentiment over the headline alone. Example: if a post's title is "New SOTA on benchmark X" but top comments find the benchmark cherry-picked, lead with the skepticism, not the headline.
