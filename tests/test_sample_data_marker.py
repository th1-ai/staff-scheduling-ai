"""A real (not `make demo`) pass on a fresh clone must never look like the
hotel's own data.

Every `config/*.example.yaml` ships with `systems.messaging.adapter: mock`, so
until the hotel connects their own messaging, anything The Planner reads or
writes through that adapter is the invented example hotel, not this property.
`core.store.Store.upsert_item` tags such an item `_sample: True` (via
`core.adapters.is_sample_source`, which honours this repo's
`config/agent.yaml: systems_used: [messaging]`), and `item.is_sample` reads it
back. This repo does not re-implement the tagging - it only has to *show* it,
which is what these tests pin: a `[SAMPLE DATA]` marker in both
`tools/review.py list` and `tools/review.py show`.

`tests/conftest.py`'s autouse fixture points AGENT_REPO_ROOT at a throwaway
copy of the shipped examples, so no hotel's own filled-in config is read here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.store import Store
from tools.review import cmd_list, cmd_show

WEEK_START = "2026-09-14"


def _sample_item(tmp_path):
    """One review-queue item as a fresh clone would produce it: the real
    (non-demo) path, with the shipped `mock` messaging adapter still in place.
    """
    settings = load_settings()
    assert settings.systems.messaging.adapter == "mock"  # the shipped default
    assert settings.demo is False  # the real path, not `make demo`
    store = Store(settings, path=tmp_path / "test.db")
    item = store.upsert_item("messaging", WEEK_START, kind="weekly_rota",
                             payload={"week_start": WEEK_START})
    store.set_fields(item.id, intent="weekly_rota", confidence=1.0,
                     draft={"plan": {"week_start": WEEK_START, "days": []}})
    store.transition(item.id, "pending_review", actor="agent")
    return store, store.get_item(item.id)


def test_a_real_pass_on_the_mock_default_tags_its_item_sample(tmp_path):
    store, item = _sample_item(tmp_path)
    store.close()
    assert item.is_sample is True
    assert item.payload.get("_sample") is True


def test_review_list_marks_the_sample_item(tmp_path, capsys):
    store, item = _sample_item(tmp_path)
    code = cmd_list(store, SimpleNamespace(status=None, kind=None, limit=50))
    store.close()
    out = capsys.readouterr().out
    assert code == 0
    assert "[SAMPLE DATA]" in out
    assert "not your property" in out


def test_review_show_marks_the_sample_item(tmp_path, capsys):
    store, item = _sample_item(tmp_path)
    code = cmd_show(store, SimpleNamespace(id=item.id))
    store.close()
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("[SAMPLE DATA]")
    assert "not your property" in out
