"""
Usage Analytics Cog
/usage         — guild stats breakdown
/usage-global  — bot-wide stats (owner)
Interactive settings panel with buttons
"""

import discord
from discord.ext import commands
from discord import app_commands
import json, logging
from datetime import datetime
from pathlib import Path

from core.config import settings, ocr_queue, stats_buffer

log = logging.getLogger("Usage")


class SettingsPanel(discord.ui.View):
    """Interactive button panel for quick settings."""

    def __init__(self, interaction: discord.Interaction, guild_settings: dict):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.gs = guild_settings
        self._refresh_labels()

    def _refresh_labels(self):
        self.toggle_drive.label  = f"☁️ Drive: {'ON' if self.gs.get('auto_drive') else 'OFF'}"
        self.toggle_drive.style  = discord.ButtonStyle.success if self.gs.get('auto_drive') else discord.ButtonStyle.secondary
        self.lang_btn.label      = f"🌐 Lang: {self.gs.get('default_language','auto').upper()}"
        self.engine_btn.label    = f"🔧 Engine: {self.gs.get('default_engine','easyocr')}"
        self.cooldown_btn.label  = f"⏳ CD: {self.gs.get('cooldown_seconds',10)}s"

    @discord.ui.button(label="☁️ Drive: OFF", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_drive(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_val = not self.gs.get("auto_drive", False)
        await settings.set_guild(interaction.guild_id, "auto_drive", new_val)
        self.gs["auto_drive"] = new_val
        self._refresh_labels()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🌐 Lang: auto", style=discord.ButtonStyle.primary, row=0)
    async def lang_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        langs = ["auto","jpn","kor","chi_sim","chi_tra","eng","ara"]
        cur   = self.gs.get("default_language","auto")
        nxt   = langs[(langs.index(cur)+1) % len(langs)] if cur in langs else "auto"
        await settings.set_guild(interaction.guild_id, "default_language", nxt)
        self.gs["default_language"] = nxt
        self._refresh_labels()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🔧 Engine: easyocr", style=discord.ButtonStyle.primary, row=0)
    async def engine_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        engines = ["claude_vision","easyocr","tesseract","manga_ocr"]
        cur     = self.gs.get("default_engine","easyocr")
        nxt     = engines[(engines.index(cur)+1) % len(engines)] if cur in engines else "claude_vision"
        await settings.set_guild(interaction.guild_id, "default_engine", nxt)
        self.gs["default_engine"] = nxt
        self._refresh_labels()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="⏳ CD: 10s", style=discord.ButtonStyle.secondary, row=1)
    async def cooldown_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        steps = [0, 5, 10, 15, 30, 60]
        cur   = self.gs.get("cooldown_seconds", 10)
        nxt   = steps[(steps.index(cur)+1) % len(steps)] if cur in steps else 10
        await settings.set_guild(interaction.guild_id, "cooldown_seconds", nxt)
        self.gs["cooldown_seconds"] = nxt
        self._refresh_labels()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🔄 Reset All", style=discord.ButtonStyle.danger, row=1)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("🚫 Manage Server required.", ephemeral=True); return
        await settings.reset_guild(interaction.guild_id)
        self.gs = settings.get_guild(interaction.guild_id)
        self._refresh_labels()
        await interaction.response.edit_message(
            embed=discord.Embed(title="✅ Settings Reset", description="All settings restored to defaults.",
                                color=0x00CC66),
            view=self)

    @discord.ui.button(label="✅ Done", style=discord.ButtonStyle.success, row=1)
    async def done_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except Exception:
            pass


class UsageCog(commands.Cog, name="Usage"):

    def __init__(self, bot):
        self.bot = bot

    # ── /usage ────────────────────────────────────────────
    @app_commands.command(name="usage",
        description="📈 View this server's OCR usage breakdown")
    async def usage(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or 0
        data     = stats_buffer.data
        gs       = settings.get_guild(guild_id)
        gk       = str(guild_id)
        now      = datetime.now()

        guild_pages  = data.get("guilds", {}).get(gk, 0)
        total_pages  = data.get("total_pages", 0)
        cache_rate   = stats_buffer.cache_hit_rate()
        history      = settings.get_history(guild_id=guild_id, limit=50)

        # Language breakdown for this guild
        lang_hist = {}
        for e in history:
            l = e.get("language","?")
            lang_hist[l] = lang_hist.get(l, 0) + 1
        top_langs = sorted(lang_hist.items(), key=lambda x:-x[1])[:3]

        # Quota
        quota_cog = self.bot.get_cog("Quota")
        quota_info = ""
        if quota_cog:
            g_quota = quota_cog._guild(guild_id)
            limit   = g_quota.get("limit", 500)
            used    = g_quota.get("used",  0)
            quota_info = f"**{used:,}** / **{'∞' if limit==0 else f'{limit:,}'}** today"

        embed = discord.Embed(
            title=f"📈 Usage — {interaction.guild.name if interaction.guild else 'Server'}",
            color=0x6C63FF, timestamp=now)

        embed.add_field(name="📄 Pages This Server", value=f"`{guild_pages:,}`", inline=True)
        embed.add_field(name="🌍 Bot Total Pages",   value=f"`{total_pages:,}`", inline=True)
        embed.add_field(name="🗄️ Cache Hit Rate",    value=f"`{cache_rate}%`",   inline=True)
        embed.add_field(name="⚙️ Queue Now",         value=f"`{ocr_queue.queue_size}` jobs", inline=True)
        if quota_info:
            embed.add_field(name="📊 Daily Quota",   value=quota_info, inline=True)

        if top_langs:
            lang_str = "\n".join(f"`{l.upper()}`: {c}" for l,c in top_langs)
            embed.add_field(name="🌐 Top Languages (this server)", value=lang_str, inline=False)

        # Recent activity count
        embed.add_field(name="🕐 Scans in History", value=f"`{len(history)}`", inline=True)

        # Server settings summary
        embed.add_field(name="⚙️ Current Settings",
            value=(f"Lang: `{gs['default_language']}` | "
                   f"Engine: `{gs['default_engine']}` | "
                   f"CD: `{gs['cooldown_seconds']}s` | "
                   f"Drive: `{'on' if gs['auto_drive'] else 'off'}`"),
            inline=False)
        embed.set_footer(text=f"Manga OCR Bot v3.1 • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed)

    # ── /settings-panel — interactive buttons ─────────────
    @app_commands.command(name="settings-panel",
        description="⚙️ Interactive server settings panel (Admin)")
    async def settings_panel(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True); return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("🚫 Requires Manage Server.", ephemeral=True); return

        gs  = settings.get_guild(interaction.guild_id)
        now = datetime.now()
        embed = discord.Embed(
            title="⚙️ Server Settings Panel",
            description="Use the buttons below to toggle settings instantly.\nChanges are saved immediately.",
            color=0x6C63FF, timestamp=now)
        embed.add_field(name="🌐 Language",  value=f"`{gs['default_language']}`", inline=True)
        embed.add_field(name="🔧 Engine",    value=f"`{gs['default_engine']}`",   inline=True)
        embed.add_field(name="⏳ Cooldown",  value=f"`{gs['cooldown_seconds']}s`",inline=True)
        embed.add_field(name="☁️ Auto Drive",value="✅" if gs["auto_drive"] else "❌", inline=True)
        embed.set_footer(text=f"Manga OCR Bot v3.1 • {now.strftime('%B')} {now.year}")

        view = SettingsPanel(interaction, gs)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(UsageCog(bot))
