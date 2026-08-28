"""Tests for tools/swaps.py - swap/sick-day requests through the store."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from core.config import load_settings, repo_root, sub_data_dir  # noqa: E402
from core.review import WriteBlocked, approve  # noqa: E402
from core.store import Store  # noqa: E402

import store_ext  # noqa: E402
import swaps  # noqa: E402


def _settings(mode="shadow"):
    return load_settings(provider="mock", mode=mode)


def test_load_requests_auto_with_no_csv_ignores_bundled_fixture_decoys():
    """Regression for the MAJOR finding: a real run (source="auto") must
    never fall back to the bundled example hotel's fixtures/inbound/*.json,
    even though this hermetic test root has them sitting right there as a
    decoy (conftest copies fixtures/ wholesale). With no
    data/imports/swap_requests.csv, it must process none - see
    tools/swaps.py:load_requests and docs/integrations.md.
    """
    decoys = sorted((repo_root() / "fixtures" / "inbound").glob("swap-*.json"))
    assert decoys, "test fixture setup is broken - expected decoy swap-*.json files"
    assert not (sub_data_dir("imports") / "swap_requests.csv").exists()

    assert swaps.load_requests(source="auto") == []
    # The explicit "fixtures" source still works - demo/tests only.
    assert swaps.load_requests(source="fixtures") != []


def test_load_requests_auto_reads_only_the_connected_csv_never_fixtures():
    """With data/imports/swap_requests.csv connected, a real run reads
    exactly that file - never merged with, or replaced by, the bundled
    fixture decoys.
    """
    imports_dir = sub_data_dir("imports")
    imports_dir.mkdir(parents=True, exist_ok=True)
    (imports_dir / "swap_requests.csv").write_text(
        "id,staff_id,date,reason,note\n"
        "real-csv-request,hk-06,2026-09-02,swap,from the real CSV\n",
        encoding="utf-8")

    requests = swaps.load_requests(source="auto")

    assert [r["id"] for r in requests] == ["real-csv-request"]
    fixture_ids = {fixture_req["id"] for fixture_req in swaps.load_requests(source="fixtures")}
    assert "real-csv-request" not in fixture_ids


def test_process_request_finds_eligible_cover():
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        req = {"id": "t-swap-1", "staff_id": "hk-06", "date": "2026-09-02", "reason": "swap"}
        item, is_new = swaps.process_request(settings, store, req, source="fixtures")
        assert is_new
        assert item.kind == "swap"
        assert item.draft["candidate_id"]
        assert item.review_status == "pending_review"
    finally:
        store.close()


def test_process_request_is_idempotent_on_request_id():
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        req = {"id": "t-swap-2", "staff_id": "hk-06", "date": "2026-09-02", "reason": "swap"}
        item1, is_new1 = swaps.process_request(settings, store, req, source="fixtures")
        item2, is_new2 = swaps.process_request(settings, store, req, source="fixtures")
        assert is_new1 and not is_new2
        assert item1.id == item2.id
    finally:
        store.close()


def test_no_eligible_cover_needs_a_human():
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        # fb-12 (Henrik) is the roster's only Sommelier.
        req = {"id": "t-swap-3", "staff_id": "fb-12", "date": "2026-09-05", "reason": "sick"}
        item, _ = swaps.process_request(settings, store, req, source="fixtures")
        assert item.draft["candidate_id"] is None
        assert item.review_status == "needs_human"
        assert "no eligible cover" in item.draft["warnings"][0].lower()
    finally:
        store.close()


def test_unknown_staff_id_needs_a_human_not_a_crash():
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        req = {"id": "t-swap-4", "staff_id": "no-such-id", "date": "2026-09-03", "reason": "swap"}
        item, _ = swaps.process_request(settings, store, req, source="fixtures")
        assert item.review_status == "needs_human"
        assert "unknown staff id" in item.draft["warnings"][0].lower()
    finally:
        store.close()


def test_shadow_mode_blocks_swap_dispatch():
    settings = _settings(mode="shadow")
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        req = {"id": "t-swap-5", "staff_id": "hk-06", "date": "2026-09-02", "reason": "swap"}
        item, _ = swaps.process_request(settings, store, req, source="fixtures")
        approve(store, item.id)
        [claimed] = store.claim_for_send(limit=1)
        try:
            swaps.dispatch_swap(settings, store, claimed)
            assert False, "expected WriteBlocked in shadow mode"
        except WriteBlocked:
            pass
    finally:
        store.close()
