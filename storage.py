"""Paths for mutable server state, independent of the application directory."""
from functools import lru_cache
import os
from pathlib import Path
import secrets
import tempfile

APP_DIR = Path(__file__).resolve().parent


def external_data_enabled() -> bool:
    return bool(os.environ.get("SIMPLE_TODO_DATA_DIR", "").strip())


def data_directory() -> Path:
    value = os.environ.get("SIMPLE_TODO_DATA_DIR", "").strip()
    directory = Path(value).expanduser().resolve() if value else Path.cwd().resolve()
    if value and directory.is_relative_to(APP_DIR):
        raise ValueError("SIMPLE_TODO_DATA_DIR must be outside the application directory")
    return directory


def data_path(filename: str) -> Path:
    directory = data_directory()
    path = (directory / Path(filename).expanduser()).resolve()
    if external_data_enabled() and not path.is_relative_to(directory):
        raise ValueError(f"State file must be inside SIMPLE_TODO_DATA_DIR: {filename}")
    return path


def _initialize_file(path: Path, contents: str) -> None:
    """Publish a complete private file without replacing an existing one."""
    if path.exists():
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Linking a fully written temporary file also handles simultaneous workers.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".simple-todo-", delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)


@lru_cache(maxsize=16)
def _persistent_secret(path: Path) -> str:
    _initialize_file(path, secrets.token_hex(32) + "\n")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Session secret file is empty: {path}")
    return value


def session_secret() -> str:
    override = os.environ.get("SIMPLE_TODO_SESSION_SECRET")
    return override or _persistent_secret(data_path("session-secret"))


def initialize_storage() -> None:
    if not external_data_enabled():
        return
    directory = data_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    session_secret()
    # This bundled file is a seed, not a live settings file in external mode.
    seed = APP_DIR / "default_smart_folders.json"
    if seed.is_file():
        _initialize_file(data_path("default_smart_folders.json"), seed.read_text(encoding="utf-8"))
