"""
Summarize a perf run directory and compare it with the previous run of the same scale, mode and source.

    python perf/report.py .perf/<run> [--baseline .perf/<older-run>]

Writes <run>/summary.json and <run>/report.md, prints the report. Standard library only.
"""

import argparse
import csv
import json
from pathlib import Path
from typing import cast

type JSON = dict[str, object]

QUERY_INCREASE = 0  # any extra query on an endpoint is flagged
LATENCY_RATIO = 1.25  # 25% slower ...
LATENCY_MIN_DELTA_MS = 2.0  # ... and at least this much slower in absolute terms
LOAD_P95_RATIO = 1.25
LOAD_P95_MIN_DELTA_MS = 5.0
LOAD_RPS_RATIO = 0.85


def read_json(path: Path) -> JSON:
    return json.loads(path.read_text()) if path.exists() else {}


def as_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def read_locust(path: Path) -> JSON:
    if not path.exists():
        return {}
    endpoints: JSON = {}
    aggregated: JSON = {}
    with path.open(newline='') as handle:
        for row in csv.DictReader(handle):
            stats: JSON = {
                'requests': int(row['Request Count']),
                'failures': int(row['Failure Count']),
                'rps': as_float(row['Requests/s']),
                'p50_ms': as_float(row['50%']),
                'p95_ms': as_float(row['95%']),
                'p99_ms': as_float(row['99%']),
                'avg_ms': as_float(row['Average Response Time']),
            }
            if row['Name'] == 'Aggregated':
                aggregated = stats
            else:
                endpoints[f'{row["Type"]} {row["Name"]}'] = stats
    return {'aggregated': aggregated, 'endpoints': endpoints}


def summarize(run_dir: Path) -> JSON:
    bench = read_json(run_dir / 'bench.json')
    return {
        'run': run_dir.name,
        'meta': read_json(run_dir / 'meta.json'),
        'bench': bench.get('endpoints', {}),
        'load': read_locust(run_dir / 'locust_stats.csv'),
    }


def obj(value: object) -> JSON:
    """The value as a JSON object, or {} if it is anything else. Keys are strings: it came from json."""
    return cast(JSON, value) if isinstance(value, dict) else {}


def get(data: object, *keys: str) -> object:
    for key in keys:
        data = obj(data).get(key)
    return data


def num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def source(meta: JSON) -> str:
    return str(meta.get('source', 'fake'))  # runs from before the FIO snapshot all used fake game data


def run_key(meta: JSON) -> tuple[object, object, str, bool]:
    """Only runs with the same key are comparable."""
    return meta.get('scale'), meta.get('mode'), source(meta), bool(meta.get('prod_like'))


def find_baseline(run_dir: Path, meta: JSON) -> Path | None:
    for candidate in sorted(run_dir.parent.iterdir(), reverse=True):
        if candidate.name >= run_dir.name or not (candidate / 'summary.json').exists():
            continue
        other = obj(read_json(candidate / 'summary.json').get('meta'))
        if run_key(other) == run_key(meta):
            return candidate
    return None


def pct(new: float, old: float) -> str:
    return f'{(new - old) / old * 100:+.0f}%' if old else 'n/a'


def compare_bench(current: JSON, baseline: JSON | None, regressions: list[str]) -> list[str]:
    bench = obj(current.get('bench'))
    if not bench:
        return []
    lines = [
        '## Endpoint benchmarks (in-process, cold = cache cleared before each request)',
        '',
        '| endpoint | queries | cold median ms | cold p95 ms | warm median ms | bytes |',
        '| --- | ---: | ---: | ---: | ---: | ---: |',
    ]
    for name, result in bench.items():
        old = get(baseline, 'bench', name)
        error = get(result, 'error')
        if error:
            regressions.append(f'{name}: {error}')
            lines.append(f'| {name} | error | {error} | | | |')
            continue
        queries = num(get(result, 'queries')) or 0
        cold = num(get(result, 'cold', 'median_ms')) or 0
        cold_p95 = num(get(result, 'cold', 'p95_ms')) or 0
        warm = num(get(result, 'warm', 'median_ms')) or 0
        size = num(get(result, 'bytes')) or 0

        queries_cell, cold_cell = f'{queries:.0f}', f'{cold:.2f}'
        old_queries = num(get(old, 'queries'))
        old_cold = num(get(old, 'cold', 'median_ms'))
        if old_queries is not None:
            queries_cell += f' ({queries - old_queries:+.0f})' if queries != old_queries else ''
            if queries - old_queries > QUERY_INCREASE:
                regressions.append(f'{name}: {old_queries:.0f} -> {queries:.0f} queries')
        if old_cold:
            cold_cell += f' ({pct(cold, old_cold)})'
            if cold > old_cold * LATENCY_RATIO and cold - old_cold >= LATENCY_MIN_DELTA_MS:
                regressions.append(f'{name}: cold median {old_cold:.2f} -> {cold:.2f} ms')
        lines.append(f'| {name} | {queries_cell} | {cold_cell} | {cold_p95:.2f} | {warm:.2f} | {size:,.0f} |')
    lines.append('')
    return lines


def compare_load(current: JSON, baseline: JSON | None, regressions: list[str]) -> list[str]:
    aggregated = obj(get(current, 'load', 'aggregated'))
    endpoints = obj(get(current, 'load', 'endpoints'))
    if not aggregated:
        return []
    load_meta = get(current, 'meta', 'load')
    lines = ['## Load test (gunicorn + Locust)', '', f'Settings: {json.dumps(load_meta)}', '']

    rps = num(aggregated.get('rps')) or 0
    p95 = num(aggregated.get('p95_ms')) or 0
    failures = num(aggregated.get('failures')) or 0
    requests = num(aggregated.get('requests')) or 0
    summary = f'**{requests:,.0f} requests, {rps:.1f} req/s, p95 {p95:.0f} ms, {failures:,.0f} failures**'
    old_rps = num(get(baseline, 'load', 'aggregated', 'rps'))
    old_p95 = num(get(baseline, 'load', 'aggregated', 'p95_ms'))
    if old_rps and old_p95:
        summary += f' (baseline {old_rps:.1f} req/s, p95 {old_p95:.0f} ms)'
        if rps < old_rps * LOAD_RPS_RATIO:
            regressions.append(f'load: throughput {old_rps:.1f} -> {rps:.1f} req/s')
        if p95 > old_p95 * LOAD_P95_RATIO and p95 - old_p95 >= LOAD_P95_MIN_DELTA_MS:
            regressions.append(f'load: p95 {old_p95:.0f} -> {p95:.0f} ms')
    if failures:
        regressions.append(f'load: {failures:,.0f} failed requests (see locust_failures.csv)')
    lines += [
        summary,
        '',
        '| request | count | fails | p50 ms | p95 ms | p99 ms |',
        '| --- | ---: | ---: | ---: | ---: | ---: |',
    ]

    def by_count(item: tuple[str, object]) -> float:
        return -(num(get(item[1], 'requests')) or 0)

    for name, stats in sorted(endpoints.items(), key=by_count):
        p95_cell = f'{num(get(stats, "p95_ms")) or 0:.0f}'
        old_endpoint_p95 = num(get(baseline, 'load', 'endpoints', name, 'p95_ms'))
        if old_endpoint_p95:
            p95_cell += f' ({pct(num(get(stats, "p95_ms")) or 0, old_endpoint_p95)})'
        lines.append(
            f'| {name} | {num(get(stats, "requests")) or 0:,.0f} | {num(get(stats, "failures")) or 0:,.0f} '
            f'| {num(get(stats, "p50_ms")) or 0:.0f} | {p95_cell} | {num(get(stats, "p99_ms")) or 0:.0f} |'
        )
    lines.append('')
    return lines


def compare(current: JSON, baseline: JSON | None) -> tuple[list[str], list[str]]:
    """Returns (report lines, regressions)."""
    regressions: list[str] = []
    lines = compare_bench(current, baseline, regressions) + compare_load(current, baseline, regressions)
    return lines, regressions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--baseline', type=Path, help='Compare with this run instead of the previous one.')
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    current = summarize(run_dir)
    (run_dir / 'summary.json').write_text(json.dumps(current, indent=2))

    meta = obj(current['meta'])
    baseline_dir: Path | None = args.baseline or find_baseline(run_dir, meta)
    baseline = read_json(baseline_dir / 'summary.json') if baseline_dir else None

    body, regressions = compare(current, baseline)
    dirty = ' (uncommitted changes)' if meta.get('git_dirty') else ''
    header = [
        f'# Perf run {run_dir.name}',
        '',
        f'scale **{meta.get("scale")}**, mode **{meta.get("mode")}**, game data **{source(meta)}**, '
        f'{"**prod-like** (2 shared cores, prod memory limits), " if meta.get("prod_like") else ""}'
        f'git `{meta.get("git_sha")}` on `{meta.get("git_branch")}`{dirty}',
        f'Baseline: `{baseline_dir.name}`'
        if baseline_dir
        else 'Baseline: none (first run at this scale, mode and source)',
        '',
    ]
    if regressions:
        header += [f'## {len(regressions)} regression(s)', '', *[f'- {r}' for r in regressions], '']
    else:
        header += ['## No regressions', '']

    report = '\n'.join(header + body)
    (run_dir / 'report.md').write_text(report)
    print(report)
    print(f'REGRESSIONS: {len(regressions)}')


if __name__ == '__main__':
    main()
