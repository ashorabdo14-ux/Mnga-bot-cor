"""
Leaderboard Cog
- /leaderboard — top scanners in this server (by pages scanned)
- /my-rank — your personal rank and stats
- /feedback — submit feedback to bot owner via webhook
"""

import discord
from discord.ext import commands
from discord import app_commands
import json, logging, os
from datetime import datetime
from pathlib import Path
from typing import Optional
from core.config import settings
from cogs.moderation import send_webhook

log = logging.getLogger("Leaderboard")

FEEDBACK_FILE = Path("data/feedback.json")


def _load_feedback() -> list:
    if FEEDBACK_FILE.exists():
        try: return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return []

def _save_feedback(fb: list):
    FEEDBACK_FILE.parent.mkdir(exist_ok=True)
    tmp = FEEDBACK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(fb[-500:], indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FEEDBACK_FILE)


class LeaderboardCog(commands.Cog, name="Leaderboard"):

    def __init__(self, bot):
        self.bot = bot
        self._user_scans: dict = {}   # guild_id → {user_id → page_count}
        self._load_scans()

    def _load_scans(self):
        scan_file = Path("data/user_scans.json")
        if scan_file.exists():
            try:
                self._user_scans = json.loads(scan_file.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save_scans(self):
        scan_file = Path("data/user_scans.json")
        scan_file.parent.mkdir(exist_ok=True)
        tmp = scan_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._user_scans, indent=2), encoding="utf-8")
        tmp.replace(scan_file)

    def record_scan(self, guild_id: int, user_id: int, pages: int = 1):
        gk = str(guild_id)
        uk = str(user_id)
        if gk not in self._user_scans:
            self._user_scans[gk] = {}
        self._user_scans[gk][uk] = self._user_scans[gk].get(uk, 0) + pages
        self._save_scans()

    def get_guild_top(self, guild_id: int, limit: int = 10) -> list:
        gk  = str(guild_id)
        raw = self._user_scans.get(gk, {})
        return sorted(raw.items(), key=lambda x: -x[1])[:limit]

    def get_user_rank(self, guild_id: int, user_id: int) -> tuple:
        gk    = str(guild_id)
        uk    = str(user_id)
        raw   = self._user_scans.get(gk, {})
        count = raw.get(uk, 0)
        rank  = sum(1 for v in raw.values() if v > count) + 1
        total = len(raw)
        return count, rank, total

    # ── /leaderboard ──────────────────────────────────────
    @app_commands.command(name="leaderboard",
        description="🏆 Top manga scanners in this server")
    @app_commands.describe(limit="Number of entries to show (max 15)")
    async def leaderboard(self, interaction: discord.Interaction, limit: int = 10):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ This command only works in a server.", ephemeral=True); return

        limit = max(1, min(limit, 15))
        top   = self.get_guild_top(interaction.guild_id, limit)
        now   = datetime.now()

        if not top:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🏆 Leaderboard — No Data Yet",
                    description="No one has scanned anything yet. Be the first!",
                    color=0x6C63FF), ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🏆 Top Scanners — {interaction.guild.name}",
            color=0xFFD700, timestamp=now)

        medals = ["🥇","🥈","🥉"] + ["🏅"] * 12
        lines  = []

        # Batch-resolve members from cache first to avoid N fetch_user() API calls
        for i, (uid, pages) in enumerate(top):
            medal  = medals[i]
            member = interaction.guild.get_member(int(uid))
            if member:
                name = member.display_name
            else:
                try:
                    user = await interaction.client.fetch_user(int(uid))
                    name = user.display_name
                except Exception:
                    name = f"User {uid}"
            lines.append(f"{medal} **{name}** — `{pages:,}` pages")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed)

    # ── /my-rank ──────────────────────────────────────────
    @app_commands.command(name="my-rank", description="📊 Your personal scan rank")
    async def my_rank(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Only works in a server.", ephemeral=True); return

        pages, rank, total = self.get_user_rank(interaction.guild_id, interaction.user.id)
        history = settings.get_history(guild_id=interaction.guild_id, user_id=interaction.user.id, limit=5)
        now = datetime.now()

        embed = discord.Embed(
            title=f"📊 {interaction.user.display_name}'s Scan Stats",
            color=0x6C63FF, timestamp=now)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="📄 Pages Scanned",  value=f"`{pages:,}`",               inline=True)
        embed.add_field(name="🏆 Server Rank",    value=f"`#{rank}` of `{total}`",     inline=True)
        embed.add_field(name="📜 Recent Scans",   value=f"`{len(history)}`",           inline=True)

        if history:
            recent = "\n".join(
                f"• `{e.get('filename','?')[:25]}` — {e.get('language','?').upper()}"
                for e in reversed(history[-3:]))
            embed.add_field(name="🕐 Last Scans", value=recent, inline=False)

        embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /feedback ─────────────────────────────────────────
    @app_commands.command(name="feedback",
        description="💬 Send feedback or report a bug to the bot developers")
    @app_commands.describe(
        message="Your feedback, bug report, or feature request",
        rating="Rate the bot (1-5 stars)",
    )
    @app_commands.choices(rating=[
        app_commands.Choice(name="⭐ 1 - Poor",      value=1),
        app_commands.Choice(name="⭐⭐ 2 - Fair",    value=2),
        app_commands.Choice(name="⭐⭐⭐ 3 - Good",  value=3),
        app_commands.Choice(name="⭐⭐⭐⭐ 4 - Great", value=4),
        app_commands.Choice(name="⭐⭐⭐⭐⭐ 5 - Excellent", value=5),
    ])
    async def feedback(
        self,
        interaction: discord.Interaction,
        message: str,
        rating: Optional[app_commands.Choice[int]] = None,
    ):
        if len(message) < 5:
            await interaction.response.send_message(
                "❌ Feedback must be at least 5 characters.", ephemeral=True); return
        if len(message) > 1000:
            message = message[:1000] + "…"

        stars = ("⭐" * rating.value) if rating else "Not rated"
        entry = {
            "user_id":  interaction.user.id,
            "username": str(interaction.user),
            "guild_id": interaction.guild_id or 0,
            "guild":    interaction.guild.name if interaction.guild else "DM",
            "rating":   rating.value if rating else None,
            "message":  message,
            "ts":       datetime.utcnow().isoformat(),
        }
        fb = _load_feedback()
        fb.append(entry)
        _save_feedback(fb)

        # Send to webhook
        wh_msg = (f"💬 **Feedback** from `{interaction.user}` "
                  f"({interaction.guild.name if interaction.guild else 'DM'})\n"
                  f"Rating: {stars}\n> {message}")
        await send_webhook(wh_msg, 0x4CAF50)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Feedback Received!",
                description=(
                    f"Thank you for your feedback!\n\n"
                    f"**Rating:** {stars}\n"
                    f"**Message:** {message[:200]}"
                ),
                color=0x4CAF50,
                timestamp=datetime.now(),
            ), ephemeral=True)


async def setup(bot):
    await bot.add_cog(LeaderboardCog(bot))
