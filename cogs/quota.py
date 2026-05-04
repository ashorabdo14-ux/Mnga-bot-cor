"""
Quota Cog — Per-guild daily scan limits
/quota view — see current usage
/quota set  — admin: set daily limit
Resets at midnight UTC via daily task
"""

import discord
from discord.ext import commands
from discord import app_commands
from discord.ext import tasks
import json, logging, asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.config import settings

log = logging.getLogger("Quota")

QUOTA_FILE   = Path("data/quota.json")
DEFAULT_DAILY = 500   # pages/day per guild, 0 = unlimited


def _load() -> dict:
    if QUOTA_FILE.exists():
        try: return json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {}

def _save(data: dict):
    tmp = QUOTA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(QUOTA_FILE)


class QuotaCog(commands.Cog, name="Quota"):

    def __init__(self, bot):
        self.bot   = bot
        self._data = _load()   # {guild_id: {limit, used, date}}
        self._midnight_reset.start()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _guild(self, guild_id: int) -> dict:
        gk = str(guild_id)
        if gk not in self._data:
            self._data[gk] = {"limit": DEFAULT_DAILY, "used": 0, "date": self._today()}
        # Reset if new day
        if self._data[gk]["date"] != self._today():
            self._data[gk]["used"] = 0
            self._data[gk]["date"] = self._today()
        return self._data[gk]

    def consume(self, guild_id: int, user_id: int, pages: int = 1):
        gk = str(guild_id)
        if gk not in self._data:
            self._data[gk] = {"limit": DEFAULT_DAILY, "used": 0, "date": self._today()}
        if self._data[gk]["date"] != self._today():
            self._data[gk]["used"] = 0
            self._data[gk]["date"] = self._today()
        self._data[gk]["used"] += pages
        _save(self._data)

    def check(self, guild_id: int, user_id: int, pages: int = 1) -> bool:
        gk = str(guild_id)
        if gk not in self._data:
            return True
        g = self._data[gk]
        if g.get("date") != self._today():
            return True  # new day
        limit = g.get("limit", DEFAULT_DAILY)
        if limit == 0: return True
        return g.get("used", 0) + pages <= limit

    @tasks.loop(hours=1)
    async def _midnight_reset(self):
        """Check every hour and reset if day changed."""
        today = self._today()
        reset_count = 0
        for gk in self._data:
            if self._data[gk].get("date","") != today:
                self._data[gk]["used"] = 0
                self._data[gk]["date"] = today
                reset_count += 1
        if reset_count:
            _save(self._data)
            log.info(f"Quota reset for {reset_count} guilds")

    # ── /quota view ───────────────────────────────────────
    @app_commands.command(name="quota", description="📊 View this server's daily scan quota")
    async def quota_view(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or 0
        g        = self._guild(guild_id)
        limit    = g.get("limit", DEFAULT_DAILY)
        used     = g.get("used",  0)
        now      = datetime.now()

        if limit == 0:
            desc = "This server has **unlimited** daily scans."
            pct  = 0
        else:
            pct  = int(used / limit * 100) if limit else 0
            bar_len = 20
            filled  = int(bar_len * pct / 100)
            bar     = "█" * filled + "░" * (bar_len - filled)
            desc    = f"`[{bar}]` {pct}%\n**{used:,}** / **{limit:,}** pages used today"

        color = 0x00CC66 if pct < 75 else (0xFF9900 if pct < 90 else 0xFF4444)
        embed = discord.Embed(title="📊 Daily Scan Quota", description=desc,
                              color=color, timestamp=now)
        embed.add_field(name="📅 Resets", value="Midnight UTC", inline=True)
        embed.add_field(name="📄 Used",   value=f"`{used:,}`",  inline=True)
        embed.add_field(name="📋 Limit",  value=f"`{'∞' if limit==0 else f'{limit:,}'}`", inline=True)
        embed.set_footer(text=f"Manga OCR Bot v3.1 • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed)

    # ── /quota set (admin) ────────────────────────────────
    @app_commands.command(name="quota-set",
        description="⚙️ Set daily page limit for this server (Admin)")
    @app_commands.describe(limit="Daily page limit (0 = unlimited)")
    async def quota_set(self, interaction: discord.Interaction, limit: int):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True); return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("🚫 Requires Manage Server.", ephemeral=True); return
        limit = max(0, min(limit, 100000))
        gk = str(interaction.guild_id)
        if gk not in self._data:
            self._data[gk] = {"limit": limit, "used": 0, "date": self._today()}
        else:
            self._data[gk]["limit"] = limit
        _save(self._data)
        label = "**Unlimited**" if limit == 0 else f"**{limit:,}** pages/day"
        await interaction.response.send_message(
            embed=discord.Embed(title="✅ Quota Updated",
                description=f"Daily limit set to {label}",
                color=0x00CC66), ephemeral=True)

    async def cog_unload(self):
        self._midnight_reset.cancel()


async def setup(bot):
    await bot.add_cog(QuotaCog(bot))
