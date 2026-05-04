"""
Config Manager v3
- SettingsManager: per-server + user prefs, atomic JSON writes
- CooldownTracker: per-user cooldown
- OCRQueue: semaphore-based concurrency limiter
- ImagePreprocessor: enhance images before OCR
- StatsBuffer: debounced stats writes (not on every request)
"""

import json, asyncio, logging, hashlib, time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict
from collections import deque

log = logging.getLogger("Config")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

import os as _os
_DEFAULT_ENGINE = _os.getenv("DEFAULT_ENGINE", "manga_ocr")
if _DEFAULT_ENGINE not in ("tesseract", "easyocr", "manga_ocr", "claude_vision"):
    _DEFAULT_ENGINE = "manga_ocr"

DEFAULT_GUILD_SETTINGS = {
    "default_language":  "auto",
    "default_engine":    _DEFAULT_ENGINE,
    "auto_drive":        False,
    "max_zip_pages":     200,
    "cooldown_seconds":  10,
    "allowed_channels":  [],
    "output_channel":    None,
    "log_channel":       None,
    "prefix":            "!",
}

DEFAULT_USER_PREFS = {
    "default_language":  None,
    "default_engine":    None,
    "notify_drive":      True,
    "compact_results":   False,
}


# ════════════════════════════════════════════════════════════
#  Settings Manager
# ════════════════════════════════════════════════════════════
class SettingsManager:
    def __init__(self):
        self._guilds_file  = DATA_DIR / "guild_settings.json"
        self._users_file   = DATA_DIR / "user_prefs.json"
        self._history_file = DATA_DIR / "history.json"
        self._guilds:  Dict[str, dict] = {}
        self._users:   Dict[str, dict] = {}
        self._history: list = []
        self._lock = asyncio.Lock()
        self._load_all()

    def _load_all(self):
        self._guilds  = self._load_json(self._guilds_file, {})
        self._users   = self._load_json(self._users_file,  {})
        raw           = self._load_json(self._history_file, [])
        self._history = raw[-1000:] if isinstance(raw, list) else []

    def _load_json(self, path: Path, default):
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Load failed {path}: {e}")
        return default

    def _save_json(self, path: Path, data):
        """Atomic write: write to .tmp then rename."""
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            tmp.replace(path)
        except Exception as e:
            log.error(f"Save failed {path}: {e}")
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    # ── Guild ────────────────────────────────────────────────
    def get_guild(self, guild_id: int) -> dict:
        gid = str(guild_id)
        merged = dict(DEFAULT_GUILD_SETTINGS)
        merged.update(self._guilds.get(gid, {}))
        return merged

    async def set_guild(self, guild_id: int, key: str, value) -> bool:
        if key not in DEFAULT_GUILD_SETTINGS:
            return False
        async with self._lock:
            gid = str(guild_id)
            if gid not in self._guilds:
                self._guilds[gid] = {}
            self._guilds[gid][key] = value
            self._save_json(self._guilds_file, self._guilds)
        return True

    async def reset_guild(self, guild_id: int):
        async with self._lock:
            self._guilds[str(guild_id)] = dict(DEFAULT_GUILD_SETTINGS)
            self._save_json(self._guilds_file, self._guilds)

    # ── User ─────────────────────────────────────────────────
    def get_user(self, user_id: int) -> dict:
        uid = str(user_id)
        merged = dict(DEFAULT_USER_PREFS)
        merged.update(self._users.get(uid, {}))
        return merged

    async def set_user(self, user_id: int, key: str, value) -> bool:
        if key not in DEFAULT_USER_PREFS:
            return False
        async with self._lock:
            uid = str(user_id)
            if uid not in self._users:
                self._users[uid] = {}
            self._users[uid][key] = value
            self._save_json(self._users_file, self._users)
        return True

    # ── Effective (user overrides guild) ─────────────────────
    def effective(self, guild_id: int, user_id: int) -> dict:
        gs = self.get_guild(guild_id)
        up = self.get_user(user_id)
        result = dict(gs)
        if up.get("default_language"):
            result["default_language"] = up["default_language"]
        if up.get("default_engine"):
            result["default_engine"]   = up["default_engine"]
        result["compact_results"] = up.get("compact_results", False)
        result["notify_drive"]    = up.get("notify_drive", True)
        return result

    # ── History ──────────────────────────────────────────────
    async def add_history(self, entry: dict):
        async with self._lock:
            entry["ts"] = datetime.utcnow().isoformat()
            self._history.append(entry)
            if len(self._history) > 1000:
                self._history = self._history[-1000:]
            self._save_json(self._history_file, self._history)

    def get_history(self, guild_id: int = None, user_id: int = None, limit: int = 10) -> list:
        entries = self._history
        if guild_id:
            entries = [e for e in entries if e.get("guild_id") == guild_id]
        if user_id:
            entries = [e for e in entries if e.get("user_id") == user_id]
        return entries[-limit:]

    # ── Channel check ────────────────────────────────────────
    def channel_allowed(self, guild_id: int, channel_id: int) -> bool:
        allowed = self.get_guild(guild_id).get("allowed_channels", [])
        return len(allowed) == 0 or channel_id in allowed


# ════════════════════════════════════════════════════════════
#  Cooldown Tracker
# ════════════════════════════════════════════════════════════
class CooldownTracker:
    def __init__(self):
        self._last: Dict[int, datetime] = {}

    def check(self, user_id: int, seconds: int) -> Optional[float]:
        if seconds <= 0:
            return None
        now  = datetime.utcnow()
        last = self._last.get(user_id)
        if last:
            elapsed = (now - last).total_seconds()
            if elapsed < seconds:
                return seconds - elapsed
        return None

    def stamp(self, user_id: int):
        self._last[user_id] = datetime.utcnow()

    def clear(self, user_id: int):
        self._last.pop(user_id, None)


# ════════════════════════════════════════════════════════════
#  OCR Queue
# ════════════════════════════════════════════════════════════
class OCRQueue:
    def __init__(self, max_concurrent: int = 3):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._queue_size = 0
        self._lock = asyncio.Lock()

    @property
    def queue_size(self) -> int:
        return self._queue_size

    async def acquire(self) -> bool:
        async with self._lock:
            self._queue_size += 1
        await self._sem.acquire()
        return True

    def release(self):
        self._sem.release()
        self._queue_size = max(0, self._queue_size - 1)


# ════════════════════════════════════════════════════════════
#  Stats Buffer  (FIX #5 — debounced writes, not on every call)
# ════════════════════════════════════════════════════════════
class StatsBuffer:
    """
    Buffers stat increments in memory.
    Flushes to disk at most every FLUSH_INTERVAL seconds,
    and always on explicit flush() or process shutdown.
    """
    FLUSH_INTERVAL = 30  # seconds

    def __init__(self, path: Path):
        self._path       = path
        self._lock       = asyncio.Lock()
        self._dirty      = False
        self._last_flush = time.monotonic()
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"total_images": 0, "total_pages": 0, "languages": {},
                "guilds": {}, "last_updated": "", "cache_hits": 0}

    def _write(self):
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            tmp.replace(self._path)
            self._dirty      = False
            self._last_flush = time.monotonic()
        except Exception as e:
            log.error(f"StatsBuffer write failed: {e}")
            tmp.unlink(missing_ok=True)

    def update(self, guild_id: int, language: str, pages: int = 1, cache_hit: bool = False):
        self._data["total_images"] += 1
        self._data["total_pages"]  += pages
        self._data["last_updated"]  = str(datetime.now().date())
        lk = language or "auto"
        self._data["languages"][lk]       = self._data["languages"].get(lk, 0) + pages
        gk = str(guild_id)
        self._data["guilds"][gk]          = self._data["guilds"].get(gk, 0) + pages
        if cache_hit:
            self._data["cache_hits"] = self._data.get("cache_hits", 0) + 1
        self._dirty = True
        # Flush if interval elapsed
        if time.monotonic() - self._last_flush >= self.FLUSH_INTERVAL:
            self._write()

    def flush(self):
        if self._dirty:
            self._write()

    @property
    def data(self) -> dict:
        return self._data

    def cache_hit_rate(self) -> float:
        total = self._data.get("total_images", 0)
        hits  = self._data.get("cache_hits",   0)
        return round(hits / total * 100, 1) if total > 0 else 0.0


# ════════════════════════════════════════════════════════════
#  Image Preprocessor
# ════════════════════════════════════════════════════════════
class ImagePreprocessor:
    @staticmethod
    def preprocess(image_data: bytes, aggressive: bool = False) -> bytes:
        """
        Enhanced manga/manhwa preprocessing pipeline:
        1. Upscale small images aggressively (manga bubbles need resolution)
        2. Convert to grayscale
        3. Denoise with median filter
        4. Adaptive thresholding (much better than fixed 128 threshold)
        5. Light sharpen pass
        """
        try:
            from PIL import Image, ImageEnhance, ImageFilter
            import io as _io, numpy as np

            img = Image.open(_io.BytesIO(image_data)).convert("RGB")
            w, h = img.size

            # Step 1: Upscale aggressively — manga text needs high resolution
            # Target minimum 1400px on shortest side for good OCR
            min_dim = min(w, h)
            max_dim = max(w, h)
            if min_dim < 1400:
                scale = 1400 / min_dim
                # Cap at 4x to avoid memory issues on large images
                scale = min(scale, 4.0)
                new_w, new_h = int(w * scale), int(h * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                log.debug(f"Upscaled {w}x{h} → {new_w}x{new_h} (×{scale:.1f})")

            # Step 2: Grayscale
            gray = img.convert("L")
            arr  = np.array(gray, dtype=np.uint8)

            # Step 3: Median denoise — removes speckle noise common in scans
            try:
                from PIL import ImageFilter as _IF
                gray = Image.fromarray(arr).filter(_IF.MedianFilter(size=3))
                arr  = np.array(gray)
            except Exception:
                pass

            # Step 4: Adaptive thresholding — far better than fixed threshold
            # Splits image into blocks and thresholds each block independently.
            # This handles uneven lighting, gradients, and toned backgrounds.
            try:
                block = 31  # block size (must be odd)
                C     = 10  # constant subtracted from mean
                h2, w2 = arr.shape
                # Compute local mean via uniform filter (fast approximation)
                from scipy.ndimage import uniform_filter
                local_mean = uniform_filter(arr.astype(np.float32), size=block)
                binary = np.where(arr < local_mean - C, 0, 255).astype(np.uint8)
                img = Image.fromarray(binary)
                log.debug("Adaptive thresholding applied")
            except ImportError:
                # Fallback: Otsu-like threshold using histogram
                hist, _ = np.histogram(arr, bins=256, range=(0, 255))
                total   = arr.size
                sum_all = np.dot(np.arange(256), hist)
                sumB = wB = wF = 0.0
                best_t = 128
                best_var = 0.0
                for t in range(256):
                    wB += hist[t]
                    if wB == 0: continue
                    wF = total - wB
                    if wF == 0: break
                    sumB += t * hist[t]
                    mB = sumB / wB
                    mF = (sum_all - sumB) / wF
                    var = wB * wF * (mB - mF) ** 2
                    if var > best_var:
                        best_var = var
                        best_t   = t
                binary = ((arr > best_t) * 255).astype(np.uint8)
                img = Image.fromarray(binary)
                log.debug(f"Otsu threshold at {best_t}")

            # Step 5: Sharpen to make edges crisper
            img = img.filter(ImageFilter.SHARPEN)

            # Extra aggressive pass: stronger contrast boost
            if aggressive:
                img = ImageEnhance.Contrast(img).enhance(2.5)

            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()

        except Exception as e:
            log.warning(f"Preprocessing failed, using original: {e}")
            return image_data

    @staticmethod
    def image_hash(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()


# ════════════════════════════════════════════════════════════
#  Drive Rate Limiter  (FIX #6)
# ════════════════════════════════════════════════════════════
class DriveRateLimiter:
    """Max N uploads per minute per guild."""
    MAX_PER_MINUTE = 5

    def __init__(self):
        self._history: Dict[int, deque] = {}

    def check(self, guild_id: int) -> bool:
        """Returns True if upload allowed, False if rate-limited."""
        now = time.monotonic()
        gid = guild_id
        if gid not in self._history:
            self._history[gid] = deque()
        dq = self._history[gid]
        # Remove entries older than 60s
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= self.MAX_PER_MINUTE:
            return False
        dq.append(now)
        return True


# ── Singletons ───────────────────────────────────────────
settings       = SettingsManager()
cooldowns      = CooldownTracker()
ocr_queue      = OCRQueue(max_concurrent=3)
preprocessor   = ImagePreprocessor()
stats_buffer   = StatsBuffer(DATA_DIR / "stats.json")
drive_limiter  = DriveRateLimiter()
