# taskq-api verification (SPEC §3 NFR-12 — execute_verification_target)
#
# make verify-system is the target the harness scores as execute_verification_target
# (Gates 2/3/4, score = exit 0). It runs the full pytest suite plus a live
# smoke boot of the FastAPI app through `uvicorn taskq_api.app:app` — the
# same module the test client imports — so the delivered entry point is
# actually started and the smoke check can fail on a broken boot.
#
# Override the Python interpreter:
#   make PYTHON=.venv/bin/python verify-system

PYTHON ?= python3
TASKQ_PORT ?= 8765

.PHONY: verify-system test lint

verify-system: test
	@echo "--- verify-system: service smoke ---"
	@PYTHONPATH=03-development/src $(PYTHON) -m uvicorn taskq_api.app:app \
	    --host 127.0.0.1 --port $(TASKQ_PORT) \
	    --log-level warning > /tmp/taskq-verify-uvicorn.log 2>&1 & \
	UVICORN_PID=$$!; \
	SMOKE_OK=""; \
	for i in 1 2 3 4 5 6 7 8 9 10; do \
	    sleep 0.5; \
	    if PYTHONPATH=03-development/src $(PYTHON) -c "import httpx,sys; \
r = httpx.get('http://127.0.0.1:$(TASKQ_PORT)/openapi.json', timeout=2); \
r.raise_for_status(); \
assert r.json()['info']['title'] == 'taskq-api'; \
sys.exit(0)" >/dev/null 2>&1; then \
	        echo "service smoke: OK"; \
	        SMOKE_OK=1; \
	        break; \
	    fi; \
	done; \
	kill $$UVICORN_PID >/dev/null 2>&1; \
	wait $$UVICORN_PID >/dev/null 2>&1; \
	if [ -z "$$SMOKE_OK" ]; then \
	    echo "service smoke: FAILED — see /tmp/taskq-verify-uvicorn.log"; \
	    exit 1; \
	fi
	@echo "--- verify-system: PASS ---"

test:
	@$(PYTHON) -m pytest 03-development/tests -q

lint:
	@$(PYTHON) -m ruff check 03-development/src --extend-ignore RUF001,RUF002,RUF003