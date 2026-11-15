"""Small JSON tables: validated hot reload and atomic writes (stdlib only)."""
import json
import os
import tempfile
import threading
from pathlib import Path

_lock = threading.RLock()
_cache = {}

def _clone(value):
    """JSON has only trees of dict/list/scalars, no cycles or shared identities."""
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value

def read_table(path):
    path = Path(path)
    raw = os.fspath(path)
    key = os.path.normcase(raw if path.is_absolute() else os.path.abspath(raw))
    with _lock:
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            _cache.pop(key, None)
            return {}
        cached = _cache.get(key)
        if cached and cached[0] == stamp:
            return _clone(cached[1])
        try:
            with path.open(encoding="utf-8") as f:
                value = json.load(f)
            if not isinstance(value, dict):
                raise ValueError("JSON table must be an object")
        except (OSError, ValueError):
            return _clone(cached[1]) if cached else {}
        _cache[key] = (stamp, value)
        return _clone(value)

def write_table(path, value):
    path = Path(path)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            _cache.pop(os.path.normcase(os.path.abspath(path)), None)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
