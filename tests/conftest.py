"""Session-wide pytest fixtures."""
from __future__ import annotations

import pytest

from aggregator.config import load_config


@pytest.fixture
def cfg():
    return load_config("config.example.toml")
