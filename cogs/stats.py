"""Stats Cog v3 — shows live queue, cache hit rate, Drive status"""

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
import logging

from core.config import ocr_queue, stats_buffer

log = logging.getLogger("Stats")


class Stats(commands.Cog, name="Stats"):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="stats", description="📊 View bot statistics")
    async def stats_slash(self, interaction: discord.Interaction):
        data   = stats_buffer.data
        now    = datetime.now()

        total_images = data.get("total_images", 0)
        total_pages  = data.get("total_pages",  0)
        guilds       = len(self.bot.guilds)
        languages    = data.get("languages", {})
        cache_rate   = stats_buffer.cache_hit_rate()

        embed = discord.Embed(title="📊 Manga OCR Bot — Statistics",
                              color=0x6C63FF, timestamp=now)

        embed.add_field(name="🖼️ Images Processed", value=f"`{total_images:,}`", inline=True)
        embed.add_field(name="📄 Pages Scanned",     value=f"`{total_pages:,}`",  inline=True)
        embed.add_field(name="🏠 Servers",           value=f"`{guilds:,}`",       inline=True)

        # FIX #12: live queue + cache rate
        embed.add_field(name="⚙️ Queue Now",    value=f"`{ocr_queue.queue_size}` jobs", inline=True)
        embed.add_field(name="🗄️ Cache Hits",   value=f"`{cache_rate}%`",               inline=True)

        ocr_cog = self.bot.get_cog("OCR")
        cache_entries = len(ocr_cog._cache) if ocr_cog else 0
        embed.add_field(name="💾 Cache Size",   value=f"`{cache_entries}` entries", inline=True)

        if languages:
            lang_display = "\n".join(
                f"`{k.upper()}`: {v:,}" for k,v in
                sorted(languages.items(), key=lambda x: -x[1])[:5])
            embed.add_field(name="🌐 Top Languages", value=lang_display, inline=False)

        drive_cog = self.bot.get_cog("GDrive")
        drive_st  = "✅ Connected" if (drive_cog and drive_cog.is_available) else "❌ Offline"
        embed.add_field(name="☁️ Google Drive", value=drive_st, inline=True)
        embed.add_field(name="🤖 Version",      value="`v3.1`",  inline=True)
        embed.add_field(name="📅 Last Scan",    value=data.get("last_updated","—"), inline=True)

        embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Stats(bot))
