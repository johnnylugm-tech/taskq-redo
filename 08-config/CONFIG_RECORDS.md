# CONFIG_RECORDS.md - taskq-redo

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260902-scoreXX-12-g4bdbfc0
- Git Commit: 4bdbfc0
- Release Date: 2026-09-02

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | TASKQ_DB_URL=sqlite:///./taskq.db; TASKQ_LOG_LEVEL=DEBUG; TASKQ_LOG_FORMAT=text; TASKQ_HOST=127.0.0.1; TASKQ_PORT=8000; TASKE_CORS_ORIGINS= |
| Production | TASKQ_DB_URL=postgresql://taskq:${DB_PASSWORD}@db.internal:5432/taskq (NFR-04: password stripped before logging); TASKQ_LOG_LEVEL=INFO; TASKQ_LOG_FORMAT=json; TASKQ_HOST=0.0.0.0; TASKQ_PORT=8000; TASKQ_CORS_ORIGINS=https://app.example.com |

## 3. Dependency List
Pinned versions from requirements.lock (SPEC §6, NFR-07 license audit point):
```
fastapi==0.115.0
pydantic==2.9.2
sqlalchemy==2.0.36
alembic==1.13.3
uvicorn==0.32.0
httpx==0.27.2
import-linter==1.12.1
pip-licenses==4.3.4
mutmut==2.5.1
pytest-benchmark==4.0.0
hypothesis==6.112.1
attrs==26.1.0
sortedcontainers==2.4.0
```

## 4. Environment Variables
12 TASKQ_* keys per SPEC §5.1; copy `.env.example` to `.env` and edit. Never commit `.env`.
| Variable | Type | Description |
|----------|------|-------------|
| TASKQ_DB_URL | secret | SQLAlchemy connection string (FR-06; NFR-04 strips password from logs). |
| TASKQ_DB_POOL_SIZE | int | SQLAlchemy pool size; pool_pre_ping=True is hard-coded (FR-06). |
| TASKQ_TASK_TIMEOUT | float | Per-task subprocess timeout in seconds; must be > 0 (FR-08). |
| TASKQ_MAX_CONCURRENT | int | Background-task ceiling; excess queues (FR-08). |
| TASKQ_DRAIN_TIMEOUT | float | Graceful drain budget on shutdown in seconds (FR-08). |
| TASKQ_RATE_BURST | int | Token-bucket burst capacity (FR-05). |
| TASKQ_RATE_PER_SEC | float | Token-bucket refill rate, tokens/sec (FR-05). |
| TASKQ_CORS_ORIGINS | secret | Comma-separated CORS allow-list; empty = reject all (NFR-02). |
| TASKQ_LOG_LEVEL | enum | One of DEBUG / INFO / WARNING / ERROR. |
| TASKQ_LOG_FORMAT | enum | `json` (machine) or `text` (human). |
| TASKQ_HOST | string | uvicorn bind address; default 127.0.0.1 (non-loopback opt-in). |
| TASKQ_PORT | int | uvicorn bind port; default 8000. |

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-09-02 | harness-v4-20260902-scoreXX-12-g4bdbfc0 | git-tag + alembic upgrade head + uvicorn restart on systemd unit `taskq-api.service` | release-engineer on-call (see RELEASE_CHECKLIST Human Context) |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 8 | No runtime config drift this phase — all 12 TASKQ_* keys unchanged from SPEC §5.1 baseline; dependency pins unchanged in requirements.lock (NFR-07). | Gate 1 evidence from FR-08/FR-09/FR-10 closed without config edits; rollout inherits the locked baseline. |

## 7. Rollback SOP
**Trigger Condition**: any of — (a) Gate 4 14-dimension composite_score drops below 85 in a re-run after a Phase 8 change; (b) FR-08 task-runner regression detected (orphan subprocess or TASKQ_TASK_TIMEOUT not honored); (c) production 5xx error rate exceeds 1% over a 5-minute window post-cutover; (d) NFR-02 CORS misconfiguration observed in production logs.

**Commands**:
```bash
# 1. Mark the release bad and freeze traffic
sudo systemctl stop taskq-api.service
sudo systemctl status taskq-api.service   # confirm 'inactive (dead)'

# 2. Pin the previous known-good tag (last green Gate 4 release)
export PREV_TAG=$(git tag --sort=-creatordate | sed -n '2p')
git checkout "$PREV_TAG"

# 3. Roll the schema back one migration (alembic down_revision chain is linear per SAD)
alembic downgrade -1

# 4. Re-install the locked dependency set for that tag
pip-sync requirements.lock

# 5. Bring the previous build back up and confirm health
sudo systemctl start taskq-api.service
curl -fsS http://127.0.0.1:8000/healthz || echo "HEALTH CHECK FAILED — escalate to on-call"

# 6. Record the rollback in §5 Deployment Log and notify the rollback owner (RELEASE_CHECKLIST Human Context).
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

## Human Context (P8 append)

### Ownership per config item
| Owner | Items |
|-------|-------|
| Release Engineering | `TASKQ_HOST`, `TASKQ_PORT`, systemd unit `taskq-api.service`, deployment method/tag. |
| Platform / DBA | `TASKQ_DB_URL`, `TASKQ_DB_POOL_SIZE`, alembic upgrade chain, backup/restore of `taskq` Postgres. |
| Service Lead (FR-08) | `TASKQ_TASK_TIMEOUT`, `TASKQ_MAX_CONCURRENT`, `TASKQ_DRAIN_TIMEOUT` (task-runner behavior). |
| Service Lead (FR-05) | `TASKQ_RATE_BURST`, `TASKQ_RATE_PER_SEC` (token-bucket tuning). |
| Security | `TASKQ_CORS_ORIGINS` (NFR-02 allow-list), secret rotation for `TASKQ_DB_URL`, log redaction policy (NFR-04). |
| Observability | `TASKQ_LOG_LEVEL`, `TASKQ_LOG_FORMAT` (json in prod, text in dev), dashboards + alerts. |

### Secret rotation cadence
- `TASKQ_DB_URL` (DB password component): rotated every 90 days via Vault dynamic credentials; rotation runbook lives in the Platform wiki. The string itself in `.env` is regenerated automatically; manual edits are a violation of NFR-04 (no plaintext in logs).
- `TASKQ_CORS_ORIGINS`: re-evaluated each release; any addition requires Security sign-off recorded in §6 Configuration Change Log.
- Any `TASKQ_*` key classified as `secret` in §4 inherits the same 90-day rotation cadence.

### Access audit log reference
- Production `.env` reads are recorded by Vault (`audit/taskq-api.log`); review access monthly by the Security owner.
- Local dev `.env` is excluded from audit by design (never committed; `.env.example` is the canonical template).
- Schema-migration audit lives at `migrations/versions/` with author + timestamp per file; reviewed at every Phase 2→3 gate.
