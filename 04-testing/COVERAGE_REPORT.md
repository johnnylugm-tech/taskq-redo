# COVERAGE_REPORT

> Phase 4 — Per-FR Coverage Summary
> Test target: `03-development/tests`
> Source root measured: `03-development/src` (cov_target)
> Pytest invocation: `.venv/bin/python -m pytest 03-development/tests --cov=03-development/src --cov-report=term-missing -q`
> Coverage tool: `coverage` 7.x (`python -m coverage report --format=total` ⇒ `100`)

## 1. Overall Coverage

| Metric | Value |
|---|---|
| Total statements | 872 |
| Missed statements | 0 |
| **Overall coverage** | **100%** |
| Gate 3 threshold | ≥ 80% |
| Pass? | ✅ (100 ≥ 80) |

The `coverage report --format=total` run prints `100` (no `%` suffix in `--format=total`), confirming the same 100% figure reported by `pytest --cov=... --cov-report=term-missing`.

## 2. Per-Module Breakdown

| Module | Stmts | Miss | Cover |
|---|---:|---:|---:|
| `03-development/src/taskq_api/__init__.py` | 0 | 0 | 100% |
| `03-development/src/taskq_api/api/__init__.py` | 0 | 0 | 100% |
| `03-development/src/taskq_api/api/deps.py` | 36 | 0 | 100% |
| `03-development/src/taskq_api/api/health.py` | 23 | 0 | 100% |
| `03-development/src/taskq_api/api/tasks.py` | 48 | 0 | 100% |
| `03-development/src/taskq_api/app.py` | 28 | 0 | 100% |
| `03-development/src/taskq_api/config.py` | 1 | 0 | 100% |
| `03-development/src/taskq_api/errors.py` | 139 | 0 | 100% |
| `03-development/src/taskq_api/models/__init__.py` | 0 | 0 | 100% |
| `03-development/src/taskq_api/models/orm.py` | 30 | 0 | 100% |
| `03-development/src/taskq_api/models/schemas.py` | 37 | 0 | 100% |
| `03-development/src/taskq_api/repository/__init__.py` | 0 | 0 | 100% |
| `03-development/src/taskq_api/repository/health_repo.py` | 44 | 0 | 100% |
| `03-development/src/taskq_api/repository/key_repo.py` | 31 | 0 | 100% |
| `03-development/src/taskq_api/repository/rate_repo.py` | 53 | 0 | 100% |
| `03-development/src/taskq_api/repository/session.py` | 48 | 0 | 100% |
| `03-development/src/taskq_api/repository/task_repo.py` | 106 | 0 | 100% |
| `03-development/src/taskq_api/service/__init__.py` | 0 | 0 | 100% |
| `03-development/src/taskq_api/service/auth.py` | 7 | 0 | 100% |
| `03-development/src/taskq_api/service/health.py` | 47 | 0 | 100% |
| `03-development/src/taskq_api/service/ratelimit.py` | 11 | 0 | 100% |
| `03-development/src/taskq_api/service/runner.py` | 135 | 0 | 100% |
| `03-development/src/taskq_api/service/tasks.py` | 48 | 0 | 100% |
| **TOTAL** | **872** | **0** | **100%** |

## 3. Uncovered Lines

None. Every statement in the measured source tree is executed by at least one test in the `03-development/tests` collection. The `Missing` column in the per-module table is uniformly `0`.

> Caveat: this 100% figure reflects statement coverage only. Two FR-05 tests (`test_burst_over_limit_returns_429_with_retry_after`, `test_rate_limit_recovers_after_refill`) failed at the *precondition* stage (see `TEST_RESULTS.md`); those failures gate FR-05 behaviour but do not produce missed statements because the runtime path the tests exercised (the write handler returning 201) is itself a covered statement. Closing those FR-05 defects is a behavioural follow-up tracked under deferred issues, not a coverage gap.

## 4. Reconciliation Notes

- Coverage was measured by `pytest --cov=03-development/src --cov-report=term-missing` against the same scoped tree (`03-development/tests`), then independently re-measured with `python -m coverage report --format=total`. Both produce `100%`.
- No `.coveragerc` is present that would alter the source scope; the layout used (`--cov=03-development/src`) matches `phase4_ctx.json`'s `cov_target` exactly, so Gate 3's `cross_artifact.py` will compare these numbers against a re-measurement using the same scope and they will reconcile.
- The 872-statement total corresponds to the 22 modules under `03-development/src/taskq_api/`; `0`-statement `__init__.py` files are counted in module count but contribute no missable lines, so the "0% missed / 100% covered" reading is correct for every row.

## 5. Raw Evidence

- Captured raw stream: `04-testing/coverage_raw.txt` (head: failing assertion tracebacks; tail: full `--cov=term-missing` table reproduced above and the `2 failed, 234 passed, 6 warnings in 112.15s (0:01:52)` summary line).
- Companion test-run summary: `04-testing/TEST_RESULTS.md`.