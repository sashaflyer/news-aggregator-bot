from aggregator.url_norm import canonicalize


def test_trailing_slash_collapsed():
    assert canonicalize("https://x.example/a/") == canonicalize("https://x.example/a")


def test_host_case_collapsed():
    assert canonicalize("https://X.Example/a") == canonicalize("https://x.example/a")


def test_strips_utm_params():
    assert canonicalize("https://x.example/a?utm_source=newsletter&id=5") == \
           canonicalize("https://x.example/a?id=5")


def test_reddit_subdomain_unified():
    assert canonicalize("https://old.reddit.com/r/x/comments/abc/foo") == \
           canonicalize("https://www.reddit.com/r/x/comments/abc/foo")


def test_scheme_lowered():
    assert canonicalize("HTTPS://x/A") == canonicalize("https://x/A")


def test_fragment_dropped():
    assert canonicalize("https://x/a#top") == canonicalize("https://x/a")
