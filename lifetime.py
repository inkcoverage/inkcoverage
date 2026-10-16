"""Fixed document expiry and single-worker leases, independent of directory mtime.

Only application-owned names are reclaimed. No filenames or document IDs enter
cleanup logs. Persistent manifests survive restart; in-memory leases protect work
until the executor actually finishes, including disconnected HTTP requests.
"""
import json
import logging
import math
import re
import shutil
import threading
import time
from pathlib import Path

log = logging.getLogger("uvicorn.error.cleanup")


class Unavailable(Exception):
    def __init__(self, status=410):
        self.status = status


class FileLifetime:
    def __init__(self, root, ttl):
        self.root = root
        self.ttl = ttl
        self.lock = threading.RLock()
        self.active = {}
        self.stats = {"runs": 0, "removed": 0, "failures": 0, "deferred": 0,
                      "lastRunAt": None, "lastFailures": 0}

    def _safe(self, directory):
        root = Path(self.root()).resolve()
        path = Path(directory)
        if path.is_symlink() or path.resolve() != root / path.name:
            raise OSError("Unsafe cleanup target")
        if not re.fullmatch(r"(?:[0-9a-f]{32}|analysis_[0-9a-f]{32})", path.name):
            raise OSError("Unowned cleanup target")
        return path.resolve()

    def _read(self, directory):
        manifest = directory / "lifetime.json"
        if manifest.is_symlink() or manifest.stat().st_size > 1024:
            raise ValueError("Invalid lifetime")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        start, end = data["uploadedAt"], data["expiresAt"]
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or end < start
                or data.get("state") not in ("uploading", "ready", "abandoned", "scratch")):
            raise ValueError("Invalid lifetime")
        return data

    def _write(self, directory, data):
        # Replace atomically; cleanup and request admission share the same lock.
        target = directory / "lifetime.json"
        temporary = directory / "lifetime.new"
        if target.is_symlink() or temporary.is_symlink():
            raise OSError("Unsafe lifetime target")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(target)

    def create(self, directory, scratch=False):
        with self.lock:
            path = self._safe(directory)
            path.mkdir(parents=True, exist_ok=False)
            now = time.time()
            data = {"uploadedAt": now, "expiresAt": now + self.ttl(),
                    "state": "scratch" if scratch else "uploading"}
            try:
                self._write(path, data)
            except BaseException:
                self._remove(path)
                raise
            self.active[path] = 1
            return data

    def ready(self, directory):
        with self.lock:
            path = self._safe(directory)
            data = self._read(path)
            if data["state"] != "uploading":
                raise Unavailable()
            data["state"] = "ready"
            self._write(path, data)
            return data

    def acquire(self, directory):
        with self.lock:
            path = self._safe(directory)
            if not path.is_dir():
                raise Unavailable(404)
            try:
                data = self._read(path)
            except (OSError, ValueError, KeyError, TypeError):
                raise Unavailable() from None
            if data["state"] != "ready" or time.time() >= data["expiresAt"]:
                raise Unavailable()
            if not (path / "upload.pdf").is_file():
                raise Unavailable(404)
            self.active[path] = self.active.get(path, 0) + 1

    def release(self, directory):
        with self.lock:
            path = self._safe(directory)
            count = self.active.get(path, 0)
            if count <= 1:
                self.active.pop(path, None)
            else:
                self.active[path] = count - 1
            if not self.active.get(path) and path.exists():
                try:
                    if self._read(path)["state"] in ("abandoned", "scratch"):
                        self._remove(path)
                except (OSError, ValueError, KeyError, TypeError):
                    pass

    def discard(self, directory):
        with self.lock:
            path = self._safe(directory)
            if not path.exists():
                return
            try:
                data = self._read(path)
                data["state"] = "abandoned"
                self._write(path, data)
            except (OSError, ValueError, KeyError, TypeError):
                # Unusable manifests are denied and retried by the sweep too.
                pass
            if not self.active.get(path):
                self._remove(path)

    def _remove(self, directory):
        try:
            path = self._safe(directory)
            shutil.rmtree(path)
            self.stats["removed"] += 1
            return True
        except FileNotFoundError:
            return True
        except OSError:
            self.stats["failures"] += 1
            log.warning("Temporary file cleanup failed; will retry")
            return False

    def remove_derivative(self, directory, parent):
        with self.lock:
            try:
                base = self._safe(parent)
                path = Path(directory)
                if (path.is_symlink() or path.resolve().parent != base
                        or not re.fullmatch(r"preview_[a-zA-Z0-9_-]+", path.name)):
                    raise OSError("Unsafe derivative")
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            except OSError:
                self.stats["failures"] += 1
                log.warning("Temporary derivative cleanup failed; will retry")

    def sweep(self):
        with self.lock:
            before = self.stats["failures"]
            now = time.time()
            self.stats["runs"] += 1
            self.stats["lastRunAt"] = now
            try:
                root = Path(self.root()).resolve()
                for directory in root.iterdir():
                    if not re.fullmatch(r"(?:[0-9a-f]{32}|analysis_[0-9a-f]{32})", directory.name):
                        continue
                    try:
                        path = self._safe(directory)
                        if self.active.get(path):
                            self.stats["deferred"] += 1
                            continue
                        try:
                            data = self._read(path)
                            expired = data["state"] != "ready" or now >= data["expiresAt"]
                        except (OSError, ValueError, KeyError, TypeError):
                            expired = True  # Legacy/crashed uploads never get a fresh TTL.
                        if expired:
                            self._remove(path)
                        else:
                            for derivative in path.glob("preview_*"):
                                self.remove_derivative(derivative, path)
                    except OSError:
                        self.stats["failures"] += 1
                        log.warning("Temporary file cleanup target inaccessible; will retry")
            except OSError:
                self.stats["failures"] += 1
                log.warning("Temporary file cleanup scan failed; will retry")
            self.stats["lastFailures"] = self.stats["failures"] - before
            log.info("Temporary cleanup: runs=%s removed=%s failures=%s deferred=%s",
                     self.stats["runs"], self.stats["removed"], self.stats["failures"], self.stats["deferred"])
            return dict(self.stats)
