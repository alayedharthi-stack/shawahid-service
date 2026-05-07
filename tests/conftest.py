"""
Phase-1 test fixtures.

Keeps imports cheap and avoids touching the real database, OpenAI, or
WhatsApp services. Each test that needs them must monkey-patch them
explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure ``app`` package is importable when running ``pytest`` from
# either the repo root or the shawahid-service folder.
_HERE = Path(__file__).resolve().parent
_SERVICE_ROOT = _HERE.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


@pytest.fixture(autouse=True)
def _disable_portfolio_ai_analysis(monkeypatch):
    """Stop ``analyze_portfolio_sync`` from touching the network in tests.

    The legacy ``_build_performance_analysis`` already wraps that call
    in try/except, but Phase-1 tests must never depend on a real GPT
    key being configured in the test environment.
    """

    def _fake_analyze_portfolio_sync(*_args, **_kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(
        "app.services.gpt_brain.analyze_portfolio_sync",
        _fake_analyze_portfolio_sync,
        raising=False,
    )
