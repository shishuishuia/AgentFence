"""Union-based permission durations, with monotonic HTTP observation bounds."""

from dataclasses import dataclass
import math

from bench.stats import check

CAUSES = ("ttl", "phase", "revoke", "uses")


@dataclass(frozen=True)
class WindowConfig:
    rounds: int = 3
    window_seconds: float = 6
    ttl_seconds: int = 2
    file_bytes: int = 4096
    formal: bool = True

    def __post_init__(self):
        check(type(self.rounds) is int and self.rounds >= 1, "invalid permission round count")
        check(type(self.window_seconds) in (int, float) and math.isfinite(self.window_seconds) and 3 <= self.window_seconds <= 120, "window must be 3..120 seconds")
        check(type(self.ttl_seconds) is int and 1 <= self.ttl_seconds <= 300 and self.ttl_seconds < self.window_seconds * .35, "TTL must fit two observed intervals inside the window")
        check(type(self.file_bytes) is int and 16 <= self.file_bytes <= 65536, "invalid permission fixture size")
        check(not self.formal or self.rounds >= 3, "formal permission experiment needs at least three rounds")


def union_ns(intervals):
    """Return a union's duration; touching/overlapping intervals are not double-counted."""
    merged = []
    for start, end in sorted(intervals):
        check(type(start) is int and type(end) is int and start <= end, "invalid authorization interval")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged)


def reduction(dynamic_ns, static_ns):
    check(type(dynamic_ns) is int and type(static_ns) is int and 0 <= dynamic_ns <= static_ns, "invalid permission reduction inputs")
    return None if static_ns == 0 else 1-dynamic_ns/static_ns


def interval_rows(record, requests):
    start, end = record["window_start_ns"], record["window_end_ns"]
    common = {"run_key":record["key"],"round":record["round"],"mode":record["mode"],"scenario":record["scenario"],
              "task_id":record["task_id"],"resource":"src/main.rs","window_start_ns":start,"window_end_ns":end}
    if record["mode"] == "baseline":
        return [{**common,"interval":1,"grant_id":"not_used","end_cause":"window_end",
                 "start_lower_ns":start,"start_upper_ns":start,"end_lower_ns":end,"end_upper_ns":end,
                 "estimated_start_ns":start,"estimated_end_ns":end,"estimated_duration_ns":end-start}]
    rows = []
    for grant in (request for request in requests if request["label"].startswith("grant:")):
        gid = grant["body"]["grant_id"]
        ttl_ns = grant["request_body"]["ttl_seconds"] * 1_000_000_000
        candidates = [("ttl", grant["started_ns"] + ttl_ns, grant["finished_ns"] + ttl_ns)]
        for request in requests:
            if request["started_ns"] < grant["started_ns"] or request["status"] >= 300:
                continue
            if request["method"] == "DELETE" and request["path"].endswith("/grants/" + gid):
                candidates.append(("revoke",request["started_ns"],request["finished_ns"]))
            if request["path"].endswith("/phase") and request["request_body"]["phase"] != "edit":
                candidates.append(("phase",request["started_ns"],request["finished_ns"]))
            if request["path"] == "/v1/tool/execute" and (request["body"].get("data") or {}).get("grant_id") == gid and request["body"]["data"].get("remaining_uses") == 0:
                candidates.append(("uses",request["started_ns"],request["finished_ns"]))
        winner = min(candidates, key=lambda candidate:(candidate[1]+candidate[2])/2)
        # The formal schedule separates causes; ambiguous ordering is a failed run.
        check(all(candidate == winner or winner[2] < candidate[1] for candidate in candidates), "invalidation ordering is ambiguous within HTTP uncertainty")
        a, b = max(start,grant["started_ns"]), max(start,grant["finished_ns"])
        c, d = min(end,winner[1]), min(end,winner[2])
        check(a <= b < c <= d <= end, "authorization interval does not fit the observation window")
        mid_start, mid_end = (a+b)//2, (c+d)//2
        rows.append({**common,"interval":len(rows)+1,"grant_id":gid,"end_cause":winner[0],
                     "start_lower_ns":a,"start_upper_ns":b,"end_lower_ns":c,"end_upper_ns":d,
                     "estimated_start_ns":mid_start,"estimated_end_ns":mid_end,"estimated_duration_ns":mid_end-mid_start})
    check(len(rows) == (2 if record["scenario"] in ("ttl","revoke") else 1), "missing permission grant intervals")
    expected = {"ttl":["ttl","ttl"],"phase":["phase"],"revoke":["revoke","phase"],"uses":["uses"]}[record["scenario"]]
    check([row["end_cause"] for row in rows] == expected, "the observed earliest invalidation cause is incorrect")
    return rows


def summarize(records, rows, config):
    comparisons = []
    for round_id in range(1, config.rounds+1):
        for scenario in CAUSES:
            pair = [record for record in records if record["round"] == round_id and record["scenario"] == scenario]
            check(len(pair) == 2 and {record["mode"] for record in pair} == {"enforced","baseline"}, "missing permission treatment")
            duration = int(config.window_seconds * 1_000_000_000)
            check(all(record["window_end_ns"]-record["window_start_ns"] == duration for record in pair), "paired task windows differ")
            dynamic = [row for row in rows if row["round"] == round_id and row["scenario"] == scenario and row["mode"] == "enforced"]
            estimate = union_ns([(row["estimated_start_ns"],row["estimated_end_ns"]) for row in dynamic])
            lower = union_ns([(row["start_upper_ns"],row["end_lower_ns"]) for row in dynamic])
            upper = union_ns([(row["start_lower_ns"],row["end_upper_ns"]) for row in dynamic])
            check(0 <= lower <= estimate <= upper <= duration, "permission duration bounds are inconsistent")
            comparisons.append({"round":round_id,"scenario":scenario,"static_duration_ns":duration,
                                "dynamic_estimated_ns":estimate,"dynamic_lower_ns":lower,"dynamic_upper_ns":upper,
                                "reduction_estimate":reduction(estimate,duration),
                                "reduction_lower_bound":reduction(upper,duration),
                                "reduction_upper_bound":reduction(lower,duration)})
    return {"status":"passed" if config.formal else "smoke_passed","qualified_observation":config.formal,
            "run_count":len(records),"interval_count":len(rows),"comparisons":comparisons,
            "clock":"time.perf_counter_ns; integer ns; HTTP request/response brackets",
            "interpretation":"Granted write-time in a fixed task observation window; midpoint estimates with measurement bounds, not attack probability or statistical confidence intervals."}
