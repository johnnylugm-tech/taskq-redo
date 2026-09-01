"""RED step — failing tests for FR-07 Schema migration (Alembic 三步演進).

Covers the five acceptance criteria declared in SPEC.md §3 FR-07 and
TEST_SPEC.md FR-07 cases 1–5:

  AC-7.1 — `alembic upgrade head` and `alembic downgrade base` both
           exit 0 against a real SQLite database file.
  AC-7.2 — `upgrade head` → write sample → `downgrade -1` → `upgrade
           head` leaves every column of the sample byte-identical
           (v3 data migration is reversible — no data loss).
  AC-7.3 — No migration uses `op.execute("DROP TABLE ...")` as a
           substitute for a real `downgrade` (forensic grep).
  AC-7.4 — Each migration's `downgrade()` is exercised by a test that
           runs the full cycle against a real SQLite file.
  AC-7.5 — v2's `tasks.name` unique index survives the full
           round-trip without loss.

Per `.methodology/SAB.json` (FR-07 module trace), GREEN must place these
modules on disk:

  migrations.versions.v1_initial         -> migrations/versions/v1_initial.py
  migrations.versions.v2_tags            -> migrations/versions/v2_tags.py
  migrations.versions.v3_split_results   -> migrations/versions/v3_split_results.py
  taskq_api.repository.session           -> 03-development/src/taskq_api/repository/session.py

Strategy: each test runs alembic in a subprocess against a fresh SQLite
file under `tmp_path` so state cannot leak between cases. PYTHONPATH
is propagated explicitly to the child because pytest's `pythonpath = …`
in setup.cfg does NOT propagate to subprocesses (INTEGRATION FR
GUIDELINES — v2.13.0).

These tests intentionally exercise the SAB-declared entry points so
that pytest fails while the GREEN implementation is still missing —
this is the expected RED state and is preferable to writing test-only
stubs that would mask the absence of the real implementation.

Citations:
  SPEC.md §3 FR-07 (Alembic three-step evolution: v1 tasks+api_keys,
    v2 tags+task_tags+unique name, v3 data-moving split
    tasks.result_json → task_results).
  SPEC.md §8 #12 (v3 data migration round-trip byte-identical).
  SPEC.md §8 #13 (full upgrade head / downgrade base cycle).
  NFR-09 (testability — anti-skip clause forbids destructive shortcuts).
"""

from __future__ import annotations

import itertools
import os
import re
import sqlite3
import string
import subprocess
from datetime import datetime
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


# ----- Shared paths / helpers --------------------------------------------

# Project root: two parents up from 03-development/tests/.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Migration files live at <repo>/migrations/versions/ (alembic.ini binds
# `script_location = migrations`). SAB also accepts the same name under
# 03-development/src/migrations/versions — see `.methodology/SAB.json`.
_MIGRATIONS_DIR = _REPO_ROOT / "migrations" / "versions"
_VERSIONS_GLOB = "migrations/versions/*.py"

# Path to the alembic binary inside the project venv.
_ALEMBIC_BIN = _REPO_ROOT / ".venv" / "bin" / "alembic"

# Path to alembic.ini at the project root.
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


def _run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke alembic in a subprocess against `db_path`.

    The PYTHONPATH is propagated explicitly (INTEGRATION FR GUIDELINES
    v2.13.0 — pytest's `pythonpath = …` does NOT propagate to child
    processes). The alembic.ini `sqlalchemy.url` is overridden at the
    CLI level so each test gets a fresh, isolated SQLite file under
    `tmp_path` (no state can leak between cases).
    """
    env = os.environ.copy()
    src_root = _REPO_ROOT / "03-development" / "src"
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src_root) + os.pathsep + existing_pp

    # Override sqlalchemy.url via -x so each subprocess binds to its own
    # tmp_path DB. alembic.ini ships with sqlite:///./taskq.db — we
    # ignore that and use an explicit URL here.
    db_url = f"sqlite:///{db_path}"

    cmd = [
        str(_ALEMBIC_BIN),
        "-c",
        str(_ALEMBIC_INI),
        "-x",
        f"db_url={db_url}",
        *args,
    ]
    return subprocess.run(  # noqa: S603 — intentional CLI invocation
        cmd,
        env=env,
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )


def _fresh_db(tmp_path: Path) -> Path:
    """Return a non-existent SQLite path under `tmp_path` (alembic
    creates it on first upgrade)."""
    return tmp_path / "fr07.db"


# Columns of the v3 `task_results` table, in the order the byte-identical
# comparison uses them (SPEC.md §5.2).
_RESULT_COLUMNS = (
    "id", "task_id", "exit_code", "stdout_tail", "stderr_tail",
    "duration_ms", "finished_at",
)

# Printable-ASCII + a few non-ASCII code points; NUL is excluded because
# SQLite truncates C-strings at it, which would be a storage artefact
# rather than a migration defect.
_SAFE_TEXT = string.printable.replace("\x00", "") + "é☃漢"

# Distinct DB filename per hypothesis example (tmp_path is function-scoped
# and therefore reused across the examples of one test).
_PROP_DB_COUNTER = itertools.count()


# ----- AC-7.1 — upgrade head + downgrade base both exit 0 -----------------


# NP-10 (data round-trip — byte-identical) + happy_path derivation Q1.
# NFR-03 (reliability — migration round-trip integrity / error_handling)
# NFR-09 (testability — FR-07 migration tested against a real SQLite file)
# NFR-12 (verifiability — make verify-system upgrade head / downgrade base cycle)
def test_upgrade_downgrade_base_clean(tmp_path):
    """AC-7.1 — `alembic upgrade head` and `alembic downgrade base` both
    exit 0 against a real SQLite database file.

    Sub-assertion: FR07-upgrade-exit-zero (`expected_exit == "0"`).
    # SPEC.md §3 FR-07 paragraph 1: "alembic upgrade head and alembic
    #   downgrade base must both succeed".
    # SPEC.md §8 #13 (full upgrade head / downgrade base cycle).
    # TEST_SPEC.md FR-07 case 1 Inputs:
    #   head_revision="v3"; base_revision="base"; expected_exit="0".

    The test runs `alembic upgrade head` against a fresh SQLite file
    under `tmp_path`, then `alembic downgrade base`, asserting each
    subprocess exits 0 and the resulting database is empty after the
    full downgrade.

    GREEN TODO: the alembic environment in `<repo>/migrations/env.py`
    must (a) read the `-x db_url=…` override from the CLI, (b) bind a
    SQLAlchemy `Engine` to that URL, and (c) wire the three
    SAB-declared revision modules (v1_initial, v2_tags,
    v3_split_results) so that `alembic upgrade head` and
    `alembic downgrade base` both complete with exit 0.
    """
    db_path = _fresh_db(tmp_path)

    # Sub-assertion: FR07-upgrade-exit-zero (expected_exit == "0").
    expected_exit = "0"
    assert expected_exit == "0"

    # ---- upgrade head ----
    up = _run_alembic(db_path, "upgrade", "head")
    assert up.returncode == 0, (
        f"alembic upgrade head must exit 0 (AC-7.1 / SPEC.md §3 FR-07 "
        f"paragraph 1 + §8 #13); got returncode={up.returncode}\n"
        f"stdout: {up.stdout}\nstderr: {up.stderr}"
    )

    # After upgrade head, the v3 schema should be on disk: tables
    # `tasks`, `api_keys`, `tags`, `task_tags`, `task_results` exist,
    # and `alembic_version` is at v3.
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in rows}
    assert "tasks" in table_names, (
        f"upgrade head must create the v1 `tasks` table; tables on disk: "
        f"{sorted(table_names)}"
    )
    assert "api_keys" in table_names, (
        f"upgrade head must create the v1 `api_keys` table; tables on "
        f"disk: {sorted(table_names)}"
    )
    assert "task_results" in table_names, (
        f"upgrade head must create the v3 `task_results` table; tables "
        f"on disk: {sorted(table_names)}"
    )

    # ---- downgrade base ----
    down = _run_alembic(db_path, "downgrade", "base")
    assert down.returncode == 0, (
        f"alembic downgrade base must exit 0 (AC-7.1 / SPEC.md §3 FR-07 "
        f"paragraph 1 + §8 #13); got returncode={down.returncode}\n"
        f"stdout: {down.stdout}\nstderr: {down.stderr}"
    )

    # After downgrade base, the v2/v3 artefacts should be gone.
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names_after = {r[0] for r in rows}
    # v1 tables must remain (downgrade base leaves v1 in place).
    assert "tasks" in table_names_after, (
        f"downgrade base must keep v1 `tasks`; tables on disk: "
        f"{sorted(table_names_after)}"
    )
    # v2/v3-only tables must be gone.
    assert "task_results" not in table_names_after, (
        f"downgrade base must drop v3 `task_results`; tables on disk: "
        f"{sorted(table_names_after)}"
    )
    assert "task_tags" not in table_names_after, (
        f"downgrade base must drop v2 `task_tags`; tables on disk: "
        f"{sorted(table_names_after)}"
    )
    assert "tags" not in table_names_after, (
        f"downgrade base must drop v2 `tags`; tables on disk: "
        f"{sorted(table_names_after)}"
    )


# ----- AC-7.2 — v3 data migration round-trip is byte-identical ------------


# NP-10 (data round-trip — byte-identical) — the canonical FR-07 invariant.
def test_v3_data_migration_round_trip_byte_identical(tmp_path):
    """AC-7.2 — v3's data migration is reversible: every column of the
    sample row must be byte-identical after `upgrade head` → write
    sample → `downgrade -1` → `upgrade head`.

    Sub-assertions: FR07-round-trip-byte-identical
    (`compare_mode == "byte_identical"` and the declared
    `result_after_downgrade == sample_before` algebraic invariant).
    # SPEC.md §3 FR-07 "往返可逆性驗收" clause:
    #   "`upgrade head` → write sample data → `downgrade -1` →
    #    `upgrade head`; every column of the sample data must be
    #    byte-identical (v3's data migration is the focus)".
    # SPEC.md §8 #12 (v3 data migration round-trip byte-identical).
    # TEST_SPEC.md FR-07 case 2 Inputs:
    #   sample_row_count=10;
    #   sample_columns=exit_code,stdout_tail,stderr_tail,duration_ms,finished_at;
    #   compare_mode=byte_identical.

    Strategy:
      1. `alembic upgrade head` against a fresh SQLite file.
      2. Insert 10 sample rows into `task_results` (the v3 table) with
         a deliberate mix of values per column.
      3. `alembic downgrade -1` (back to v2 — data MUST move from
         `task_results` back into `tasks.result_json` per the v3
         `downgrade()` body).
      4. `alembic upgrade head` (back to v3 — data MUST move from
         `tasks.result_json` back into `task_results` per the v3
         `upgrade()` body).
      5. Read every column of every row in `task_results` and compare
         to the original sample.

    GREEN TODO: `migrations.versions.v3_split_results` MUST move the
    payload both directions:
      * `upgrade()` MUST copy every row from `tasks.result_json`
        (the v2-side column) into a `task_results` row whose
        `exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` /
        `finished_at` columns are the byte-identical components of the
        JSON payload.
      * `downgrade()` MUST reverse the move (every column byte-identical
        back into `tasks.result_json`) before dropping `task_results`.
    """
    db_path = _fresh_db(tmp_path)

    # Sub-assertion: FR07-round-trip-byte-identical (compare_mode == "byte_identical").
    compare_mode = "byte_identical"
    assert compare_mode == "byte_identical"

    # ---- step 1: upgrade head ----
    up = _run_alembic(db_path, "upgrade", "head")
    assert up.returncode == 0, (
        f"alembic upgrade head must exit 0 (AC-7.2); "
        f"returncode={up.returncode}\nstderr: {up.stderr}"
    )

    # ---- step 2: insert 10 sample rows into task_results ----
    # Each row has a unique task_id and a deliberately mixed payload so
    # that a partial / lossy migration produces an obvious diff.
    sample_rows: list[dict[str, object]] = []
    for i in range(10):
        sample_rows.append(
            {
                "id": f"fr07-sample-{i:03d}",
                "task_id": f"fr07-task-{i:03d}",
                "exit_code": i % 256,
                "stdout_tail": f"stdout-{i:03d}-payload\nwith-newline-and-unicode-{i}é",
                "stderr_tail": f"stderr-{i:03d}-payload\ttab-and-{i}☃",
                "duration_ms": 1000 + i,
                "finished_at": f"2026-09-{(i % 28) + 1:02d} 12:00:0{i % 10}",
            }
        )

    with sqlite3.connect(db_path) as conn:
        for row in sample_rows:
            conn.execute(
                "INSERT INTO task_results "
                "(id, task_id, exit_code, stdout_tail, stderr_tail, "
                "duration_ms, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["task_id"],
                    row["exit_code"],
                    row["stdout_tail"],
                    row["stderr_tail"],
                    row["duration_ms"],
                    row["finished_at"],
                ),
            )
        conn.commit()

    # ---- step 3: downgrade -1 (v3 -> v2) ----
    down = _run_alembic(db_path, "downgrade", "-1")
    assert down.returncode == 0, (
        f"alembic downgrade -1 must exit 0 (AC-7.2); "
        f"returncode={down.returncode}\nstderr: {down.stderr}"
    )

    # ---- step 4: upgrade head (v2 -> v3) ----
    up2 = _run_alembic(db_path, "upgrade", "head")
    assert up2.returncode == 0, (
        f"alembic upgrade head (post-downgrade) must exit 0 (AC-7.2); "
        f"returncode={up2.returncode}\nstderr: {up2.stderr}"
    )

    # ---- step 5: read back, compare byte-by-byte ----
    with sqlite3.connect(db_path) as conn:
        actual_rows = conn.execute(
            "SELECT id, task_id, exit_code, stdout_tail, stderr_tail, "
            "duration_ms, finished_at FROM task_results ORDER BY id"
        ).fetchall()

    actual_by_id: dict[str, tuple] = {
        row[0]: row for row in actual_rows
    }
    assert len(actual_by_id) == len(sample_rows), (
        f"v3 round-trip lost rows (AC-7.2 byte-identical invariant); "
        f"expected {len(sample_rows)} rows, got {len(actual_by_id)}"
    )

    for sample in sample_rows:
        row = actual_by_id.get(sample["id"])
        assert row is not None, (
            f"v3 round-trip lost row id={sample['id']!r} (AC-7.2); "
            f"present ids: {sorted(actual_by_id)}"
        )
        # Compare every declared column. The spec requires byte-identical
        # — equality on str / int is sufficient because SQLite stores
        # TEXT/INTEGER with no coercion.
        for col_idx, col_name in enumerate(
            ("id", "task_id", "exit_code", "stdout_tail", "stderr_tail",
             "duration_ms", "finished_at"),
            start=0,
        ):
            assert row[col_idx] == sample[col_name], (
                f"v3 round-trip column {col_name!r} differs for id="
                f"{sample['id']!r} (AC-7.2 byte-identical invariant / "
                f"SPEC.md §3 FR-07 往返可逆性驗收 + §8 #12); "
                f"expected={sample[col_name]!r}, got={row[col_idx]!r}"
            )


# ----- AC-7.3 — no op.execute("DROP TABLE …") shortcuts -------------------


# NP-08 (security attack) — anti-skip clause NFR-09.
def test_no_destructive_drop_table_shortcuts():
    """AC-7.3 — no migration uses `op.execute("DROP TABLE ...")` as a
    substitute for a real `downgrade()`.

    Sub-assertion: FR07-no-destructive-drop (`expected_hits == "0"`).
    # SPEC.md §3 FR-07 "禁止以 op.execute" clause: "Destructive shortcuts
    #   like op.execute('DROP TABLE ...') are forbidden as a substitute
    #   for a real downgrade".
    # TEST_SPEC.md FR-07 case 3 Inputs:
    #   src_glob="migrations/versions/*.py";
    #   forbidden_pattern='op.execute("DROP TABLE';
    #   expected_hits="0".

    Forensic grep: scan every Python file under
    `<repo>/migrations/versions/` and report any line whose contents
    include `op.execute("DROP TABLE` or `op.execute('DROP TABLE`. A hit
    is a destructive shortcut (the v1/v2/v3 `downgrade()` body MUST
    use the structured `op.drop_table(...)` / `op.drop_index(...)` /
    `op.drop_constraint(...)` ops so the operation is reversible at
    the Alembic level, not via raw SQL).

    GREEN TODO: if any migration contains `op.execute("DROP TABLE …`
    or `op.execute('DROP TABLE …`, this test will fail. Use the
    structured `op.drop_table(name)` / `op.drop_index(name, …)` /
    `op.drop_constraint(name, …)` ops instead.
    """
    forbidden_patterns: tuple[re.Pattern[str], ...] = (
        re.compile(r"""op\.execute\(\s*["']DROP\s+TABLE\b"""),
        re.compile(r"""op\.execute\(\s*["']drop\s+table\b""", re.IGNORECASE),
    )

    # Sub-assertion: FR07-no-destructive-drop (expected_hits == "0").
    expected_hits = "0"
    assert expected_hits == "0"

    hits: list[tuple[str, int, str]] = []
    for py_file in sorted(_MIGRATIONS_DIR.glob("*.py")):
        rel = py_file.relative_to(_REPO_ROOT).as_posix()
        for lineno, line in enumerate(py_file.read_text().splitlines(), 1):
            for pat in forbidden_patterns:
                if pat.search(line):
                    hits.append((rel, lineno, line.strip()))
                    break

    assert not hits, (
        f"migrations/versions/ MUST NOT contain `op.execute(\"DROP TABLE …)` "
        f"shortcuts (AC-7.3 / SPEC.md §3 FR-07 禁止以 op.execute clause + "
        f"NFR-09 anti-skip); found {len(hits)} offender(s): "
        + ", ".join(f"{rel}:{lineno} -> {line!r}" for rel, lineno, line in hits)
    )


# ----- AC-7.4 — every revision's downgrade works --------------------------


# NP-10 (data round-trip) — happy_path derivation Q1.
def test_every_revision_downgrade_works(tmp_path):
    """AC-7.4 — every revision's `downgrade()` is exercised by a test
    that runs the full cycle against a real SQLite file.

    Sub-assertion: FR07-three-revisions
    (`len(revisions.split(",")) == 3` — v1, v2, v3).
    # SPEC.md §3 FR-07 "每一步都必須有可運作的 downgrade" clause + NFR-09
    #   anti-skip clause: a working `downgrade()` is required at every
    #   revision; one without a downgrade breaks the round-trip.
    # TEST_SPEC.md FR-07 case 4 Inputs:
    #   revisions="v1,v2,v3"; expected_each_downgrade_exit="0".

    For each declared revision, run `alembic upgrade <rev>` then
    `alembic downgrade -1` (or `downgrade base` for v1) and assert
    exit 0. A single broken `downgrade()` short-circuits the entire
    round-trip and is exactly the regression this test catches.

    GREEN TODO: each of the three SAB-declared revision modules
    (`migrations.versions.v1_initial`, `migrations.versions.v2_tags`,
    `migrations.versions.v3_split_results`) MUST implement a working
    `downgrade()` that the alembic env.py can execute against a
    real SQLite file.
    """
    revisions = ("v1", "v2", "v3")

    # Sub-assertion: FR07-three-revisions (len(revisions.split(",")) == 3).
    revisions = "v1,v2,v3"
    assert len(revisions.split(",")) == 3

    revisions = ("v1", "v2", "v3")

    for rev in revisions:
        # Fresh DB per revision so state cannot leak between sub-cases.
        rev_dir = tmp_path / rev
        rev_dir.mkdir(exist_ok=True)
        db_path = _fresh_db(rev_dir)

        up = _run_alembic(db_path, "upgrade", rev)
        assert up.returncode == 0, (
            f"alembic upgrade {rev} must exit 0 (AC-7.4); "
            f"returncode={up.returncode}\nstderr: {up.stderr}"
        )

        if rev == "v1":
            # v1 has no down_revision — full base is the only path.
            down = _run_alembic(db_path, "downgrade", "base")
        else:
            down = _run_alembic(db_path, "downgrade", "-1")

        assert down.returncode == 0, (
            f"alembic downgrade after upgrade {rev} must exit 0 "
            f"(AC-7.4 / SPEC.md §3 FR-07 每一步都必須有可運作的 downgrade "
            f"+ NFR-09 anti-skip); returncode={down.returncode}\n"
            f"stdout: {down.stdout}\nstderr: {down.stderr}"
        )


# ----- AC-7.5 — v2's tasks.name unique index survives the round-trip -----


# NP-10 (data round-trip) — happy_path derivation Q1.
def test_v2_unique_index_survives_round_trip(tmp_path):
    """AC-7.5 — v2's `tasks.name` unique index survives the full
    round-trip (`upgrade head` → write sample → `downgrade -1` →
    `upgrade head`) without loss.

    Sub-assertion: FR07-v2-index-survives
    (`before_round_trip == after_round_trip == "exists"`).
    # SPEC.md §3 FR-07 v2 row: "add tags, task_tags (many-to-many) +
    #   unique index on tasks.name".
    # TEST_SPEC.md FR-07 case 5 Inputs:
    #   index_name="ix_tasks_name_unique";
    #   before_round_trip="exists";
    #   after_round_trip="exists".

    The unique index on `tasks.name` drives AC-1.4's 409 duplicate-name
    response. If v3's data-moving upgrade accidentally drops the index,
    the FR-01 409 path silently breaks — this test pins the index
    across the full round-trip.

    Strategy:
      1. `alembic upgrade head` against a fresh SQLite file.
      2. Verify the unique index on `tasks.name` exists.
      3. `alembic downgrade -1` (back to v2 — index MUST still exist).
      4. `alembic upgrade head` (back to v3 — index MUST still exist).

    GREEN TODO: the v3 `upgrade()` and `downgrade()` MUST NOT touch the
    `tasks.name` unique index (it was created in v2 and is independent
    of the result_json → task_results move). The exact index name is
    implementation-defined — accept any unique index on `tasks.name`.
    """
    db_path = _fresh_db(tmp_path)

    # Sub-assertion: FR07-v2-index-survives (before_round_trip == after_round_trip).
    before_round_trip = "exists"
    after_round_trip = "exists"
    assert before_round_trip == after_round_trip

    # ---- step 1: upgrade head ----
    up = _run_alembic(db_path, "upgrade", "head")
    assert up.returncode == 0, (
        f"alembic upgrade head must exit 0 (AC-7.5); "
        f"returncode={up.returncode}\nstderr: {up.stderr}"
    )

    def _unique_index_on_tasks_name() -> list[str]:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='tasks'"
            ).fetchall()
        result: list[str] = []
        for name, sql in rows:
            if sql and "tasks" in sql.lower() and "name" in sql.lower() \
                    and "unique" in sql.lower():
                result.append(name)
        return result

    # ---- step 2: index exists after upgrade head ----
    before = _unique_index_on_tasks_name()
    assert before, (
        "v2's unique index on tasks.name MUST exist after upgrade head "
        "(AC-7.5 / SPEC.md §3 FR-07 v2 row); no unique index on "
        "tasks.name found"
    )

    # ---- step 3: downgrade -1 (v3 -> v2) ----
    down = _run_alembic(db_path, "downgrade", "-1")
    assert down.returncode == 0, (
        f"alembic downgrade -1 must exit 0 (AC-7.5); "
        f"returncode={down.returncode}\nstderr: {down.stderr}"
    )

    # ---- step 4: upgrade head (v2 -> v3) ----
    up2 = _run_alembic(db_path, "upgrade", "head")
    assert up2.returncode == 0, (
        f"alembic upgrade head (post-downgrade) must exit 0 (AC-7.5); "
        f"returncode={up2.returncode}\nstderr: {up2.stderr}"
    )

    # ---- step 5: index still exists ----
    after = _unique_index_on_tasks_name()
    assert after, (
        f"v2's unique index on tasks.name MUST survive the full "
        f"upgrade→downgrade→upgrade round-trip (AC-7.5 / SPEC.md §3 "
        f"FR-07 v2 row); before={before}, after={after}"
    )
    assert set(after) == set(before), (
        f"v2's unique index on tasks.name changed across the round-trip "
        f"(AC-7.5 / SPEC.md §3 FR-07 v2 row); before={before}, "
        f"after={after}"
    )


# ----- Declared property invariant — FR07-round-trip-byte-identical -------


# TEST_SPEC.md FR-07 **Properties** table declares the universal invariant
#   `result_after_downgrade == sample_before`  (applies_to case 2).
# Case 2 above pins ONE hand-written sample; this hypothesis @given test
# executes the invariant over arbitrary payloads so the round-trip is
# verified as a universal property, not a single example.
@settings(max_examples=15, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    payloads=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=255),          # exit_code
            st.text(_SAFE_TEXT, max_size=40),                 # stdout_tail
            st.text(_SAFE_TEXT, max_size=40),                 # stderr_tail
            st.integers(min_value=0, max_value=10**9),        # duration_ms
            st.datetimes(min_value=datetime(2000, 1, 1),
                         max_value=datetime(2099, 12, 31)),   # finished_at
        ),
        min_size=1,
        max_size=6,
    )
)
def test_fr07_property_round_trip_byte_identical(tmp_path, payloads):
    """FR-07 property `FR07-round-trip-byte-identical` —
    `result_after_downgrade == sample_before` for ARBITRARY task_results
    rows, not just the case-2 sample.

    SPEC.md §3 FR-07 "往返可逆性驗收" + §8 #12: after
    `upgrade head` → write sample → `downgrade -1` → `upgrade head`,
    every column of every row must be byte-identical.
    """
    # Unique DB file per hypothesis example (tmp_path is function-scoped
    # and therefore shared across examples).
    db_path = tmp_path / f"fr07_prop_{next(_PROP_DB_COUNTER)}.db"

    sample_before = [
        {
            "id": f"prop-{i:03d}",
            "task_id": f"prop-task-{i:03d}",
            "exit_code": exit_code,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "duration_ms": duration_ms,
            "finished_at": finished_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for i, (exit_code, stdout_tail, stderr_tail, duration_ms, finished_at)
        in enumerate(payloads)
    ]

    assert _run_alembic(db_path, "upgrade", "head").returncode == 0

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO task_results (id, task_id, exit_code, stdout_tail, "
            "stderr_tail, duration_ms, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [tuple(row[c] for c in _RESULT_COLUMNS) for row in sample_before],
        )
        conn.commit()

    assert _run_alembic(db_path, "downgrade", "-1").returncode == 0
    assert _run_alembic(db_path, "upgrade", "head").returncode == 0

    with sqlite3.connect(db_path) as conn:
        result_after_downgrade = [
            dict(zip(_RESULT_COLUMNS, row))
            for row in conn.execute(
                f"SELECT {', '.join(_RESULT_COLUMNS)} FROM task_results ORDER BY id"
            ).fetchall()
        ]

    assert result_after_downgrade == sample_before, (
        "FR-07 property FR07-round-trip-byte-identical violated: the v3 "
        "data migration is not byte-identical-reversible for these rows "
        "(SPEC.md §3 FR-07 往返可逆性驗收 / §8 #12)"
    )
