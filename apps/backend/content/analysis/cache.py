"""Durable, file-based cache of the URL analysis JSON (the "source of truth"
for a resource), under ``cache/analysis/`` (ADR 0009 / docs/storage.md).

The DB already caches analyses by resource key; this file cache is the durable
artefact the operator can inspect and that survives a DB reset. Both share the
same TTL (``CONTENT_ANALYSIS_TTL_HOURS``). Only used when the cache is enabled.
"""

import json
import time
from pathlib import Path

from content.storage.layout import sanitize_filename


class AnalysisJsonCache:
    def __init__(self, cache_root: Path, ttl_hours: float):
        self.root = Path(cache_root) / "analysis"
        self.ttl_seconds = ttl_hours * 3600.0

    def _path(self, resource_key: str) -> Path:
        name = sanitize_filename(resource_key.replace(":", "_"), max_length=200)
        return self.root / f"{name}.json"

    def exists(self, resource_key: str) -> bool:
        return self._path(resource_key).is_file()

    def load(self, resource_key: str) -> dict | None:
        """The cached analysis payload if present and still fresh, else None."""
        path = self._path(resource_key)
        if not path.is_file():
            return None
        if self.ttl_seconds and time.time() - path.stat().st_mtime > self.ttl_seconds:
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def save(self, resource_key: str, payload: dict) -> None:
        """Persist the analysis payload as the durable source of truth. Failures
        are non-fatal — the cache is an optimization, never a correctness gate."""
        path = self._path(resource_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        except OSError:
            pass

    def purge(self) -> int:
        """Delete every cached analysis file. Returns how many were removed."""
        removed = 0
        if not self.root.exists():
            return 0
        for path in self.root.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed
