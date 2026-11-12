#!/usr/bin/env python3
"""
commit_messages.py - Utility for storing and retrieving AI-generated commit messages.

Writes commit messages and timestamps to a SQLite database (commit_messages.db),
allowing automated generation by LLMs and manual review/application at the command line.

Features:
  - Automatically identifies the repository root (`git rev-parse --show-toplevel`)
    so commit_messages.db is placed at the root of the active git project.
  - Optional user-wide database via `--global` (~/.commit_messages.db).
  - Idempotent `--commit`:
      * If the message was already committed, it recognizes it and avoids duplicate commits.
      * If you run `./commit_messages.py --commit` and then `./commit_messages.py --commit --push`,
        it automatically detects the commit is already done and proceeds to push cleanly!
  - Metadata tracking in SQLite: records commit_hash, committed_at, and pushed_at.
  - Safety checks on first commit:
      * Warns and refuses to commit if the message is > 20 minutes old.
      * Warns and refuses to commit if the message already appears in the last 20 git commits.
      * Optional `--force` to bypass these checks.

Examples:
  # Save a commit message (from LLM or user):
  commit_messages.py save "Fix wiki list hierarchy and tighten spacing"
  commit_messages.py -m "Fix wiki list hierarchy and tighten spacing"
  echo "My commit message" | commit_messages.py save -

  # Commit using the last message (with safety checks):
  commit_messages.py --commit
  commit_messages.py --commit --push
  commit_messages.py --push

  # Subcommand syntax also supported:
  commit_messages.py commit
  commit_messages.py commit --push

  # Inspect saved messages:
  commit_messages.py list
  commit_messages.py show
"""

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_NAME = "commit_messages.db"
GLOBAL_DB_NAME = ".commit_messages.db"
MAX_COMMIT_AGE_MINUTES = 20
MAX_RECENT_GIT_COMMITS = 20


def get_git_repo_root() -> Path | None:
    """Return root directory of the current git repository, if inside one."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return Path(res.stdout.strip()).resolve()
    except Exception:
        pass
    return None


def get_default_db_path(use_global: bool = False) -> Path:
    """
    Return default database path:
    1. If use_global: ~/.commit_messages.db
    2. If COMMIT_MESSAGES_DB env var is set: that path
    3. If inside a git repository: <repo_root>/commit_messages.db
    4. Fallback: <current_working_directory>/commit_messages.db
    """
    if use_global:
        return Path.home() / GLOBAL_DB_NAME

    env_path = os.environ.get("COMMIT_MESSAGES_DB")
    if env_path:
        return Path(env_path).resolve()

    repo_root = get_git_repo_root()
    if repo_root:
        return repo_root / DEFAULT_DB_NAME

    return Path.cwd() / DEFAULT_DB_NAME


def get_git_info() -> tuple[str | None, str | None]:
    """Attempt to detect current git branch and repository directory."""
    branch = None
    repo = None
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            branch = res.stdout.strip() or None
    except Exception:
        pass

    repo_root = get_git_repo_root()
    if repo_root:
        repo = str(repo_root)

    return branch, repo


def get_db_connection(db_path: Path) -> sqlite3.Connection:
    """Open SQLite connection and ensure schema exists with auto-migration."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS commit_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                branch TEXT,
                repo TEXT,
                commit_hash TEXT,
                committed_at TEXT,
                pushed_at TEXT
            )
            """
        )
        # Handle migrations for databases created before new columns
        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(commit_messages)").fetchall()
        }
        for col in ["commit_hash", "committed_at", "pushed_at"]:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE commit_messages ADD COLUMN {col} TEXT")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_commit_messages_ts ON commit_messages(timestamp DESC)"
        )
    return conn


def save_message(db_path: Path, message: str) -> int:
    """Save a commit message and current timestamp into the database."""
    message = message.strip()
    if not message:
        print("Error: commit message cannot be empty.", file=sys.stderr)
        sys.exit(1)

    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    branch, repo = get_git_info()

    conn = get_db_connection(db_path)
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO commit_messages (message, timestamp, branch, repo)
            VALUES (?, ?, ?, ?)
            """,
            (message, now_iso, branch, repo),
        )
        row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_last_message(db_path: Path) -> sqlite3.Row | None:
    """Retrieve the most recent commit message record."""
    conn = get_db_connection(db_path)
    cursor = conn.execute(
        """
        SELECT id, message, timestamp, branch, repo, commit_hash, committed_at, pushed_at
        FROM commit_messages ORDER BY id DESC LIMIT 1
        """
    )
    row = cursor.fetchone()
    conn.close()
    return row


def get_message_by_id(db_path: Path, message_id: int) -> sqlite3.Row | None:
    """Retrieve a commit message record by its primary key ID."""
    conn = get_db_connection(db_path)
    cursor = conn.execute(
        """
        SELECT id, message, timestamp, branch, repo, commit_hash, committed_at, pushed_at
        FROM commit_messages WHERE id = ?
        """,
        (message_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row


def list_messages(db_path: Path, limit: int = 10) -> list[sqlite3.Row]:
    """Retrieve the most recent N commit messages."""
    conn = get_db_connection(db_path)
    cursor = conn.execute(
        """
        SELECT id, message, timestamp, branch, repo, commit_hash, committed_at, pushed_at
        FROM commit_messages ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def update_message_metadata(
    db_path: Path,
    message_id: int,
    commit_hash: str | None = None,
    committed_at: str | None = None,
    pushed_at: str | None = None,
) -> None:
    """Update commit_hash, committed_at, or pushed_at metadata for a message record."""
    updates = []
    params = []
    if commit_hash is not None:
        updates.append("commit_hash = ?")
        params.append(commit_hash)
    if committed_at is not None:
        updates.append("committed_at = ?")
        params.append(committed_at)
    if pushed_at is not None:
        updates.append("pushed_at = ?")
        params.append(pushed_at)

    if not updates:
        return

    params.append(message_id)
    sql = f"UPDATE commit_messages SET {', '.join(updates)} WHERE id = ?"
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(sql, params)
    conn.close()


def read_message_arg(msg_arg: str | None) -> str:
    """Read message from argument string or stdin if '-' or empty piped input."""
    if msg_arg and msg_arg != "-":
        return msg_arg
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    if msg_arg == "-":
        return sys.stdin.read().strip()
    return ""


def is_commit_pushed(commit_hash: str) -> bool:
    """Check if the given commit hash is already present on any remote tracking branch."""
    if not commit_hash:
        return False
    try:
        res = subprocess.run(
            ["git", "branch", "-r", "--contains", commit_hash],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0 and bool(res.stdout.strip())
    except Exception:
        return False


def get_git_head_commit() -> tuple[str | None, str | None]:
    """Return (head_hash, head_subject) of the current repository."""
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%H%x00%B"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            parts = res.stdout.split("\x00", 1)
            h = parts[0].strip()
            msg = parts[1].strip() if len(parts) > 1 else ""
            return h or None, msg or None
    except Exception:
        pass
    return None, None


def get_unstaged_tracked_files() -> list[tuple[str, str]]:
    """
    Return list of (status, file_path) for tracked files modified or deleted
    in the working tree but not staged for commit.
    """
    try:
        res = subprocess.run(
            ["git", "diff", "--name-status"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            items = []
            for line in res.stdout.strip().splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    items.append((parts[0].strip(), parts[1].strip()))
                elif parts:
                    items.append(("M", parts[0].strip()))
            return items
    except Exception:
        pass
    return []


def is_message_already_committed(row: sqlite3.Row) -> tuple[bool, str | None]:
    """
    Check if the message in `row` has already been committed in this git repo.
    Returns (already_committed, commit_hash).
    """
    commit_hash = row["commit_hash"]
    if commit_hash:
        # Verify commit still exists in repository
        check = subprocess.run(
            ["git", "cat-file", "-e", f"{commit_hash}^{{commit}}"],
            capture_output=True,
            check=False,
        )
        if check.returncode == 0:
            return True, commit_hash

    # Also check if HEAD commit message matches this row's message
    head_hash, head_msg = get_git_head_commit()
    if head_hash and head_msg:
        cand_msg = row["message"].strip()
        cand_subject = cand_msg.splitlines()[0].strip()
        head_subject = head_msg.splitlines()[0].strip()
        if cand_msg == head_msg or (cand_subject and cand_subject == head_subject):
            return True, head_hash

    return False, None


def validate_commit_allowed(
    row: sqlite3.Row,
    max_age_minutes: int = MAX_COMMIT_AGE_MINUTES,
    max_recent_commits: int = MAX_RECENT_GIT_COMMITS,
    force: bool = False,
) -> None:
    """
    Validate that the message is fresh (< max_age_minutes) and not already
    present in the last max_recent_commits of the git repository.
    Raises SystemExit(1) if validation fails and force is False.
    """
    if force:
        return

    # 1. Age check
    ts_str = row["timestamp"]
    try:
        saved_time = datetime.fromisoformat(ts_str)
        now = datetime.now(timezone.utc).astimezone()
        age_seconds = (now - saved_time).total_seconds()
        if age_seconds > max_age_minutes * 60:
            age_minutes = age_seconds / 60
            print(
                f"Warning: The last commit message is {age_minutes:.1f} minutes old "
                f"(exceeds the {max_age_minutes}-minute limit).\n"
                f"  Recorded timestamp: {ts_str}\n"
                f"  Message: {row['message']}\n"
                "Refusing to commit. Generate a fresh message or pass --force to bypass.",
                file=sys.stderr,
            )
            sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass

    # 2. Duplicate check against last 20 git commits
    candidate_msg = row["message"].strip()
    candidate_subject = candidate_msg.splitlines()[0].strip() if candidate_msg else ""

    try:
        res = subprocess.run(
            ["git", "log", f"-n{max_recent_commits}", "--format=%B%x00"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            recent_commits = [c.strip() for c in res.stdout.split("\x00") if c.strip()]
            for commit_body in recent_commits:
                commit_subject = commit_body.splitlines()[0].strip() if commit_body else ""
                if candidate_msg == commit_body or (
                    candidate_subject and candidate_subject == commit_subject
                ):
                    print(
                        f"Warning: The commit message already appears in the last {max_recent_commits} git commits:\n"
                        f"  Existing commit:  '{commit_subject}'\n"
                        f"  Candidate message: '{candidate_subject}'\n"
                        "Refusing to commit duplicate message. Generate a fresh message or pass --force to bypass.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass


def execute_push(db_path: Path, row: sqlite3.Row, commit_hash: str | None = None) -> None:
    """Execute git push and record pushed_at timestamp in SQLite."""
    hash_display = f"commit {commit_hash[:8]}" if commit_hash else "commits"
    print(f"\nPushing {hash_display} to remote: git push")
    push_res = subprocess.run(["git", "push"], check=True)
    if push_res.returncode == 0:
        now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        update_message_metadata(db_path, row["id"], pushed_at=now_iso)
        print(f"Push succeeded. Recorded pushed_at timestamp.")


def execute_commit(
    db_path: Path,
    add: bool = False,
    push: bool = False,
    force: bool = False,
) -> None:
    """
    Execute git commit using the latest saved message with idempotency:
    - If already committed: detects this, skips redundant commit, and proceeds to push if requested.
    - If not yet committed: validates age & uniqueness, commits, records metadata, and pushes if requested.
    """
    row = get_last_message(db_path)
    if not row:
        print("Error: No commit message found in database.", file=sys.stderr)
        sys.exit(1)

    already_committed, existing_hash = is_message_already_committed(row)

    if already_committed:
        # Update metadata if missing
        if not row["commit_hash"] and existing_hash:
            now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            update_message_metadata(db_path, row["id"], commit_hash=existing_hash, committed_at=now_iso)

        hash_short = existing_hash[:8] if existing_hash else "HEAD"
        print(f"Message #{row['id']} was already committed ({hash_short}).")

        if push:
            # Check if this commit was already pushed
            if is_commit_pushed(existing_hash) or row["pushed_at"]:
                print(f"Notice: Commit {hash_short} is already pushed to remote. Nothing to do.")
            else:
                print(f"Commit {hash_short} is not yet on remote. Proceeding to push...")
                execute_push(db_path, row, commit_hash=existing_hash)
        else:
            print("No further commit required. (To push, run with --push)")
        return

    # Not yet committed: perform standard safety validation
    validate_commit_allowed(row, force=force)

    msg = row["message"]

    if add:
        print("Staging all changes: git add -A")
        subprocess.run(["git", "add", "-A"], check=True)
    elif not force:
        unstaged_tracked = get_unstaged_tracked_files()
        if unstaged_tracked:
            print(
                f"Warning: {len(unstaged_tracked)} tracked file(s) have been modified but not added to this commit:",
                file=sys.stderr,
            )
            for status, fpath in unstaged_tracked:
                print(f"  [{status}] {fpath}", file=sys.stderr)
            print(
                "\nTo include them, stage them with 'git add <files>' or pass --add / -a to stage all changes.\n"
                "To proceed anyway and commit ONLY currently staged changes, pass --force / -f.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Check if there are staged changes
    staged_check = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if staged_check.returncode == 0:
        unstaged_check = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if unstaged_check.stdout.strip():
            print(
                "Warning: There are unstaged changes in the repository, but nothing is staged.\n"
                "Stage your changes first with 'git add <files>' or pass --add / -a to stage all changes.",
                file=sys.stderr,
            )
        else:
            print("Nothing to commit (working tree clean).", file=sys.stderr)
        sys.exit(1)

    print(f"Committing with message from #{row['id']} ({row['timestamp']}):\n{msg}\n")
    subprocess.run(["git", "commit", "-m", msg], check=True)

    # Retrieve new commit hash and record in SQLite
    new_head_hash, _ = get_git_head_commit()
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    update_message_metadata(db_path, row["id"], commit_hash=new_head_hash, committed_at=now_iso)

    if push:
        execute_push(db_path, row, commit_hash=new_head_hash)


def execute_standalone_push(db_path: Path) -> None:
    """Handle standalone `commit_messages.py --push` invocation."""
    row = get_last_message(db_path)
    if not row:
        print("Running: git push")
        subprocess.run(["git", "push"], check=True)
        return

    already_committed, existing_hash = is_message_already_committed(row)
    if not already_committed:
        print(
            "Notice: The latest message in the database has not been committed yet.\n"
            "Run 'commit_messages.py --commit --push' to commit and push.",
            file=sys.stderr,
        )
        sys.exit(1)

    if is_commit_pushed(existing_hash) or row["pushed_at"]:
        hash_short = existing_hash[:8] if existing_hash else "HEAD"
        print(f"Notice: Commit {hash_short} is already pushed to remote. Nothing to do.")
        return

    execute_push(db_path, row, commit_hash=existing_hash)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Utility for AI LLM commit messages stored in SQLite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite database (default: <repo_root>/commit_messages.db)",
    )
    parser.add_argument(
        "-g",
        "--global",
        dest="use_global",
        action="store_true",
        help="Use user-wide database (~/.commit_messages.db) instead of repository-local",
    )
    parser.add_argument(
        "-m",
        "--message",
        type=str,
        default=None,
        help="Quick save: record this message and exit.",
    )
    parser.add_argument(
        "-c",
        "--commit",
        action="store_true",
        help="Execute git commit using latest saved message (idempotent, with age & duplicate checks)",
    )
    parser.add_argument(
        "-p",
        "--push",
        action="store_true",
        help="Push to git remote (idempotent; pushes if committed and not yet on remote)",
    )
    parser.add_argument(
        "-a",
        "--add",
        action="store_true",
        help="Stage all changes (git add -A) before committing",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Bypass age (>20 min) and duplicate message checks when committing",
    )

    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # save subcommand
    save_parser = subparsers.add_parser("save", help="Save a commit message to database")
    save_parser.add_argument(
        "text",
        nargs="?",
        default=None,
        help="The commit message text (or '-' to read from standard input)",
    )

    # last subcommand
    subparsers.add_parser("last", help="Print only the last commit message (for git commit -m)")

    # show subcommand
    show_parser = subparsers.add_parser("show", help="Show full details of last or specific message")
    show_parser.add_argument("id", nargs="?", type=int, help="Optional message ID to inspect")

    # list subcommand
    list_parser = subparsers.add_parser("list", help="List recent commit messages")
    list_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="Number of records to display (default: 10)",
    )

    # commit subcommand
    commit_parser = subparsers.add_parser(
        "commit", help="Execute git commit using the latest saved message (idempotent)"
    )
    commit_parser.add_argument(
        "-a",
        "--add",
        action="store_true",
        help="Stage all changes (git add -A) before committing",
    )
    commit_parser.add_argument(
        "-p",
        "--push",
        action="store_true",
        help="Push to git remote after commit (or push if already committed)",
    )
    commit_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Bypass age (>20 min) and duplicate message checks",
    )

    args = parser.parse_args()
    db_path = args.db or get_default_db_path(args.use_global)

    # Handle quick flag: commit_messages.py -m "..."
    if args.message is not None:
        msg = read_message_arg(args.message)
        row_id = save_message(db_path, msg)
        print(f"Saved commit message [id={row_id}] to {db_path.name}")
        return

    # Handle top-level --commit flag
    if args.commit:
        execute_commit(db_path, add=args.add, push=args.push, force=args.force)
        return

    # Handle standalone top-level --push flag
    if args.push and args.command is None:
        execute_standalone_push(db_path)
        return

    # Handle no subcommand provided
    if args.command is None:
        if not sys.stdin.isatty():
            msg = sys.stdin.read().strip()
            if msg:
                row_id = save_message(db_path, msg)
                print(f"Saved commit message [id={row_id}] to {db_path.name}")
                return
        parser.print_help()
        return

    if args.command == "save":
        msg = read_message_arg(args.text)
        if not msg:
            print("Error: No message provided. Pass message as argument or via stdin.", file=sys.stderr)
            sys.exit(1)
        row_id = save_message(db_path, msg)
        print(f"Saved commit message [id={row_id}] to {db_path.name}")

    elif args.command == "last":
        row = get_last_message(db_path)
        if not row:
            print("Error: No commit messages found in database.", file=sys.stderr)
            sys.exit(1)
        print(row["message"])

    elif args.command == "show":
        row = get_message_by_id(db_path, args.id) if args.id else get_last_message(db_path)
        if not row:
            target = f"id={args.id}" if args.id else "latest"
            print(f"Error: No commit message found ({target}).", file=sys.stderr)
            sys.exit(1)
        print(f"ID:           {row['id']}")
        print(f"Timestamp:    {row['timestamp']}")
        if row["branch"]:
            print(f"Branch:       {row['branch']}")
        if row["repo"]:
            print(f"Repo:         {row['repo']}")
        if row["commit_hash"]:
            print(f"Commit Hash:  {row['commit_hash']}")
        if row["committed_at"]:
            print(f"Committed At: {row['committed_at']}")
        if row["pushed_at"]:
            print(f"Pushed At:    {row['pushed_at']}")
        print("-" * 50)
        print(row["message"])

    elif args.command == "list":
        rows = list_messages(db_path, limit=args.limit)
        if not rows:
            print(f"No commit messages recorded in {db_path.name}.")
            return
        print(f"Recent commit messages in {db_path.name} (limit={args.limit}):")
        print("-" * 75)
        for r in rows:
            status = "pending"
            if r["pushed_at"]:
                status = "pushed"
            elif r["commit_hash"]:
                status = "committed"
            first_line = r["message"].splitlines()[0] if r["message"] else ""
            if len(first_line) > 45:
                first_line = first_line[:42] + "..."
            branch_info = f" [{r['branch']}]" if r["branch"] else ""
            print(f"#{r['id']:<4} {r['timestamp'][:19]} [{status:<9}] {first_line}{branch_info}")

    elif args.command == "commit":
        execute_commit(
            db_path,
            add=args.add or parser.get_default("add"),
            push=args.push or parser.get_default("push"),
            force=args.force or parser.get_default("force"),
        )


if __name__ == "__main__":
    main()
