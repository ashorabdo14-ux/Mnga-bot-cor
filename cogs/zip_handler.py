"""
ZIP Handler Cog — v3.1
+ Parallel page processing (asyncio.gather with semaphore)
+ Optional translation per page
+ Leaderboard + quota integration
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio, io, zipfile, logging, time
from pathlib import Path
from datetime import datetime
from typing import Optional

from core.config      import settings, cooldowns, ocr_queue, stats_buffer, drive_limiter
from core.translator  import translate_text, SOURCE_DETECT

log = logging.getLogger("ZIP")

IMAGE_EXTENSIONS  = {".jpg",".jpeg",".png",".webp",".bmp",".tiff",".gif"}
MAX_ZIP_SIZE_MB   = 50
DRIVE_MAX_TEXT_MB = 15
PARALLEL_WORKERS  = 4   # max concurrent page OCR tasks


class ZipProgress:
    def __init__(self, total):
        self.total    = total
        self.current  = 0
        self.errors   = 0
        self.start_ts = time.monotonic()
        self._lock    = asyncio.Lock()

    async def increment(self, error=False):
        async with self._lock:
            self.current += 1
            if error: self.errors += 1

    @property
    def percent(self):
        return int(self.current / self.total * 100) if self.total else 0

    def eta_str(self):
        if self.current == 0: return "calculating..."
        elapsed   = time.monotonic() - self.start_ts
        rate      = self.current / elapsed
        remaining = (self.total - self.current) / rate if rate > 0 else 0
        return f"~{int(remaining//60)}m {int(remaining%60)}s" if remaining >= 60 else f"~{int(remaining)}s"

    def bar(self, length=18):
        filled = int(length * self.current / self.total) if self.total else 0
        return "`[" + "█"*filled + "░"*(length-filled) + f"]` {self.percent}%"


class ZipHandler(commands.Cog, name="ZipHandler"):

    def __init__(self, bot):
        self.bot = bot
        self._guild_locks: dict = {}

    def _get_guild_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._guild_locks:
            self._guild_locks[guild_id] = asyncio.Lock()
        return self._guild_locks[guild_id]

    def _sort_images(self, files):
        def key(name):
            parts, cur = [], ""
            for c in Path(name).stem:
                if c.isdigit(): cur += c
                else:
                    if cur: parts.append(int(cur)); cur = ""
            if cur: parts.append(int(cur))
            return parts or [0]
        return sorted(files, key=key)

    async def _progress_edit(self, msg, prog, archive_name, phase=""):
        embed = discord.Embed(title="⚙️ Processing ZIP...",
                              description=f"**{archive_name}**{' • ' + phase if phase else ''}",
                              color=0x6C63FF)
        embed.add_field(name="📊 Progress",
                        value=f"{prog.bar()}\n`{prog.current}/{prog.total}` pages", inline=False)
        embed.add_field(name="⏱️ ETA",    value=prog.eta_str(),  inline=True)
        embed.add_field(name="❌ Errors", value=str(prog.errors), inline=True)
        embed.set_footer(text=f"Manga OCR Bot v3.1 • {datetime.now().strftime('%B %Y')}")
        try: await msg.edit(embed=embed)
        except Exception: pass

    @app_commands.command(name="zip", description="📦 Extract text from a full manga chapter ZIP")
    @app_commands.describe(
        archive="Your .zip file of manga images",
        language="Language of the text",
        engine="OCR engine",
        output_format="Output file format (default: txt)",
        upload_drive="Upload combined result to Google Drive",
        chapter_name="Chapter name/number",
        preprocess="Enhance images before OCR",
        translate_to="Auto-translate each page to this language (optional)",
    )
    @app_commands.choices(
        language=[
            app_commands.Choice(name="🇯🇵 Japanese",            value="jpn"),
            app_commands.Choice(name="🇰🇷 Korean",              value="kor"),
            app_commands.Choice(name="🇨🇳 Chinese Simplified",  value="chi_sim"),
            app_commands.Choice(name="🇹🇼 Chinese Traditional", value="chi_tra"),
            app_commands.Choice(name="🇬🇧 English",             value="eng"),
            app_commands.Choice(name="🌐 Auto-Detect",           value="auto"),
        ],
        engine=[
            app_commands.Choice(name="⚡ Fast (Tesseract)",         value="tesseract"),
            app_commands.Choice(name="🎯 Accurate (EasyOCR)",       value="easyocr"),
            app_commands.Choice(name="🔮 Best (Manga-OCR)",         value="manga_ocr"),
            app_commands.Choice(name="✨ Perfect (Claude Vision)",  value="claude_vision"),
        ],
        output_format=[
            app_commands.Choice(name="📄 TXT — plain text (default)",   value="txt"),
            app_commands.Choice(name="📝 Markdown — for Notion/Obsidian", value="md"),
            app_commands.Choice(name="📊 JSON — structured data",        value="json"),
            app_commands.Choice(name="🎬 SRT — subtitles for video",     value="srt"),
        ],
        translate_to=[
            app_commands.Choice(name="🚫 No translation",    value="none"),
            app_commands.Choice(name="🇸🇦 Arabic",           value="ar"),
            app_commands.Choice(name="🇬🇧 English",          value="en"),
            app_commands.Choice(name="🇫🇷 French",           value="fr"),
            app_commands.Choice(name="🇩🇪 German",           value="de"),
            app_commands.Choice(name="🇪🇸 Spanish",          value="es"),
            app_commands.Choice(name="🇷🇺 Russian",          value="ru"),
        ],
    )
    async def zip_slash(
        self,
        interaction: discord.Interaction,
        archive:       discord.Attachment,
        language:      Optional[app_commands.Choice[str]] = None,
        engine:        Optional[app_commands.Choice[str]] = None,
        output_format: Optional[app_commands.Choice[str]] = None,
        upload_drive:  bool = True,
        chapter_name:  Optional[str] = None,
        preprocess:    bool = True,
        translate_to:  Optional[app_commands.Choice[str]] = None,
    ):
        guild_id   = interaction.guild_id or 0
        user_id    = interaction.user.id
        channel_id = interaction.channel_id or 0

        if guild_id and not settings.channel_allowed(guild_id, channel_id):
            await interaction.response.send_message(
                embed=discord.Embed(title="🚫 Wrong Channel",
                    description="OCR not enabled here.", color=0xFF4444), ephemeral=True); return

        gs = settings.get_guild(guild_id)
        if rem := cooldowns.check(user_id, gs.get("cooldown_seconds", 10)):
            await interaction.response.send_message(
                embed=discord.Embed(title="⏳ Cooldown",
                    description=f"Wait **{rem:.1f}s**", color=0xFF9900), ephemeral=True); return

        if not archive.filename.lower().endswith(".zip"):
            await interaction.response.send_message(
                embed=discord.Embed(title="❌ Not a ZIP",
                    description="Upload a `.zip` file.", color=0xFF4444), ephemeral=True); return

        size_mb = archive.size / 1024 / 1024
        if size_mb > MAX_ZIP_SIZE_MB:
            await interaction.response.send_message(
                embed=discord.Embed(title="❌ Too Large",
                    description=f"ZIP must be under **{MAX_ZIP_SIZE_MB}MB** (yours: {size_mb:.1f}MB).",
                    color=0xFF4444), ephemeral=True); return

        guild_lock = self._get_guild_lock(guild_id)
        if guild_lock.locked():
            await interaction.response.send_message(
                embed=discord.Embed(title="⏳ Already Processing",
                    description="Already processing a ZIP for this server. Please wait.",
                    color=0xFF9900), ephemeral=True); return

        eff       = settings.effective(guild_id, user_id)
        lang      = language.value if language else eff["default_language"]
        eng       = engine.value   if engine   else eff["default_engine"]
        fmt       = output_format.value if output_format else "txt"
        chap      = chapter_name   or Path(archive.filename).stem
        max_pages = gs.get("max_zip_pages", 200)
        tgt_lang  = translate_to.value if translate_to and translate_to.value != "none" else None

        await interaction.response.defer(thinking=True)
        cooldowns.stamp(user_id)

        init_embed = discord.Embed(title="📦 ZIP Received — Starting...",
                                   description=f"**{archive.filename}** ({size_mb:.1f}MB)",
                                   color=0x6C63FF)
        prog_msg = await interaction.followup.send(embed=init_embed, wait=True)

        ocr_cog = self.bot.get_cog("OCR")
        if not ocr_cog:
            await prog_msg.edit(embed=discord.Embed(title="❌ OCR not loaded", color=0xFF4444)); return

        async with guild_lock:
            await ocr_queue.acquire()
            try:
                # Streaming download
                session = await ocr_cog.get_session()
                zip_chunks = []
                async with session.get(archive.url) as resp:
                    if resp.status != 200:
                        raise ValueError(f"Download failed HTTP {resp.status}")
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        zip_chunks.append(chunk)
                zip_data = b"".join(zip_chunks); del zip_chunks

                with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                    all_names = zf.namelist()
                    img_files = [
                        f for f in all_names
                        if Path(f).suffix.lower() in IMAGE_EXTENSIONS
                        and not Path(f).name.startswith(".")
                        and "__MACOSX" not in f
                    ]
                    img_files = self._sort_images(img_files)[:max_pages]

                    if not img_files:
                        await prog_msg.edit(embed=discord.Embed(title="❌ No Images",
                            description="No supported images in ZIP.", color=0xFF4444)); return

                    prog = ZipProgress(len(img_files))
                    # BUG 10 FIX: was `page_imgs = {fname: zf.read(fname) for fname in img_files}`
                    # which loaded ALL decompressed images into RAM at once before processing.
                    # Now: keep compressed zip_data and read each page lazily during processing.
                    # Each worker gets its own ZipFile instance (thread-safe, read-only).
                    zip_bytes = zip_data  # compressed bytes — much smaller than all decompressed pages
                del zip_data   # free the original reference

                # ── Quota check before starting ───────────────
                quota_cog = self.bot.get_cog("Quota")
                if quota_cog and not quota_cog.check(guild_id, user_id, len(img_files)):
                    await prog_msg.edit(embed=discord.Embed(
                        title="📊 Daily Quota Reached",
                        description=f"This server's daily limit would be exceeded by {len(img_files)} pages. "
                                    f"Use `/quota` to check remaining quota.",
                        color=0xFF9900))
                    return

                # ── Parallel OCR with semaphore ────────────────
                sem     = asyncio.Semaphore(PARALLEL_WORKERS)
                results = {}

                async def process_page(idx: int, fname: str):
                    async with sem:
                        try:
                            loop = asyncio.get_running_loop()
                            # Lazy read: each worker opens its own ZipFile instance (thread-safe)
                            def _read_page():
                                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as _zf:
                                    return _zf.read(fname)
                            img_data = await loop.run_in_executor(None, _read_page)
                            t, e = await ocr_cog._run_ocr(img_data, lang, eng, preprocess)
                            results[idx] = (t, e, None)
                        except Exception as ex:
                            results[idx] = ("", eng, str(ex))
                        await prog.increment(error=(results[idx][2] is not None))
                        if prog.current % 5 == 0 or prog.current == prog.total:
                            await self._progress_edit(prog_msg, prog, archive.filename, "OCR")

                tasks = [process_page(i, fname)
                         for i, fname in enumerate(img_files, 1)]
                await asyncio.gather(*tasks)
                del zip_bytes  # all pages processed, free compressed zip data

                # ── Optional translation ────────────────────────
                trans_results = {}
                src_code = SOURCE_DETECT.get(lang)
                if tgt_lang:
                    await self._progress_edit(prog_msg, prog, archive.filename, "Translating…")
                    for idx in sorted(results.keys()):
                        text = results[idx][0]
                        if text.strip():
                            try:
                                translated = await translate_text(text, tgt_lang, src_code)
                                trans_results[idx] = translated
                            except Exception:
                                trans_results[idx] = None
                        await asyncio.sleep(0.1)  # rate limit

                # ── Assemble output (format-aware) ────────────
                from core.image_utils import format_txt, format_markdown, format_json, format_srt
                pages_data = []
                for idx, fname in enumerate(img_files, 1):
                    text, eng_used, err = results.get(idx, ("","","error"))
                    pages_data.append({
                        "page": idx,
                        "filename": Path(fname).name,
                        "text": "" if err else text.strip(),
                        "translation": trans_results.get(idx) if tgt_lang else None,
                        "error": err,
                        "engine": eng_used,
                    })

                fmt_map = {
                    "txt":  (format_txt,      f"{chap}_{lang}.txt",  "text/plain"),
                    "md":   (format_markdown, f"{chap}_{lang}.md",   "text/markdown"),
                    "json": (format_json,     f"{chap}_{lang}.json", "application/json"),
                    "srt":  (format_srt,      f"{chap}_{lang}.srt",  "text/plain"),
                }
                fmt_fn, txt_name, _ = fmt_map.get(fmt, fmt_map["txt"])
                ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
                txt_name  = txt_name.replace(f"{chap}", f"{chap}_{ts}")
                combined  = fmt_fn(pages_data, chapter=chap) if fmt != "srt" else fmt_fn(pages_data)
                txt_bytes = combined.encode("utf-8")

                fmt_icons = {"txt":"📄","md":"📝","json":"📊","srt":"🎬"}
                final = discord.Embed(title="✅ ZIP Complete!", description=f"**{chap}**",
                                      color=0x00FF88, timestamp=datetime.now())
                final.add_field(name="📄 Pages",   value=str(len(img_files)),   inline=True)
                final.add_field(name="❌ Errors",  value=str(prog.errors),      inline=True)
                final.add_field(name="📝 Chars",   value=f"{len(combined):,}",  inline=True)
                final.add_field(name="🌐 Language",value=lang.upper(),           inline=True)
                final.add_field(name="🔧 Engine",  value=eng,                    inline=True)
                final.add_field(name="📁 Size",    value=f"{len(txt_bytes)/1024:.1f} KB", inline=True)
                final.add_field(name="📂 Format",  value=f"{fmt_icons.get(fmt,'📄')} {fmt.upper()}", inline=True)
                if tgt_lang:
                    final.add_field(name="🌍 Translation", value=tgt_lang.upper(), inline=True)
                final.set_footer(text=f"Manga OCR Bot v3.1 • {datetime.now().strftime('%B %Y')}")
                await prog_msg.edit(embed=final)

                if len(txt_bytes) <= 8 * 1024 * 1024:
                    buf = io.BytesIO(txt_bytes); buf.seek(0)
                    await interaction.followup.send(content="📄 **OCR Result:**",
                                                    file=discord.File(buf, filename=txt_name))

                # Drive
                use_drive = upload_drive or eff.get("auto_drive") or len(txt_bytes) > 8 * 1024 * 1024
                if use_drive:
                    if len(txt_bytes) > DRIVE_MAX_TEXT_MB * 1024 * 1024:
                        await interaction.followup.send(
                            embed=discord.Embed(title="⚠️ Drive Skip",
                                description=f"File too large ({len(txt_bytes)/1024/1024:.1f}MB > {DRIVE_MAX_TEXT_MB}MB).",
                                color=0xFF9900))
                    elif not drive_limiter.check(guild_id):
                        await interaction.followup.send(
                            embed=discord.Embed(title="☁️ Drive Rate Limited",
                                description="Too many uploads this minute.", color=0xFF9900))
                    else:
                        drive_cog = self.bot.get_cog("GDrive")
                        if drive_cog and drive_cog.is_available:
                            link = await drive_cog.upload_text(combined, filename=txt_name)
                            if link and eff.get("notify_drive", True):
                                await interaction.followup.send(
                                    embed=discord.Embed(title="☁️ Uploaded to Google Drive",
                                        description=f"📂 **{txt_name}**\n[🔗 Open]({link})",
                                        color=0x34A853))

                # Leaderboard + quota + history
                lb = self.bot.get_cog("Leaderboard")
                if lb: lb.record_scan(guild_id, user_id, len(img_files))
                q = self.bot.get_cog("Quota")
                if q: q.consume(guild_id, user_id, len(img_files))
                await settings.add_history({"type":"zip","guild_id":guild_id,"user_id":user_id,
                                             "filename":archive.filename,"language":lang,"engine":eng,
                                             "chars":len(combined),"pages":len(img_files)})
                stats_buffer.update(guild_id, lang, pages=len(img_files))

            except zipfile.BadZipFile:
                await prog_msg.edit(embed=discord.Embed(title="❌ Corrupt ZIP",
                    description="The ZIP file is damaged.", color=0xFF4444))
            except Exception as e:
                log.error(f"ZIP error: {e}", exc_info=True)
                await prog_msg.edit(embed=discord.Embed(title="❌ Failed",
                    description=f"```{str(e)[:500]}```", color=0xFF4444))
                cooldowns.clear(user_id)
            finally:
                ocr_queue.release()


async def setup(bot):
    await bot.add_cog(ZipHandler(bot))
