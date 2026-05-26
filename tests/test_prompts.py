import pytest

from aggregator.prompts import load


def test_load_refuses_paths_outside_prompts_dir():
    with pytest.raises(ValueError):
        load("../etc/passwd")


def test_load_refuses_non_md_files():
    with pytest.raises(ValueError):
        load("foo.txt")


def test_load_existing_template_works():
    content = load("general_crypto.md")
    assert content  # non-empty
