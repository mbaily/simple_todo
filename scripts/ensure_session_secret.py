#!/usr/bin/env python3
"""Ensure SIMPLE_TODO_SESSION_SECRET is present in .env.

If the key already exists, does nothing.
If it's missing (or .env doesn't exist), generates a random 64-char hex
secret and appends it.

Usage:
    python scripts/ensure_session_secret.py          # uses .env in data dir/cwd
    python scripts/ensure_session_secret.py /path/to/.env
"""

import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import data_path

KEY = "SIMPLE_TODO_SESSION_SECRET"


def main():
    env_path = data_path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    env_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Check if key already present
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.strip().startswith(f"{KEY}="):
                    print(f"{KEY} already set in {env_path}")
                    return

    # Generate and append
    secret = secrets.token_hex(32)
    descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a") as f:
        f.write(f"{KEY}={secret}\n")
    print(f"Generated {KEY} in {env_path}")


if __name__ == "__main__":
    main()
