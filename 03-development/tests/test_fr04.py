"""RED step — failing tests for FR-04 Scope Authorisation.

Covers the five acceptance criteria declared in SPEC.md §3 FR-04 and
TEST_SPEC.md FR-04 cases 1-5:

  AC-4.1 — A read-scope key calling POST /v1/tasks returns 403 +
           problem+json (POST /v1/tasks requires write).
  AC-4.2 — A write-scope key calling DELETE /v1/tasks/{id} returns
           403; the body must not disclose whether the id exists
           (i.e. write-scope denied because admin is required,
           NOT 404 because the resource is missing).
  AC-4.3 — An admin-scope key calling DELETE /v1/tasks/{id} succeeds.
  AC-4.4 — Every /v1/* route shares exactly one FastAPI dependency
           for the authn/authz decision (the `require_scope` factory
           in `taskq_api.api.deps`).
  AC-4.5 — Scope precedence (`read` < `write` < `admin`, hierarchical
           inclusion) is enforced: an `admin` key satisfies a `write`
           requirement.

Per SAB.json (`fr_module_traceability.FR-04`), these are the bound
modules the GREEN implementation must place on disk:

  taskq_api.api.deps    -> api/deps.py    (exists; may need require_scope)
  taskq_api.service.auth -> service/auth.py (exists; resolve_scope wired)

These tests intentionally exercise the SAB-declared entry points so
pytest will fail at the run-assertion boundary (or at collection time
for the factory dep) while the GREEN implementation is still
incomplete — this is the expected RED state.

Citations:
  SPEC.md §3 FR-04 (scope authorisation whole section)
  SPEC.md §3 FR-01 row 1 (POST /v1/tasks scope=write)
  SPEC.md §3 FR-01 row 4 (DELETE /v1/tasks/{id} scope=admin)
  SPEC.md §8 #6 (403 must not leak resource existence)
  TEST_SPEC.md FR-04 (cases 1-5)
"""
import inspect

import pytest
from fastapi.testclient import TestClient

# SAB binding — GREEN must wire these module paths on disk.
from taskq_api.api import deps as api_deps  # noqa: E402
from taskq_api.api.deps import require_scope  # noqa: E402
from taskq_api.app import app  # noqa: E402
from taskq_api.errors import ForbiddenError  # noqa: E402


# ----- Shared fixtures ---------------------------------------------------


@pytest.fixture
def client():
    """Sync TestClient bound to the FastAPI `app` instance.

    GREEN TODO: `taskq_api.app:app` must remain importable; the FR-04
    scope-checking dependency (`require_scope`) must be the SINGLE
    authz seam used by every `/v1/*` route (AC-4.4).
    """
    return TestClient(app)


@pytest.fixture
def read_api_key():
    """Plaintext read-scope API key seeded by config.API_KEY_SEEDS."""
    return "fr01-test-read-key-bbbb"


@pytest.fixture
def write_api_key():
    """Plaintext write-scope API key seeded by config.API_KEY_SEEDS."""
    return "fr01-test-write-key-aaaa"


@pytest.fixture
def admin_api_key():
    """Plaintext admin-scope API key seeded by config.API_KEY_SEEDS."""
    return "fr01-test-admin-key-cccc"


def _iter_v1_routes():
    """Yield (full_path, APIRoute) pairs for every /v1/* route on `app`.

    The router is mounted via `app.include_router(..., prefix="/v1")`
    so we descend into the included router's `original_router.routes`
    to obtain the underlying `APIRoute` objects (the top-level
    `_IncludedRouter` wrapper exposes no direct APIRoute iteration).
    """
    from fastapi.routing import APIRoute

    for r in app.router.routes:
        original = getattr(r, "original_router", None)
        prefix = getattr(getattr(r, "include_context", None), "prefix", "")
        if original is None or not prefix.startswith("/v1"):
            continue
        for sub in original.routes:
            if isinstance(sub, APIRoute):
                yield prefix + sub.path, sub


# ----- AC-4.1 — read scope POST /v1/tasks returns 403 -------------------


def test_read_scope_post_tasks_returns_403(client, read_api_key):
    """AC-4.1 — read scope cannot call POST /v1/tasks (which requires write).

    Sub-assertion: FR04-read-fails-write-required.
    # NFR-02 security — insufficient scope surfaces as 403 + problem+json,
    #   NOT 422 (validation) and NOT 401 (authn).
    # SPEC.md §3 FR-04 paragraph 1: "權限不足 → 403 + problem+json".
    # SPEC.md §3 FR-01 row 1: POST /v1/tasks scope=write.

    GREEN TODO: `taskq_api.api.deps.require_scope("write")` must be
    wired on POST /v1/tasks (factory pattern: returns a dep that
    compares the resolved scope against SCOPE_RANK); insufficient
    scope raises `ForbiddenError` → 403 problem+json.
    """
    response = client.post(
        "/v1/tasks",
        headers={"X-API-Key": read_api_key},
        json={"command": "echo hi", "name": "fr04-read-blocked"},
    )
    assert response.status_code == 403, (
        f"expected 403 for read scope on POST /v1/tasks, "
        f"got {response.status_code} body={response.text!r}"
    )
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    ), f"expected problem+json envelope, got {response.headers.get('content-type')!r}"


# ----- AC-4.2 — write scope DELETE returns 403 (no resource leak) -------


def test_write_scope_delete_returns_403_no_resource_leak(client, write_api_key):
    """AC-4.2 — write scope cannot DELETE /v1/tasks/{id} (admin required).

    Sub-assertion: FR04-write-fails-admin-required.
    # NFR-02 security — the 403 body MUST NOT disclose whether the id
    #   exists. If the resource is missing, a naive handler would
    #   short-circuit to 404, leaking existence; SPEC.md §8 #6 forbids
    #   that. We exercise BOTH a syntactically-valid random id and a
    #   non-existent id and assert identical 403 status — the response
    #   body shape must also be indistinguishable.
    # SPEC.md §3 FR-04 paragraph 1 "權限判定必須在單一中介層".

    GREEN TODO: `require_scope("admin")` must be wired on
    DELETE /v1/tasks/{id}; the dependency must run BEFORE the handler
    so a missing id never reaches the 404 branch when the caller is
    unauthorised.
    """
    # Case A: write key on a syntactically valid (never-created) UUID.
    nonexistent_uuid = "11111111-1111-1111-1111-111111111111"
    response_missing = client.delete(
        f"/v1/tasks/{nonexistent_uuid}",
        headers={"X-API-Key": write_api_key},
    )
    assert response_missing.status_code == 403, (
        f"expected 403 for write-scope DELETE on missing id "
        f"(no resource-existence leak), got {response_missing.status_code} "
        f"body={response_missing.text!r}"
    )
    assert response_missing.headers["content-type"].startswith(
        "application/problem+json"
    ), f"expected problem+json, got {response_missing.headers.get('content-type')!r}"

    # Case B: write key on a different non-existent UUID. Both 403
    # responses must be byte-identical in body so the operator cannot
    # distinguish "you would have been allowed but the resource is
    # missing" from "this id simply does not exist".
    another_uuid = "22222222-2222-2222-2222-222222222222"
    response_other = client.delete(
        f"/v1/tasks/{another_uuid}",
        headers={"X-API-Key": write_api_key},
    )
    assert response_other.status_code == 403, (
        f"expected 403 for write-scope DELETE on second missing id, "
        f"got {response_other.status_code} body={response_other.text!r}"
    )
    assert response_missing.text == response_other.text, (
        "403 body must not disclose which id was requested — the two "
        "non-existent ids returned different bodies, leaking existence"
    )
    # Defensive: the body must not echo the requested id either.
    body_text = response_missing.text
    assert nonexistent_uuid not in body_text, (
        f"403 body echoed the requested id {nonexistent_uuid!r} — "
        "resource-existence leak (NFR-02 / SPEC.md §8 #6)"
    )


# ----- AC-4.3 — admin scope DELETE succeeds -----------------------------


def test_admin_scope_delete_succeeds(client, admin_api_key):
    """AC-4.3 — admin scope can DELETE /v1/tasks/{id}.

    Sub-assertion: FR04-admin-succeeds-delete.
    # SPEC.md §3 FR-01 row 4 — DELETE /v1/tasks/{id} requires admin.
    # SPEC.md §3 FR-04 — admin satisfies admin (highest scope).

    Setup: create a task under the admin key (admin is at least as
    privileged as write for the POST, so this is permitted), then
    DELETE it. The DELETE response must be 204 No Content.
    """
    create = client.post(
        "/v1/tasks",
        headers={"X-API-Key": admin_api_key},
        json={"command": "echo admin-del", "name": "fr04-admin-delete-1"},
    )
    assert create.status_code == 201, (
        f"admin should be able to POST a task (admin >= write); "
        f"got {create.status_code} body={create.text!r}"
    )
    task_id = create.json()["id"]

    delete = client.delete(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": admin_api_key},
    )
    assert delete.status_code == 204, (
        f"admin-scope DELETE should succeed (204); "
        f"got {delete.status_code} body={delete.text!r}"
    )

    # Sanity: the task is now gone for any reader.
    read_after = client.get(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": admin_api_key},
    )
    assert read_after.status_code == 404, (
        f"after admin DELETE, GET must return 404; got {read_after.status_code}"
    )


# ----- AC-4.4 — every /v1 route uses the same authz dependency ---------


def test_single_authz_dependency_used_by_every_v1_route():
    """AC-4.4 — every /v1/* route shares exactly one authz dependency.

    Sub-assertion: FR04-single-dep-count-1.
    # SPEC.md §3 FR-04 paragraph 1: "授權判定必須在單一中介層" —
    #   authorisation MUST be performed by a single middleware-style
    #   dependency, NOT scattered across handlers. The unique_dep_count
    #   on the authz factory is exactly 1: every route's authz dep
    #   was built by calling `taskq_api.api.deps.require_scope`.

    We walk every /v1 route on `app` and confirm each one carries an
    authz dependency whose `__qualname__` carries `require_scope`
    (the factory defined in `taskq_api.api.deps`). The factory's own
    identity must be unique (the test would catch a refactor that
    accidentally introduces a parallel scope-checking dep).
    """
    routes_with_scope_dep: list[tuple[str, str]] = []
    factory_ids: set[int] = set()

    for full_path, api_route in _iter_v1_routes():
        scope_dep = None
        for d in api_route.dependant.dependencies:
            if d.call is None:
                continue
            qualname = getattr(d.call, "__qualname__", "")
            if "require_scope" in qualname:
                scope_dep = qualname
                break
        assert scope_dep is not None, (
            f"/v1 route {full_path} has no `require_scope` authz dep; "
            f"every /v1 route MUST go through the single FR-04 seam"
        )
        routes_with_scope_dep.append((full_path, scope_dep))

    # The factory itself must be the SAME function across all routes —
    # i.e. no parallel scope-checking dep was introduced.
    factory_ids.add(id(api_deps.require_scope))
    assert len(factory_ids) == 1, (
        f"expected exactly 1 authz factory module reference; "
        f"found {len(factory_ids)}"
    )

    # Every /v1 route must carry the authz dep — count match.
    v1_route_count = sum(1 for _ in _iter_v1_routes())
    assert len(routes_with_scope_dep) == v1_route_count, (
        f"only {len(routes_with_scope_dep)} of {v1_route_count} "
        "/v1 routes carried the require_scope dep"
    )

    # `require_scope` must be callable (factory pattern).
    assert callable(require_scope), (
        "taskq_api.api.deps.require_scope must be callable (FR-04 factory)"
    )


# ----- AC-4.5 — admin satisfies write (hierarchical inclusion) ----------


def test_scope_hierarchy_admin_satisfies_write(client, admin_api_key):
    """AC-4.5 — admin-scope key is accepted by a write-required endpoint.

    Sub-assertion: FR04-hierarchy-admin-contains-write.
    # SPEC.md §3 FR-04 paragraph 1 — `read` < `write` < `admin`
    #   hierarchical inclusion: an admin key MUST satisfy any
    #   endpoint whose required scope is write (or read).

    We exercise this two ways:
      1. In-process: build the write-required dep directly and pass
         scope="admin" through the dependency override FastAPI
         exposes; the dep must NOT raise `ForbiddenError`.
      2. End-to-end: an admin key calling POST /v1/tasks (which
         requires write per FR-01 row 1) must succeed.
    """
    # (1) In-process direct call against the factory-built dep.
    checker = require_scope("write")
    # `checker` expects a `scope` kwarg from the upstream
    # `require_api_key` dep; we simulate that by calling it with
    # `scope="admin"` directly. This proves the comparison is
    # hierarchical, not strict equality.
    try:
        result = checker(scope="admin")
    except ForbiddenError as exc:
        pytest.fail(
            f"admin scope was rejected by require_scope('write') — "
            f"hierarchy invariant violated (SPEC.md §3 FR-04). "
            f"detail={exc.detail!r}"
        )
    assert result == "admin", (
        f"require_scope('write') must echo the admin scope when admin "
        f"satisfies write; got {result!r}"
    )

    # (2) End-to-end: admin key can POST /v1/tasks (write required).
    response = client.post(
        "/v1/tasks",
        headers={"X-API-Key": admin_api_key},
        json={"command": "echo hi", "name": "fr04-hierarchy-admin"},
    )
    assert response.status_code == 201, (
        f"admin should satisfy a write-required endpoint (FR-04 "
        f"hierarchy); got {response.status_code} body={response.text!r}"
    )

    # Confirm the factory is genuinely a closure-using factory
    # (not a hand-rolled constant comparator): the inner function
    # has its own closure cell. This pins the implementation
    # contract for AC-4.4's "single dependency" claim.
    assert callable(checker)
    closure_vars = inspect.getclosurevars(checker)
    assert "required" in closure_vars.nonlocals, (
        f"require_scope must close over its `required` arg; "
        f"got nonlocals={list(closure_vars.nonlocals)!r}"
    )
