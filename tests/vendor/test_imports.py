"""Confirm vendored modules import. If an upstream module references another
upstream module we did NOT vendor, this test will surface it loudly."""
import importlib
import pytest

MODULES = [
    "aggregator.vendor.last30days.reddit",
    "aggregator.vendor.last30days.reddit_public",
    "aggregator.vendor.last30days.reddit_enrich",
    "aggregator.vendor.last30days.polymarket",
    "aggregator.vendor.last30days.dedupe",
    "aggregator.vendor.last30days.cluster",
    "aggregator.vendor.last30days.rerank",
    "aggregator.vendor.last30days.signals",
    "aggregator.vendor.last30days.relevance",
    "aggregator.vendor.last30days.normalize",
    "aggregator.vendor.last30days.schema",
    "aggregator.vendor.last30days.http",
    "aggregator.vendor.last30days.dates",
    "aggregator.vendor.last30days.env",
    "aggregator.vendor.last30days.log",
    "aggregator.vendor.last30days.store",
]


@pytest.mark.parametrize("modname", MODULES)
def test_import(modname):
    importlib.import_module(modname)
