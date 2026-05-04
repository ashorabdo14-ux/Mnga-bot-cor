"""
Image Utilities
- deskew: fix rotated/tilted manga scans
- bubble_crop: detect and crop speech bubbles
- confidence: OCR confidence scoring
- output_formats: TXT, JSON, Markdown, SRT
- thumbnail: generate Discord embed thumbnail
"""

import io, logging
from typing import Optional

log = logging.getLogger("ImageUtils")


# ════════════════════════════════════════════════════════════
#  Deskew — fix tilted manga pages
# ════════════════════════════════════════════════════════════
def deskew_image(image_data: bytes) -> bytes:
    """Detect and correct image tilt angle using Hough transform."""
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(image_data)).convert("L")
        arr = np.array(img)
        # Simple variance-based angle detection
        from scipy.ndimage import rotate as scipy_rotate
        from skimage.transform import hough_line, hough_line_peaks
        from skimage.feature import canny
        edges = canny(arr, sigma=2)
        h, angles, dists = hough_line(edges)
        _, peak_angles, _ = hough_line_peaks(h, angles, dists, num_peaks=10)
        if len(peak_angles) == 0:
            return image_data
        # Median angle in degrees
        angle_deg = float(np.median(peak_angles) * 180 / np.pi)
        # Normalize: if nearly vertical/horizontal, no correction needed
        if abs(angle_deg) < 0.5 or abs(angle_deg) > 89:
            return image_data
        # Correct by rotating
        corrected = scipy_rotate(arr, -angle_deg, reshape=True, cval=255)
        out = Image.fromarray(corrected.astype(np.uint8))
        buf = io.BytesIO(); out.save(buf, format="PNG"); buf.seek(0)
        log.info(f"Deskew: corrected {angle_deg:.1f}°")
        return buf.read()
    except ImportError:
        return _simple_deskew(image_data)
    except Exception as e:
        log.warning(f"Deskew failed: {e}")
        return image_data


def _simple_deskew(image_data: bytes) -> bytes:
    """Fallback deskew using PIL only — detects large rotations (90/180/270)."""
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(image_data))
        # Use EXIF orientation if present
        img = ImageOps.exif_transpose(img)
        buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
        return buf.read()
    except Exception:
        return image_data


# ════════════════════════════════════════════════════════════
#  Speech Bubble Detection
# ════════════════════════════════════════════════════════════
def detect_text_regions(image_data: bytes, max_regions: int = 20) -> list:
    """
    Detect text regions (speech bubbles) in manga image.
    Returns list of (x, y, w, h) bounding boxes, sorted top-to-bottom left-to-right.
    Falls back to full-image region if detection fails.
    """
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(image_data)).convert("L")
        arr = np.array(img)
        h, w = arr.shape

        # Threshold + find contours using simple connected components
        binary = (arr < 200).astype(np.uint8)
        from scipy import ndimage
        labeled, num_features = ndimage.label(binary)
        regions = []
        for i in range(1, min(num_features + 1, max_regions * 3)):
            mask = labeled == i
            ys, xs = np.where(mask)
            if len(ys) < 50: continue  # skip tiny noise
            x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
            rw, rh = x2 - x1, y2 - y1
            # Filter: text regions are roughly 20px-600px wide
            if 20 < rw < w * 0.8 and 20 < rh < h * 0.5:
                regions.append((x1, y1, rw, rh))

        if not regions:
            return [(0, 0, w, h)]

        # Sort: top-to-bottom, then left-to-right (manga reading order)
        regions.sort(key=lambda r: (r[1] // 100, r[0]))
        return regions[:max_regions]
    except Exception as e:
        log.warning(f"Region detection failed: {e}")
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_data))
            return [(0, 0, img.width, img.height)]
        except Exception:
            return []


def crop_region(image_data: bytes, region: tuple, padding: int = 5) -> bytes:
    """Crop a region from the image with optional padding."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_data))
        x, y, w, h = region
        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(img.width,  x + w + padding)
        y2 = min(img.height, y + h + padding)
        cropped = img.crop((x1, y1, x2, y2))
        buf = io.BytesIO(); cropped.save(buf, format="PNG"); buf.seek(0)
        return buf.read()
    except Exception as e:
        log.warning(f"Crop failed: {e}")
        return image_data


# ════════════════════════════════════════════════════════════
#  Output Formatters
# ════════════════════════════════════════════════════════════
def format_txt(pages: list[dict], chapter: str = "") -> str:
    """Plain text output."""
    lines = []
    if chapter:
        lines += [f"{'='*60}", f"Chapter: {chapter}", f"{'='*60}", ""]
    for p in pages:
        lines += [f"{'─'*40}", f"Page {p['page']}: {p.get('filename','')}", f"{'─'*40}", p.get('text',''), ""]
    return "\n".join(lines)


def format_json(pages: list[dict], chapter: str = "", language: str = "") -> str:
    """Structured JSON output — useful for developers."""
    import json
    from datetime import datetime
    data = {
        "chapter":    chapter,
        "language":   language,
        "generated":  datetime.utcnow().isoformat() + "Z",
        "total_pages": len(pages),
        "pages": [
            {
                "page":     p["page"],
                "filename": p.get("filename", ""),
                "text":     p.get("text", "").strip(),
                "chars":    len(p.get("text", "")),
                "engine":   p.get("engine", ""),
                "translation": p.get("translation"),
            }
            for p in pages
        ]
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def format_markdown(pages: list[dict], chapter: str = "") -> str:
    """Markdown output for GitHub/Notion/Obsidian."""
    lines = []
    if chapter:
        lines += [f"# {chapter}", ""]
    for p in pages:
        lines += [f"## Page {p['page']}", ""]
        text = p.get("text","").strip()
        if text:
            # BUG 5 FIX: don't wrap empty lines with "> " — that breaks markdown blockquotes.
            # Use proper blockquote: empty blockquote lines need just ">"
            for line in text.split("\n"):
                lines.append(f"> {line}" if line.strip() else ">")
        if p.get("translation"):
            lines += ["", f"**Translation:** {p['translation']}"]
        lines += ["", "---", ""]
    return "\n".join(lines)


def format_srt(pages: list[dict], seconds_per_page: float = 5.0) -> str:
    """
    SRT subtitle format — useful for webtoon reading videos / slideshows.
    Each page = one subtitle entry.
    """
    lines = []
    for i, p in enumerate(pages, 1):
        start = (i - 1) * seconds_per_page
        end   = i * seconds_per_page
        text  = p.get("text","").strip()[:200]  # SRT lines should be short
        lines += [
            str(i),
            f"{_srt_time(start)} --> {_srt_time(end)}",
            text,
            "",
        ]
    return "\n".join(lines)


def _srt_time(seconds: float) -> str:
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ════════════════════════════════════════════════════════════
#  Thumbnail Generator
# ════════════════════════════════════════════════════════════
def make_thumbnail(image_data: bytes, max_size: tuple = (320, 320)) -> Optional[bytes]:
    """Create a small thumbnail for Discord embed preview."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_data)).convert("RGB")
        img.thumbnail(max_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        log.warning(f"Thumbnail failed: {e}")
        return None


# ════════════════════════════════════════════════════════════
#  Confidence Scorer
# ════════════════════════════════════════════════════════════
def confidence_score(text: str) -> dict:
    """
    Heuristic confidence scoring of OCR output.
    Returns {"score": 0-100, "label": str, "issues": [str]}
    """
    issues = []
    score  = 100

    if not text or not text.strip():
        return {"score": 0, "label": "No text", "issues": ["Empty output"]}

    stripped = text.strip()
    chars    = len(stripped)

    # Too short
    if chars < 5:
        score -= 40; issues.append("Very short output")

    # High ratio of non-printable / garbage chars
    garbage_chars = sum(1 for c in stripped if ord(c) < 32 and c not in "\n\t")
    if garbage_chars / max(chars, 1) > 0.05:
        score -= 20; issues.append("High garbage character ratio")

    # Suspiciously many single characters (OCR artifacts)
    words  = stripped.split()
    single = sum(1 for w in words if len(w) == 1 and not w.isalpha())
    if words and single / len(words) > 0.3:
        score -= 15; issues.append("Many single-character tokens")

    # Lots of question marks or boxes (common OCR fail)
    fail_chars = stripped.count("?") + stripped.count("□") + stripped.count("■")
    if fail_chars / max(chars, 1) > 0.05:
        score -= 35; issues.append("Many replacement characters")

    # All caps (often tesseract mis-read)
    alpha = [c for c in stripped if c.isalpha()]
    if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.9:
        score -= 5; issues.append("Unexpected all-caps")

    score = max(0, min(100, score))
    if score >= 80:   label = "High ✅"
    elif score >= 50: label = "Medium ⚠️"
    else:             label = "Low ❌"

    return {"score": score, "label": label, "issues": issues}
