"""Recompute the README's numeric claims from the bundled evidence; no model calls."""

import json
from math import isclose
from pathlib import Path

DATA = json.loads(Path(__file__).with_name("results.json").read_text(encoding="utf-8"))
ROWS = DATA["initial_and_merged_24"]
MROWS = DATA["mcs_common24"]
IDS = set(DATA["common24_ids"])
FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "total_tokens",
)
assert len(ROWS) == len({r["case"] for r in ROWS}) == len(DATA["common24_ids"]) == 24
assert {r["case"] for r in ROWS} == IDS
assert len(MROWS) == len({r["case"] for r in MROWS}) == len(IDS) == 24
assert {r["case"] for r in MROWS} == IDS


def check(r):
    t = r["tokens"]
    assert r["valid"] and isinstance(r["resolved"], bool)
    assert all(isinstance(t[k], int) and t[k] >= 0 for k in FIELDS)
    assert t["total_tokens"] == t["input_tokens"] + t["output_tokens"]
    assert t["uncached_input_tokens"] == t["input_tokens"] - t["cached_input_tokens"]


for row in ROWS:
    for stages in row["arms"].values():
        for stage, value in stages.items():
            for r in value if stage == "additional_attempts" else [value]:
                check(r)
        if "merged" in stages:
            retries = stages["additional_attempts"]
            expected = next(
                (r for r in retries if r["resolved"]),
                retries[-1] if retries else stages["initial"],
            )
            assert len(retries) <= 3
            for key in ("resolved", "valid", "tokens", "outcome_sha256"):
                assert expected[key] == stages["merged"][key]
for row in MROWS:
    for r in row["arms"].values():
        check(r)
    original = next(r for r in ROWS if r["case"] == row["case"])
    assert original["instance_id"] == row["instance_id"] and original["repo"] == row["repo"]
    assert original["arms"]["gateway_off"]["initial"]["resolved"] == row["arms"]["gateway_off"]["resolved"]
    assert original["arms"]["gateway_off"]["initial"]["tokens"] == row["arms"]["gateway_off"]["tokens"]
    assert original["arms"]["gateway_off"]["initial"]["outcome_sha256"] == row["arms"]["gateway_off"]["outcome_sha256"]


solved_off = {r["case"] for r in MROWS if r["arms"]["gateway_off"]["resolved"]}
solved_pc = {r["case"] for r in MROWS if r["arms"]["MCS"]["resolved"]}
assert solved_off == solved_pc and len(solved_pc) == 16


def aggregate(records):
    return {
        "cases": len(records),
        "passed": sum(r["resolved"] for r in records),
        **{k: sum(r["tokens"][k] for r in records) for k in FIELDS},
    }


summaries = {}
for cohort, ids, stage, arms in [
    ("common24_initial", IDS, "initial", ["gateway_off", "S", "CS", "MCS"]),
    ("common24_merged", IDS, "merged", ["gateway_off", "S", "CS", "MCS"]),
]:
    sums = {}
    for arm in arms:
        records = (
            [r["arms"]["MCS"] for r in MROWS]
            if arm == "MCS"
            else [r["arms"][arm][stage] for r in ROWS if r["case"] in ids]
        )
        sums[arm] = aggregate(records)
    baseline = sums["gateway_off"]["total_tokens"]
    for r in sums.values():
        r["reduction_percent"] = 100 * (baseline - r["total_tokens"]) / baseline
    summaries[cohort] = sums

assert [summaries["common24_initial"][a]["total_tokens"] for a in ["gateway_off", "S", "CS", "MCS"]] == [
    26410221,
    19247973,
    17680166,
    15766986,
]
assert [summaries["common24_initial"][a]["passed"] for a in ["gateway_off", "S", "CS", "MCS"]] == [16, 15, 15, 16]
assert [summaries["common24_merged"][a]["total_tokens"] for a in ["gateway_off", "S", "CS", "MCS"]] == [
    26410221,
    19063917,
    17464234,
    15766986,
]
assert [summaries["common24_merged"][a]["passed"] for a in ["gateway_off", "S", "CS", "MCS"]] == [16, 16, 16, 16]
initial = summaries["common24_initial"]
attempt_count = 0
attempt_totals = {}
for arm in ["gateway_off", "S", "CS"]:
    attempts = [r for row in ROWS for r in row["arms"][arm]["additional_attempts"]]
    attempt_count += len(attempts)
    attempt_totals[arm] = initial[arm]["total_tokens"] + sum(r["tokens"]["total_tokens"] for r in attempts)
assert attempt_count == 3
assert attempt_totals == {"gateway_off": 26410221, "S": 20364538, "CS": 18188031}
assert initial["MCS"]["cached_input_tokens"] - initial["gateway_off"]["cached_input_tokens"] == -10753664
assert initial["MCS"]["uncached_input_tokens"] - initial["gateway_off"]["uncached_input_tokens"] == 139880
assert initial["MCS"]["output_tokens"] - initial["gateway_off"]["output_tokens"] == -29451
extra = DATA["mcs_extra"]
assert extra["memory_generation_input"] + extra["memory_generation_output"] == 2041992
assert initial["MCS"]["total_tokens"] + 2041992 == 17808978
assert round(100 * (1 - 17808978 / initial["gateway_off"]["total_tokens"]), 2) == 32.57
assert round(initial["MCS"]["reduction_percent"], 2) == 40.30
assert extra["memory_observation"]["observed_primary_memory_content_sends"] == 0
# Check the advertised paired maxima, not maxima taken independently per arm.
for arm, expected_case in [("S", 202), ("CS", 464), ("MCS", 202)]:
    totals = (
        {r["case"]: r["arms"] for r in MROWS}
        if arm == "MCS"
        else {r["case"]: {a: r["arms"][a]["initial"] for a in [arm, "gateway_off"]} for r in ROWS if r["case"] in IDS}
    )
    best = max(
        totals,
        key=lambda i: 1 - totals[i][arm]["tokens"]["total_tokens"] / totals[i]["gateway_off"]["tokens"]["total_tokens"],
    )
    assert best == expected_case
    assert totals[best][arm]["resolved"] and totals[best]["gateway_off"]["resolved"]
for name, arms in summaries.items():
    print("\n" + name)
    for arm, r in arms.items():
        print(
            f"{arm:12} {r['total_tokens']:>12,} tokens  {r['reduction_percent']:>7.2f}% fewer  "
            f"{r['passed']}/{r['cases']} passed"
        )
print(
    "\nVerified: only 24 case IDs, original/retry alignment, token arithmetic and README totals. "
    "No inference or grading performed."
)

# Recompute the explicitly modeled cache-cost estimate, without altering missing observations.
estimate = json.loads(Path(__file__).with_name("cost-estimate.json").read_text(encoding="utf-8"))
rates = estimate["points_per_million"]


def solving_points(tokens):
    return (
        tokens["uncached_input_tokens"] * rates["uncached_input"]
        + tokens["cached_input_tokens"] * rates["cached_input"]
        + tokens["output_tokens"] * rates["output"]
    ) / 1_000_000


assert isclose(solving_points(initial["MCS"]), estimate["pc_solving_points"])
assert isclose(solving_points(initial["gateway_off"]), estimate["off_solving_points"])
assert round(100 * (1 - estimate["pc_solving_points"] / estimate["off_solving_points"]), 2) == 23.51
assert estimate["memory_input"] == extra["memory_generation_input"]
assert estimate["memory_output"] == extra["memory_generation_output"]
assert estimate["missing_cache_records"] == extra["missing_generation_cache_records"]
assert estimate["observed_memory_input"] + estimate["missing_cache_input"] == estimate["memory_input"]
ratio = estimate["observed_memory_cached"] / estimate["observed_memory_input"]
cache = estimate["observed_memory_cached"] + estimate["missing_cache_input"] * ratio
memory_points = (
    (estimate["memory_input"] - cache) * rates["uncached_input"]
    + cache * rates["cached_input"]
    + estimate["memory_output"] * rates["output"]
) / 1_000_000
saving = (1 - (estimate["pc_solving_points"] + memory_points) / estimate["off_solving_points"]) * 100
assert round(saving, 2) == 15.42
assert round(ratio * 100, 3) == 96.994
print(
    f"Estimated memory generation: {memory_points:.5f} points; estimated saving: {saving:.2f}% (not an observed bill)."
)

paired = estimate["paired_case_bounds"]
assert len(paired) == 24 and {r["case"] for r in paired} == IDS
mrows_by_case = {r["case"]: r for r in MROWS}
for row in paired:
    records = mrows_by_case[row["case"]]["arms"]
    assert row["powercontext_pass"] == records["MCS"]["resolved"]
    assert row["off_pass"] == records["gateway_off"]["resolved"]
    assert row["off_solving"] == records["gateway_off"]["tokens"]["total_tokens"]
    assert row["pc_solving_plus_memory"] >= records["MCS"]["tokens"]["total_tokens"]
    assert isclose(row["off_solving_points"], solving_points(records["gateway_off"]["tokens"]))
    assert row["pc_plus_memory_points_min"] <= row["pc_plus_memory_points_max"]
assert sum(r["pc_solving_plus_memory"] for r in paired) == 17808978
assert isclose(sum(r["pc_plus_memory_points_min"] for r in paired), 386.35531)
assert isclose(sum(r["pc_plus_memory_points_max"] for r in paired), 491.81524)


def token_saving(row):
    return (1 - row["pc_solving_plus_memory"] / row["off_solving"]) * 100


def estimated_cost_saving(row):
    cost = (
        row["pc_plus_memory_points_max"] - (row["pc_plus_memory_points_max"] - row["pc_plus_memory_points_min"]) * ratio
    )
    return (1 - cost / row["off_solving_points"]) * 100


for metric, expected in [(token_saving, 63.49), (estimated_cost_saving, 51.98)]:
    peak = max(paired, key=metric)
    assert peak["case"] == 456 and peak["powercontext_pass"] and peak["off_pass"]
    assert round(metric(peak), 2) == expected
print("Verified 24-task peaks: case 456, 63.49% tokens and 51.98% estimated cost; both passed.")

print(f"Verified {attempt_count} additional attempts and their recorded totals: {attempt_totals}.")

# This separate OFF/MS run measures requests, not the older OFF/MCS token run.
request_data = json.loads(Path(__file__).with_name("request-counts-24.json").read_text(encoding="utf-8"))
assert request_data["schema"] == "powercontext.request-counts.v1"
assert request_data["source"]["summary_sha256"] == "98333adce1892a283e55fc551c321c6a3411a6e0eefd6cf7158579c17ead504e"
assert request_data["source"]["generated_at"] == "2026-11-22T21:56:44.214489+00:00"
request_ids = request_data["case_ids"]
assert len(request_ids) == len(set(request_ids)) == 24
# Same task IDs, independently recorded outcomes and request counts.
assert set(request_ids) == IDS
request_cells = request_data["cells"]
assert len(request_cells) == len({r["id"] for r in request_cells}) == 48
request_pairs = {}
for record in request_cells:
    assert set(record) == {
        "id",
        "case",
        "arm",
        "primary_physical_requests",
        "primary_completed_responses",
        "valid_scored",
        "official_resolved",
        "outcome_sha256",
    }
    case, arm = record["case"], record["arm"]
    assert case in request_ids and arm in {"OFF", "MS"}
    assert record["id"] == f"{arm}-{case:04d}-r1"
    assert record["valid_scored"] is True and isinstance(record["official_resolved"], bool)
    requests, completed = record["primary_physical_requests"], record["primary_completed_responses"]
    assert type(requests) is int and type(completed) is int and 0 <= completed <= requests
    digest = record["outcome_sha256"]
    assert isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    pair = request_pairs.setdefault(case, {})
    assert arm not in pair
    pair[arm] = record
assert all(set(pair) == {"OFF", "MS"} for pair in request_pairs.values())
request_totals = {}
for arm, expected_requests, expected_completed in [("OFF", 708, 708), ("MS", 539, 538)]:
    records = [pair[arm] for pair in request_pairs.values()]
    request_totals[arm] = sum(r["primary_physical_requests"] for r in records)
    assert request_totals[arm] == expected_requests
    assert sum(r["primary_completed_responses"] for r in records) == expected_completed
    assert sum(r["official_resolved"] for r in records) == 15
assert {c for c, pair in request_pairs.items() if pair["OFF"]["official_resolved"]} == {
    c for c, pair in request_pairs.items() if pair["MS"]["official_resolved"]
}
assert [
    (r["id"], r["primary_physical_requests"] - r["primary_completed_responses"])
    for r in request_cells
    if r["primary_physical_requests"] != r["primary_completed_responses"]
] == [("MS-0388-r1", 1)]
assert isclose(request_totals["OFF"] / 24, 29.5)
assert isclose(request_totals["MS"] / 24, 22.458333333333332)
request_reduction = 100 * (1 - request_totals["MS"] / request_totals["OFF"])
assert round(request_reduction, 2) == 23.87
print(
    f"Verified separate OFF/MS run: 24 pairs, 708 -> 539 primary requests ({request_reduction:.2f}% fewer), "
    "708/538 completed responses; the same 15 tasks solved, all 48 records valid. "
    "Bundled arithmetic only; no model calls or regrading."
)
