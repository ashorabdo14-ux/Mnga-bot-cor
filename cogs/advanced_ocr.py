"""
Advanced OCR Cog
- /ocr-compare  : run same image through 2+ engines, show side-by-side diff
- /ocr-url      : OCR from image URL (no upload needed)
- /ocr-format   : choose output format: txt / json / markdown / srt
- /ocr-deskew   : auto-fix tilted image then OCR
- /ocr-confidence: show OCR quality score
"""

import discord
from discord.ext import commands
from discord import app_commands
import io, logging, asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config      import settings, cooldowns, ocr_queue, preprocessor, stats_buffer
from core.image_utils import (format_txt, format_json, format_markdown, format_srt,
                               deskew_image, confidence_score, make_thumbnail)

log = logging.getLogger("AdvancedOCR")

FORMAT_CHOICES = [
    app_commands.Choice(name="📄 TXT (default)", value="txt"),
    app_commands.Choice(name="🔧 JSON (structured)", value="json"),
    app_commands.Choice(name="📝 Markdown",  value="md"),
    app_commands.Choice(name="🎬 SRT (subtitle)", value="srt"),
]

ENGINE_CHOICES_MULTI = [
    app_commands.Choice(name="⚡ Tesseract",        value="tesseract"),
    app_commands.Choice(name="🎯 EasyOCR",          value="easyocr"),
    app_commands.Choice(name="🔮 Manga-OCR",        value="manga_ocr"),
    app_commands.Choice(name="✨ Claude Vision",    value="claude_vision"),
]

LANG_FLAGS = {"jpn":"🇯🇵","kor":"🇰🇷","chi_sim":"🇨🇳","chi_tra":"🇹🇼","eng":"🇬🇧","ara":"🇸🇦","auto":"🌐"}
ENGINE_ICONS_ADV = {"tesseract":"⚡","easyocr":"🎯","manga_ocr":"🔮","claude_vision":"✨"}


class AdvancedOCR(commands.Cog, name="AdvancedOCR"):

    def __init__(self, bot):
        self.bot = bot

    def _ocr_cog(self):
        return self.bot.get_cog("OCR")

    @staticmethod
    def _warn(t, d): return discord.Embed(title=t, description=d, color=0xFF9900)
    @staticmethod
    def _error(t, d): return discord.Embed(title=t, description=d, color=0xFF4444)

    # ── /ocr-compare ─────────────────────────────────────
    @app_commands.command(name="ocr-compare",
        description="🔬 Compare results from 2 OCR engines on the same image")
    @app_commands.describe(
        image="Image to scan",
        language="Language of the text",
        engine_a="First engine",
        engine_b="Second engine",
    )
    @app_commands.choices(
        language=[
            app_commands.Choice(name="🇯🇵 Japanese",   value="jpn"),
            app_commands.Choice(name="🇰🇷 Korean",     value="kor"),
            app_commands.Choice(name="🇨🇳 Chinese",    value="chi_sim"),
            app_commands.Choice(name="🇬🇧 English",    value="eng"),
            app_commands.Choice(name="🌐 Auto",         value="auto"),
        ],
        engine_a=ENGINE_CHOICES_MULTI,
        engine_b=ENGINE_CHOICES_MULTI,
    )
    async def ocr_compare(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        language: Optional[app_commands.Choice[str]] = None,
        engine_a: Optional[app_commands.Choice[str]] = None,
        engine_b: Optional[app_commands.Choice[str]] = None,
    ):
        guild_id = interaction.guild_id or 0
        user_id  = interaction.user.id
        gs = settings.get_guild(guild_id)
        if rem := cooldowns.check(user_id, gs.get("cooldown_seconds", 10)):
            await interaction.response.send_message(
                embed=self._warn("⏳ Cooldown", f"Wait **{rem:.1f}s**"),
                ephemeral=True); return

        lang = language.value if language else "auto"
        eng1 = engine_a.value if engine_a else "claude_vision"
        eng2 = engine_b.value if engine_b else "easyocr"
        if eng1 == eng2:
            eng2 = "easyocr" if eng1 != "easyocr" else "manga_ocr"

        await interaction.response.defer(thinking=True)
        cooldowns.stamp(user_id)

        ocr = self._ocr_cog()
        if not ocr:
            await interaction.followup.send(embed=self._error("❌", "OCR cog not loaded")); return

        await ocr_queue.acquire()
        try:
            img_data = await ocr._download_image(image.url)

            # Run both engines in parallel
            t1, t2 = await asyncio.gather(
                ocr._run_ocr(img_data, lang, eng1),
                ocr._run_ocr(img_data, lang, eng2),
            )
            text1, used1 = t1
            text2, used2 = t2

            # Confidence scores
            c1 = confidence_score(text1)
            c2 = confidence_score(text2)

            now = datetime.now()
            flag = LANG_FLAGS.get(lang, "🌐")
            embed = discord.Embed(
                title=f"🔬 Engine Comparison — {image.filename}",
                description=f"Language: **{flag} {lang.upper()}**",
                color=0x9C27B0,
                timestamp=now,
            )

            def preview(t, n=400):
                s = t.strip()[:n]
                return f"```{s}```" if s else "*No text detected*"

            embed.add_field(
                name=f"{ENGINE_ICONS_ADV.get(used1,'🔍')} Engine A: `{used1}`  |  Confidence: {c1['label']} ({c1['score']}%)",
                value=preview(text1), inline=False)
            embed.add_field(
                name=f"{ENGINE_ICONS_ADV.get(used2,'🔍')} Engine B: `{used2}`  |  Confidence: {c2['label']} ({c2['score']}%)",
                value=preview(text2), inline=False)

            # Recommend winner
            winner = used1 if c1["score"] >= c2["score"] else used2
            embed.add_field(name="🏆 Recommended",
                            value=f"`{winner}` has higher confidence", inline=False)
            embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")

            # Combined .txt
            combined = (
                f"=== Engine A: {used1} (confidence {c1['score']}%) ===\n{text1}\n\n"
                f"=== Engine B: {used2} (confidence {c2['score']}%) ===\n{text2}"
            )
            buf = io.BytesIO(combined.encode("utf-8")); buf.seek(0)
            fname = f"compare_{Path(image.filename).stem}_{now.strftime('%Y%m%d_%H%M%S')}.txt"
            await interaction.followup.send(embed=embed, file=discord.File(buf, filename=fname))

        except Exception as e:
            log.error(f"Compare error: {e}", exc_info=True)
            cooldowns.clear(user_id)
            await interaction.followup.send(embed=self._error("❌ Error", f"```{str(e)[:400]}```"))
        finally:
            ocr_queue.release()

    # ── /ocr-url ─────────────────────────────────────────
    @app_commands.command(name="ocr-url",
        description="🔗 OCR an image from a URL (no upload needed)")
    @app_commands.describe(
        url="Direct image URL (must end in .jpg/.png/.webp etc.)",
        language="Language of the text",
    )
    @app_commands.choices(language=[
        app_commands.Choice(name="🇯🇵 Japanese",   value="jpn"),
        app_commands.Choice(name="🇰🇷 Korean",     value="kor"),
        app_commands.Choice(name="🇨🇳 Chinese",    value="chi_sim"),
        app_commands.Choice(name="🇬🇧 English",    value="eng"),
        app_commands.Choice(name="🌐 Auto",         value="auto"),
    ])
    async def ocr_url(
        self,
        interaction: discord.Interaction,
        url: str,
        language: Optional[app_commands.Choice[str]] = None,
    ):
        guild_id = interaction.guild_id or 0
        user_id  = interaction.user.id

        # Basic URL validation
        if not url.startswith(("http://","https://")):
            await interaction.response.send_message(
                embed=self._error("❌ Invalid URL", "URL must start with http:// or https://"),
                ephemeral=True); return

        valid_exts = {".jpg",".jpeg",".png",".webp",".gif",".bmp"}
        url_lower = url.lower().split("?")[0]
        url_looks_invalid = not any(url_lower.endswith(ext) for ext in valid_exts)

        gs = settings.get_guild(guild_id)
        if rem := cooldowns.check(user_id, gs.get("cooldown_seconds", 10)):
            await interaction.response.send_message(
                embed=self._warn("⏳ Cooldown", f"Wait **{rem:.1f}s**"),
                ephemeral=True); return

        lang = language.value if language else "auto"
        await interaction.response.defer(thinking=True)
        cooldowns.stamp(user_id)

        if url_looks_invalid:
            await interaction.followup.send(
                embed=self._warn("⚠️ URL Warning",
                    "URL doesn't look like an image. Attempting anyway..."),
                ephemeral=True)

        ocr = self._ocr_cog()
        if not ocr:
            await interaction.followup.send(embed=self._error("❌","OCR not loaded")); return

        await ocr_queue.acquire()
        try:
            img_data = await ocr._download_image(url)
            eff_url = settings.effective(guild_id, user_id)
            text, eng_used = await ocr._run_ocr(img_data, lang, eff_url["default_engine"])
            stats_buffer.update(guild_id, lang)

            fname_hint = Path(url.split("?")[0]).name or "image"
            now  = datetime.now()
            flag = LANG_FLAGS.get(lang, "🌐")
            embed = discord.Embed(
                title=f"{flag} OCR from URL",
                description=f"`{url[:80]}{'...' if len(url)>80 else ''}`",
                color=0x6C63FF, timestamp=now)
            preview = text.strip()[:1000] + "…" if len(text.strip()) > 1000 else text.strip()
            if preview:
                embed.add_field(name="📝 Extracted Text",
                                value=f"```{preview}```", inline=False)
            else:
                embed.add_field(name="⚠️ No Text", value="No readable text found.", inline=False)
            embed.add_field(name="🔧 Engine", value=eng_used, inline=True)
            embed.add_field(name="📊 Chars",  value=f"`{len(text):,}`", inline=True)
            embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")

            buf = io.BytesIO(text.encode("utf-8")); buf.seek(0)
            await interaction.followup.send(
                embed=embed,
                file=discord.File(buf, filename=f"ocr_url_{now.strftime('%Y%m%d_%H%M%S')}.txt"))

        except Exception as e:
            cooldowns.clear(user_id)
            await interaction.followup.send(
                embed=self._error("❌ Failed", f"```{str(e)[:400]}```"))
        finally:
            ocr_queue.release()

    # ── /ocr-format ───────────────────────────────────────
    @app_commands.command(name="ocr-format",
        description="📋 OCR with specific output format (JSON/Markdown/SRT)")
    @app_commands.describe(
        image="Manga/manhwa image",
        output_format="Output format",
        language="Language",
    )
    @app_commands.choices(output_format=FORMAT_CHOICES, language=[
        app_commands.Choice(name="🇯🇵 Japanese",   value="jpn"),
        app_commands.Choice(name="🇰🇷 Korean",     value="kor"),
        app_commands.Choice(name="🇨🇳 Chinese",    value="chi_sim"),
        app_commands.Choice(name="🇬🇧 English",    value="eng"),
        app_commands.Choice(name="🌐 Auto",         value="auto"),
    ])
    async def ocr_format(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        output_format: app_commands.Choice[str],
        language: Optional[app_commands.Choice[str]] = None,
    ):
        guild_id = interaction.guild_id or 0
        user_id  = interaction.user.id
        gs = settings.get_guild(guild_id)
        if rem := cooldowns.check(user_id, gs.get("cooldown_seconds", 10)):
            await interaction.response.send_message(
                embed=self._warn("⏳ Cooldown", f"Wait **{rem:.1f}s**"), ephemeral=True); return

        lang = language.value if language else "auto"
        fmt  = output_format.value
        await interaction.response.defer(thinking=True)
        cooldowns.stamp(user_id)

        ocr = self._ocr_cog()
        if not ocr:
            await interaction.followup.send(embed=self._error("❌","OCR not loaded")); return

        await ocr_queue.acquire()
        try:
            img_data = await ocr._download_image(image.url)
            eff_fmt = settings.effective(guild_id, user_id)
            text, eng_used = await ocr._run_ocr(img_data, lang, eff_fmt["default_engine"])

            pages = [{"page": 1, "filename": image.filename, "text": text, "engine": eng_used}]
            stem  = Path(image.filename).stem
            now   = datetime.now()
            ts    = now.strftime("%Y%m%d_%H%M%S")

            if fmt == "json":
                output   = format_json(pages, chapter=stem, language=lang)
                ext, mime = "json", "application/json"
            elif fmt == "md":
                output   = format_markdown(pages, chapter=stem)
                ext, mime = "md", "text/markdown"
            elif fmt == "srt":
                output   = format_srt(pages)
                ext, mime = "srt", "text/plain"
            else:
                output   = format_txt(pages, chapter=stem)
                ext, mime = "txt", "text/plain"

            flag = LANG_FLAGS.get(lang, "🌐")
            embed = discord.Embed(
                title=f"{flag} OCR → {output_format.name}",
                description=f"**{image.filename}**",
                color=0x6C63FF, timestamp=now)
            embed.add_field(name="📋 Format",  value=output_format.name, inline=True)
            embed.add_field(name="🔧 Engine",  value=eng_used,           inline=True)
            embed.add_field(name="📊 Chars",   value=f"`{len(text):,}`", inline=True)
            embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")

            buf = io.BytesIO(output.encode("utf-8")); buf.seek(0)
            fname = f"ocr_{stem}_{ts}.{ext}"
            await interaction.followup.send(embed=embed, file=discord.File(buf, filename=fname))

        except Exception as e:
            cooldowns.clear(user_id)
            await interaction.followup.send(embed=self._error("❌", f"```{str(e)[:400]}```"))
        finally:
            ocr_queue.release()

    # ── /ocr-deskew ───────────────────────────────────────
    @app_commands.command(name="ocr-deskew",
        description="📐 Auto-fix tilted image then OCR")
    @app_commands.describe(
        image="Manga page that may be rotated/tilted",
        language="Language",
    )
    @app_commands.choices(language=[
        app_commands.Choice(name="🇯🇵 Japanese",   value="jpn"),
        app_commands.Choice(name="🇰🇷 Korean",     value="kor"),
        app_commands.Choice(name="🇨🇳 Chinese",    value="chi_sim"),
        app_commands.Choice(name="🇬🇧 English",    value="eng"),
        app_commands.Choice(name="🌐 Auto",         value="auto"),
    ])
    async def ocr_deskew(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        language: Optional[app_commands.Choice[str]] = None,
    ):
        guild_id = interaction.guild_id or 0
        user_id  = interaction.user.id
        if rem := cooldowns.check(user_id, settings.get_guild(guild_id).get("cooldown_seconds",10)):
            await interaction.response.send_message(
                embed=self._warn("⏳ Cooldown", f"Wait **{rem:.1f}s**"), ephemeral=True); return

        lang = language.value if language else "auto"
        await interaction.response.defer(thinking=True)
        cooldowns.stamp(user_id)

        ocr = self._ocr_cog()
        if not ocr:
            await interaction.followup.send(embed=self._error("❌","OCR not loaded")); return

        await ocr_queue.acquire()
        try:
            raw = await ocr._download_image(image.url)
            loop = asyncio.get_running_loop()

            # Deskew first
            deskewed = await loop.run_in_executor(None, deskew_image, raw)
            deskew_applied = deskewed != raw

            # Then OCR on deskewed image
            eff_dsk = settings.effective(guild_id, user_id)
            text, eng_used = await ocr._run_ocr(deskewed, lang, eff_dsk["default_engine"], preprocess=True)
            conf = confidence_score(text)

            now  = datetime.now()
            flag = LANG_FLAGS.get(lang, "🌐")
            embed = discord.Embed(
                title=f"📐 Deskew + OCR — {image.filename}",
                color=0x6C63FF, timestamp=now)
            preview = text.strip()[:1000] + "…" if len(text.strip())>1000 else text.strip()
            if preview:
                embed.add_field(name="📝 Extracted Text",
                                value=f"```{preview}```", inline=False)
            else:
                embed.add_field(name="⚠️ No Text", value="No readable text found.", inline=False)
            embed.add_field(name="📐 Deskew",        value="✅ Applied" if deskew_applied else "➖ Not needed", inline=True)
            embed.add_field(name="📊 Confidence",    value=f"{conf['label']} ({conf['score']}%)", inline=True)
            embed.add_field(name="🔧 Engine",        value=eng_used, inline=True)
            embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")

            buf = io.BytesIO(text.encode("utf-8")); buf.seek(0)
            fname = f"deskew_{Path(image.filename).stem}_{now.strftime('%Y%m%d_%H%M%S')}.txt"
            await interaction.followup.send(embed=embed, file=discord.File(buf, filename=fname))

        except Exception as e:
            cooldowns.clear(user_id)
            await interaction.followup.send(embed=self._error("❌", f"```{str(e)[:400]}```"))
        finally:
            ocr_queue.release()

    # ── /ocr-confidence ───────────────────────────────────
    @app_commands.command(name="ocr-confidence",
        description="🎯 Show OCR quality score for an image")
    @app_commands.describe(
        image="Image to analyze",
        language="Language of the text",
    )
    @app_commands.choices(language=[
        app_commands.Choice(name="🇯🇵 Japanese",   value="jpn"),
        app_commands.Choice(name="🇰🇷 Korean",     value="kor"),
        app_commands.Choice(name="🇨🇳 Chinese",    value="chi_sim"),
        app_commands.Choice(name="🇬🇧 English",    value="eng"),
        app_commands.Choice(name="🌐 Auto",         value="auto"),
    ])
    async def ocr_confidence(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        language: Optional[app_commands.Choice[str]] = None,
    ):
        guild_id = interaction.guild_id or 0
        user_id  = interaction.user.id
        if rem := cooldowns.check(user_id, settings.get_guild(guild_id).get("cooldown_seconds",10)):
            await interaction.response.send_message(
                embed=self._warn("⏳ Cooldown", f"Wait **{rem:.1f}s**"), ephemeral=True); return

        await interaction.response.defer(thinking=True)
        cooldowns.stamp(user_id)

        ocr = self._ocr_cog()
        if not ocr:
            await interaction.followup.send(embed=self._error("❌","OCR not loaded")); return

        lang = language.value if language else "auto"

        await ocr_queue.acquire()
        try:
            img_data = await ocr._download_image(image.url)
            # Run all 3 engines and score each
            results = []
            for eng in ["claude_vision", "manga_ocr", "easyocr", "tesseract"]:
                try:
                    t, u = await ocr._run_ocr(img_data, lang, eng)
                    c = confidence_score(t)
                    results.append((eng, t, c))
                except Exception:
                    results.append((eng, "", {"score":0,"label":"Error","issues":[]}))

            now  = datetime.now()
            best = max(results, key=lambda x: x[2]["score"])
            embed = discord.Embed(
                title=f"🎯 OCR Confidence Report — {image.filename}",
                color=0x9C27B0, timestamp=now)
            icons = {"tesseract":"⚡","easyocr":"🎯","manga_ocr":"🔮","claude_vision":"✨"}
            for eng, text, conf in results:
                issues = ", ".join(conf.get("issues",[])) or "None"
                embed.add_field(
                    name=f"{icons.get(eng,'🔍')} {eng}",
                    value=f"Score: **{conf['score']}%** {conf['label']}\nIssues: {issues}",
                    inline=False)
            embed.add_field(name="🏆 Best Engine",
                            value=f"`{best[0]}` — {best[2]['score']}%", inline=False)
            embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            cooldowns.clear(user_id)
            await interaction.followup.send(embed=self._error("❌", f"```{str(e)[:400]}```"))
        finally:
            ocr_queue.release()


async def setup(bot):
    await bot.add_cog(AdvancedOCR(bot))
