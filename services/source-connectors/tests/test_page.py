import dataclasses

import pytest

from app.connectors import Page


def test_last_page_defaults():
    p = Page(records=[{"a": 1}])
    assert p.next_cursor is None and p.has_more is False


def test_has_more_requires_cursor():
    with pytest.raises(ValueError):
        Page(records=[], has_more=True)


def test_page_is_immutable():
    p = Page(records=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.has_more = True  # type: ignore[misc]
