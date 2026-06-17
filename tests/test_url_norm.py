from aggregator.url_norm import canonicalize, dedup_key


def test_trailing_slash_collapsed():
    assert canonicalize("https://x.example/a/") == canonicalize("https://x.example/a")


def test_host_case_collapsed():
    assert canonicalize("https://X.Example/a") == canonicalize("https://x.example/a")


def test_strips_utm_params():
    assert canonicalize("https://x.example/a?utm_source=newsletter&id=5") == \
           canonicalize("https://x.example/a?id=5")



def test_scheme_lowered():
    assert canonicalize("HTTPS://x/A") == canonicalize("https://x/A")


def test_fragment_dropped():
    assert canonicalize("https://x/a#top") == canonicalize("https://x/a")


def test_dedup_key_normal_url():
    item = {"url": "https://X.Example/a/?utm_source=z", "id": "r:1"}
    assert dedup_key(item) == "https://x.example/a"


def test_dedup_key_no_url_falls_back_to_id():
    item = {"url": "", "id": "polymarket:abc"}
    assert dedup_key(item) == "id:polymarket:abc"


def test_dedup_key_neither_url_nor_id():
    item = {"url": "", "id": ""}
    assert dedup_key(item) is None


def test_dedup_key_empty_dict():
    assert dedup_key({}) is None
