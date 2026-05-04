"""
bubble_detector.py — Professional Manga/Manhwa Bubble Detector
───────────────────────────────────────────────────────────────
الخوارزمية:
  1. preprocess (upscale + adaptive threshold)
  2. pytesseract PSM 11 → كل كلمة مع block_num + موضعها
  3. تجميع بـblock_num (نفس الـblock = نفس المجموعة)
  4. دمج الـblocks المتقاربة رأسياً (< 2.5x ارتفاع السطر)
  5. ترتيب المجموعات: أعلى → أسفل
  6. داخل كل مجموعة: ترتيب الكلمات بـyrow ثم left (يسار→يمين)
"""

import io, logging, re
import numpy as np
from PIL import Image, ImageFilter
from scipy.ndimage import uniform_filter
from collections import defaultdict

log = logging.getLogger("BubbleDetector")


# ════════════════════════════════════════════════════════════════════
#  Public API
# ════════════════════════════════════════════════════════════════════
def extract_bubbles(image_data: bytes, lang: str = "eng",
                    conf_threshold: int = 40) -> list[dict]:
    """
    اكتشف الفقاعات واستخرج نصها مرتباً.
    Returns: list[{"index", "text", "bbox"}] — مرتب أعلى→أسفل
    """
    try:
        import pytesseract

        proc, _ = _preprocess(image_data)
        PH, PW  = proc.height, proc.width

        df = pytesseract.image_to_data(
            proc, lang=_tess_lang(lang),
            config="--oem 1 --psm 11",
            output_type=pytesseract.Output.DATAFRAME
        )

        words = df[(df.conf > conf_threshold) &
                   (df.text.str.strip() != "")].copy()
        words = words[['block_num','left','top','width','height','text','conf']]
        words = words.reset_index(drop=True)

        if words.empty:
            return [{"index": 1, "text": "", "bbox": (0, 0, PW, PH)}]

        avg_h = max(float(words['height'].median()), 20)

        # ── Step 1: جمّع بـblock_num ─────────────────────────
        block_groups = defaultdict(list)
        for i, row in words.iterrows():
            block_groups[int(row['block_num'])].append(i)

        # ── Step 2: دمج الـblocks المتقاربة ──────────────────
        # نحسب top_min لكل block، ثم ندمج اللي فجوتهم < 2.5x avg_h
        GAP = avg_h * 2.5

        def block_top(indices):
            return float(words.loc[indices, 'top'].min())

        sorted_blocks = sorted(block_groups.values(), key=block_top)

        merged = []
        current = list(sorted_blocks[0])
        for nxt in sorted_blocks[1:]:
            cur_bot = float(words.loc[current, 'top'].max()) + avg_h
            nxt_top = block_top(nxt)
            if nxt_top - cur_bot < GAP:
                current.extend(nxt)   # دمج
            else:
                merged.append(current)
                current = list(nxt)
        merged.append(current)

        # ── Step 3: بنّي نص كل فقاعة ─────────────────────────
        ROW_SNAP = max(int(avg_h * 0.7), 10)
        bubbles  = []

        for idx, group in enumerate(merged, 1):
            gw = words.loc[group].copy()

            # bbox
            x1 = int(gw['left'].min())
            y1 = int(gw['top'].min())
            x2 = int((gw['left'] + gw['width']).max())
            y2 = int((gw['top']  + gw['height']).max())

            # ترتيب الكلمات: صف (top//ROW_SNAP) ثم يسار
            gw = gw.copy()
            gw['row'] = (gw['top'] // ROW_SNAP)

            lines = []
            for _, row_grp in gw.sort_values('left').groupby('row', sort=True):
                line = ' '.join(row_grp.sort_values('left')['text'].tolist())
                if line.strip():
                    lines.append(line)

            text = '\n'.join(lines).strip()
            if not text:
                continue

            # فلتر: احذف الفقاعة لو أغلب كلماتها noise
            word_list = text.split()
            if word_list:
                # كلمة حقيقية = على الأقل حرفان أبجديان متتاليان
                real_words = sum(1 for ww in word_list
                                 if re.search(r'[a-zA-Z\u3040-\u9fff\uac00-\ud7af]{2,}', ww))
                real_ratio = real_words / len(word_list)
                # احذف الفقاعة لو:
                # - أقل من 40% كلمات حقيقية، أو
                # - كلمة واحدة فقط وقصيرة جداً (< 3 أحرف)
                if real_ratio < 0.40:
                    continue
                if len(word_list) == 1 and len(text.strip()) < 3:
                    continue

            bubbles.append({
                "index": idx,
                "text":  text,
                "bbox":  (x1, y1, x2-x1, y2-y1),
            })

        log.info(f"BubbleDetector: {len(bubbles)} فقاعة")
        return bubbles

    except ImportError:
        log.error("pytesseract غير مثبّت")
        return []
    except Exception as e:
        log.error(f"BubbleDetector error: {e}")
        return []


def bubbles_to_text(bubbles: list[dict], separator: str = "\n\n") -> str:
    """دمج الفقاعات في نص واحد مرتب."""
    return separator.join(b["text"] for b in bubbles if b.get("text","").strip())


# ════════════════════════════════════════════════════════════════════
#  Preprocessing
# ════════════════════════════════════════════════════════════════════
def _preprocess(image_data: bytes, target_min: int = 1400) -> tuple:
    img = Image.open(io.BytesIO(image_data)).convert("RGB")
    w, h = img.size

    # حذف اللوحة الداكنة إذا وُجدت في الأسفل
    try:
        arr_c = np.array(img.convert("L"))
        if arr_c[int(h*0.75):, :].mean() < 150:
            from PIL import ImageDraw
            ImageDraw.Draw(img).rectangle([(0, int(h*0.75)), (w, h)],
                                          fill=(255, 255, 255))
    except Exception:
        pass

    scale = 1.0
    if min(w, h) < target_min:
        scale = min(target_min / min(w, h), 4.0)
        img   = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)

    gray = img.convert("L").filter(ImageFilter.MedianFilter(3))
    arr  = np.array(gray, dtype=np.float32)
    try:
        lm   = uniform_filter(arr, size=31)
        bin_ = np.where(arr < lm - 10, 0, 255).astype(np.uint8)
        proc = Image.fromarray(bin_).filter(ImageFilter.SHARPEN)
    except Exception:
        proc = gray

    return proc, scale


# ════════════════════════════════════════════════════════════════════
#  Language map
# ════════════════════════════════════════════════════════════════════
_LANG_MAP = {
    "jpn": "jpn", "kor": "kor",
    "chi_sim": "chi_sim", "chi_tra": "chi_tra",
    "eng": "eng", "ara": "ara",
    "auto": "jpn+kor+chi_sim+eng",
}

def _tess_lang(lang: str) -> str:
    return _LANG_MAP.get(lang, "eng")
