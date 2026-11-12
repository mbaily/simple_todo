#!/usr/bin/env bash
set -euo pipefail

# Set these privately in the shell; no installation-specific defaults are stored here.
: "${SIMPLE_TODO_SSH_HOST:?Set SIMPLE_TODO_SSH_HOST to user@host}"
: "${SIMPLE_TODO_REMOTE_DB:?Set SIMPLE_TODO_REMOTE_DB to the remote database path}"
scp -- "${SIMPLE_TODO_SSH_HOST}:${SIMPLE_TODO_REMOTE_DB}" "${1:-./todo_app.db}"
