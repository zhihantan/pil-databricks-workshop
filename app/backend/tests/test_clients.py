"""Unit tests for SQL warehouse auto-discovery (no hardcoded IDs).

``_pick_warehouse`` is the pure, off-platform-testable core of
``resolve_warehouse_id``: given the warehouses the app's principal can see, it
picks the best usable one (serverless + RUNNING preferred).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.services.clients import _pick_warehouse


@dataclass
class _State:
    value: str


@dataclass
class _WH:
    id: str
    enable_serverless_compute: bool = False
    state: object = None  # str or _State — resolver handles both


def test_prefers_serverless_running_over_others():
    picked = _pick_warehouse(
        [
            _WH("classic-stopped", enable_serverless_compute=False, state="STOPPED"),
            _WH("serverless-running", enable_serverless_compute=True, state="RUNNING"),
            _WH("serverless-stopped", enable_serverless_compute=True, state="STOPPED"),
        ]
    )
    assert picked == "serverless-running"


def test_prefers_serverless_even_if_stopped_over_classic_running():
    picked = _pick_warehouse(
        [
            _WH("classic-running", enable_serverless_compute=False, state="RUNNING"),
            _WH("serverless-stopped", enable_serverless_compute=True, state="STOPPED"),
        ]
    )
    assert picked == "serverless-stopped"


def test_handles_enum_like_state_objects():
    picked = _pick_warehouse(
        [
            _WH("a", enable_serverless_compute=True, state=_State("STOPPED")),
            _WH("b", enable_serverless_compute=True, state=_State("RUNNING")),
        ]
    )
    assert picked == "b"


def test_falls_back_to_any_warehouse_when_none_ideal():
    picked = _pick_warehouse([_WH("only-classic", enable_serverless_compute=False, state="STOPPED")])
    assert picked == "only-classic"


def test_stable_order_for_ties_keeps_first():
    picked = _pick_warehouse(
        [
            _WH("first", enable_serverless_compute=True, state="RUNNING"),
            _WH("second", enable_serverless_compute=True, state="RUNNING"),
        ]
    )
    assert picked == "first"


def test_empty_list_returns_none():
    assert _pick_warehouse([]) is None


def test_skips_entries_without_id():
    picked = _pick_warehouse(
        [
            _WH(None, enable_serverless_compute=True, state="RUNNING"),  # type: ignore[arg-type]
            _WH("has-id", enable_serverless_compute=False, state="STOPPED"),
        ]
    )
    assert picked == "has-id"
