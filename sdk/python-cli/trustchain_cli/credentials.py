"""
trustchain_cli.credentials — local credential cache for `trustchain login`.

Deliberately minimal: one JSON file, one token, mode 0600. No encryption,
no keyring integration — this is a local dev/ops convenience (matching
`gh auth login`'s basic mode), not a secrets manager. Anyone who can read
your home directory can already read your shell history/env, which is
just as sensitive; this doesn't try to defend against a threat model the
rest of a developer's machine doesn't either.
"""

import json
import os
import stat
from pathlib import Path
from typing import Optional


def _credentials_path() -> Path:
    config_dir = Path(os.environ.get("TRUSTCHAIN_CONFIG_DIR", str(Path.home() / ".trustchain")))
    return config_dir / "credentials.json"


def save_token(token: str, email: str, base_url: str) -> None:
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token, "email": email, "base_url": base_url}, indent=2))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner read/write only


def load() -> Optional[dict]:
    path = _credentials_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_token() -> Optional[str]:
    data = load()
    return data.get("token") if data else None


def clear() -> None:
    path = _credentials_path()
    if path.exists():
        path.unlink()
