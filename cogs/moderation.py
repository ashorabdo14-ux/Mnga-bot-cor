"""
Moderation Cog
- Blacklist/whitelist users and servers
- /admin broadcast — send message to all servers
- /admin reload — hot-reload a cog without restart
- /admin backup — download data/ as ZIP
- Webhook logging for all critical events
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio, io, json, logging, os, zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import settings

log = logging.getLogger("Moderation")

BLACKLIST_FILE = Path("data/blacklist.json")


def _load_blacklist() -> dict:
    if BLACKLIST_FILE.exists():
        try:
            return json.loads(BLACKLIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"users": [], "guilds": []}

def _save_blacklist(bl: dict):
    BLACKLIST_FILE.parent.mkdir(exist_ok=True)
    tmp = BLACKLIST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(bl, indent=2), encoding="utf-8")
    tmp.replace(BLACKLIST_FILE)

_blacklist = _load_blacklist()


def is_blacklisted(user_id: int, guild_id: int = 0) -> bool:
    return user_id in _blacklist["users"] or guild_id in _blacklist["guilds"]


def is_bot_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        app   = await interaction.client.application_info()
        owner = app.owner
        if interaction.user.id != owner.id:
            raise app_commands.MissingPermissions(["bot_owner"])
        return True
    return app_commands.check(predicate)


async def send_webhook(message: str, color: int = 0x6C63FF):
    """Send to Discord webhook if WEBHOOK_LOG_URL is set."""
    url = os.getenv("WEBHOOK_LOG_URL", "").strip()
    if not url:
        return
    try:
        import aiohttp
        payload = {"embeds": [{
            "description": message[:2000],
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
        }]}
        async with aiohttp.ClientSession() as s:
            await s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5))
    except Exception as e:
        log.warning(f"Webhook failed: {e}")


class ModerationCog(commands.Cog, name="Moderation"):

    def __init__(self, bot):
        self.bot = bot

    mod_group = app_commands.Group(name="mod", description="🛡️ Bot moderation (Owner only)")

    # ── /mod blacklist-user ───────────────────────────────
    @mod_group.command(name="blacklist-user", description="🚫 Block a user from using the bot")
    @app_commands.describe(user_id="User ID to blacklist", reason="Reason (optional)")
    @is_bot_owner()
    async def blacklist_user(self, interaction: discord.Interaction,
                              user_id: str, reason: Optional[str] = "No reason given"):
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID", ephemeral=True); return

        if uid not in _blacklist["users"]:
            _blacklist["users"].append(uid)
            _save_blacklist(_blacklist)
            await send_webhook(f"🚫 User `{uid}` blacklisted. Reason: {reason}", 0xFF4444)
            await interaction.response.send_message(
                embed=discord.Embed(title="✅ User Blacklisted",
                    description=f"User `{uid}` can no longer use the bot.\nReason: {reason}",
                    color=0xFF4444), ephemeral=True)
        else:
            await interaction.response.send_message(
                f"⚠️ User `{uid}` is already blacklisted.", ephemeral=True)

    # ── /mod unblacklist-user ─────────────────────────────
    @mod_group.command(name="unblacklist-user", description="✅ Remove user from blacklist")
    @is_bot_owner()
    async def unblacklist_user(self, interaction: discord.Interaction, user_id: str):
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("❌ Invalid ID", ephemeral=True); return
        if uid in _blacklist["users"]:
            _blacklist["users"].remove(uid)
            _save_blacklist(_blacklist)
            await interaction.response.send_message(
                embed=discord.Embed(title="✅ Unblacklisted",
                    description=f"User `{uid}` restored.", color=0x00CC66), ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ User not in blacklist.", ephemeral=True)

    # ── /mod blacklist-guild ──────────────────────────────
    @mod_group.command(name="blacklist-guild", description="🚫 Block a server from using the bot")
    @is_bot_owner()
    async def blacklist_guild(self, interaction: discord.Interaction,
                               guild_id: str, reason: Optional[str] = "No reason"):
        try:
            gid = int(guild_id)
        except ValueError:
            await interaction.response.send_message("❌ Invalid guild ID", ephemeral=True); return
        if gid not in _blacklist["guilds"]:
            _blacklist["guilds"].append(gid)
            _save_blacklist(_blacklist)
            # Leave the guild if currently in it
            guild_obj = interaction.client.get_guild(gid)
            if guild_obj:
                await guild_obj.leave()
            await send_webhook(f"🚫 Guild `{gid}` blacklisted + left. Reason: {reason}", 0xFF4444)
            await interaction.response.send_message(
                embed=discord.Embed(title="✅ Guild Blacklisted",
                    description=f"Guild `{gid}` blocked.\nReason: {reason}",
                    color=0xFF4444), ephemeral=True)
        else:
            await interaction.response.send_message("Already blacklisted.", ephemeral=True)

    # ── /mod broadcast ────────────────────────────────────
    @mod_group.command(name="broadcast",
        description="📢 Send an announcement to all servers")
    @app_commands.describe(
        message="Announcement message",
        channel_name="Channel name to post in (default: general/announcements)",
    )
    @is_bot_owner()
    async def broadcast(self, interaction: discord.Interaction,
                         message: str, channel_name: str = "general"):
        await interaction.response.defer(thinking=True, ephemeral=True)
        sent, failed = 0, 0
        embed = discord.Embed(
            title="📢 Announcement from Manga OCR Bot",
            description=message,
            color=0x6C63FF,
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Manga OCR Bot v3")
        for guild in interaction.client.guilds:
            target_ch = None
            for ch in guild.text_channels:
                if channel_name.lower() in ch.name.lower():
                    target_ch = ch; break
            if not target_ch:
                # Fallback: first channel bot can write to
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        target_ch = ch; break
            if target_ch:
                try:
                    await target_ch.send(embed=embed)
                    sent += 1
                except Exception:
                    failed += 1
            else:
                failed += 1
            await asyncio.sleep(0.5)  # rate limit respect

        await interaction.followup.send(
            embed=discord.Embed(
                title="📢 Broadcast Complete",
                description=f"✅ Sent to **{sent}** servers\n❌ Failed: **{failed}**",
                color=0x00CC66), ephemeral=True)
        await send_webhook(f"📢 Broadcast sent to {sent} servers. Failed: {failed}")

    # ── /mod reload ───────────────────────────────────────
    @mod_group.command(name="reload", description="🔄 Hot-reload a cog without restarting")
    @app_commands.describe(cog_name="Cog name, e.g. cogs.ocr_commands")
    @is_bot_owner()
    async def reload_cog(self, interaction: discord.Interaction, cog_name: str):
        try:
            await interaction.client.reload_extension(cog_name)
            await interaction.response.send_message(
                embed=discord.Embed(title="✅ Reloaded",
                    description=f"`{cog_name}` reloaded successfully.", color=0x00CC66),
                ephemeral=True)
            log.info(f"Reloaded cog: {cog_name}")
        except Exception as e:
            await interaction.response.send_message(
                embed=discord.Embed(title="❌ Reload Failed",
                    description=f"```{str(e)[:500]}```", color=0xFF4444),
                ephemeral=True)

    # ── /mod backup ───────────────────────────────────────
    @mod_group.command(name="backup", description="💾 Download all bot data as a ZIP")
    @is_bot_owner()
    async def backup(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            data_dir  = Path("data")
            buf       = io.BytesIO()
            file_count = 0
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in data_dir.glob("*.json"):
                    zf.write(f, f.name)
                    file_count += 1
            buf.seek(0)
            now   = datetime.now()
            fname = f"manga_ocr_backup_{now.strftime('%Y%m%d_%H%M%S')}.zip"
            await interaction.followup.send(
                embed=discord.Embed(title="💾 Data Backup",
                    description=f"Contains **{file_count}** data files.",
                    color=0x00CC66),
                file=discord.File(buf, filename=fname),
                ephemeral=True)
            await send_webhook(f"💾 Backup downloaded by owner. {file_count} files.")
        except Exception as e:
            await interaction.followup.send(
                embed=discord.Embed(title="❌ Backup Failed",
                    description=f"```{str(e)[:400]}```", color=0xFF4444),
                ephemeral=True)

    # ── /mod blacklist-status ─────────────────────────────
    @mod_group.command(name="blacklist-status", description="📋 View current blacklist")
    @is_bot_owner()
    async def blacklist_status(self, interaction: discord.Interaction):
        bl = _blacklist
        embed = discord.Embed(title="🚫 Blacklist Status", color=0xFF4444)
        users  = ", ".join(str(u) for u in bl["users"][:20])  or "None"
        guilds = ", ".join(str(g) for g in bl["guilds"][:20]) or "None"
        embed.add_field(name=f"👤 Users ({len(bl['users'])})",   value=users[:1000],  inline=False)
        embed.add_field(name=f"🏠 Guilds ({len(bl['guilds'])})", value=guilds[:1000], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /mod error handlers ───────────────────────────────
    @mod_group.error
    async def mod_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        """discord.py Group.error requires exactly 2 params (no self)."""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                embed=discord.Embed(title="🚫 Owner Only",
                    description="This command is reserved for the bot owner.",
                    color=0xFF4444), ephemeral=True)


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
    # Register blacklist check as global bot check (prefix commands)
    @bot.check
    async def global_blacklist_check(ctx):
        gid = ctx.guild.id if ctx.guild else 0
        if is_blacklisted(ctx.author.id, gid):
            raise commands.CheckFailure("You are blacklisted from using this bot.")
        return True

    # Register blacklist check for slash commands
    async def global_slash_blacklist_check(interaction: discord.Interaction) -> bool:
        gid = interaction.guild_id or 0
        if is_blacklisted(interaction.user.id, gid):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🚫 Banned",
                    description="You are blacklisted from using this bot.",
                    color=0xFF4444),
                ephemeral=True)
            return False
        return True

    bot.tree.interaction_check = global_slash_blacklist_check
