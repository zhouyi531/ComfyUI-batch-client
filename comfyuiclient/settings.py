"""Runtime settings shared by the web server and the CLI.

Several ComfyUI instances can run on one machine, so the address is a setting
the UI can change at runtime instead of a start-up constant. Resolution order
is: explicit per-request address > saved setting > COMFY_BASE_URL env var.

The settings file is small and read on demand, so a change made through the web
UI takes effect immediately for every caller, including a CLI run started later.
"""

import json
import os
from typing import List

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

MAX_RECENT_SERVERS = 8


def normalize_server(addr: str) -> str:
    """Strip scheme, trailing slash and whitespace from a host:port address."""
    s = (addr or '').strip()
    for prefix in ("http://", "https://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s.rstrip('/').strip()


DEFAULT_COMFY_SERVER = normalize_server(os.environ.get("COMFY_BASE_URL", "127.0.0.1:8188"))


def read_settings() -> dict:
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"Warning: could not read {SETTINGS_FILE}: {e}")
        return {}


def _write_settings(settings: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def get_comfy_server() -> str:
    """The ComfyUI address to use when a request doesn't name one."""
    return normalize_server(read_settings().get('comfy_server') or '') or DEFAULT_COMFY_SERVER


def has_saved_server() -> bool:
    return bool(normalize_server(read_settings().get('comfy_server') or ''))


def get_recent_servers() -> List[str]:
    """Recently used addresses, most recent first."""
    recent = [normalize_server(s) for s in read_settings().get('recent_servers') or []]
    seen, out = set(), []
    for s in recent:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def set_comfy_server(addr: str) -> str:
    """Persist the default ComfyUI address; returns the normalized address."""
    server = normalize_server(addr)
    if not server:
        raise ValueError("Server address is empty")

    settings = read_settings()
    recent = [s for s in get_recent_servers() if s != server]
    settings['comfy_server'] = server
    settings['recent_servers'] = ([server] + recent)[:MAX_RECENT_SERVERS]
    _write_settings(settings)
    return server
