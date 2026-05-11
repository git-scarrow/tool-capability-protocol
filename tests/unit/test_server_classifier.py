"""Unit tests for tcp.measurement.server_classifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcp.measurement.server_classifier import (
    ServerClassifier,
    _tokenize,
    _token_counts,
)


# Synthetic corpus: three well-separated topics so the model has a clear signal.
NOTION_PROMPTS = [
    "check the Notion work item status and update the page",
    "query the Notion database for active work items",
    "list pages in the Notion workspace and update properties",
    "fetch the work item from Notion and review its status",
    "update the Notion page properties for this work item",
    "create a new page in the Notion workspace",
    "search Notion pages mentioning the work item",
    "review work items in the Notion database",
]
ORACLE_PROMPTS = [
    "run a SQL query against the Oracle database",
    "describe the Oracle table schema and columns",
    "list rows from the Oracle table where status equals active",
    "execute SQL on Oracle and return matching rows",
    "Oracle table indexes and constraints inspection",
    "select rows from Oracle database table by id",
    "show Oracle schemas and tables matching pattern",
    "query Oracle for matching database rows",
]
FETCH_PROMPTS = [
    "fetch the contents of this URL as markdown",
    "download the HTML page from the URL",
    "retrieve the web page contents at this URL",
    "fetch markdown content from the URL endpoint",
    "get HTML from URL and extract content",
    "scrape the URL and return markdown text",
    "fetch URL and parse the HTML response",
    "retrieve URL content as plain markdown",
]


@pytest.fixture(scope="module")
def trained_model() -> ServerClassifier:
    examples: list[tuple[str, str]] = []
    examples.extend((t, "notion-agents") for t in NOTION_PROMPTS)
    examples.extend((t, "oracle-remote") for t in ORACLE_PROMPTS)
    examples.extend((t, "fetch") for t in FETCH_PROMPTS)
    return ServerClassifier.fit(examples, min_df=1, min_confidence=0.0)


def test_tokenize_filters_stopwords_and_short_tokens() -> None:
    tokens = _tokenize("Check the database at /tmp/x — a quick prompt")
    # 'the', 'at', 'a' are stopwords; single-char tokens dropped.
    assert "check" in tokens
    assert "database" in tokens
    assert "quick" in tokens
    assert "prompt" in tokens
    assert "the" not in tokens
    assert "a" not in tokens


def test_token_counts() -> None:
    assert _token_counts(["a", "b", "a", "c", "a"]) == {"a": 3, "b": 1, "c": 1}


def test_empty_prompt_returns_empty(trained_model: ServerClassifier) -> None:
    assert trained_model.predict("") == {}
    assert trained_model.predict("   ") == {}
    assert trained_model.top_server("") is None


def test_predict_picks_correct_class(trained_model: ServerClassifier) -> None:
    top = trained_model.top_server("query the Oracle database for status")
    assert top is not None
    assert top[0] == "oracle-remote"

    top = trained_model.top_server("fetch the URL contents as markdown")
    assert top is not None
    assert top[0] == "fetch"

    top = trained_model.top_server("update Notion work item page properties")
    assert top is not None
    assert top[0] == "notion-agents"


def test_predict_probabilities_sum_to_one(trained_model: ServerClassifier) -> None:
    # Disable confidence threshold to see all classes.
    trained_model.min_confidence = 0.0
    probs = trained_model.predict("Oracle SQL query for rows")
    assert pytest.approx(sum(probs.values()), rel=1e-6) == 1.0
    trained_model.min_confidence = 0.0


def test_abstain_below_confidence() -> None:
    """If min_confidence > max probability, predict() returns empty."""
    examples = [
        ("alpha beta gamma", "A"),
        ("delta epsilon zeta", "B"),
    ]
    clf = ServerClassifier.fit(examples, min_df=1, min_confidence=0.99)
    # Confidence threshold suppresses anything not nearly certain.
    result = clf.predict("alpha beta gamma delta epsilon zeta")
    assert result == {} or max(result.values()) >= 0.99


def test_save_load_roundtrip(trained_model: ServerClassifier, tmp_path: Path) -> None:
    out = tmp_path / "model.json"
    trained_model.save(out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["schema_version"] == 1
    assert set(data["classes"]) == {"notion-agents", "oracle-remote", "fetch"}

    restored = ServerClassifier.load(out)
    assert restored.classes == trained_model.classes
    assert restored.vocab == trained_model.vocab
    # Predictions agree.
    prompt = "fetch the URL contents"
    assert restored.top_server(prompt) == trained_model.top_server(prompt)


def test_fit_raises_on_empty() -> None:
    with pytest.raises(ValueError):
        ServerClassifier.fit([])


def test_unknown_tokens_do_not_error(trained_model: ServerClassifier) -> None:
    # All-OOV prompt should not crash; classifier may abstain or pick by prior.
    result = trained_model.predict("xyzzy plover frobnitz")
    assert isinstance(result, dict)


def test_long_prompt_does_not_explode(trained_model: ServerClassifier) -> None:
    # Sanity check the fit-path O(n) fix: a long prompt should classify quickly.
    long_text = ("oracle sql query database rows " * 500)
    top = trained_model.top_server(long_text)
    assert top is not None
    assert top[0] == "oracle-remote"
