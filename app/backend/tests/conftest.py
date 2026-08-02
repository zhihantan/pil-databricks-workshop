"""Shared pytest fixtures for the backend tests."""

from __future__ import annotations

import pytest

from backend.services import demo_store


@pytest.fixture(autouse=True)
def _reset_demo_store():
    """Reset the process-level demo store before each test for isolation."""
    demo_store.reset()
    yield
    demo_store.reset()
