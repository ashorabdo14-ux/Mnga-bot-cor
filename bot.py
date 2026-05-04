"""Manga/Manhwa OCR Bot — v3.1 Final"""

import discord
from discord.ext import commands, tasks
import asyncio, logging, os, signal, sys
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
VERSION = "3.1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")],
)
log = logging.getLogger("Bot")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents,
                   help_command=None, description="🔍 Manga/Manhwa OCR Bot")

def validate_env():
    token = os.getenv("DISCORD_TOKEN","").strip()
    if not token or token == "your_discord_bot_token_here":
        log.critical("❌ DISCORD_TOKEN not set or is placeholder!")
        sys.exit(1)
    return token

async def load_extensions():
    cogs = [
        "cogs.ocr_commands",
        "cogs.zip_handler",
        "cogs.gdrive_uploader",
        "cogs.stats",
        "cogs.help_menu",
        "cogs.admin_settings",
        "cogs.health",
        "cogs.translate_cmd",
        "cogs.advanced_ocr",
        "cogs.moderation",
        "cogs.leaderboard",
        "cogs.quota",
        "cogs.usage",
    ]
    for cog in cogs:
        try:
            await bot.load_extension(cog)
            log.info(f"✅ {cog}")
        except Exception as e:
            log.error(f"❌ {cog}: {e}")

@bot.event
async def on_error(event, *args, **kwargs):
    log.exception(f"Unhandled error in '{event}'")

@bot.tree.error
async def on_app_command_error(interaction, error):
    msg = "An unexpected error occurred."
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        msg = f"⏳ Try again in {error.retry_after:.1f}s."
    elif isinstance(error, discord.app_commands.MissingPermissions):
        msg = "🚫 You lack permission."
    elif isinstance(error, discord.app_commands.NoPrivateMessage):
        msg = "🚫 Server only."
    else:
        log.error(f"Slash error /{getattr(interaction.command,'name','?')}: {error}", exc_info=True)
    try:
        emb = discord.Embed(title="❌ Error", description=msg, color=0xFF4444)
        if interaction.response.is_done():
            await interaction.followup.send(embed=emb, ephemeral=True)
        else:
            await interaction.response.send_message(embed=emb, ephemeral=True)
    except Exception: pass

@bot.event
async def on_ready():
    log.info(f"Bot: {bot.user} | v{VERSION} | {len(bot.guilds)} guilds")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name=f"📖 Manga | /help | v{VERSION}"))
    try:
        synced = await bot.tree.sync()
        log.info(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        log.error(f"Sync error: {e}")

@bot.event
async def on_guild_join(guild):
    log.info(f"➕ {guild.name} ({guild.id})")

@tasks.loop(hours=24)
async def daily_backup():
    """Auto-backup data files every 24h — sends zip to bot owner via DM."""
    import zipfile, io as _io
    from pathlib import Path
    try:
        data_dir = Path("data")
        if not data_dir.exists() or not any(data_dir.glob("*.json")):
            log.info("Daily backup: no data files yet, skipping")
            return
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in data_dir.glob("*.json"):
                zf.write(f, f.name)
        buf.seek(0)
        size_kb = buf.getbuffer().nbytes / 1024
        log.info(f"✅ Daily backup ready: {size_kb:.1f}KB")

        # Send the actual file to the bot owner via DM (not just a text message)
        from cogs.moderation import send_webhook
        await send_webhook(f"💾 Daily backup — {size_kb:.1f}KB — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
        try:
            app_info = await bot.application_info()
            owner    = app_info.owner
            fname    = f"backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.zip"
            await owner.send(
                content=f"💾 **Daily Backup** — `{size_kb:.1f}KB`",
                file=discord.File(buf, filename=fname))
        except Exception as dm_err:
            log.warning(f"Could not DM backup to owner: {dm_err}")
    except Exception as e:
        log.warning(f"Daily backup failed: {e}")

@daily_backup.before_loop
async def before_backup():
    await bot.wait_until_ready()

# Store main loop reference for safe signal handling (Python 3.10+ compat)
_main_loop = None

def _shutdown(sig, frame):
    log.info(f"Signal {signal.Signals(sig).name} — shutting down...")
    try:
        from core.config import stats_buffer
        stats_buffer.flush()
    except Exception: pass
    if _main_loop and not _main_loop.is_closed():
        _main_loop.stop()

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)

async def main():
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    token = validate_env()

    # Ensure data/ directory exists before any cog writes JSON files
    # (leaderboard, quota, feedback all write to data/*.json at runtime)
    from pathlib import Path
    Path("data").mkdir(exist_ok=True)
    log.info("✅ data/ directory ready")

    daily_backup.start()
    async with bot:
        await load_extensions()
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
