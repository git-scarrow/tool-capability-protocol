"""TF-IDF Multinomial Naive Bayes classifier for MCP server relevance.

Given a task prompt, predicts which MCP server(s) are most likely needed.
Trained on rows from decisions.jsonl where first_tool_name is an MCP tool
(unambiguous oracle signal).

Design constraints:
  - stdlib only: runs inside the proxy container without extra dependencies
  - JSON-serializable model: train anywhere, load anywhere
  - Inference < 1ms on typical prompts

Usage:
    clf = ServerClassifier.load("tcp/measurement/server_classifier_model.json")
    scores = clf.predict("check the Lab work items and update the status")
    # -> {"notion-agents": 0.87, "oracle-remote": 0.11, ...}
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Common English words that carry no signal for tool selection.
_STOPWORDS = frozenset(
    "the a an and or but in on at to for of with is are was were be been "
    "being have has had do does did will would could should may might must "
    "can this that these those it its i you he she we they me him her us them "
    "my your his our their what which who when where how all any some no not "
    "just get use need want make give take run file read write set get list "
    "from into out up down via per let see".split()
)
_MIN_TOKEN_LEN = 3
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS]


def _token_counts(tokens: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    return counts


@dataclass
class ServerClassifier:
    """TF-IDF weighted Multinomial Naive Bayes, one class per MCP server.

    Model parameters stored as plain dicts for JSON round-trip.
    All log-probabilities are natural log (math.log).
    """

    vocab: list[str]  # ordered vocab list; index → token
    idf: dict[str, float]  # token → IDF weight
    classes: list[str]  # ordered server names
    log_prior: dict[str, float]  # class → log P(class)
    log_likelihood: dict[str, dict[str, float]]  # class → token → log P(token|class)
    alpha: float = 1.0  # Laplace smoothing
    min_confidence: float = 0.15  # suppress servers below this probability
    schema_version: int = 1

    # ── Prediction ─────────────────────────────────────────────────────────────

    def predict(self, prompt: str) -> dict[str, float]:
        """Return {server: probability} for servers above min_confidence.

        An empty dict means the classifier has no confident prediction —
        the proxy should fall back to the pack manifest defaults.
        """
        tokens = _tokenize(prompt)
        if not tokens:
            return {}
        tfidf = self._tfidf_vector(tokens)
        scores: dict[str, float] = {}
        for cls in self.classes:
            score = self.log_prior[cls]
            ll = self.log_likelihood[cls]
            for token, weight in tfidf.items():
                if token in ll:
                    score += weight * ll[token]
            scores[cls] = score

        # Softmax over raw scores.
        max_score = max(scores.values())
        exp_scores = {c: math.exp(s - max_score) for c, s in scores.items()}
        total = sum(exp_scores.values())
        probs = {c: v / total for c, v in exp_scores.items()}

        return {c: p for c, p in sorted(probs.items(), key=lambda x: -x[1])
                if p >= self.min_confidence}

    def top_server(self, prompt: str) -> tuple[str, float] | None:
        """Return (server_name, probability) for the top prediction, or None."""
        result = self.predict(prompt)
        if not result:
            return None
        top = max(result.items(), key=lambda x: x[1])
        return top

    def _tfidf_vector(self, tokens: list[str]) -> dict[str, float]:
        counts = _token_counts(tokens)
        n = len(tokens)
        vec: dict[str, float] = {}
        for token, count in counts.items():
            if token in self.idf:
                tf = count / n
                vec[token] = tf * self.idf[token]
        return vec

    # ── Serialization ──────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "schema_version": self.schema_version,
                    "alpha": self.alpha,
                    "min_confidence": self.min_confidence,
                    "vocab": self.vocab,
                    "idf": self.idf,
                    "classes": self.classes,
                    "log_prior": self.log_prior,
                    "log_likelihood": self.log_likelihood,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "ServerClassifier":
        data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            vocab=data["vocab"],
            idf=data["idf"],
            classes=data["classes"],
            log_prior=data["log_prior"],
            log_likelihood=data["log_likelihood"],
            alpha=data.get("alpha", 1.0),
            min_confidence=data.get("min_confidence", 0.15),
            schema_version=data.get("schema_version", 1),
        )

    # ── Training ───────────────────────────────────────────────────────────────

    @classmethod
    def fit(
        cls,
        examples: list[tuple[str, str]],
        *,
        alpha: float = 1.0,
        min_confidence: float = 0.15,
        max_vocab: int = 8000,
        min_df: int = 2,
    ) -> "ServerClassifier":
        """Train from (prompt_text, server_name) pairs.

        Only call with examples where the server label is unambiguous
        (rows where first_tool_name is an MCP tool).

        Parameters:
            examples: (text, label) pairs. Labels are MCP server names.
            alpha: Laplace smoothing strength.
            min_confidence: inference threshold applied at predict() time.
            max_vocab: maximum vocabulary size (top by document frequency).
            min_df: minimum document frequency to include a token.
        """
        if not examples:
            raise ValueError("no training examples provided")

        texts, labels = zip(*examples)
        classes = sorted(set(labels))
        n_total = len(examples)

        # Tokenize all documents.
        tokenized = [_tokenize(t) for t in texts]

        # Build vocabulary by document frequency.
        df: dict[str, int] = {}
        for tokens in tokenized:
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1

        # Filter by min_df and cap at max_vocab (top by df).
        vocab_sorted = sorted(
            (t for t, c in df.items() if c >= min_df),
            key=lambda t: -df[t],
        )[:max_vocab]
        vocab_set = set(vocab_sorted)

        # IDF = log((N + 1) / (df + 1)) + 1  (sklearn-style smoothed).
        idf = {
            t: math.log((n_total + 1) / (df[t] + 1)) + 1.0
            for t in vocab_sorted
        }

        # Per-class token frequency counts.
        class_token_sum: dict[str, dict[str, float]] = {c: {} for c in classes}
        class_counts: dict[str, int] = {c: 0 for c in classes}

        for tokens, label in zip(tokenized, labels):
            class_counts[label] += 1
            if not tokens:
                continue
            n_tok = len(tokens)
            counts = _token_counts(tokens)
            tfidf_vec = {
                t: (c / n_tok) * idf[t]
                for t, c in counts.items()
                if t in vocab_set
            }
            for token, weight in tfidf_vec.items():
                class_token_sum[label][token] = (
                    class_token_sum[label].get(token, 0.0) + weight
                )

        # Log-prior: log(count / total).
        log_prior = {c: math.log(class_counts[c] / n_total) for c in classes}

        # Log-likelihood with Laplace smoothing.
        # P(token | class) = (sum_tfidf(token, class) + alpha) / (total_class_weight + alpha * V)
        log_likelihood: dict[str, dict[str, float]] = {}
        V = len(vocab_sorted)
        for cls_name in classes:
            token_sums = class_token_sum[cls_name]
            total_weight = sum(token_sums.values())
            denom = total_weight + alpha * V
            log_likelihood[cls_name] = {
                token: math.log((token_sums.get(token, 0.0) + alpha) / denom)
                for token in vocab_sorted
            }

        return ServerClassifier(
            vocab=vocab_sorted,
            idf=idf,
            classes=classes,
            log_prior=log_prior,
            log_likelihood=log_likelihood,
            alpha=alpha,
            min_confidence=min_confidence,
        )
