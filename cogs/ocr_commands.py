"""
OCR Commands Cog — v3.1
+ Leaderboard integration (record scans)
+ TranslateCommands.store_last() integration
+ Thumbnail preview in embed
+ Confidence score shown in result
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp, asyncio, io, json, logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from core.config      import settings, cooldowns, ocr_queue, preprocessor, stats_buffer, drive_limiter
from core.image_utils import confidence_score, make_thumbnail

log = logging.getLogger("OCR")

LANGUAGE_OPTIONS = [
    app_commands.Choice(name="🇯🇵 Japanese (Manga)",            value="jpn"),
    app_commands.Choice(name="🇰🇷 Korean (Manhwa)",             value="kor"),
    app_commands.Choice(name="🇨🇳 Chinese Simplified (Manhua)", value="chi_sim"),
    app_commands.Choice(name="🇹🇼 Chinese Traditional",         value="chi_tra"),
    app_commands.Choice(name="🇬🇧 English",                     value="eng"),
    app_commands.Choice(name="🇸🇦 Arabic",                      value="ara"),
    app_commands.Choice(name="🌐 Auto-Detect",                   value="auto"),
]
ENGINE_OPTIONS = [
    app_commands.Choice(name="⚡ Fast (Tesseract)",        value="tesseract"),
    app_commands.Choice(name="🎯 Accurate (EasyOCR)",      value="easyocr"),
    app_commands.Choice(name="🔮 Best (Manga-OCR)",        value="manga_ocr"),
    app_commands.Choice(name="✨ Perfect (Claude Vision)", value="claude_vision"),
]
LANG_FLAGS  = {"jpn":"🇯🇵","kor":"🇰🇷","chi_sim":"🇨🇳","chi_tra":"🇹🇼","eng":"🇬🇧","ara":"🇸🇦","auto":"🌐"}
LANG_NAMES  = {"jpn":"Japanese","kor":"Korean","chi_sim":"Chinese (Simplified)",
               "chi_tra":"Chinese (Traditional)","eng":"English","ara":"Arabic","auto":"Auto"}
ENGINE_ICONS = {"tesseract":"⚡","easyocr":"🎯","manga_ocr":"🔮","claude_vision":"✨"}

HTTP_RETRY_ATTEMPTS = 3
HTTP_RETRY_DELAY    = 1.5


class OCRCommands(commands.Cog, name="OCR"):

    def __init__(self, bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: dict = {}
        self._cache_order: list = []
        self.CACHE_MAX = 200

    @property
    def stats(self) -> dict:
        return stats_buffer.data

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    # ── HTTP with retry ───────────────────────────────────
    async def get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        return self._session

    async def _download_image(self, url: str) -> bytes:
        session = await self.get_session()
        delay = HTTP_RETRY_DELAY
        last_err = None
        for attempt in range(1, HTTP_RETRY_ATTEMPTS + 1):
            try:
                async with session.get(url) as r:
                    if r.status == 200:
                        data = await r.read()
                        if len(data) < 100:
                            raise ValueError("File too small")
                        return data
                    raise ValueError(f"HTTP {r.status}")
            except Exception as e:
                last_err = e
                if attempt < HTTP_RETRY_ATTEMPTS:
                    await asyncio.sleep(delay); delay *= 2
        raise ValueError(f"Download failed after {HTTP_RETRY_ATTEMPTS} attempts: {last_err}")

    # ── LRU Cache ─────────────────────────────────────────
    def _cache_get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def _cache_put(self, key: str, value: str):
        if key in self._cache:
            self._cache_order.remove(key)
        self._cache[key] = value
        self._cache_order.append(key)
        if len(self._cache_order) > self.CACHE_MAX:
            self._cache.pop(self._cache_order.pop(0), None)

    # ── OCR Core ──────────────────────────────────────────
    async def _run_ocr(self, image_data: bytes, language: str, engine: str,
                        preprocess: bool = True) -> tuple:
        if preprocess:
            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(None, preprocessor.preprocess, image_data)
        else:
            proc = image_data

        cache_key = f"{preprocessor.image_hash(proc)}:{language}:{engine}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached, engine + " (cached)"

        used_engine = engine

        def _work():
            nonlocal used_engine

            # ══ Bubble-aware OCR pipeline ══════════════════════════
            # للياباني: manga-ocr على كل فقاعة لوحدها (أفضل نتيجة)
            # للباقي: tesseract bubble-by-bubble + clean
            try:
                from core.bubble_detector import extract_bubbles
                import io as _io
                from PIL import Image as _PIL

                bubbles = extract_bubbles(image_data, lang=language)

                if len(bubbles) > 1:
                    # عندنا فقاعات واضحة — OCR كل فقاعة لوحدها
                    log.info(f"BubbleOCR: {len(bubbles)} فقاعة")
                    bubble_results = []

                    # نعيد الصورة المعالجة كـbytes عشان نقدر نقطع منها
                    proc_buf = _io.BytesIO()
                    _PIL.open(_io.BytesIO(proc)).save(proc_buf, format="PNG")
                    proc_bytes = proc_buf.getvalue()

                    for b in bubbles:
                        # قطع منطقة الفقاعة مع padding
                        from core.image_utils import crop_region
                        x, y, bw, bh = b["bbox"]
                        pad = 20
                        cropped = crop_region(proc_bytes, (x, y, bw, bh), padding=pad)

                        if engine == "claude_vision":
                            # Claude Vision يعمل على الصورة الأصلية وليس المعالجة
                            r = self._eng_claude(image_data, language)
                            if r.startswith("[FALLBACK]"):
                                r = self._eng_manga(cropped, language)
                                used_engine = "manga_ocr"
                            if r.startswith("[FALLBACK]"):
                                r = self._eng_tess(cropped, language)
                                used_engine = "tesseract"
                        elif engine == "manga_ocr":
                            r = self._eng_manga(cropped, language)
                            if r.startswith("[FALLBACK]"):
                                r = self._eng_tess(cropped, language)
                                used_engine = "tesseract"
                        elif engine == "easyocr":
                            r = self._eng_easy(cropped, language)
                            if r.startswith("[FALLBACK]"):
                                r = self._eng_tess(cropped, language)
                                used_engine = "tesseract"
                        else:
                            r = self._eng_tess(cropped, language)

                        r = self._clean_ocr_text(r, language)
                        if r.strip():
                            bubble_results.append(r.strip())

                    if bubble_results:
                        return "\n\n".join(bubble_results)

            except Exception as e:
                log.warning(f"BubbleOCR failed, fallback: {e}")

            # ══ Fallback: الصورة كاملة (السلوك القديم) ══════════════
            if engine == "claude_vision":
                # Claude Vision يحتاج الصورة الأصلية (ملونة) وليس المعالجة
                r = self._eng_claude(image_data, language)
                if r.startswith("[FALLBACK]"): used_engine="manga_ocr"; r=self._eng_manga(proc, language)
                if r.startswith("[FALLBACK]"): used_engine="easyocr";   r=self._eng_easy(proc, language)
                if r.startswith("[FALLBACK]"): used_engine="tesseract"; r=self._eng_tess(proc, language)
            elif engine == "manga_ocr":
                r = self._eng_manga(proc, language)
                if r.startswith("[FALLBACK]"): used_engine="easyocr";   r=self._eng_easy(proc, language)
                if r.startswith("[FALLBACK]"): used_engine="tesseract"; r=self._eng_tess(proc, language)
            elif engine == "easyocr":
                r = self._eng_easy(proc, language)
                if r.startswith("[FALLBACK]"): used_engine="tesseract"; r=self._eng_tess(proc, language)
            else:
                r = self._eng_tess(proc, language)
            return r

        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, _work)
        self._cache_put(cache_key, text)
        return text, used_engine

    def _eng_claude(self, data: bytes, lang: str) -> str:
        """Claude Vision OCR — يقرأ خطوط المانغا الزخرفية/العريضة بشكل مثالي."""
        import os, base64, urllib.request, json as _json
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return "[FALLBACK] ANTHROPIC_API_KEY not set"
        try:
            if data[:8] == b"\x89PNG\r\n\x1a\n":   mime = "image/png"
            elif data[:3] == b"\xff\xd8\xff":        mime = "image/jpeg"
            elif data[:4] == b"RIFF" and data[8:12] == b"WEBP": mime = "image/webp"
            elif data[:6] in (b"GIF87a", b"GIF89a"): mime = "image/gif"
            else:                                     mime = "image/png"

            b64 = base64.standard_b64encode(data).decode()
            lang_hints = {
                "jpn":     "The text is Japanese (manga). Read panels RIGHT-TO-LEFT, top-to-bottom.",
                "kor":     "The text is Korean (manhwa). Read panels top-to-bottom, LEFT-TO-RIGHT.",
                "chi_sim": "The text is Simplified Chinese (manhua). Read top-to-bottom, left-to-right.",
                "chi_tra": "The text is Traditional Chinese (manhua). Read top-to-bottom, right-to-left.",
                "eng":     "The text is English. Read panels left-to-right, top-to-bottom.",
                "ara":     "The text is Arabic. Preserve right-to-left reading order.",
                "auto":    "Detect the language automatically and use the appropriate reading order.",
            }
            lang_hint = lang_hints.get(lang, "Detect the language automatically.")
            system_prompt = (
                "You are a precise OCR engine specialized in manga, manhwa, and manhua. "
                "Extract ALL text exactly as it appears — speech bubbles, SFX, signs, narration boxes. "
                "Output ONLY the extracted text, one block per line, blank line between blocks. "
                "Do NOT translate. Do NOT add commentary. If no text: output [NO TEXT]"
            )
            payload = _json.dumps({
                "model": "claude-opus-4-5",
                "max_tokens": 2048,
                "system": system_prompt,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text",  "text": lang_hint + "\nExtract every text block in reading order. Separate blocks with blank lines."},
                ]}],
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages", data=payload,
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = _json.loads(resp.read())
            for block in result.get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    return block["text"].strip()
            return "[FALLBACK] Claude returned empty response"
        except Exception as e:
            log.error(f"Claude Vision OCR error: {e}", exc_info=True)
            return f"[FALLBACK] Claude Vision error: {e}"

    def _eng_manga(self, data, lang):
        try:
            from manga_ocr import MangaOcr
            from PIL import Image
            if not hasattr(self, "_mocr"): self._mocr = MangaOcr()
            text = self._mocr(Image.open(io.BytesIO(data)))
            return self._clean_ocr_text(text, lang)
        except ImportError: return "[FALLBACK] manga-ocr not installed"
        except Exception as e: return f"[FALLBACK] {e}"

    def _eng_easy(self, data, lang):
        try:
            import easyocr, numpy as np
            from PIL import Image
            lmap = {"jpn":["ja"],"kor":["ko"],"chi_sim":["ch_sim"],"chi_tra":["ch_tra"],
                    "eng":["en"],"ara":["ar"],"auto":["ja","ko","ch_sim","en"]}
            langs = lmap.get(lang, ["en"])
            lkey  = ",".join(sorted(langs))
            if not hasattr(self, "_easy_readers"): self._easy_readers = {}
            if lkey not in self._easy_readers:
                self._easy_readers[lkey] = easyocr.Reader(langs, gpu=False, verbose=False)
            arr = np.array(Image.open(io.BytesIO(data)))
            text = "\n".join(self._easy_readers[lkey].readtext(arr, detail=0, paragraph=True))
            return self._clean_ocr_text(text, lang)
        except ImportError: return "[FALLBACK] easyocr not installed"
        except Exception as e: return f"[FALLBACK] {e}"

    def _eng_tess(self, data, lang):
        try:
            import pytesseract
            from PIL import Image
            lmap = {"jpn":"jpn","kor":"kor","chi_sim":"chi_sim","chi_tra":"chi_tra",
                    "eng":"eng","ara":"ara","auto":"jpn+kor+chi_sim+eng"}
            tess_lang = lmap.get(lang, "eng")

            # PSM 11 = sparse text, best for manga speech bubbles
            # PSM 6  = uniform block (fallback for dense pages)
            # OEM 1  = LSTM only (most accurate modern engine)
            primary_config   = "--oem 1 --psm 11"
            fallback_config  = "--oem 1 --psm 6"

            img = Image.open(io.BytesIO(data))
            text = pytesseract.image_to_string(img, lang=tess_lang, config=primary_config)

            # If PSM 11 gave almost nothing, retry with PSM 6
            if len(text.strip()) < 5:
                text = pytesseract.image_to_string(img, lang=tess_lang, config=fallback_config)

            return self._clean_ocr_text(text, lang)
        except Exception as e:
            log.error(f"Tesseract: {e}"); return f"[OCR Error: {e}]"

    def _clean_ocr_text(self, text: str, lang: str) -> str:
        """
        Post-process OCR output to fix common misreads.
        Handles the garbled characters that Tesseract/EasyOCR produce on manga.
        """
        import re

        if not text:
            return text

        # ── Remove junk lines (single chars, pure symbols, noise) ──
        lines = text.splitlines()
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            # Skip empty or single char lines
            if len(stripped) <= 1:
                continue
            # Skip page number patterns like "155 / 250", "155/250"
            if re.match(r'^\d+\s*/\s*\d+$', stripped):
                continue
            if re.match(r'^p(age|g)\.?\s*\d+$', stripped, re.IGNORECASE):
                continue
            # Skip lines that are mostly non-alphanumeric garbage
            alnum = sum(1 for c in stripped if c.isalnum())
            if len(stripped) > 3 and alnum / len(stripped) < 0.35:
                continue
            # Skip short decoration artifacts (< 4 chars, no real letters)
            if len(stripped) < 4 and not re.search(r'[a-zA-Z\u3040-\u9fff\uac00-\ud7af]', stripped):
                continue
            clean_lines.append(line)

        text = "\n".join(clean_lines)

        # ── Common Tesseract misreads for English manga ──
        if lang in ("eng", "auto"):
            fixes = [
                # Manga bold font common misreads
                (r'\bFORCET\b',      'FORGET'),
                (r'\bAPOUT\b',       'ABOUT'),
                (r'\bUNINWADABLE\b', 'UNINVADABLE'),
                (r'\bCAY\b',         'GUY'),
                (r'\bCY\b',          'GUY'),
                (r'\bCENT\b',        "CAN'T"),
                (r'\bCONT\b',        "CAN'T"),
                (r'\bGENT\b',        "CAN'T"),
                # "I CAN'T ACCEPT THIS" garbled variants
                (r'\b[Ww]e\s+ann[cC][oO][Tt]\s+[Tt][Hh][Ii][AaSs]\.?', "I CAN'T ACCEPT THIS."),
                (r'\bI\s+[CG][AO]N[\'`]?T\s+ACCEPT\s+THI[SA]\.?',      "I CAN'T ACCEPT THIS."),
                # / في نهاية الجملة = ! (لكن مو بين أرقام مثل 1/2 أو 5/10)
                (r'(?<![0-9])/(?![0-9a-zA-Z])', '!'),
                (r'CITIZEN\.',       'CITIZEN,'),
                # Mixed case errors
                (r'\bAp([A-Z])',     r'AB\1'),
                (r'\bUI([A-Z])',     r'UN\1'),
                (r'\b([A-Z])l\b',   r'\1I'),
                (r'0([A-Z])',        r'O\1'),
                (r'([A-Z])0',        r'\1O'),
                (r'\|',              'I'),
                # Spacing
                (r' ([!?,;:])',      r'\1'),
                (r'  +',            ' '),
            ]
            for pattern, replacement in fixes:
                text = re.sub(pattern, replacement, text)

        # ── Remove trailing whitespace from each line ──
        text = "\n".join(line.rstrip() for line in text.splitlines())

        # ── Collapse 3+ consecutive blank lines to 2 ──
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    # ── Log channel helper ────────────────────────────────
    async def _log_error(self, guild_id: int, message: str):
        gs = settings.get_guild(guild_id)
        ch_id = gs.get("log_channel")
        if not ch_id: return
        try:
            ch = self.bot.get_channel(ch_id)
            if ch:
                await ch.send(embed=discord.Embed(title="🔴 OCR Error",
                    description=message, color=0xFF4444, timestamp=datetime.now()))
        except Exception as e:
            log.warning(f"log_channel send failed: {e}")

    # ── Embed builder ─────────────────────────────────────
    def _result_embed(self, text, lang, engine_used, filename,
                       compact=False, conf: dict = None) -> discord.Embed:
        flag   = LANG_FLAGS.get(lang, "🌐")
        lname  = LANG_NAMES.get(lang, lang)
        eicon  = ENGINE_ICONS.get(engine_used.split()[0], "🔍")
        chars  = len(text.strip())
        cached = "cached" in engine_used
        now    = datetime.now()

        embed = discord.Embed(
            title=f"{flag} OCR Result — {filename}",
            color=0xFF9900 if chars < 3 else 0x6C63FF,
            timestamp=now,
        )
        if chars < 3:
            embed.add_field(name="⚠️ No Text Detected",
                value="No readable text found. Try a different engine or enable preprocessing.",
                inline=False)
        else:
            limit   = 350 if compact else 1000
            stripped = text.strip()
            snippet  = stripped[:limit] + ("…" if len(stripped) > limit else "")
            embed.add_field(name="📝 Extracted Text", value=f"```{snippet}```", inline=False)

        cache_tag = " 🔁" if cached else ""
        embed.add_field(name="🌐 Language", value=f"{flag} {lname}", inline=True)
        embed.add_field(name="🔧 Engine",   value=f"{eicon} {engine_used}{cache_tag}", inline=True)
        embed.add_field(name="📊 Chars",    value=f"`{chars:,}`", inline=True)

        # Confidence score (NEW)
        if conf:
            embed.add_field(name="🎯 Confidence", value=f"{conf['label']} `{conf['score']}%`", inline=True)

        embed.set_footer(text=f"Manga OCR Bot v3.1 • {now.strftime('%B')} {now.year}  |  Full text in .txt")
        return embed

    @staticmethod
    def _error_embed(title, desc):
        return discord.Embed(title=title, description=desc, color=0xFF4444)

    @staticmethod
    def _warn_embed(title, desc):
        return discord.Embed(title=title, description=desc, color=0xFF9900)

    # ── /ocr ──────────────────────────────────────────────
    @app_commands.command(name="ocr", description="🔍 Extract text from a Manga/Manhwa image")
    @app_commands.describe(
        image="Manga/manhwa image (JPG/PNG/WEBP)",
        language="Language of the text",
        engine="OCR engine (auto-fallback if unavailable)",
        upload_drive="Upload result to Google Drive?",
        preprocess="Enhance image before OCR (recommended)",
        show_confidence="Show OCR quality score",
    )
    @app_commands.choices(language=LANGUAGE_OPTIONS, engine=ENGINE_OPTIONS)
    async def ocr_slash(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        language: Optional[app_commands.Choice[str]] = None,
        engine:   Optional[app_commands.Choice[str]] = None,
        upload_drive: bool = False,
        preprocess:   bool = True,
        show_confidence: bool = False,
    ):
        guild_id   = interaction.guild_id or 0
        user_id    = interaction.user.id
        channel_id = interaction.channel_id or 0

        # Quota check (NEW)
        quota_cog = self.bot.get_cog("Quota")
        if quota_cog and not quota_cog.check(guild_id, user_id, 1):
            await interaction.response.send_message(
                embed=self._warn_embed("📊 Daily Quota Reached",
                    "This server has reached its daily scan limit. Resets at midnight UTC."),
                ephemeral=True); return

        if guild_id and not settings.channel_allowed(guild_id, channel_id):
            await interaction.response.send_message(
                embed=self._warn_embed("🚫 Wrong Channel", "OCR not enabled here."),
                ephemeral=True); return

        gs = settings.get_guild(guild_id)
        if rem := cooldowns.check(user_id, gs.get("cooldown_seconds", 10)):
            await interaction.response.send_message(
                embed=self._warn_embed("⏳ Cooldown", f"Wait **{rem:.1f}s**"),
                ephemeral=True); return

        if Path(image.filename).suffix.lower() not in {".jpg",".jpeg",".png",".webp",".gif",".bmp",".tiff"}:
            await interaction.response.send_message(
                embed=self._error_embed("❌ Invalid File", "Please attach an image."),
                ephemeral=True); return

        eff  = settings.effective(guild_id, user_id)
        lang = language.value if language else eff["default_language"]
        eng  = engine.value   if engine   else eff["default_engine"]

        await interaction.response.defer(thinking=True)
        cooldowns.stamp(user_id)
        await ocr_queue.acquire()
        try:
            data = await self._download_image(image.url)

            # ── تحذير الصور منخفضة الجودة ────────────────
            try:
                from PIL import Image as _PIL
                _img = _PIL.open(io.BytesIO(data))
                _w, _h = _img.size
                if _w * _h < 150_000:
                    await interaction.followup.send(
                        embed=self._warn_embed(
                            "⚠️ صورة منخفضة الجودة",
                            f"دقة الصورة **{_w}×{_h}px** صغيرة جداً.\n"
                            "قد تكون النتائج غير دقيقة.\n"
                            "📌 الرجاء إرسال صورة بجودة أعلى للحصول على نتائج أفضل."
                        ),
                        ephemeral=True
                    )
            except Exception:
                pass
            # ─────────────────────────────────────────────

            text, eng_used = await self._run_ocr(data, lang, eng, preprocess)
            cache_hit = "cached" in eng_used
            conf = confidence_score(text) if show_confidence else None

            # Thumbnail for embed preview (NEW)
            loop = asyncio.get_running_loop()
            thumb_bytes = await loop.run_in_executor(None, make_thumbnail, data, (256,256))

            stats_buffer.update(guild_id, lang, cache_hit=cache_hit)
            await settings.add_history({"type":"single","guild_id":guild_id,"user_id":user_id,
                                         "filename":image.filename,"language":lang,
                                         "engine":eng_used,"chars":len(text)})

            # Notify leaderboard cog (NEW)
            lb_cog = self.bot.get_cog("Leaderboard")
            if lb_cog: lb_cog.record_scan(guild_id, user_id, 1)

            # Store for /translate (NEW)
            tr_cog = self.bot.get_cog("Translate")
            if tr_cog: tr_cog.store_last(user_id, text)

            # Quota consume (NEW)
            if quota_cog: quota_cog.consume(guild_id, user_id, 1)

            embed = self._result_embed(text, lang, eng_used, image.filename,
                                        compact=eff.get("compact_results", False), conf=conf)

            # Attach thumbnail to embed
            files_to_send = []
            txt_name = f"ocr_{Path(image.filename).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            buf = io.BytesIO(text.encode("utf-8")); buf.seek(0)
            files_to_send.append(discord.File(buf, filename=txt_name))

            if thumb_bytes:
                thumb_buf = io.BytesIO(thumb_bytes); thumb_buf.seek(0)
                thumb_file = discord.File(thumb_buf, filename="thumb.png")
                embed.set_thumbnail(url="attachment://thumb.png")
                files_to_send.append(thumb_file)

            await interaction.followup.send(embed=embed, files=files_to_send)

            # Drive upload
            if upload_drive or eff.get("auto_drive"):
                if not drive_limiter.check(guild_id):
                    await interaction.followup.send(
                        embed=self._warn_embed("☁️ Drive Rate Limited", "Too many uploads this minute."),
                        ephemeral=True)
                else:
                    drive_cog = self.bot.get_cog("GDrive")
                    if drive_cog and drive_cog.is_available:
                        link = await drive_cog.upload_text(text, filename=txt_name)
                        if link and eff.get("notify_drive", True):
                            await interaction.followup.send(
                                embed=discord.Embed(title="☁️ Saved to Google Drive",
                                    description=f"[📂 Open file]({link})", color=0x34A853))

        except Exception as e:
            log.error(f"OCR error: {e}", exc_info=True)
            cooldowns.clear(user_id)
            await interaction.followup.send(embed=self._error_embed("❌ OCR Failed", f"```{str(e)[:600]}```"))
            await self._log_error(guild_id, f"OCR failed `{image.filename}`: ```{str(e)[:300]}```")
        finally:
            ocr_queue.release()

    # ── !ocr prefix ───────────────────────────────────────
    @commands.command(name="ocr", aliases=["scan","read"])
    async def ocr_prefix(self, ctx: commands.Context, lang: str = "auto"):
        if not ctx.message.attachments:
            await ctx.send(embed=self._warn_embed("📎 No Image", "Attach an image. E.g.: `!ocr jpn`")); return
        guild_id = ctx.guild.id if ctx.guild else 0
        user_id  = ctx.author.id
        if rem := cooldowns.check(user_id, settings.get_guild(guild_id).get("cooldown_seconds",10)):
            await ctx.send(embed=self._warn_embed("⏳ Cooldown", f"Wait **{rem:.1f}s**")); return
        quota_cog = self.bot.get_cog("Quota")
        if quota_cog and not quota_cog.check(guild_id, user_id, 1):
            await ctx.send(embed=self._warn_embed("📊 Quota", "Daily scan limit reached.")); return
        cooldowns.stamp(user_id)
        att = ctx.message.attachments[0]
        async with ctx.typing():
            await ocr_queue.acquire()
            try:
                data = await self._download_image(att.url)
                text, eng_used = await self._run_ocr(data, lang, "manga_ocr")
                stats_buffer.update(guild_id, lang)
                lb = self.bot.get_cog("Leaderboard")
                if lb: lb.record_scan(guild_id, user_id, 1)
                tr = self.bot.get_cog("Translate")
                if tr: tr.store_last(user_id, text)
                if quota_cog: quota_cog.consume(guild_id, user_id, 1)
                await settings.add_history({"type":"single","guild_id":guild_id,"user_id":user_id,
                                             "filename":att.filename,"language":lang,
                                             "engine":eng_used,"chars":len(text)})
                embed = self._result_embed(text, lang, eng_used, att.filename)
                buf = io.BytesIO(text.encode("utf-8")); buf.seek(0)
                await ctx.send(embed=embed,
                               file=discord.File(buf, filename=f"ocr_{Path(att.filename).stem}.txt"))
            except Exception as e:
                cooldowns.clear(user_id)
                await ctx.send(embed=self._error_embed("❌ Error", f"`{e}`"))
            finally:
                ocr_queue.release()

    # ── !ocr-bulk prefix ──────────────────────────────────
    @commands.command(name="ocr-bulk", aliases=["bulkscan","multi"])
    async def ocr_bulk_prefix(self, ctx: commands.Context, lang: str = "auto"):
        atts = [a for a in ctx.message.attachments
                if Path(a.filename).suffix.lower() in {".jpg",".jpeg",".png",".webp",".bmp"}][:5]
        if not atts:
            await ctx.send(embed=self._warn_embed("📎 No Images", "Attach 1–5 images.")); return
        guild_id = ctx.guild.id if ctx.guild else 0
        user_id  = ctx.author.id
        if rem := cooldowns.check(user_id, settings.get_guild(guild_id).get("cooldown_seconds",10)):
            await ctx.send(embed=self._warn_embed("⏳ Cooldown", f"Wait **{rem:.1f}s**")); return
        cooldowns.stamp(user_id)
        status_msg = await ctx.send(
            embed=discord.Embed(title=f"🔍 Scanning {len(atts)} image(s)...", color=0x6C63FF))
        all_texts = []
        await ocr_queue.acquire()
        try:
            for idx, att in enumerate(atts, 1):
                try:
                    data = await self._download_image(att.url)
                    text, eng_used = await self._run_ocr(data, lang, "manga_ocr")
                    all_texts.append(f"=== [{idx}] {att.filename} ===\n{text}\n")
                    embed = self._result_embed(text, lang, eng_used, att.filename)
                    embed.title = f"🔍 [{idx}/{len(atts)}] {att.filename}"
                    buf = io.BytesIO(text.encode("utf-8")); buf.seek(0)
                    await ctx.send(embed=embed,
                                   file=discord.File(buf, filename=f"ocr_{idx}_{att.filename}.txt"))
                except Exception as e:
                    await ctx.send(embed=self._error_embed(f"❌ Image {idx}", str(e)[:300]))
            if len(all_texts) > 1:
                combined = "\n".join(all_texts); cbuf = io.BytesIO(combined.encode("utf-8")); cbuf.seek(0)
                await ctx.send(content="📄 **Combined result:**",
                               file=discord.File(cbuf, filename=f"ocr_combined_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"))
            stats_buffer.update(guild_id, lang, pages=len(all_texts))
            lb = self.bot.get_cog("Leaderboard")
            if lb: lb.record_scan(guild_id, user_id, len(all_texts))
        finally:
            ocr_queue.release()
        await status_msg.delete()

    # ── /history with pagination ──────────────────────────
    @app_commands.command(name="history", description="📜 Show your recent OCR scans")
    @app_commands.describe(limit="Number of entries (max 15)", page="Page number")
    async def history_slash(self, interaction: discord.Interaction,
                             limit: int = 5, page: int = 1):
        limit = max(1, min(limit, 15))
        page  = max(1, page)
        all_entries = settings.get_history(
            guild_id=interaction.guild_id or 0,
            user_id=interaction.user.id,
            limit=100,
        )
        if not all_entries:
            await interaction.response.send_message(
                embed=self._warn_embed("📜 No History", "You haven't scanned anything yet!"),
                ephemeral=True); return

        total_pages = max(1, (len(all_entries) + limit - 1) // limit)
        page = min(page, total_pages)
        start = (page - 1) * limit
        entries = list(reversed(all_entries))[start:start + limit]

        embed = discord.Embed(title="📜 Your Recent Scans", color=0x6C63FF, timestamp=datetime.now())
        for e in entries:
            ts    = e.get("ts","")[:16].replace("T"," ")
            lang  = e.get("language","?")
            chars = e.get("chars",0)
            fname = e.get("filename","?")[:30]
            icon  = "📦" if e.get("type")=="zip" else "🖼️"
            embed.add_field(name=f"{icon} {fname}",
                value=f"`{lang.upper()}` • `{chars:,}` chars • {ts}", inline=False)
        embed.set_footer(text=f"Page {page}/{total_pages} • {len(all_entries)} total entries")

        # Pagination buttons
        view = HistoryPaginatorView(
            interaction, page, total_pages, limit, self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def cog_unload(self):
        stats_buffer.flush()
        if self._session: await self._session.close()


# ── History Paginator (NEW) ───────────────────────────────
class HistoryPaginatorView(discord.ui.View):
    def __init__(self, interaction, current_page, total_pages, limit, cog):
        super().__init__(timeout=60)
        self.interaction   = interaction
        self.current_page  = current_page
        self.total_pages   = total_pages
        self.limit         = limit
        self.cog           = cog
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current_page <= 1
        self.next_btn.disabled = self.current_page >= self.total_pages
        self.page_label.label  = f"{self.current_page}/{self.total_pages}"

    async def _refresh(self, interaction: discord.Interaction):
        all_entries = settings.get_history(
            guild_id=interaction.guild_id or 0,
            user_id=interaction.user.id, limit=100)
        start   = (self.current_page - 1) * self.limit
        entries = list(reversed(all_entries))[start:start + self.limit]
        embed   = discord.Embed(title="📜 Your Recent Scans", color=0x6C63FF, timestamp=datetime.now())
        for e in entries:
            ts    = e.get("ts","")[:16].replace("T"," ")
            lang  = e.get("language","?")
            chars = e.get("chars",0)
            fname = e.get("filename","?")[:30]
            icon  = "📦" if e.get("type")=="zip" else "🖼️"
            embed.add_field(name=f"{icon} {fname}",
                value=f"`{lang.upper()}` • `{chars:,}` chars • {ts}", inline=False)
        embed.set_footer(text=f"Page {self.current_page}/{self.total_pages} • {len(all_entries)} total")
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self._refresh(interaction)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self._refresh(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(OCRCommands(bot))
