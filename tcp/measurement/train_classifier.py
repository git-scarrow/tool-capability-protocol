"""Train and evaluate the MCP server relevance classifier.

Reads decisions.jsonl, trains a ServerClassifier on MCP-first rows,
evaluates with stratified 80/20 split, prints per-class metrics,
and saves the model to tcp/measurement/server_classifier_model.json.

Usage:
    python tcp/measurement/train_classifier.py [--log PATH] [--out PATH]
                                                [--min-examples N]
                                                [--min-confidence F]
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

from tcp.measurement.server_classifier import ServerClassifier

DEFAULT_LOG = Path.home() / ".tcp-shadow" / "proxy" / "decisions.jsonl"
DEFAULT_OUT = Path(__file__).parent / "server_classifier_model.json"
DEFAULT_MIN_EXAMPLES = 20
DEFAULT_MIN_CONFIDENCE = 0.15


def _extract_mcp_server(tool_name: str) -> str | None:
    if not tool_name or not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    if len(parts) < 2:
        return None
    server = parts[1].strip()
    return server if server else None


def load_examples(
    log_path: Path,
    min_examples: int,
) -> tuple[list[tuple[str, str]], Counter]:
    """Return (text, server) pairs from rows where first_tool is MCP.

    Prefers prompt_text (schema 3) over prompt_excerpt; skips rows with
    neither. Filters servers below min_examples threshold.
    """
    all_pairs: list[tuple[str, str]] = []
    skipped_no_text = 0
    skipped_builtin = 0

    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        first_tool = row.get("first_tool_name")
        server = _extract_mcp_server(first_tool) if first_tool else None
        if not server:
            skipped_builtin += 1
            continue

        text = row.get("prompt_text") or row.get("prompt_excerpt") or ""
        if not text.strip():
            skipped_no_text += 1
            continue

        all_pairs.append((text.strip(), server))

    server_counts: Counter = Counter(s for _, s in all_pairs)
    eligible = {s for s, c in server_counts.items() if c >= min_examples}
    pairs = [(t, s) for t, s in all_pairs if s in eligible]

    print(f"Rows parsed:           {skipped_builtin + skipped_no_text + len(all_pairs)}")
    print(f"  builtin-first (skip):{skipped_builtin:>6}")
    print(f"  no text (skip):      {skipped_no_text:>6}")
    print(f"  MCP rows total:      {len(all_pairs):>6}")
    print(f"  after min_examples≥{min_examples}: {len(pairs):>5}")
    print()
    print("Server distribution (after filter):")
    for server, count in sorted(server_counts.items(), key=lambda x: -x[1]):
        marker = "✓" if server in eligible else "✗"
        print(f"  {marker} {server:<40} {count:>5}")
    print()

    return pairs, server_counts


def stratified_split(
    pairs: list[tuple[str, str]],
    test_frac: float = 0.2,
    seed: int = 42,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (train, test) split stratified by server label."""
    by_class: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for pair in pairs:
        by_class[pair[1]].append(pair)

    rng = random.Random(seed)
    train, test = [], []
    for cls_pairs in by_class.values():
        rng.shuffle(cls_pairs)
        n_test = max(1, round(len(cls_pairs) * test_frac))
        test.extend(cls_pairs[:n_test])
        train.extend(cls_pairs[n_test:])

    rng.shuffle(train)
    rng.shuffle(test)
    return train, test


def evaluate(
    clf: ServerClassifier,
    test_pairs: list[tuple[str, str]],
) -> dict[str, dict[str, float]]:
    """Return per-class and macro-average precision, recall, F1."""
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    correct = 0

    for text, true_label in test_pairs:
        pred = clf.predict(text)
        if pred:
            top_pred = max(pred, key=pred.get)  # type: ignore[arg-type]
        else:
            top_pred = "__abstain__"

        if top_pred == true_label:
            correct += 1
            tp[true_label] += 1
        else:
            fp[top_pred] += 1
            fn[true_label] += 1

    metrics: dict[str, dict[str, float]] = {}
    for cls in clf.classes:
        p = tp[cls] / (tp[cls] + fp[cls]) if (tp[cls] + fp[cls]) > 0 else 0.0
        r = tp[cls] / (tp[cls] + fn[cls]) if (tp[cls] + fn[cls]) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        support = tp[cls] + fn[cls]
        metrics[cls] = {"precision": p, "recall": r, "f1": f1, "support": support}

    n = len(test_pairs)
    accuracy = correct / n if n > 0 else 0.0
    abstain_count = sum(1 for t, _ in test_pairs if not clf.predict(t))
    macro_p = sum(m["precision"] for m in metrics.values()) / len(metrics)
    macro_r = sum(m["recall"] for m in metrics.values()) / len(metrics)
    macro_f1 = sum(m["f1"] for m in metrics.values()) / len(metrics)
    metrics["__macro__"] = {
        "precision": macro_p,
        "recall": macro_r,
        "f1": macro_f1,
        "support": float(n),
        "accuracy": accuracy,
        "abstain_rate": abstain_count / n if n > 0 else 0.0,
    }
    return metrics


def print_report(metrics: dict[str, dict[str, float]]) -> None:
    macro = metrics.get("__macro__", {})
    print(f"Accuracy:    {macro.get('accuracy', 0):.3f}")
    print(f"Abstain rate:{macro.get('abstain_rate', 0):.3f}  (no prediction above threshold)")
    print(f"Macro avg:   P={macro.get('precision',0):.3f}  R={macro.get('recall',0):.3f}  F1={macro.get('f1',0):.3f}")
    print()
    print(f"{'Server':<40} {'P':>6} {'R':>6} {'F1':>6} {'N':>5}")
    print("-" * 64)
    for cls, m in sorted(metrics.items(), key=lambda x: -x[1].get("f1", 0)):
        if cls == "__macro__":
            continue
        print(
            f"{cls:<40} {m['precision']:>6.3f} {m['recall']:>6.3f}"
            f" {m['f1']:>6.3f} {int(m['support']):>5}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train MCP server relevance classifier")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-examples", type=int, default=DEFAULT_MIN_EXAMPLES)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--alpha", type=float, default=1.0, help="Laplace smoothing")
    args = parser.parse_args()

    if not args.log.exists():
        parser.error(f"decisions log not found: {args.log}")

    print(f"Loading from: {args.log}")
    pairs, _ = load_examples(args.log, args.min_examples)
    if len(pairs) < 10:
        parser.error(f"insufficient examples ({len(pairs)}); need at least 10")

    train, test = stratified_split(pairs, seed=args.seed)
    print(f"Train: {len(train)}   Test: {len(test)}")
    print()

    print("Training classifier...")
    clf = ServerClassifier.fit(
        train,
        alpha=args.alpha,
        min_confidence=args.min_confidence,
    )
    print(f"  Vocab size: {len(clf.vocab)}")
    print(f"  Classes:    {len(clf.classes)}")
    print()

    print("Evaluation (test set):")
    print("-" * 64)
    metrics = evaluate(clf, test)
    print_report(metrics)
    print()

    clf.save(args.out)
    print(f"Model saved → {args.out}")


if __name__ == "__main__":
    main()
