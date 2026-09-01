"""`python -m taskq_api` entry point (SPEC.md §3 FR-03).

[FR-03] Implements the operator-facing CLI declared by FR-03:

  python -m taskq_api key create --scope <scope>

The `key create` subcommand:
  * generates a fresh, cryptographically random plaintext API key,
  * prints the plaintext to stdout EXACTLY ONCE (NFR-04 / AC-3.6),
  * persists only the SHA-256 hash in the api_keys store via
    `ApiKeyRepo.add` (the repo never sees the plaintext again after
    this call returns).

The CLI deliberately avoids a default scope — operators must pick
`read` / `write` / `admin` explicitly, matching the
`taskq_api.api.deps.SCOPE_RANK` ordering so the created key can
be authorised against the same hierarchy.

The argparse subparsers are declared with `required=True`, which
guarantees `args.handler` is set; the implementation contract
forbids a defensive `if handler is None` fallback for that case.
Citations:
  SPEC.md §3 FR-03 ("`python -m taskq_api key create --scope <scope>`")
  NFR-04 (plaintext printed exactly once, hash persisted) [FR-03]
  TEST_SPEC.md FR-03 case 6 (AC-3.6)
"""
import argparse
import secrets

from taskq_api.repository.key_repo import ApiKeyRepo

# pragma: no error-handling

# 24 random bytes -> ~32 url-safe base64 chars; comfortably above the
# 16-char floor pinned by AC-3.6. Centralised so the entropy choice is
# obvious at the call site.
_PLAINTEXT_RANDOM_BYTES = 24

# Exit code is the conventional Unix "success" so shell callers can
# branch on it without parsing stderr.
_EXIT_OK = 0


def _cmd_key_create(args: argparse.Namespace) -> int:
    """Generate a new API key for the requested scope, print plaintext.

    Returns `_EXIT_OK` on success. The plaintext is written to stdout
    via a bare `print` so the operator can capture it via shell
    redirection; logging handlers are intentionally not configured
    here (NFR-04: the plaintext must not appear in any log channel).
    """
    plaintext = secrets.token_urlsafe(_PLAINTEXT_RANDOM_BYTES)
    key_repo = ApiKeyRepo()
    key_repo.add(plaintext, args.scope)
    # Plaintext appears on stdout EXACTLY ONCE and never on stderr.
    # No debug/log lines are emitted in this command path.
    print(plaintext)
    return _EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree for `python -m taskq_api` (FR-03)."""
    parser = argparse.ArgumentParser(
        prog="python -m taskq_api",
        description="taskq_api operator CLI (SPEC.md §3 FR-03).",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        help="subcommand to execute",
    )

    # `python -m taskq_api key <action> [options]`
    key_parser = subparsers.add_parser("key", help="manage API keys")
    key_subparsers = key_parser.add_subparsers(
        dest="action",
        required=True,
        help="key sub-action",
    )

    # `python -m taskq_api key create --scope <scope>`
    create = key_subparsers.add_parser(
        "create", help="create a new API key for the given scope"
    )
    create.add_argument(
        "--scope",
        required=True,
        choices=("read", "write", "admin"),
        help="scope to grant the new key (read|write|admin)",
    )
    create.set_defaults(handler=_cmd_key_create)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse argv, dispatch to the chosen subcommand, return exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    # `add_subparsers(required=True)` guarantees `handler` is set;
    # no defensive fallback is required (per the implementation
    # contract for this entry point).
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
