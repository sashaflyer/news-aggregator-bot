from aggregator.delivery._html_filter import sanitize_outgoing


def test_strips_disallowed_tag():
    # Disallowed tag is removed; inner text preserved.
    assert sanitize_outgoing("<script>x</script>hello") == "xhello"


def test_keeps_b_and_a():
    out = sanitize_outgoing('<b>bold</b> <a href="https://x.example/a">link</a>')
    assert "<b>bold</b>" in out
    assert '<a href="https://x.example/a">link</a>' in out


def test_drops_non_http_href():
    out = sanitize_outgoing('<a href="javascript:alert(1)">x</a>')
    assert "javascript" not in out
    # Opening anchor tag stripped entirely; closing tag also stripped to match.
    assert "<a" not in out


def test_keeps_i_code_pre():
    out = sanitize_outgoing("<i>italic</i> <code>x</code> <pre>y</pre>")
    assert "<i>italic</i>" in out
    assert "<code>x</code>" in out
    assert "<pre>y</pre>" in out


def test_passthrough_when_no_tags():
    assert sanitize_outgoing("just text & stuff") == "just text & stuff"


def test_drops_relative_href():
    out = sanitize_outgoing('<a href="/local">x</a>')
    assert "<a" not in out


def test_nested_disallowed_in_allowed():
    out = sanitize_outgoing("<b><script>x</script></b>")
    assert "<script>" not in out
    assert "</script>" not in out
    assert "<b>x</b>" in out


def test_malformed_anchor_href_stripped():
    out = sanitize_outgoing('<a href=bad>text</a>')
    assert "<a" not in out
    assert "text" in out


def test_multiple_attributes_keeps_only_href():
    out = sanitize_outgoing('<a target="_blank" href="https://x">link</a>')
    assert 'target' not in out
    assert '<a href="https://x">link</a>' in out


def test_allowed_tag_i_passthrough():
    out = sanitize_outgoing("<i>italic</i>")
    assert "<i>italic</i>" in out


def test_allowed_tag_code_passthrough():
    out = sanitize_outgoing("<code>code</code>")
    assert "<code>code</code>" in out


def test_allowed_tag_pre_passthrough():
    out = sanitize_outgoing("<pre>pre</pre>")
    assert "<pre>pre</pre>" in out


def test_disallowed_script_strips_tags_preserves_text():
    out = sanitize_outgoing("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "</script>" not in out
    assert "alert(1)" in out
