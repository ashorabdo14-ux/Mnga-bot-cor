"""
Admin & Settings Cog
Server admins: configure OCR defaults, cooldowns, allowed channels, auto-Drive
Users: set personal language/engine preferences
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime
from typing import Optional

from core.config import settings, cooldowns, ocr_queue

log = logging.getLogger("Admin")

LANG_CHOICES = [
    app_commands.Choice(name="🇯🇵 Japanese",           value="jpn"),
    app_commands.Choice(name="🇰🇷 Korean",             value="kor"),
    app_commands.Choice(name="🇨🇳 Chinese Simplified", value="chi_sim"),
    app_commands.Choice(name="🇹🇼 Chinese Traditional",value="chi_tra"),
    app_commands.Choice(name="🇬🇧 English",            value="eng"),
    app_commands.Choice(name="🇸🇦 Arabic",             value="ara"),
    app_commands.Choice(name="🌐 Auto-Detect",          value="auto"),
]
ENGINE_CHOICES = [
    app_commands.Choice(name="⚡ Fast (Tesseract)",         value="tesseract"),
    app_commands.Choice(name="🎯 Accurate (EasyOCR)",       value="easyocr"),
    app_commands.Choice(name="🔮 Best (Manga-OCR)",         value="manga_ocr"),
    app_commands.Choice(name="✨ Perfect (Claude Vision)",  value="claude_vision"),
]


def is_admin():
    """Decorator: requires Manage Guild permission."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise app_commands.NoPrivateMessage()
        if not interaction.user.guild_permissions.manage_guild:
            raise app_commands.MissingPermissions(["manage_guild"])
        return True
    return app_commands.check(predicate)


class AdminSettings(commands.Cog, name="Admin"):

    def __init__(self, bot):
        self.bot = bot

    # ════════════════════════════════════════════════════════
    # SERVER SETTINGS  (admin only)
    # ════════════════════════════════════════════════════════

    server_group = app_commands.Group(
        name="server",
        description="⚙️ Server-wide OCR settings (Admin only)",
    )

    @server_group.command(name="config", description="📋 View current server settings")
    @is_admin()
    async def server_config(self, interaction: discord.Interaction):
        gs = settings.get_guild(interaction.guild_id)
        now = datetime.now()
        embed = discord.Embed(
            title="⚙️ Server OCR Settings",
            description=f"**{interaction.guild.name}**",
            color=0x6C63FF,
            timestamp=now,
        )
        embed.add_field(name="🌐 Default Language",  value=f"`{gs['default_language']}`", inline=True)
        embed.add_field(name="🔧 Default Engine",    value=f"`{gs['default_engine']}`", inline=True)
        embed.add_field(name="⏳ Cooldown",           value=f"`{gs['cooldown_seconds']}s`", inline=True)
        embed.add_field(name="📦 Max ZIP Pages",      value=f"`{gs['max_zip_pages']}`", inline=True)
        embed.add_field(name="☁️ Auto Drive Upload",  value="✅ On" if gs["auto_drive"] else "❌ Off", inline=True)

        allowed = gs.get("allowed_channels", [])
        if allowed:
            mentions = " ".join(f"<#{c}>" for c in allowed[:5])
            embed.add_field(name="📢 Allowed Channels", value=mentions, inline=False)
        else:
            embed.add_field(name="📢 Allowed Channels", value="All channels", inline=False)

        out_ch = gs.get("output_channel")
        embed.add_field(name="📤 Output Channel",
                        value=f"<#{out_ch}>" if out_ch else "Same channel", inline=True)
        embed.set_footer(text=f"Manga OCR Bot • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @server_group.command(name="language", description="🌐 Set server default language")
    @app_commands.choices(language=LANG_CHOICES)
    @is_admin()
    async def server_language(self, interaction: discord.Interaction,
                               language: app_commands.Choice[str]):
        await settings.set_guild(interaction.guild_id, "default_language", language.value)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Default Language Updated",
                description=f"Server default language set to **{language.name}**",
                color=0x00CC66,
            ), ephemeral=True)

    @server_group.command(name="engine", description="🔧 Set server default OCR engine")
    @app_commands.choices(engine=ENGINE_CHOICES)
    @is_admin()
    async def server_engine(self, interaction: discord.Interaction,
                             engine: app_commands.Choice[str]):
        await settings.set_guild(interaction.guild_id, "default_engine", engine.value)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Default Engine Updated",
                description=f"Server default engine set to **{engine.name}**",
                color=0x00CC66,
            ), ephemeral=True)

    @server_group.command(name="cooldown", description="⏳ Set OCR cooldown per user (seconds)")
    @app_commands.describe(seconds="Cooldown in seconds (0 = disabled, max 300)")
    @is_admin()
    async def server_cooldown(self, interaction: discord.Interaction, seconds: int):
        seconds = max(0, min(seconds, 300))
        await settings.set_guild(interaction.guild_id, "cooldown_seconds", seconds)
        label = f"{seconds}s" if seconds > 0 else "Disabled"
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Cooldown Updated",
                description=f"User cooldown set to **{label}**",
                color=0x00CC66,
            ), ephemeral=True)

    @server_group.command(name="auto-drive", description="☁️ Toggle auto Google Drive upload")
    @app_commands.describe(enabled="Turn auto Drive upload on or off")
    @is_admin()
    async def server_auto_drive(self, interaction: discord.Interaction, enabled: bool):
        await settings.set_guild(interaction.guild_id, "auto_drive", enabled)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Auto Drive Updated",
                description=f"Auto Google Drive upload: **{'Enabled ✅' if enabled else 'Disabled ❌'}**",
                color=0x00CC66,
            ), ephemeral=True)

    @server_group.command(name="max-pages", description="📦 Max pages per ZIP batch")
    @app_commands.describe(pages="Max pages (10–500)")
    @is_admin()
    async def server_max_pages(self, interaction: discord.Interaction, pages: int):
        pages = max(10, min(pages, 500))
        await settings.set_guild(interaction.guild_id, "max_zip_pages", pages)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Max Pages Updated",
                description=f"Max ZIP pages set to **{pages}**",
                color=0x00CC66,
            ), ephemeral=True)

    @server_group.command(name="allow-channel", description="📢 Allow/restrict OCR to a channel")
    @app_commands.describe(
        channel="Channel to allow",
        remove="Remove this channel from the allowed list?",
    )
    @is_admin()
    async def server_allow_channel(self, interaction: discord.Interaction,
                                    channel: discord.TextChannel,
                                    remove: bool = False):
        gs = settings.get_guild(interaction.guild_id)
        allowed = list(gs.get("allowed_channels", []))
        if remove:
            if channel.id in allowed:
                allowed.remove(channel.id)
                msg = f"Removed {channel.mention} from allowed channels."
            else:
                msg = f"{channel.mention} was not in the allowed list."
        else:
            if channel.id not in allowed:
                allowed.append(channel.id)
                msg = f"Added {channel.mention} to allowed channels."
            else:
                msg = f"{channel.mention} is already allowed."
        await settings.set_guild(interaction.guild_id, "allowed_channels", allowed)
        await interaction.response.send_message(
            embed=discord.Embed(title="✅ Channel Updated", description=msg, color=0x00CC66),
            ephemeral=True)

    @server_group.command(name="reset", description="🔄 Reset all server settings to defaults")
    @is_admin()
    async def server_reset(self, interaction: discord.Interaction):
        await settings.reset_guild(interaction.guild_id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Settings Reset",
                description="All server settings have been reset to defaults.",
                color=0x00CC66,
            ), ephemeral=True)

    # ════════════════════════════════════════════════════════
    # USER PREFERENCES (everyone)
    # ════════════════════════════════════════════════════════

    pref_group = app_commands.Group(
        name="pref",
        description="👤 Your personal OCR preferences",
    )

    @pref_group.command(name="language", description="🌐 Set your preferred OCR language")
    @app_commands.choices(language=LANG_CHOICES)
    async def pref_language(self, interaction: discord.Interaction,
                             language: app_commands.Choice[str]):
        await settings.set_user(interaction.user.id, "default_language", language.value)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Preference Saved",
                description=f"Your default language: **{language.name}**",
                color=0x00CC66,
            ), ephemeral=True)

    @pref_group.command(name="engine", description="🔧 Set your preferred OCR engine")
    @app_commands.choices(engine=ENGINE_CHOICES)
    async def pref_engine(self, interaction: discord.Interaction,
                           engine: app_commands.Choice[str]):
        await settings.set_user(interaction.user.id, "default_engine", engine.value)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Preference Saved",
                description=f"Your default engine: **{engine.name}**",
                color=0x00CC66,
            ), ephemeral=True)

    @pref_group.command(name="compact", description="📐 Toggle compact result display")
    @app_commands.describe(enabled="Compact mode: shorter embeds, less text preview")
    async def pref_compact(self, interaction: discord.Interaction, enabled: bool):
        await settings.set_user(interaction.user.id, "compact_results", enabled)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Preference Saved",
                description=f"Compact results: **{'On ✅' if enabled else 'Off ❌'}**",
                color=0x00CC66,
            ), ephemeral=True)

    @pref_group.command(name="drive-notify", description="☁️ Toggle Drive upload notification")
    async def pref_drive_notify(self, interaction: discord.Interaction, enabled: bool):
        await settings.set_user(interaction.user.id, "notify_drive", enabled)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Preference Saved",
                description=f"Drive upload notifications: **{'On ✅' if enabled else 'Off ❌'}**",
                color=0x00CC66,
            ), ephemeral=True)

    @pref_group.command(name="view", description="👤 View your current preferences")
    async def pref_view(self, interaction: discord.Interaction):
        gs  = settings.get_guild(interaction.guild_id or 0)
        up  = settings.get_user(interaction.user.id)
        eff = settings.effective(interaction.guild_id or 0, interaction.user.id)
        now = datetime.now()
        embed = discord.Embed(
            title="👤 Your OCR Preferences",
            description="Personal overrides + effective settings for this server",
            color=0x6C63FF,
            timestamp=now,
        )
        embed.add_field(name="🌐 Language (effective)", value=f"`{eff['default_language']}`", inline=True)
        embed.add_field(name="🔧 Engine (effective)",   value=f"`{eff['default_engine']}`", inline=True)
        embed.add_field(name="📐 Compact Mode",          value="✅" if eff.get("compact_results") else "❌", inline=True)
        embed.add_field(name="☁️ Drive Notify",          value="✅" if eff.get("notify_drive", True) else "❌", inline=True)
        if up.get("default_language"):
            embed.add_field(name="🔒 Your Language Override", value=f"`{up['default_language']}`", inline=True)
        if up.get("default_engine"):
            embed.add_field(name="🔒 Your Engine Override",   value=f"`{up['default_engine']}`", inline=True)
        embed.set_footer(text=f"Manga OCR Bot • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ════════════════════════════════════════════════════════
    # BOT STATUS  (admin only)
    # ════════════════════════════════════════════════════════

    @app_commands.command(name="bot-status", description="🤖 Show full bot runtime status (Admin)")
    @is_admin()
    async def botstatus(self, interaction: discord.Interaction):
        now = datetime.now()
        import sys, os
        embed = discord.Embed(title="🤖 Bot Runtime Status", color=0x6C63FF, timestamp=now)

        # Queue & Cache
        ocr_cog   = self.bot.get_cog("OCR")
        cache_size = len(ocr_cog._cache) if ocr_cog else 0
        embed.add_field(name="⚙️ OCR Queue", value=f"`{ocr_queue.queue_size}` jobs pending", inline=True)
        embed.add_field(name="🗄️ Cache",     value=f"`{cache_size}` entries", inline=True)

        # Drive
        drive_cog = self.bot.get_cog("GDrive")
        drive_status = "✅ Connected" if (drive_cog and drive_cog.is_available) else "❌ Offline"
        embed.add_field(name="☁️ Google Drive", value=drive_status, inline=True)

        # Bot info
        embed.add_field(name="🏠 Servers",  value=f"`{len(self.bot.guilds)}`", inline=True)
        embed.add_field(name="🐍 Python",   value=f"`{sys.version.split()[0]}`", inline=True)
        embed.add_field(name="📅 Date",     value=f"`{now.strftime('%Y-%m-%d')}`", inline=True)

        # Stats
        if ocr_cog:
            st = ocr_cog.stats
            embed.add_field(name="📊 Total Images", value=f"`{st.get('total_images', 0):,}`", inline=True)
            embed.add_field(name="📄 Total Pages",  value=f"`{st.get('total_pages',  0):,}`", inline=True)

        embed.set_footer(text=f"Manga OCR Bot • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Error handlers ────────────────────────────────────────
    @server_group.error
    @pref_group.error
    @botstatus.error
    async def admin_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        """discord.py Group.error requires exactly 2 params (no self)."""
        if isinstance(error, (app_commands.MissingPermissions, app_commands.NoPrivateMessage)):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🚫 Permission Denied",
                    description="You need **Manage Server** permission to use this command.",
                    color=0xFF4444,
                ), ephemeral=True)
        else:
            log.error(f"Admin command error: {error}")


async def setup(bot):
    await bot.add_cog(AdminSettings(bot))
