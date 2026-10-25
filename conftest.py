# -*- coding: utf-8 -*-
"""pytest global fixture.

The UI language is now "English by default + switchable to Chinese". Many
legacy assertions are based on Chinese wording (report titles, log hints),
so the language is pinned to Chinese during tests to keep the results
independent from the language persisted on the user machine.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def _force_chinese_ui():
    """Pin the UI language to Chinese for the duration of each test."""
    old = i18n.get_language()
    i18n.set_language("zh")
    yield
    i18n.set_language(old)
