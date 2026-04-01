"""Tests for TCP-MT-3 real-tool corpus benchmark."""

from tcp.harness.benchmark import benchmark_exposure_paths, summarize_comparisons
from tcp.harness.benchmark_mt3 import (
    build_mt3_environment,
    build_mt3_tasks,
    run_mt3_benchmark,
)
from tcp.harness.corpus import build_mt3_corpus, corpus_summary


def test_corpus_meets_minimum_size():
    _, entries = build_mt3_corpus()
    summary = corpus_summary(entries)
    assert summary["total_descriptors"] >= 50


def test_corpus_has_heterogeneous_sources():
    _, entries = build_mt3_corpus()
    summary = corpus_summary(entries)
    assert len(summary["sources"]) >= 5


def test_corpus_has_heterogeneous_categories():
    _, entries = build_mt3_corpus()
    summary = corpus_summary(entries)
    assert len(summary["categories"]) >= 5


def test_mt3_zero_false_allows():
    descriptors, _ = build_mt3_corpus()
    tasks = build_mt3_tasks()
    env = build_mt3_environment(network=False)

    comparisons = benchmark_exposure_paths(descriptors, tasks, env)
    summary = summarize_comparisons(comparisons)

    assert summary["bitmask_false_allows"] == 0
    assert summary["schema_false_allows"] == 0
    assert summary["tcp_false_allows"] == 0


def test_mt3_zero_false_rejections():
    descriptors, _ = build_mt3_corpus()
    tasks = build_mt3_tasks()
    env = build_mt3_environment(network=False)

    comparisons = benchmark_exposure_paths(descriptors, tasks, env)
    summary = summarize_comparisons(comparisons)

    assert summary["bitmask_false_rejections"] == 0


def test_mt3_prompt_bytes_reduction_exceeds_threshold():
    descriptors, _ = build_mt3_corpus()
    tasks = build_mt3_tasks()
    env = build_mt3_environment(network=False)

    comparisons = benchmark_exposure_paths(descriptors, tasks, env)
    summary = summarize_comparisons(comparisons)

    assert summary["mean_prompt_bytes_reduction"] > 500


def test_mt3_suite_at_scale():
    results = run_mt3_benchmark(repetitions=2)
    s = results["suite_summary"]

    assert s["bitmask_false_allows"] == 0
    assert s["bitmask_false_rejections"] == 0
    assert s["mean_prompt_bytes_reduction"] > 500
    assert results["corpus"]["total_descriptors"] >= 50
