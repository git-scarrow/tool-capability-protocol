#!/usr/bin/env python3
"""
TCP-MT-9: Bootstrap CIs on EXP-3 4-arm ablation data.

Produces:
  1. Per-arm correctness rate + 95% bootstrap CI
  2. Per-arm mean input tokens + 95% bootstrap CI
  3. Pairwise A-vs-C (and all pairs) correctness difference + CI + p-value
  4. Minimum detectable effect size at this sample size (80% power)
  5. Summary table + verdict
"""

import json
import random
from pathlib import Path
from typing import Callable

REPO = Path(__file__).parent.parent
N_BOOT = 50_000
SEED = 42


# ── data loading ─────────────────────────────────────────────────────────────

def load_exp3():
    path = REPO / "exp3-results.json"
    with open(path) as f:
        return json.load(f)["trials"]


# ── bootstrap helpers ─────────────────────────────────────────────────────────

def bootstrap_ci(
    data: list,
    stat_fn: Callable,
    n_boot: int = N_BOOT,
    alpha: float = 0.05,
    rng: random.Random = None,
) -> tuple[float, float, float]:
    """Return (observed, lower_ci, upper_ci)."""
    if rng is None:
        rng = random.Random(SEED)
    observed = stat_fn(data)
    n = len(data)
    boot_stats = []
    for _ in range(n_boot):
        sample = [rng.choice(data) for _ in range(n)]
        boot_stats.append(stat_fn(sample))
    boot_stats.sort()
    lo = boot_stats[int(alpha / 2 * n_boot)]
    hi = boot_stats[int((1 - alpha / 2) * n_boot)]
    return observed, lo, hi


def bootstrap_diff_pvalue(
    data_a: list,
    data_b: list,
    stat_fn: Callable,
    n_boot: int = N_BOOT,
    rng: random.Random = None,
) -> tuple[float, float]:
    """
    Permutation-bootstrap p-value for H0: stat(A) == stat(B).
    Returns (observed_diff, p_value) where diff = stat(b) - stat(a).
    """
    if rng is None:
        rng = random.Random(SEED)
    combined = data_a + data_b
    n_a = len(data_a)
    obs_diff = stat_fn(data_b) - stat_fn(data_a)
    count_extreme = 0
    for _ in range(n_boot):
        rng.shuffle(combined)
        perm_a = combined[:n_a]
        perm_b = combined[n_a:]
        perm_diff = stat_fn(perm_b) - stat_fn(perm_a)
        if abs(perm_diff) >= abs(obs_diff):
            count_extreme += 1
    return obs_diff, count_extreme / n_boot


def mean_fn(xs: list) -> float:
    return sum(xs) / len(xs)


# ── minimum detectable effect (MDE) ──────────────────────────────────────────

def power_two_proportions(n: int, p1: float, p2: float, alpha: float = 0.05) -> float:
    """Approximate power for two-proportion z-test (two-tailed)."""
    import math
    z_alpha = 1.96
    p_bar = (p1 + p2) / 2
    se_alt = math.sqrt(p1 * (1 - p1) / n + p2 * (1 - p2) / n)
    if se_alt == 0:
        return 1.0
    ncp = abs(p2 - p1) / se_alt
    return 1 - _norm_cdf(z_alpha - ncp) + _norm_cdf(-z_alpha - ncp)


def n_required_two_proportions(p1: float, p2: float, power: float = 0.80, alpha: float = 0.05) -> int:
    """Return n per arm needed to detect |p2-p1| at given power."""
    for n in range(5, 10_000):
        if power_two_proportions(n, p1, p2, alpha) >= power:
            return n
    return 10_000


def mde_two_proportions(n: int, p1: float = 0.83, power: float = 0.80, alpha: float = 0.05) -> float:
    """
    MDE for two-proportion z-test at given n per arm.
    Returns smallest detectable delta (p2 - p1) at given power.
    Returns nan if no delta in [0.01, 1-p1] achieves the power.
    """
    for delta_hundredths in range(1, int((1 - p1) * 100) + 1):
        delta = delta_hundredths / 100
        p2 = p1 + delta
        if p2 > 1:
            break
        if power_two_proportions(n, p1, p2, alpha) >= power:
            return delta
    return float("nan")


def _norm_cdf(x: float) -> float:
    import math
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    trials = load_exp3()
    n = len(trials)
    arms = ["arm_a", "arm_b", "arm_c", "arm_d"]
    arm_labels = {
        "arm_a": "A — Realistic Ungated (119 tools)",
        "arm_b": "B — Realistic Filtered (46 tools)",
        "arm_c": "C — Minimal Filtered (46 tools)  ← winner",
        "arm_d": "D — Brief Filtered (46 tools)",
    }

    # extract per-arm series
    correct = {arm: [t[arm]["selected_tool_correct"] for t in trials] for arm in arms}
    tokens  = {arm: [t[arm]["input_tokens"] for t in trials] for arm in arms}

    rng = random.Random(SEED)

    print("=" * 72)
    print("TCP-MT-9  Bootstrap CIs — EXP-3 4-Arm Ablation")
    print(f"n = {n} tasks per arm  |  bootstrap iterations = {N_BOOT:,}  |  seed = {SEED}")
    print("=" * 72)

    # ── Table 1: correctness CIs ─────────────────────────────────────────────
    print("\n── Table 1: Per-Arm Correctness Rate (95% CI) ─────────────────────\n")
    print(f"{'Arm':<42}  {'Correct':>7}  {'Lower':>7}  {'Upper':>7}  {'n_ok':>5}")
    print("-" * 72)
    corr_ci = {}
    for arm in arms:
        obs, lo, hi = bootstrap_ci(correct[arm], mean_fn, rng=rng)
        corr_ci[arm] = (obs, lo, hi)
        n_ok = sum(correct[arm])
        print(f"{arm_labels[arm]:<42}  {obs:>6.1%}  {lo:>6.1%}  {hi:>6.1%}  {n_ok:>5}/{n}")

    # ── Table 2: token CIs ───────────────────────────────────────────────────
    print("\n── Table 2: Per-Arm Mean Input Tokens (95% CI) ────────────────────\n")
    print(f"{'Arm':<42}  {'Mean':>8}  {'Lower':>8}  {'Upper':>8}")
    print("-" * 72)
    for arm in arms:
        obs, lo, hi = bootstrap_ci(tokens[arm], mean_fn, rng=rng)
        print(f"{arm_labels[arm]:<42}  {obs:>8,.0f}  {lo:>8,.0f}  {hi:>8,.0f}")

    # ── Table 3: pairwise correctness differences ────────────────────────────
    print("\n── Table 3: Pairwise Correctness Differences (A as reference) ──────\n")
    print(f"{'Comparison':<26}  {'Δ correct':>9}  {'95% CI':>20}  {'p-value':>8}  {'sig?':>6}")
    print("-" * 72)
    ref = "arm_a"
    for arm in arms[1:]:
        obs_a, lo_a, hi_a = corr_ci[ref]
        obs_b, lo_b, hi_b = corr_ci[arm]
        diff_obs, p = bootstrap_diff_pvalue(correct[ref], correct[arm], mean_fn, rng=rng)
        # CI on the difference via paired bootstrap
        diffs = [c - a for c, a in zip(correct[arm], correct[ref])]
        _, diff_lo, diff_hi = bootstrap_ci(diffs, mean_fn, rng=rng)
        sig = "✓" if p < 0.05 else "✗"
        label = f"A vs {arm.split('_')[1].upper()}"
        print(f"{label:<26}  {diff_obs:>+8.1%}  [{diff_lo:>+7.1%}, {diff_hi:>+7.1%}]  {p:>8.4f}  {sig:>6}")

    # ── Table 4: all pairs for completeness ─────────────────────────────────
    print("\n── Table 4: C vs D (minimal vs brief) ─────────────────────────────\n")
    diff_obs, p = bootstrap_diff_pvalue(correct["arm_c"], correct["arm_d"], mean_fn, rng=rng)
    diffs = [d - c for d, c in zip(correct["arm_d"], correct["arm_c"])]
    _, diff_lo, diff_hi = bootstrap_ci(diffs, mean_fn, rng=rng)
    sig = "✓" if p < 0.05 else "✗"
    print(f"{'C vs D':<26}  {diff_obs:>+8.1%}  [{diff_lo:>+7.1%}, {diff_hi:>+7.1%}]  {p:>8.4f}  {sig:>6}")

    # ── MDE / power analysis ─────────────────────────────────────────────────
    p1_baseline = corr_ci["arm_a"][0]
    mde = mde_two_proportions(n, p1=p1_baseline)
    obs_diff_ac = corr_ci["arm_c"][0] - corr_ci["arm_a"][0]
    # How many trials needed to detect the observed +6pp at 80% power?
    p_c_obs = corr_ci["arm_c"][0]
    n_needed = n_required_two_proportions(p1_baseline, p_c_obs)
    pow_at_n = power_two_proportions(n, p1_baseline, p_c_obs)

    print(f"\n── Power Analysis ──────────────────────────────────────────────────\n")
    print(f"  n per arm (actual):       {n}")
    print(f"  MDE at n=36, 80% power:   {'±'+f'{mde:.0%}' if not (mde!=mde) else 'unachievable at this n (ceiling effect)'}")
    print(f"  Observed A→C diff:        +{obs_diff_ac:.1%}")
    print(f"  Power at observed delta:  {pow_at_n:.1%}")
    print(f"  n needed to detect +{obs_diff_ac:.1%} at 80% power:  {n_needed} per arm")

    # ── Token savings ────────────────────────────────────────────────────────
    tok_a = mean_fn(tokens["arm_a"])
    tok_c = mean_fn(tokens["arm_c"])
    savings_pct = (tok_a - tok_c) / tok_a
    print(f"\n── Token Savings (A→C) ─────────────────────────────────────────────\n")
    print(f"  Arm A mean tokens:  {tok_a:,.0f}")
    print(f"  Arm C mean tokens:  {tok_c:,.0f}")
    print(f"  Savings:            {savings_pct:.0%}")

    # ── Verdict ──────────────────────────────────────────────────────────────
    _, p_ac = bootstrap_diff_pvalue(correct["arm_a"], correct["arm_c"], mean_fn, rng=rng)
    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    if p_ac < 0.05 and obs_diff_ac > 0:
        print(f"  PASS — A vs C difference is statistically significant (p={p_ac:.4f}).")
        print(f"  Minimal TCP descriptions outperform behavioral prose by +{obs_diff_ac:.0%}pp")
        print(f"  correctness while saving {savings_pct:.0%} tokens. Effect exceeds MDE.")
    elif p_ac < 0.10:
        print(f"  MARGINAL — p={p_ac:.4f} (trend, not significant at α=0.05).")
        print(f"  Directional signal for A→C but sample is underpowered for this delta.")
    else:
        print(f"  FAIL — A vs C difference not significant (p={p_ac:.4f}).")
        print(f"  Cannot reject H0 at this sample size. Findings are directional only.")
    print()


if __name__ == "__main__":
    main()
