#!/usr/bin/env python3
"""Deploy Simple Todo, generate its systemd unit, and restart the service."""

import argparse
import ast
from dataclasses import dataclass
import grp
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import json
import tempfile


DEFAULT_TARGET_DIR = "/opt/simple_todo"
DEFAULT_DATA_DIR = "/var/lib/simple_todo"
SERVICE_USER = "www-data"
SERVICE_GROUP = "www-data"
SERVICE_FILE = "simple_todo.service"

# Excluded deployment state is also protected from rsync --delete.
EXCLUDES = (
    ".git", ".gitignore", ".venv", "__pycache__", ".pytest_cache", "tests",
    "test_*.py", "test_output.log", "*.db", "*.db-*", "*.db.*", "*.sqlite*",
    "session-secret", "settings.json", ".swp", "*.log", ".vscode",
    "deploy.py", "deploy.sh", "deploy.sh.old", "run_dev.sh", ".env", ".htpasswd",
    "certs/***", "backups/***",
)
INCLUDES = (
    "*.py", "requirements.txt", "default_smart_folders.json", SERVICE_FILE,
    "templates/***", "static/***", "scripts/***", "locales/***", "docs/***",
)
EXTERNAL_ENV_KEYS = {"SIMPLE_TODO_DB_PATH", "SIMPLE_TODO_DATA_DIR"}


@dataclass(frozen=True)
class Deployment:
    source: Path
    target: Path
    state: Path
    external_data: bool
    python_bin: str = sys.executable
    uv_bin: str | None = None


def resolve_uv(requested=None, *, no_uv=False):
    """Resolve the uv executable for package dependency installation.

    Precedence:
    1. If no_uv is True, return None
    2. Explicit path passed via requested (--uv)
    3. UV environment variable
    4. Auto-detected uv binary in PATH or common system/user locations
    5. None if not found (falls back to pip)
    """
    if no_uv:
        return None

    if requested:
        candidate = shutil.which(requested)
        if not candidate:
            expanded = Path(requested).expanduser()
            if expanded.is_file():
                candidate = str(expanded.resolve())
        if not candidate or not Path(candidate).is_file():
            raise ValueError(f"uv executable not found: {requested}")
        if not os.access(candidate, os.X_OK):
            raise ValueError(f"uv executable is not executable: {requested}")
        return candidate

    if os.environ.get("UV"):
        return resolve_uv(os.environ["UV"])

    candidates = []
    which_uv = shutil.which("uv")
    if which_uv:
        candidates.append(Path(which_uv))
    candidates.append(Path("/usr/local/bin/uv"))
    candidates.append(Path("/usr/bin/uv"))
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        candidates.append(Path(f"/home/{sudo_user}/.local/bin/uv"))
    candidates.append(Path.home() / ".local/bin/uv")

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    return None


def resolve_python(requested=None):
    """Resolve the Python executable to use for the virtual environment.

    Precedence:
    1. Explicit path / command passed via requested (--python)
    2. PYTHON environment variable
    3. Auto-detected Python 3.15 candidate (system or user locations)
    4. sys.executable fallback
    """
    if requested:
        candidate = shutil.which(requested)
        if not candidate:
            expanded = Path(requested).expanduser()
            if expanded.is_file():
                candidate = str(expanded.resolve())
        if not candidate or not Path(candidate).is_file():
            raise ValueError(f"Python interpreter not found: {requested}")
        if not os.access(candidate, os.X_OK):
            raise ValueError(f"Python interpreter is not executable: {requested}")
        return candidate

    if os.environ.get("PYTHON"):
        return resolve_python(os.environ["PYTHON"])

    candidates = []
    which_315 = shutil.which("python3.15")
    if which_315:
        candidates.append(Path(which_315))
    candidates.append(Path("/usr/local/bin/python3.15"))
    candidates.append(Path("/opt/python3.15/bin/python3.15"))
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        candidates.append(Path(f"/home/{sudo_user}/.local/bin/python3.15"))
    candidates.append(Path.home() / ".local/bin/python3.15")

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    return sys.executable


def python_version(python_bin):
    """Return (major, minor) tuple for a Python executable."""
    try:
        resolved = Path(python_bin).resolve()
        if resolved == Path(sys.executable).resolve():
            return sys.version_info[0], sys.version_info[1]
    except Exception:
        pass
    try:
        output = subprocess.run(
            [str(python_bin), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        parts = output.split(".")
        return int(parts[0]), int(parts[1])
    except Exception:
        return None


def venv_python_version(venv_dir):
    """Return (major, minor) tuple of an existing virtual environment if detectable, else None."""
    cfg_file = Path(venv_dir) / "pyvenv.cfg"
    if cfg_file.is_file():
        try:
            for line in cfg_file.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                if key.strip() in ("version", "version_info"):
                    match = re.search(r"(\d+)\.(\d+)", value.strip())
                    if match:
                        return int(match.group(1)), int(match.group(2))
        except OSError:
            pass
    return None


def should_recreate_venv(venv_dir, target_python):
    """Check if an existing virtual environment needs recreation."""
    python_bin = Path(venv_dir) / "bin/python"
    if python_bin.is_symlink() and not python_bin.exists():
        return True
    venv_ver = venv_python_version(venv_dir)
    target_ver = python_version(target_python)
    if venv_ver is not None and target_ver is not None and venv_ver != target_ver:
        return True
    return False


def deployment_paths(target=DEFAULT_TARGET_DIR, state=None, python=None, uv=None, no_uv=False, *, source=None):
    source = Path(source or Path(__file__).resolve().parent).expanduser().resolve()
    target = Path(target).expanduser().resolve()
    selected_state = state or os.environ.get("SIMPLE_TODO_DATA_DIR") or installed_data_directory(target) or DEFAULT_DATA_DIR
    state = Path(selected_state or target).expanduser().resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("TARGET_DIRECTORY and the source directory must not overlap")
    if selected_state and (state.is_relative_to(target) or state.is_relative_to(source)):
        raise ValueError("DATA_DIRECTORY must be outside both source and deployment directories")
    resolved_python = resolve_python(python) if python is not None else resolve_python()
    if no_uv:
        resolved_uv = None
    elif uv is not None:
        resolved_uv = resolve_uv(uv)
    else:
        resolved_uv = resolve_uv()
    return Deployment(source, target, state, bool(selected_state), resolved_python, resolved_uv)


def installed_data_directory(target):
    """Keep an existing installation's data root when the default changes."""
    unit = target / SERVICE_FILE
    if not unit.is_file():
        return None
    result = None
    for line in unit.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("Environment="):
            for assignment in shlex.split(line.strip().partition("=")[2]):
                key, _, value = assignment.partition("=")
                if key == "SIMPLE_TODO_DATA_DIR" and value:
                    result = value.replace("%%", "%")
    return result


def preserve_wiki_settings(deployment):
    """Move installed URL overrides into private settings before replacing code."""
    installed = deployment.target / "config.py"
    if not deployment.external_data or not installed.is_file():
        return
    keys = {"ENABLE_DOKUWIKI_LINKS", "DOKUWIKI_LINK_FSTRING", "DOKUWIKI_ITEM_LINK_FSTRING", "DOKUWIKI_BURST_CONFIRMATION"}

    def constants(path):
        values = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                for name in node.targets:
                    if isinstance(name, ast.Name) and name.id in keys:
                        values[name.id] = ast.literal_eval(node.value)
        return values

    previous = constants(installed)
    defaults = constants(deployment.source / "config.py")
    path = deployment.state / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(settings, dict):
        raise ValueError("Site settings must contain a JSON object")
    additions = {key: value for key, value in previous.items()
                 if key not in settings and key in defaults and value != defaults[key]}
    if not additions:
        return
    settings.update(additions)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=deployment.state, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(settings, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def sync_files(source, target):
    command = ["rsync", "-av", "--delete"]
    command.extend(f"--exclude={pattern}" for pattern in EXCLUDES)
    command.extend(f"--include={pattern}" for pattern in INCLUDES)
    command.extend(["--exclude=*", "--", f"{source}/", f"{target}/"])
    subprocess.run(command, check=True)


def environment_key(line):
    key, separator, _ = line.partition("=")
    key = key.strip()
    return key if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else None


def merge_environment(source, state, *, external_data=False):
    """Seed missing keys without replacing deployment settings or session secrets."""
    source_file = source / ".env"
    destination = state / ".env"
    if not source_file.is_file() and not destination.is_file():
        return
    existing = destination.read_text() if destination.is_file() else ""
    content = existing
    keys = {environment_key(line) for line in existing.splitlines()}
    if source_file.is_file():
        for line in source_file.read_text().splitlines():
            key = environment_key(line)
            if external_data and key in EXTERNAL_ENV_KEYS:
                continue
            if key is not None and key not in keys:
                if content and not content.endswith("\n"):
                    content += "\n"
                content += line + "\n"
                keys.add(key)
                print(f"  Added new key: {key}")
    if "SIMPLE_TODO_SESSION_SECRET" not in keys:
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"SIMPLE_TODO_SESSION_SECRET={secrets.token_hex(32)}\n"
        print("  Generated new SIMPLE_TODO_SESSION_SECRET")
    # Restrict access before writing, including when creating a new file.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)
        stream.truncate()


def systemd_quote(value):
    """Quote a systemd value, including literal percent specifiers."""
    value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{value}"'


def render_service(deployment):
    target, state = deployment.target, deployment.state
    data_environment = (
        f"Environment={systemd_quote(f'SIMPLE_TODO_DATA_DIR={state}')}\n"
        if deployment.external_data else ""
    )
    # ExecStart expands dollars separately from systemd's ordinary quoting.
    python = systemd_quote(str(target / ".venv/bin/python").replace("$", "$$"))
    return f"""[Unit]
Description=Simple Todo FastAPI Service
After=network.target

[Service]
Type=simple
User={SERVICE_USER}
Group={SERVICE_GROUP}
WorkingDirectory={str(target).replace('%', '%%')}
Environment={systemd_quote(f'PATH={target}/.venv/bin:/usr/local/bin:/usr/bin:/bin')}
{data_environment}Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=-{str(state / '.env').replace('%', '%%')}
UMask=0077

ExecStart={python} main.py

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def change_owner(path, uid, gid, *, recursive=False):
    os.chown(path, uid, gid, follow_symlinks=False)
    if recursive:
        for directory, directories, files in os.walk(path):
            for name in directories + files:
                os.chown(Path(directory) / name, uid, gid, follow_symlinks=False)


def deploy(deployment, *, restart=True, clear_venv=False):
    uid = pwd.getpwnam(SERVICE_USER).pw_uid
    gid = grp.getgrnam(SERVICE_GROUP).gr_gid
    target, state = deployment.target, deployment.state
    target_python = getattr(deployment, "python_bin", sys.executable)
    if deployment.external_data:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.chmod(0o700)
        change_owner(state, uid, gid)
    target.mkdir(parents=True, exist_ok=True)

    preserve_wiki_settings(deployment)
    if (state / "settings.json").is_file():
        change_owner(state / "settings.json", uid, gid)

    print(f"Deploying Simple Todo to: {target}")
    print("Syncing application files...")
    sync_files(deployment.source, target)
    print("Files synced successfully.")

    venv = target / ".venv"
    recreate = clear_venv
    if venv.is_dir() and not recreate and should_recreate_venv(venv, target_python):
        recreate = True
        venv_ver = venv_python_version(venv)
        target_ver = python_version(target_python)
        old_str = f"Python {venv_ver[0]}.{venv_ver[1]}" if venv_ver else "existing version"
        new_str = f"Python {target_ver[0]}.{target_ver[1]}" if target_ver else str(target_python)
        print(f"Virtual environment {old_str} does not match {new_str}. Recreating...")

    if recreate:
        print(f"Recreating virtual environment in {venv} with {target_python}...")
        subprocess.run([target_python, "-m", "venv", "--clear", str(venv)], check=True)
    elif not venv.is_dir():
        print(f"Creating virtual environment in {venv} with {target_python}...")
        subprocess.run([target_python, "-m", "venv", str(venv)], check=True)
    python = str(venv / "bin/python")
    uv_bin = getattr(deployment, "uv_bin", None)
    print("Installing dependencies...")
    if uv_bin:
        print(f"Using uv ({uv_bin})...")
        subprocess.run(
            [uv_bin, "pip", "install", "--link-mode=copy", "--python", python, "-r", "requirements.txt"],
            cwd=target,
            check=True,
        )
    else:
        print("Using pip...")
        subprocess.run([python, "-m", "pip", "install", "--upgrade", "pip"], cwd=target, check=True)
        subprocess.run([python, "-m", "pip", "install", "-r", "requirements.txt"], cwd=target, check=True)

    print("Merging deployment environment settings...")
    merge_environment(deployment.source, state, external_data=deployment.external_data)
    if (state / ".env").is_file():
        change_owner(state / ".env", uid, gid)
    (target / SERVICE_FILE).write_text(render_service(deployment))
    print(f"Setting permissions for {SERVICE_USER}:{SERVICE_GROUP}...")
    change_owner(target, uid, gid, recursive=True)

    if restart:
        print(f"Restarting {SERVICE_FILE}...")
        subprocess.run(["systemctl", "restart", SERVICE_FILE], check=True)

    installer_desc = f"uv ({uv_bin})" if uv_bin else "pip"
    print(f"""
Deployment Complete!
App location: {target}
State location: {state}
Virtual Env : {venv} ({target_python})
Installer   : {installer_desc}
User/Group  : {SERVICE_USER}:{SERVICE_GROUP}

For a first installation or a changed systemd unit:
  cp {target / SERVICE_FILE} /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now {SERVICE_FILE}
  systemctl restart {SERVICE_FILE}

After changing {state / '.env'} or settings.json, restart the service:
  sudo systemctl restart simple_todo
""")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Deploy Simple Todo and restart its systemd service.",
        epilog="DATA_DIRECTORY can also be set with SIMPLE_TODO_DATA_DIR.",
    )
    parser.add_argument("target_directory", nargs="?", default=DEFAULT_TARGET_DIR,
                        help=f"Application directory (default: {DEFAULT_TARGET_DIR})")
    parser.add_argument("data_directory", nargs="?",
                        help=f"Data directory (default: SIMPLE_TODO_DATA_DIR or {DEFAULT_DATA_DIR})")
    parser.add_argument("-p", "--python",
                        help="Python interpreter executable to use for the virtual environment "
                             "(default: auto-detect Python 3.15 or current Python)")
    parser.add_argument("--clear-venv", action="store_true",
                        help="Recreate the virtual environment even if it already exists")
    parser.add_argument("--uv",
                        help="Path to uv executable for installing dependencies "
                             "(default: auto-detect uv in system or user locations)")
    parser.add_argument("--no-uv", action="store_true",
                        help="Do not use uv; use pip to install dependencies")
    parser.add_argument("--no-restart", action="store_true",
                        help="Prepare files without restarting (for first installation or data migration)")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required to run deploy.py")
    try:
        python_bin = resolve_python(args.python)
    except (ValueError, FileNotFoundError) as error:
        parser.error(str(error))

    try:
        uv_bin = resolve_uv(args.uv, no_uv=args.no_uv)
    except (ValueError, FileNotFoundError) as error:
        parser.error(str(error))

    target_ver = python_version(python_bin)
    if target_ver and target_ver < (3, 11):
        parser.error(f"Target Python {target_ver[0]}.{target_ver[1]} is not supported. Python 3.11 or newer is required.")

    if os.geteuid() != 0:
        print("Please run as root (sudo)", file=sys.stderr)
        return 1
    try:
        deployment = deployment_paths(args.target_directory, args.data_directory, python_bin, uv_bin, args.no_uv)
        deploy_kwargs = {"restart": not args.no_restart}
        if args.clear_venv:
            deploy_kwargs["clear_venv"] = True
        deploy(deployment, **deploy_kwargs)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
