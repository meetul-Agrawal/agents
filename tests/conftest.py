"""Pin the deterministic classifier for the test suite.

`orchestrator.default_classifier()` prefers the LLM when a provider is
configured. Tests must not depend on a network call or on a model's run-to-run
drift, so the whole suite runs on the rules unless a test opts in explicitly.
"""

import pytest


@pytest.fixture(autouse=True)
def _deterministic_classifier(monkeypatch):
    monkeypatch.setenv("CA_CLASSIFIER", "rules")
    # SA-1's optional LLM phrasing pass is off in tests for the same reason: no
    # network call, no run-to-run drift. Tests exercise it by monkeypatching
    # sa1_general._llm_phrase directly.
    monkeypatch.setenv("CA_SA1_PHRASE", "off")
