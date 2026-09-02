# Release Notes — taskq-redo v1.0.0

> **Release date**: 2026-09-02
> **Release commit**: `2d029ce56faf383b2a199731f914374dd48f63fc` (`release(P6): Gate4 PASS score=96.7 — pipeline complete`)
> **Phase**: 6 — Final Review / Gate 4
> **Prior release**: none — this is the first formal release (`git tag -l` returns empty; no earlier `release(...)` commit subject exists in `git log`).

---

## Gate 4 Composite Score

**96.73 / 100** — PASS

Sourced from `.methodology/quality_manifest.json` (`gate_results.gate4.score = 96.73`), the persistent source-of-truth per `phase6_plan.md` v2.12.0. Detailed per-dimension breakdown in `06-quality/QUALITY_REPORT.md`.

| Dimension | Score | Threshold |
|---|---|---|
| Linting (ruff) | 100.0 | ≥ 90 |
| Type Safety (pyright) | 100.0 | ≥ 85 |
| Test Coverage | 100.0 (242/242 stmts) | ≥ 80 |
| Security (bandit) | 95.0 | ≥ 80 |
| Secrets Scanning (gitleaks) | 100.0 | ≥ 100 |
| License Compliance | 100.0 | ≥ 100 |
| Mutation Testing (mutmut) | 100.0 (killed=17, survived=0) | ≥ 70 |
| Architecture (CRG) | 91.7 | ≥ 80 |
| Readability | 94.4 | ≥ 80 |
| Error Handling | 100.0 | ≥ 80 |
| Documentation | 88.5 | ≥ 80 |
| Performance (NFR-01, p95 < 30 ms) | 100.0 (p95 ≈ 0.39 ms) | ≥ 80 |
| Integration Coverage | 82.0 (30 httpx cases) | ≥ 60 |
| Test Assertion Quality | 97.1 | ≥ 80 |
| Execute Verification Target | 100.0 | ≥ 80 |
| Traceability | 100.0 | ≥ 80 |

Mutation-testing evidence: `.methodology/mutation_score.json` (`killed=17`, `survived=0`, `mutated_files=12`, scope `service`+`repository`, cache_sha256 `046971ec…`). `.mutmut-cache` present in repo root.

---

## Functional Requirements Delivered

All 10 FRs cleared Gate 1 and are carried into this release. Per-FR Gate 1 scores from `.methodology/quality_manifest.json` `gate_results.gate1`.

| FR ID | Feature | Gate 1 Score |
|---|---|---|
| FR-01 | Task resource CRUD API (`POST/GET/LIST/DELETE /tasks`), name-uniqueness, cursor pagination (limit ≤ 200, default 50), transactional result cascading | 100.0 |
| FR-02 | Task execution endpoint (`POST /tasks/{id}/run`) — 202 + run_id, async subprocess (no `shell=True`), state machine `pending→running→succeeded/failed/timeout`, child kill on timeout, history newest-first | 99.75 |
| FR-03 | API-Key auth — 401 on missing/invalid/revoked, `hmac.compare_digest`, hash-only storage, plaintext printed once at create | 100.0 |
| FR-04 | Scope authorization — per-API-key scope constraints on route handlers; leak guard verified | 100.0 |
| FR-05 | Per-scope token-bucket rate limiting — 429 + `Retry-After` over burst, refill recovery | 99.5 |
| FR-06 | Repository session/transaction scaffolding (SQLAlchemy 2.x), request-scoped cleanup | 100.0 |
| FR-07 | Schema migrations — `v1_initial`, `v2_tags`, `v3_split_results` (reversible) | 100.0 |
| FR-08 | Runner process supervision — subprocess execution, child termination on timeout, no orphan processes | 100.0 |
| FR-09 | Health endpoints (`/livez`, `/readyz`) without auth; DB connectivity probe | 100.0 |
| FR-10 | Centralized error envelope — problem+json across all endpoints, DB-URL redaction | 99.5 |

---

## Phase-Wide Change Summary (FR-01 → FR-10)

The 10 FR close commits captured at the head of this release (verified against `git log --format='%H %h %s'`):

| FR | Commit | Subject |
|---|---|---|
| FR-01 | `070d3e70282b3c402c90979226f25e63b53906dc` | `feat(FR-01): Gate1 PASS — score=100.0 [phase=5]` |
| FR-02 | `37f5334ef35db7399a5b81214335469724824ce3` | `feat(FR-02): Gate1 PASS — score=99.8 [phase=5]` |
| FR-03 | `73700aeb7075780c10d2cf05d59e0e3790e05908` | `feat(FR-03): Gate1 PASS — score=100.0 [phase=5]` |
| FR-04 | `988c5c8a77cbdfe3228c959d54ecb3178bc095ae` | `feat(FR-04): Gate1 PASS — score=100.0 [phase=5]` |
| FR-05 | `91af3fc4886532fb4dc72a2f6452c0bbba9f2e9e` | `feat(FR-05): Gate1 PASS — score=99.5 [phase=5]` |
| FR-06 | `7f67e02e238bb5e2100b15f98b58d48dd8748251` | `feat(FR-06): Gate1 PASS — score=100.0 [phase=5]` |
| FR-07 | `2d526e918abbcbc7a0e321d2bc5be47709d02b58` | `feat(FR-07): Gate1 PASS — score=100.0 [phase=5]` |
| FR-08 | `2860c95dee869382cd30180ec8ccc8d0426f1150` | `feat(FR-08): Gate1 PASS — score=100.0 [phase=5]` |
| FR-09 | `6446fab206204a16c036fb0fcabd612bf5632433` | `feat(FR-09): Gate1 PASS — score=100.0 [phase=5]` |
| FR-10 | `87792d351444c4a431ea60a4ffb3f1cd6d6f72c9` | `feat(FR-10): Gate1 PASS — score=99.5 [phase=5]` |

Adjacent pipeline commitments (verified):

- Gate 2 close: `b105a1270bfd1f5c26e91d29fbfad0f40b3127b2` `feat(P3): Gate2 PASS score=98.0`
- P3 exit: `4ab988505b28a3e2093fa3907043e29468e16403` `feat(P3-post-gate2): Gate 2 PASS + all 10 FR(s) Gate1 PASS; P3 exit`
- P3 → P4 handover: `f58d685525676f7858409b5ecf5b2eae0ff563f2` `handover: advance to Phase 4`
- Gate 3 PASS test commit (representative, one of five identical subjects): `a9b1163ed46c27b5b807bec5b56a486d2b51d980` `test(P4): Gate3 PASS score=97.7 — full test suite`
- P5 → P6 handover: `d1c4397c7f9a1d964db61d464c87dd7cf2126231` `handover: advance to Phase 6`
- P6 release: `2d029ce56faf383b2a199731f914374dd48f63fc` `release(P6): Gate4 PASS score=96.7 — pipeline complete`

> No `release(...)` or `tag` commit precedes `2d029ce` for this repository; `git tag -l` is empty. The first formal release therefore has no "since prior release" delta — the FR list above is the full deliverable.

---

## Known Limitations

From `06-quality/QUALITY_REPORT.md` and the persistent SoT `.methodology/quality_manifest.json`:

- **Defect counts**: Critical 0, High 0, Medium 0, Low 0 per Gate 4 evidence. The 5 LOW `B101 assert_used` bandit findings in `taskq_api/service/runner.py` (carried from Gate 3) are intentional post-condition invariants covered by the mutation suite (killed=17/17). No remediation required.
- **Documentation score 88.5**: 10 of 87 public symbols lack docstrings (notably 5 HTTP-error classes in `errors.py`, which are documented at the module level). Above the 80 threshold but below 100.
- **Architecture score 91.7**: `repository-task` community is large (56 nodes by Leiden-by-file grouping) — a known tool artefact of the `03-development/src/taskq_api/repository/` directory's shared SQLAlchemy import root, not a coupling fault (cohesion 0.39 > 0.17 floor).
- **No production deployment artefacts** shipped in this release: no SBOM signing, no container image, no changelog automation. SBOM (`sbom.json`) is generated but not signed.
- **Performance baseline**: measured at service layer (mean ~119 µs for `get_by_id`, ~698 µs for `list`); full HTTP p95 is enforced in CI via `make verify-system` (executed per NFR-12).
- **No external integrations**: API-key auth, task runner, and rate-limiter are self-contained; no third-party queueing, secret store, or observability backend is wired.

---

## References

- Quality composite & dimension breakdown: `06-quality/QUALITY_REPORT.md`
- Verification provenance: `05-verification/VERIFICATION_REPORT.md`
- System baseline (P5): `05-verification/BASELINE.md`
- Persistent SoT: `.methodology/quality_manifest.json`
- Mutation evidence: `.methodology/mutation_score.json`, `.mutmut-cache`
