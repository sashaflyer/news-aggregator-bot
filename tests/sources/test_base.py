from datetime import datetime, timezone

from aggregator.sources.base import Item


def test_item_roundtrip_to_dict():
    item = Item(
        id="reddit:t3_abc",
        source="reddit",
        title="hello",
        url="https://reddit.com/abc",
        text="body",
        created_at=datetime(2026, 5, 24, 8, 0, tzinfo=timezone.utc),
        engagement_raw={"upvotes": 100},
        metadata={"subreddit": "CryptoCurrency"},
    )
    d = item.to_dict()
    assert d["id"] == "reddit:t3_abc"
    assert d["engagement_raw"]["upvotes"] == 100
    assert isinstance(d["created_at"], str)
