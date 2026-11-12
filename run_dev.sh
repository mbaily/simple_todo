#!/usr/bin/env bash
set -euo pipefail

# Run the FastAPI app in dev mode with auto-reload.
# Uses the same plain HTTP host/port as main.py (127.0.0.1:3102).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect python binary (python3 or python)
PYTHON_BIN="${PYTHON_BIN:-${PYTHON:-}}"
if [[ -z "$PYTHON_BIN" ]]; then
	if command -v python3 >/dev/null 2>&1; then
		PYTHON_BIN="python3"
	elif command -v python >/dev/null 2>&1; then
		PYTHON_BIN="python"
	else
		echo "[run_dev] ERROR: neither python3 nor python found in PATH" >&2
		exit 1
	fi
fi

# An explicit external root owns its configuration; do not read the source .env.
if [[ -n "${SIMPLE_TODO_DATA_DIR:-}" ]]; then
	export SIMPLE_TODO_DATA_DIR
	SIMPLE_TODO_DATA_DIR=$("$PYTHON_BIN" -c 'from pathlib import Path; import os; print(Path(os.environ["SIMPLE_TODO_DATA_DIR"]).expanduser().resolve())')
fi
ENV_FILE="${SIMPLE_TODO_DATA_DIR:-$SCRIPT_DIR}/.env"
cd "$SCRIPT_DIR"
if [[ -f "$ENV_FILE" ]]; then
	set -a
	# shellcheck disable=SC1091
	source "$ENV_FILE"
	set +a
fi

# Prefer the current interpreter (e.g., venv) and run uvicorn as a module.

HOST=${SIMPLE_TODO_HOST:-127.0.0.1}
PORT=${SIMPLE_TODO_PORT:-3102}

USE_BASIC_AUTH=${SIMPLE_TODO_BASIC_AUTH:-0}
HTPASSWD_PATH=$("$PYTHON_BIN" -c 'from auth import _htpasswd_path; print(_htpasswd_path())')

UVICORN_ARGS=(main:app --reload --host "$HOST" --port "$PORT" --proxy-headers)

if [[ "${USE_BASIC_AUTH,,}" =~ ^(1|true|yes|on)$ ]]; then
	if [[ ! -f "$HTPASSWD_PATH" ]]; then
		echo "[run_dev] ERROR: SIMPLE_TODO_BASIC_AUTH is enabled but $HTPASSWD_PATH not found" >&2
		exit 1
	fi
	# If your htpasswd uses $apr1$ or bcrypt, passlib is required.
	if ! "$PYTHON_BIN" -c "import passlib" >/dev/null 2>&1; then
		echo "[run_dev] ERROR: SIMPLE_TODO_BASIC_AUTH is enabled but passlib is not installed." >&2
		echo "[run_dev] Tip: pip install passlib" >&2
		echo "[run_dev] Or use {SHA} hashes in .htpasswd if you want no extra deps." >&2
		exit 1
	fi
	echo "[run_dev] HTTP Basic Auth enabled"
	echo "[run_dev] htpasswd: $HTPASSWD_PATH"
fi

exec "$PYTHON_BIN" -m uvicorn "${UVICORN_ARGS[@]}"
