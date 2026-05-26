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


def test_keeps_code_and_i():
    out = sanitize_outgoing("<i>italic</i> <code>x</code> <pre>y</pre>")
    assert "<i>italic</i>" in out
    assert "<code>x</code>" in out
    assert "<pre>y</pre>" in out


def test_passthrough_when_no_tags():
    assert sanitize_outgoing("just text & stuff") == "just text & stuff"


def test_drops_relative_href():
    out = sanitize_outgoing('<a href="/local">x</a>')
    assert "<a" not in out
