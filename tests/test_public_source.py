"""Check that common private runtime files stay out of future source additions."""
from pathlib import Path
import subprocess


def test_private_files_are_ignored_but_public_environment_example_is_not(tmp_path):
    # Use an isolated repository so this check also works from a source archive.
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_bytes((root / '.gitignore').read_bytes())
    private = ['user.db', 'user.db-wal', 'user.db-shm', 'user.db.backup',
               'user.sqlite3', '.env', '.env.production', '.env.test', 'settings.json',
               'session-secret', '.htpasswd', '.htpasswd.old', 'certs/server.key',
               'backups/snapshot.json', '.notes.swp', 'ai_progress_example.txt',
               'ai_task1.txt', 'latest_commit.txt']
    result = subprocess.run(['git', 'check-ignore', '--stdin'], cwd=tmp_path,
                            input='\n'.join(private + ['.env.example']) + '\n',
                            capture_output=True, text=True, check=True)
    assert set(result.stdout.splitlines()) == set(private)
