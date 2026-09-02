# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

## Human Context (P8 append)

### Deployment runbook
URL: `https://runbooks.internal/taskq-api/deploy` (follow the "Standard cutover" checklist; the Gate 4 release tag is the input parameter). Steps mirror §5 Deployment Log in CONFIG_RECORDS: tag → alembic upgrade → systemd restart → `/healthz` check.

### Rollback owner + on-call
- Rollback owner: Release Engineering on-call (PagerDuty schedule `taskq-release`).
- On-call escalation: Service Lead (FR-08) → Platform/DBA → Engineering Director.
- Rollback procedure: CONFIG_RECORDS §7 Rollback SOP. Any rollback must be recorded in CONFIG_RECORDS §5 Deployment Log and trigger a Phase 9 post-mortem entry.

### Post-release monitoring dashboard
Grafana: `https://grafana.internal/d/taskq-api-overview` — panels include p50/p95/p99 latency, 5xx rate, FR-08 in-flight task count vs `TASKQ_MAX_CONCURRENT`, and `TASKQ_TASK_TIMEOUT` kill rate. Alert: 5xx > 1% over 5 min pages the rollback owner.

### Customer comms template
```
Subject: taskq-api release <TAG> deployed

We have rolled out <TAG> to production on <DATE>. No customer action required.

What changed: <one-line summary from RELEASE_NOTES.md>
Rollback plan: in place; if you see errors, contact support and we will roll back within the SLA.

— taskq Release Engineering
```
Send via the customer-comms channel after the `/healthz` check passes; delay at least 30 min after cutover to confirm dashboards are green.
