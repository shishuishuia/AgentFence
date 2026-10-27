"""Integer-nanosecond, nearest-rank summaries with no prefilled thresholds."""

from dataclasses import dataclass
import math
import statistics


class BenchError(RuntimeError):
    """Fixed, non-sensitive benchmark invariant failure."""


def check(condition, message):
    if not condition:
        raise BenchError(message)


@dataclass(frozen=True)
class Config:
    iterations: int = 100
    rounds: int = 3
    warmup: int = 20
    file_bytes: int = 4096
    formal: bool = True

    def __post_init__(self):
        for name in ("iterations", "rounds", "warmup", "file_bytes"):
            value = getattr(self, name)
            check(type(value) is int and value > 0, "benchmark counts must be positive integers")
        check(16 <= self.file_bytes <= 65536, "file_bytes must be 16..65536")
        check(not self.formal or (self.iterations >= 100 and self.rounds >= 3), "formal benchmark needs at least 100 samples per group and 3 rounds")


def percentile(values, percent):
    check(values and 0 < percent <= 100, "invalid percentile input")
    return sorted(values)[math.ceil(len(values) * percent / 100) - 1]


def distribution(values):
    check(values and all(type(value) is int and value > 0 for value in values), "latencies must be positive integer nanoseconds")
    return {"count": len(values), "min_ns": min(values), "max_ns": max(values),
            "mean_ns": statistics.fmean(values), "p50_ns": percentile(values, 50),
            "p95_ns": percentile(values, 95), "p99_ns": percentile(values, 99)}


def summarize(samples, config: Config):
    check(all(row["valid"] is True and row["phase"] == "measured" for row in samples), "invalid/warmup samples cannot become successful measurements")
    groups, pooled = [], []
    for round_id in range(1, config.rounds + 1):
        for mode in ("enforced", "baseline"):
            for scenario in ("read", "write", "deny"):
                rows = [r for r in samples if (r["round"], r["mode"], r["scenario"]) == (round_id, mode, scenario)]
                check(len(rows) == config.iterations and [r["sequence"] for r in rows] == list(range(1, config.iterations + 1)), "missing, duplicated or reordered group samples")
                groups.append({"round": round_id, "mode": mode, "scenario": scenario, **distribution([r["elapsed_ns"] for r in rows])})
    check(len(samples) == config.rounds * 2 * 3 * config.iterations, "unexpected sample count")
    for mode in ("enforced", "baseline"):
        for scenario in ("read", "write", "deny"):
            pooled.append({"mode": mode, "scenario": scenario, **distribution([r["elapsed_ns"] for r in samples if r["mode"] == mode and r["scenario"] == scenario])})
    comparisons = []
    for round_id in range(1, config.rounds + 1):
        for scenario in ("read", "write", "deny"):
            a = next(g for g in groups if (g["round"], g["mode"], g["scenario"]) == (round_id, "enforced", scenario))
            b = next(g for g in groups if (g["round"], g["mode"], g["scenario"]) == (round_id, "baseline", scenario))
            comparisons.append({"round": round_id, "scenario": scenario, "p50_difference_ns": a["p50_ns"] - b["p50_ns"], "p50_relative_difference": a["p50_ns"] / b["p50_ns"] - 1})
    return {"status": "passed" if config.formal else "smoke_passed", "qualified_sampling": config.formal,
            "sample_count": len(samples), "percentile_method": "nearest-rank ceil(n*p/100), raw integer ns",
            "groups": groups, "pooled": pooled, "paired_round_comparisons": comparisons,
            "interpretation": "Observed client end-to-end difference, not isolated policy CPU cost or a performance guarantee."}
