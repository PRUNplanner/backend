# Performance and load testing

Runs the backend against a throwaway Postgres + Redis in Docker (in memory,
separate ports, gone afterwards), seeded with real game data from FIO and
deterministic fake users, plans and empires (see [Game data](#game-data)), then:

1. **Benchmarks** (`manage.py perf_bench`): in-process requests to the main
   endpoints as a power user, recording SQL query count, response size and
   latency with a cold cache (database path) and a warm cache.
2. **Load test** (`perf/locustfile.py`, full mode only): gunicorn with the
   production config, hit by Locust with anonymous visitors and logged-in
   planners who read and save plans and empires.
3. **Report** (`perf/report.py`): writes `summary.json` and `report.md` and
   flags regressions against the previous run of the same scale, mode and game
   data source.

Needs Docker running and `uv`. Locust is fetched by `uvx`; it is not a project
dependency.

```bash
perf/run.sh                                # small scale, benchmarks + 60 s load test
perf/run.sh --mode quick                   # benchmarks only (about a minute)
perf/run.sh --scale medium --users 100 --duration 3m
perf/run.sh --mode quick --only planning   # just the planning endpoints
perf/run.sh --keep                         # leave the stack up afterwards
perf/run.sh --no-snapshot                  # fake game data instead of the FIO snapshot
```

In Claude Code, `/perf` in the workspace runs this and interprets the result.

## Scales

| scale | users | plans (approx.) | fake planets | CXPC days |
| --- | ---: | ---: | ---: | ---: |
| small | 100 | 1,000 | 500 | 30 |
| medium | 1,000 | 10,000 | 3,000 | 60 |
| large | 5,000 | 75,000 | 6,000 | 90 |

`perf_user_0` is the power user (3× the plans, extra empires) the benchmarks
run as. All seeded users have the password `perf-password`.

With the FIO snapshot the scale only sets users, plans and empires; all real
planets, materials, buildings and recipes are loaded. The planets, materials,
buildings and recipes of a scale apply to fake game data only.

## Game data

`manage.py perf_snapshot` downloads the public game data from FIO once
(materials, buildings, recipes, all planets with resources, fees and COGC
programs, and the exchanges), one request after another, and saves it to
`perf/snapshot/gamedata.json.gz` (gitignored) with its download date. `run.sh`
runs it when the file is missing, and falls back to fake game data if FIO is
unreachable.

```bash
uv run --env-file perf/env.perf backend/manage.py perf_snapshot                   # refresh the snapshot
uv run --env-file perf/env.perf backend/manage.py perf_snapshot --cxpc-sample 20  # plus real AI1 price history for 20 tickers
```

`seed_perf --source auto|snapshot|fake` (default `auto`: the snapshot if it
exists) loads it through the production FIO importers. Price history is fake
except for the `--cxpc-sample` tickers. Fake plans use real planets, buildings
and recipe ids, and half of them sit on 20 popular planets. After seeding, the
analytics aggregations run (plan insights and the empire material
snapshots), so the analytics endpoints serve real data. The empire states behind
the material snapshots come from a rough model of the plans, not the frontend's
calculation.

The source is written to `targets.json` and `meta.json`, and a run is only
compared with runs of the same source. Refreshing the snapshot changes the
data, so expect a one-off shift in bytes and latency after a refresh.

## Results

Each run writes `.perf/<timestamp>-<scale>-<mode>/` (gitignored): `meta.json`,
`bench.json`, `locust_stats.csv` and friends, `server.log`, `summary.json` and
`report.md`. Regressions are flagged when an endpoint makes more queries, its
cold median is 25% and 2 ms slower, the load test's p95 is 25% and 5 ms
slower or its throughput drops 15%, or any request fails.

Compare timings only between runs on the same machine; query counts are
comparable everywhere.

## Digging into an endpoint

With the stack left up (`--keep`):

```bash
uv run --env-file perf/env.perf backend/manage.py perf_bench --show-sql planning.plan_list
uv run --env-file perf/env.perf backend/manage.py perf_bench --only planning --iterations 50
```

When adding an endpoint the frontend relies on, add it to `build_endpoints()`
in `core/management/commands/perf_bench.py` and, if it matters under load, a
task in `perf/locustfile.py`.
