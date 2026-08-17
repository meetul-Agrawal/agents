"""Keep the agents' own LLM passes off by default for the test suite.

Intent classification (`classify_llm`) has no deterministic offline mode —
it is the model or nothing — so tests that exercise routing either mock
`complete_structured`/`_understand` directly or opt into a real call via the
session-scoped `llm_available` fixture. What stays off by default here is
everything downstream that would otherwise add its own network call and
run-to-run drift on top of that.
"""

import pytest


@pytest.fixture(autouse=True)
def _deterministic_agents(monkeypatch):
    # The agents' optional LLM phrasing pass is off in tests for the same reason:
    # no network call, no run-to-run drift. Tests exercise it by monkeypatching
    # sa1_general._llm_phrase directly.
    monkeypatch.setenv("CA_PHRASE", "off")
    # SA-1's LLM tool-selection fallback is off too — tests exercise it by
    # monkeypatching sa1_general._plan_tools / _compose_answer directly.
    monkeypatch.setenv("CA_SA1_TOOLS", "off")
