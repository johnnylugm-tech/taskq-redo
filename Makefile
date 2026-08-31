# taskq-api verification (SPEC §3 NFR-12 — execute_verification_target)
#
# make verify-system is the target the harness scores as execute_verification_target
# (Gate 2/3/4, score = exit 0). It runs the full pytest suite plus a smoke
# boot of the FastAPI app through the same `from taskq_api.app import app`
# import the test client uses.
#
# Override the Python interpreter:
#   make PYTHON=.venv/bin/python verify-system

PYTHON ?= python3

.PHONY: verify-system test lint

verify-system: test
	@echo "--- verify-system: import smoke ---"
	@$(PYTHON) -c "from taskq_api.app import app; assert app.title == 'taskq-api'; print('app import: OK')"
	@echo "--- verify-system: PASS ---"

test:
	@$(PYTHON) -m pytest 03-development/tests -q

lint:
	@$(PYTHON) -m ruff check 03-development/src --extend-ignore RUF001,RUF002,RUF003
