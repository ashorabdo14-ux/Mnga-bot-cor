"""
Translation Cog
/translate — translate last OCR result or custom text
/ocr-translate — OCR + auto-translate in one step
"""

import discord
from discord.ext import commands
from discord import app_commands
import io, logging
from datetime import datetime
from typing import Optional
from pathlib import Path

from core.config   import settings, cooldowns, ocr_queue, preprocessor
from core.translator import translate_text, TARGET_LANG_OPTIONS, SOURCE_DETECT

log = logging.getLogger("Translate")

TARGET_CHOICES = [
    app_commands.Choice(name=name, value=code)
    for name, code in TARGET_LANG_OPTIONS
]

LANG_FLAGS = {"jpn":"🇯🇵","kor":"🇰🇷","chi_sim":"🇨🇳","chi_tra":"🇹🇼",
              "eng":"🇬🇧","ara":"🇸🇦","auto":"🌐"}


class TranslateCommands(commands.Cog, name="Translate"):

    def __init__(self, bot):
        self.bot = bot
        # Per-user last OCR text cache for /translate
        self._last_text: dict = {}   # user_id → text

    def store_last(self, user_id: int, text: str):
        self._last_text[user_id] = text[:10000]

    # ── /translate ────────────────────────────────────────
    @app_commands.command(name="translate",
        description="🌍 Translate your last OCR result (or paste text)")
    @app_commands.describe(
        target="Language to translate into",
        text="Text to translate (leave empty to translate your last OCR scan)",
    )
    @app_commands.choices(target=TARGET_CHOICES)
    async def translate_slash(
        self,
        interaction: discord.Interaction,
        target: app_commands.Choice[str],
        text: Optional[str] = None,
    ):
        user_id = interaction.user.id
        src_text = text or self._last_text.get(user_id)

        if not src_text:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❓ Nothing to Translate",
                    description=(
                        "No OCR result found.\n"
                        "First run `/ocr` or `/zip`, then `/translate`.\n"
                        "Or paste text directly: `/translate text:your text here`"
                    ),
                    color=0xFF9900,
                ), ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            translated = await translate_text(src_text[:5000], target.value)
            if not translated:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Translation Failed",
                        description="Could not reach translation service. Try again later.",
                        color=0xFF4444))
                return

            now   = datetime.now()
            embed = discord.Embed(
                title=f"🌍 Translation → {target.name}",
                color=0x4CAF50,
                timestamp=now,
            )
            preview = translated[:1000] + "…" if len(translated) > 1000 else translated
            embed.add_field(name="📝 Translated Text",
                            value=f"```{preview}```", inline=False)
            embed.add_field(name="📊 Characters",
                            value=f"`{len(translated):,}`", inline=True)
            embed.add_field(name="🎯 Target",
                            value=target.name, inline=True)
            # BUG 8 FIX: was hardcoded `if False` — now correctly checks for DeepL key
            import os as _os
            service = "DeepL ✨" if _os.getenv("DEEPL_API_KEY","").strip() else "Google Translate"
            embed.add_field(name="🔧 Service", value=service, inline=True)
            embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")

            # File attachment
            combined = f"=== Original ===\n{src_text}\n\n=== Translation ({target.value}) ===\n{translated}"
            buf = io.BytesIO(combined.encode("utf-8")); buf.seek(0)
            fname = f"translation_{target.value}_{now.strftime('%Y%m%d_%H%M%S')}.txt"
            await interaction.followup.send(
                embed=embed, file=discord.File(buf, filename=fname))

        except Exception as e:
            log.error(f"Translation error: {e}", exc_info=True)
            await interaction.followup.send(
                embed=discord.Embed(title="❌ Error",
                    description=f"```{str(e)[:400]}```", color=0xFF4444))

    # ── /ocr-translate — one-step OCR + translate ─────────
    @app_commands.command(name="ocr-translate",
        description="🔍🌍 Extract text AND translate in one step")
    @app_commands.describe(
        image="Manga/manhwa image",
        source_language="Language in the image",
        target="Translate into this language",
    )
    @app_commands.choices(
        source_language=[
            app_commands.Choice(name="🇯🇵 Japanese",            value="jpn"),
            app_commands.Choice(name="🇰🇷 Korean",              value="kor"),
            app_commands.Choice(name="🇨🇳 Chinese Simplified",  value="chi_sim"),
            app_commands.Choice(name="🇹🇼 Chinese Traditional", value="chi_tra"),
            app_commands.Choice(name="🌐 Auto-Detect",           value="auto"),
        ],
        target=TARGET_CHOICES,
    )
    async def ocr_translate_slash(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        source_language: app_commands.Choice[str],
        target: app_commands.Choice[str],
    ):
        guild_id = interaction.guild_id or 0
        user_id  = interaction.user.id

        if guild_id and not settings.channel_allowed(guild_id, interaction.channel_id or 0):
            await interaction.response.send_message(
                embed=discord.Embed(title="🚫 Wrong Channel",
                    description="OCR not enabled here.", color=0xFF4444), ephemeral=True); return

        gs = settings.get_guild(guild_id)
        if rem := cooldowns.check(user_id, gs.get("cooldown_seconds", 10)):
            await interaction.response.send_message(
                embed=discord.Embed(title="⏳ Cooldown",
                    description=f"Wait **{rem:.1f}s**", color=0xFF9900), ephemeral=True); return

        if Path(image.filename).suffix.lower() not in {".jpg",".jpeg",".png",".webp",".bmp",".gif",".tiff"}:
            await interaction.response.send_message(
                embed=discord.Embed(title="❌ Invalid File",
                    description="Please attach an image.", color=0xFF4444), ephemeral=True); return

        await interaction.response.defer(thinking=True)
        cooldowns.stamp(user_id)

        ocr_cog = self.bot.get_cog("OCR")
        if not ocr_cog:
            await interaction.followup.send(
                embed=discord.Embed(title="❌ OCR not loaded", color=0xFF4444)); return

        await ocr_queue.acquire()
        try:
            img_data = await ocr_cog._download_image(image.url)
            # BUG 9 FIX: was hardcoded "easyocr" — now uses the user's effective engine
            eff = settings.effective(guild_id, user_id)
            ocr_text, eng_used = await ocr_cog._run_ocr(
                img_data, source_language.value, eff["default_engine"])

            # Translate
            src_code = SOURCE_DETECT.get(source_language.value)
            translated = await translate_text(ocr_text, target.value, src_code)
            self.store_last(user_id, ocr_text)

            flag = LANG_FLAGS.get(source_language.value, "🌐")
            now  = datetime.now()
            embed = discord.Embed(
                title=f"{flag} OCR + 🌍 Translation — {image.filename}",
                color=0x6C63FF, timestamp=now)

            # OCR preview
            ocr_preview = ocr_text.strip()[:500] + "…" if len(ocr_text.strip()) > 500 else ocr_text.strip()
            if ocr_preview:
                embed.add_field(name=f"{flag} Original Text",
                                value=f"```{ocr_preview}```", inline=False)
            else:
                embed.add_field(name="⚠️ No Text Detected",
                                value="No readable text in the image.", inline=False)

            # Translation preview
            if translated:
                tr_preview = translated[:500] + "…" if len(translated) > 500 else translated
                embed.add_field(name=f"🌍 {target.name}",
                                value=f"```{tr_preview}```", inline=False)
            else:
                embed.add_field(name="⚠️ Translation Failed",
                                value="Could not translate. See .txt for OCR result.", inline=False)

            embed.add_field(name="🔧 Engine",    value=eng_used, inline=True)
            embed.add_field(name="📊 OCR Chars", value=f"`{len(ocr_text):,}`", inline=True)
            embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")

            # File
            content = (
                f"=== OCR ({source_language.value}) ===\n{ocr_text}\n\n"
                + (f"=== Translation ({target.value}) ===\n{translated}" if translated else "")
            )
            buf = io.BytesIO(content.encode("utf-8")); buf.seek(0)
            fname = f"ocr_trans_{Path(image.filename).stem}_{now.strftime('%Y%m%d_%H%M%S')}.txt"
            await interaction.followup.send(embed=embed, file=discord.File(buf, filename=fname))

        except Exception as e:
            log.error(f"OCR+Translate error: {e}", exc_info=True)
            cooldowns.clear(user_id)
            await interaction.followup.send(
                embed=discord.Embed(title="❌ Failed",
                    description=f"```{str(e)[:400]}```", color=0xFF4444))
        finally:
            ocr_queue.release()


async def setup(bot):
    await bot.add_cog(TranslateCommands(bot))
