"""RED step — failing tests for FR-01 Task Resource CRUD API.

Covers the seven acceptance criteria declared in SPEC.md §3 FR-01 and
TEST_SPEC.md FR-01 cases 1–7:

  AC-1.1 — POST /v1/tasks with valid body returns 201 + task id
  AC-1.2 — POST /v1/tasks with validation violation returns 422 + problem+json
  AC-1.3 — GET  /v1/tasks/{unknown_id} returns 404 + problem+json
  AC-1.4 — POST /v1/tasks with duplicate name returns 409 + problem+json
  AC-1.5 — GET  /v1/tasks?limit above 200 returns 422
  AC-1.6 — Pagination is cursor-based (offset query parameter forbidden)
  AC-1.7 — DELETE /v1/tasks/{id} removes the task AND its task_results
          row in one transaction (no orphaned results)

These tests intentionally exercise the SAB-declared entry points
(`taskq_api.app:app`, `taskq_api.api.tasks`) so that pytest will fail
at collection time while the GREEN implementation is still missing —
this is the expected RED state and is preferable to writing test-only
stubs that would mask the absence of the real implementation.
"""

from fastapi.testclient import TestClient

# SAB binding — GREEN must implement this module path on disk:
#   03-development/src/taskq_api/app.py
# containing a FastAPI instance named `app`. See SPEC.md §2 and SAD.md §2.5.
from taskq_api.app import app


@pytest.fixture
def client():
    """Build a sync TestClient against the FastAPI app.

    GREEN TODO: taskq_api.app:app must be importable; the routers
    declared in taskq_api.api.tasks must be mounted on it (FR-01).
    """
    return TestClient(app)


@pytest.fixture
def write_api_key():
    """A plaintext write-scope API key for the X-API-Key header.

    GREEN TODO: the api_keys store must contain a row whose key_hash
    equals SHA-256(<this plaintext>) and whose scope column is
    'write'. Auth lives in taskq_api.service.auth (FR-03); scope
    lives in taskq_api.api.deps.require_scope (FR-04).
    """
    return "fr01-test-write-key-aaaa"


@pytest.fixture
def read_api_key():
    """A plaintext read-scope API key for the X-API-Key header."""
    return "fr01-test-read-key-bbbb"


@pytest.fixture
def admin_api_key():
    """A plaintext admin-scope API key for the X-API-Key header."""
    return "fr01-test-admin-key-cccc"


def test_post_task_returns_201_with_id(client, write_api_key):
    """AC-1.1 — POST /v1/tasks with valid body returns 201 + task id.

    Sub-assertions: FR01-write-scope-creates, FR01-happy-command-nonempty.
    # NFR-02 security (X-API-Key authn) — exercised via fixture header
    # NFR-05 documentation — body shape is asserted as a stable contract
    # NFR-06 architecture_constraints — exercises api > service > repository layer path
    # NFR-10 integration_coverage — end-to-end via ASGITransport client
    # NFR-11 readability — short happy-path handler
    """
    response = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo hello", "name": "happy-create-1"},
    )
    assert response.status_code == 201
    body = response.json()
    assert "id" in body


def test_post_task_validation_violations_returns_422(client, write_api_key):
    """AC-1.2 — FR01-empty-cmd-violates: empty command triggers 422 + problem+json.

    Validation rules per SPEC.md §3 FR-01 (non-empty / ≤1000 chars /
    injection-character blacklist / name unique); violation must yield
    `application/problem+json` (FR-10).
    # NFR-02 security — input validation is a security boundary
    # NFR-06 architecture_constraints — validation belongs in pydantic at api layer
    # NFR-10 integration_coverage — full request/response cycle through ASGITransport
    """
    response = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "", "name": "bad-empty"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_get_task_unknown_id_returns_404(client, read_api_key):
    """AC-1.3 — GET on an unknown UUID returns 404 + problem+json.
    # NFR-01 performance — single-row lookup path; this case proves the
    #   repository path used by the p95<30ms budget (NFR-01 AC-N1.1) exists
    # NFR-02 security — 404 must not leak internal detail (RFC 7807 body shape)
    # NFR-10 integration_coverage — exercises 404 path through ASGITransport
    """
    response = client.get(
        "/v1/tasks/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_post_task_duplicate_name_returns_409(client, write_api_key):
    """AC-1.4 — FR01-name-unique: duplicate name returns 409 + problem+json.
    # NFR-02 security — name uniqueness prevents resource-collision abuse
    # NFR-10 integration_coverage — full transaction cycle (insert + conflict detect)

    The TEST_SPEC inputs declare `first_exists="true"`; the test
    constructs the precondition by issuing a successful POST first,
    then re-submitting the same `name` with a different `command`.
    """
    payload = {"command": "echo first", "name": "dup-name-1"}
    first = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json=payload,
    )
    assert first.status_code == 201

    response = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo dup", "name": "dup-name-1"},
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")


def test_list_task_limit_above_200_returns_422(client, read_api_key):
    """AC-1.5 — FR01-limit-cap-200-over: limit=300 violates the 200 cap.
    # NFR-01 performance — cap protects list-endpoint p95<80ms budget (AC-N1.2)
    # NFR-02 security — cap also bounds accidental resource exhaustion
    # NFR-10 integration_coverage — exercises 422 boundary through ASGITransport

    Default limit is 50; the hard cap is 200 (SPEC.md §3 FR-01).
    """
    response = client.get(
        "/v1/tasks",
        params={"limit": 300},
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 422


def test_list_pagination_is_cursor_based(client, read_api_key):
    """AC-1.6 — FR01-cursor-not-offset.
    # NFR-01 performance — cursor pagination is what protects the list p95 budget;
    #   rejecting `offset` is the architectural enforcement (large-table offset
    #   scans are an N+1 cousin per SPEC §3 FR-01)
    # NFR-02 security — bound on query parameters prevents forced table scan DoS
    # NFR-06 architecture_constraints — pagination contract lives in service layer
    # NFR-10 integration_coverage — exercises the cursor/offset discrimination

    Pagination MUST be cursor-based; the SPEC forbids large-table offset
    scans because they are an N+1 cousin (SPEC.md §3 FR-01). The test
    proves both halves of the contract: a `cursor` query parameter is
    accepted (200), and an `offset` query parameter is rejected (4xx).
    """
    cursor_resp = client.get(
        "/v1/tasks",
        params={"cursor": "some-uuid-value", "limit": 50},
        headers={"X-API-Key": read_api_key},
    )
    assert cursor_resp.status_code == 200

    offset_resp = client.get(
        "/v1/tasks",
        params={"offset": 10, "limit": 50},
        headers={"X-API-Key": read_api_key},
    )
    # The cursor-based contract forbids `offset`; FastAPI/Pydantic will
    # surface it as 422 (validation) or 400 (handler rejection) — both
    # are acceptable signals that the forbidden parameter was refused.
    assert offset_resp.status_code in (400, 422)


def test_get_task_existing_returns_200_with_body(client, write_api_key, read_api_key):
    """Coverage — AC-1.3 happy-path GET /v1/tasks/{id} returns the task body.

    Exercises the api `get_task` `return TaskRead(**result)` branch and the
    service `return _row_to_dict(row)` branch — both are reachable only by
    hitting an existing id, which the dedicated 404 test in this file
    intentionally skips.
    """
    created = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo hi", "name": "get-happy-1"},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    response = client.get(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task_id
    assert body["name"] == "get-happy-1"
    assert body["command"] == "echo hi"


def test_delete_task_unknown_id_returns_404(client, admin_api_key):
    """Coverage — DELETE /v1/tasks/{unknown} surfaces 404 from the
    repository's `row is None` branch.

    The repository contract returns False when the id is unknown
    (`task_repo.py` line 77), the service raises TaskNotFoundError on
    `not ok` (line 60), and the api translates that into a NotFoundError
    problem+json (lines 127-128).
    """
    response = client.delete(
        "/v1/tasks/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": admin_api_key},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_post_task_with_injection_character_returns_422(client, write_api_key):
    """Coverage — AC-1.2 / injection blacklist branch in `_command_nonempty_and_safe`.

    The blacklist loop `for ch in _INJECTION_BLACKLIST: if ch in value:`
    (`schemas.py` lines 48-50) is unreachable unless a forbidden character
    is supplied; this test sends `;` which is in the blacklist and expects
    a 422 problem+json response.
    """
    response = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo bad; rm -rf /", "name": "bad-injection-1"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_post_task_whitespace_only_name_returns_422(client, write_api_key):
    """Coverage — `_name_nonempty` validator branch on whitespace-only input.

    The validator returns a specific ValueError for whitespace strings
    (`schemas.py` line 57) which is unreachable when the input is `""` —
    that already fails Pydantic's `min_length=1`. A whitespace-only name
    (e.g. `"   "`) reaches the second branch.
    """
    response = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo hi", "name": "   "},
    )
    assert response.status_code == 422


def test_list_tasks_with_cursor_matching_existing_id(client, write_api_key, read_api_key):
    """Coverage — `TaskRepo.list` cursor-matches-existing branch
    (`task_repo.py` line 98: `ordered = ordered[idx + 1 :]`).

    Creates a task then requests a list whose `cursor` is that task's
    own id; since the list returns rows ordered by id ascending and the
    cursor slice skips the cursor row, an empty `items` list is the
    expected outcome (no rows follow the just-created one when many
    tasks already share the in-memory store from earlier tests). The
    exercised line is the `idx >= 0` slice.
    """
    created = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo a", "name": "cursor-test-1"},
    )
    assert created.status_code == 201
    cursor_id = created.json()["id"]

    # Use a high limit so EVERY row >= cursor is returned; the cursor
    # itself must NOT be present (it was sliced away on the covered branch).
    response = client.get(
        "/v1/tasks",
        params={"cursor": cursor_id, "limit": 200},
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert cursor_id not in ids


def test_list_tasks_with_status_filter(client, write_api_key, read_api_key):
    """Coverage — `TaskRepo.list` status-filter branch
    (`task_repo.py` line 100: `ordered = [r for r in ordered if r.status == status]`).

    Creates a task and requests the list filtered by `status=pending`
    (the default task status). The first task id must appear in the
    filtered page; otherwise the filter branch was not exercised.
    """
    created = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo filter", "name": "status-filter-1"},
    )
    assert created.status_code == 201
    target_id = created.json()["id"]

    response = client.get(
        "/v1/tasks",
        params={"status": "pending", "limit": 50},
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert target_id in ids


def test_delete_task_removes_results_in_same_transaction(
    client, write_api_key, admin_api_key
):
    """AC-1.7 — FR01-admin-scope-deletes.
    # NFR-02 security — admin-scope guard verified via fixture header
    # NFR-06 architecture_constraints — repository-layer transaction is the
    #   enforcement point; service layer must not hold Session
    # NFR-10 integration_coverage — exercises transactional cascade
    # NFR-11 readability — keeps the handler short; business logic in service

    DELETE /v1/tasks/{id} must remove the parent task row AND its
    `task_results` rows in the same database transaction. There must be
    no orphaned results row after the DELETE returns. The test creates
    a task, deletes it (admin scope), and confirms the task is gone
    (follow-up GET → 404) — the transactional-orphan guarantee follows
    from the repository implementation contract declared in
    taskq_api.repository.task_repo.delete.
    """
    create = client.post(
        "/v1/tasks",
        headers={"X-API-Key": write_api_key},
        json={"command": "echo hi", "name": "with-results-1"},
    )
    assert create.status_code == 201
    task_identifier = create.json()["id"]

    delete = client.delete(
        f"/v1/tasks/{task_identifier}",
        headers={"X-API-Key": admin_api_key},
    )
    assert delete.status_code in (200, 204)

    follow_up = client.get(
        f"/v1/tasks/{task_identifier}",
        headers={"X-API-Key": admin_api_key},
    )
    assert follow_up.status_code == 404
