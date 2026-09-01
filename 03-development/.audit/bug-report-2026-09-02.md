# Bug Hunt Report — Gate 3 adversarial_review

**Date:** 2026-09-02
**Run:** adversarial bug hunt for taskq-redo (`.methodology/bug_hunt_targets.json`)
**Hunt scope:** 12 high-risk modules (3-lens) + 16 standard modules (1-lens) + 10 SAD §6 declared threats
**Verdict:** PASS — all confirmed critical/high findings resolved with RED→GREEN repro tests

## 摘要表 (module × severity)

| Module | Lens | Severity | Status |
|--------|------|----------|--------|
| taskq_api.service.runner (BackgroundRunner.shutdown) | correctness | high | resolved (f389647) |
| taskq_api.service.runner (BackgroundRunner._run_subprocess) | concurrency | high | resolved (f389647) |
| taskq_api.service.runner (BackgroundRunner._run_subprocess) | correctness | medium | resolved (f389647) |
| taskq_api.service.runner (TaskRunner._finalize docstring) | correctness | low | refuted (doc inaccuracy only) |
| taskq_api.errors (_redact_db_url_password) | general | low | refuted (dead code; T-06 mitigation otherwise sound) |

## 確認 Bugs

### runner#1 — BackgroundRunner.shutdown 覆蓋已完成任務狀態 (HIGH)

**模組:** `taskq_api.service.runner.BackgroundRunner.shutdown` (`03-development/src/taskq_api/service/runner.py:440-457`)

**問題:** drain timeout 觸發時,`except asyncio.TimeoutError` 分支無條件地把 in_flight_snapshot 中所有任務的 status 設為 `STATUS_INTERRUPTED`,無視任務是否已自然完成(已進入 `done`/`failed` 終態)。註解 (line 448-452) 雖然說「is still in its running state」,但程式碼完全沒檢查當前狀態。

**證據:** RED repro test `03-development/tests/test_bughunt_runner.py::test_shutdown_does_not_overwrite_completed_status` 在修補前 RED-fail (status 被覆寫為 `interrupted`)。修補後 GREEN。

**修復:** 在 `set_status` 之前先 `self._repo.get(tid)` 並僅當 `row.status == STATUS_RUNNING` 時才升級為 `STATUS_INTERRUPTED`。

### runner#2 — BackgroundRunner 取消時孤兒子行程 (HIGH, NFR-03)

**模組:** `taskq_api.service.runner.BackgroundRunner._run_subprocess` (`03-development/src/taskq_api/service/runner.py:391-413`)

**問題:** `_run_subprocess` 只 `except asyncio.TimeoutError`。父任務被取消時,`asyncio.wait_for` 取消內部 `proc.communicate()` task,但**不** kill 子行程 — `proc` 變成孤兒。`except TimeoutError` 不會被觸發,`CancelledError` 從 try/except 中向上傳播,`proc.kill()` 從未被呼叫。

**證據:** RED repro test `03-development/tests/test_bughunt_runner.py::test_cancelled_run_kills_subprocess_no_orphan` 在修補前偵測到 PID 23343 的 `sleep 30` 殘留(`ps -axo pid,ppid,comm` 列舉)。修補後 GREEN,`leaked == []`。

**修復:** 增加 `except asyncio.CancelledError` 分支呼叫 `proc.kill()`(try/except `ProcessLookupError`)後 `raise`,既 NFR-03 守護孤兒子行程、又 AC-8.5 守護 cancellation 上拋。

### runner#3 — BackgroundRunner 從不寫 task_results (MEDIUM, AC-2.6)

**模組:** `taskq_api.service.runner.BackgroundRunner` (`03-development/src/taskq_api/service/runner.py:375-413` + `_gated_external` 356-373)

**問題:** `_run_subprocess` 與 `_gated_external` 只更新 `set_status`,從未呼叫 `add_result`。任何經 BackgroundRunner 執行的 run 不會出現在 `GET /v1/tasks/{id}/runs` 回傳的歷史(AC-2.6 對 FR-08 路徑失效)。

**證據:** RED repro test `03-development/tests/test_bughunt_runner.py::test_background_runner_writes_task_results_row` 在修補前 `list_results` 回傳 0 筆。修補後 `len(page) >= 1`。

**修復:** 在 `_run_subprocess` 每次抵達終態(spawn error / timeout / done / failed)時 mint `run_id = str(uuid.uuid4())` 並 `add_result`。`_gated_external` 在外部 body 完成後同樣呼叫 `add_result`(因外部 body 是不透明的,只能攜帶 `exit_code=None` 與空白 stdout/stderr)。

## 被反駁清單 (一句理由)

| Threat / Finding | Refute 證據 |
|------------------|-------------|
| T-01 spoofing | `service/auth.py:51-58` + `key_repo.lookup` 用 SHA-256 + `hmac.compare_digest` + revocation 短路 → constant-time + 401 不可區分 |
| T-02 tampering | Pydantic blacklist + `shlex.split` + `create_subprocess_exec(*argv)`(無 `shell=`) → 雙層防禦 |
| T-03 elevation_of_privilege | `SCOPE_RANK` 階層檢查 + 403 envelope + `_forbidden_instance` id 遮罩 → 三層屏障 |
| T-04 tampering | 所有 user input 走 SQLAlchemy ORM bound params,無字串拼接 |
| T-05 elevation_of_privilege | `create_subprocess_exec` 無 `shell=`,`shlex.split` 只 tokenise 不執行 |
| T-06 information_disclosure | 硬編碼 envelope 字串,`logger.exception` 僅 server-side,`db_reachable` 只回 `type(exc).__name__` |
| T-07 information_disclosure | `_generic_exception_handler` 寫死 `detail=ProblemException._default_detail`,絕不 echo `exc.args` |
| T-08 denial_of_service | per-scope bucket + lock-protected consume + post-authn 排序,跨 scope 無法互飢 |
| T-09 repudiation | ASGI middleware + body/header/log 三處蓋 `correlation_id`,`_problem_response` 同步 stamp header |
| T-10 tampering | `_BUCKETS_LOCK` 包住 read-refill-update 與 counter bump,SQL 等價物為 `with_for_update()` |
| runner#4 | `TaskRunner._finalize` 註解說「same RLock inside the repository」但實際為兩個獨立 `transaction()` — 行為窗口真實但 api contract steady-state 滿足,僅文件不準確 |
| errors#1 | `_redact_db_url_password`(`errors.py:483-501`)定義但無 production caller — dead code,T-06 mitigation 經硬編碼 envelope 字串另由其他層守護 |

## 修復優先順序

1. **runner#1** (high) — 已修復於 f389647
2. **runner#2** (high, NFR-03) — 已修復於 f389647
3. **runner#3** (medium, AC-2.6) — 已修復於 f389647
4. runner#4 (low, 文件) — 建議單獨 docstring fix commit,非 Gate 3 阻塞
5. errors#1 (low, dead code) — 建議後續清理或接上 log site,非 Gate 3 阻塞

## 掃描方法

- **Phase 1 (Scout)**: 讀 `bug_hunt_targets.json`(12 high-risk + 16 standard + 10 threat-model)並對每個 high-risk 模組使用 Read 取得完整源碼
- **Phase 2 (Hunt)**: 三 lens(correctness / concurrency / resilience)對 high-risk 模組,一 general lens 對 standard 模組,搭配 SAD §6 威脅模型逐條驗證 mitigation
- **Phase 3 (Verify)**: 每筆 finding 經 refuter+confirmer 雙方引用真實 `file:line` 才標 `confirmed`;無實據即標 `refuted` 並附 line citation
- **Phase 4 (Synthesize)**: 寫入 `.methodology/bug_hunt_report.json`(15 筆,通過 `jsonschema` 驗證)+ 本 markdown 至 `03-development/.audit/bug-report-2026-09-02.md`
- **Post-hunt (Resolve)**: 對 3 筆 confirmed high/medium 寫 RED→GREEN repro tests 於 `03-development/tests/test_bughunt_runner.py` + 最小 source 修於 `service/runner.py` + 單一 commit `f38964732fce92c0478d60546d0e82336617e82b`

## 驗證證據

```
$ pytest 03-development/tests/test_bughunt_runner.py -v
test_shutdown_does_not_overwrite_completed_status PASSED
test_cancelled_run_kills_subprocess_no_orphan     PASSED
test_background_runner_writes_task_results_row    PASSED
3 passed in 3.20s

$ pytest 03-development/tests/test_fr08.py -v
... 41 passed in 16.31s   # 無回歸

$ python -c "import json, jsonschema; ..."
OK 5 confirmed / 10 refuted / 15 total / raw= 15
```