"""Help Menu Cog v3"""
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime


class HelpMenu(commands.Cog, name="Help"):

    def __init__(self, bot):
        self.bot = bot

    def _build_embed(self) -> discord.Embed:
        now = datetime.now()
        embed = discord.Embed(
            title="📖 Manga/Manhwa OCR Bot v3 — Guide",
            description=(
                "Extract text from any manga, manhwa, or manhua image!\n"
                "🇯🇵 Japanese · 🇰🇷 Korean · 🇨🇳 Chinese · 🇬🇧 English · 🇸🇦 Arabic\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0x6C63FF, timestamp=now,
        )
        embed.add_field(name="🔍 `/ocr` — Single Image",
            value="`image` `language` `engine` `upload_drive` `preprocess`", inline=False)
        embed.add_field(name="📦 `/zip` — Full Chapter",
            value="`archive` `language` `engine` `output_format` `upload_drive` `chapter_name` `preprocess`", inline=False)
        embed.add_field(name="🌍 `/translate` · `/ocr-translate`",
            value="Translate text or OCR+translate in one step.", inline=False)
        embed.add_field(name="📊 `/ocr-compare` · `/ocr-confidence`",
            value="Compare two engines side-by-side or rank all engines.", inline=False)
        embed.add_field(name="📜 `/history` — Recent Scans",
            value="Your last scans with language, chars, timestamps.", inline=False)
        embed.add_field(name="🏆 `/leaderboard` · `/my-rank` · `/feedback`",
            value="Top scanners in this server + personal rank + send feedback.", inline=False)
        embed.add_field(name="⚙️ `/server` — Admin Settings",
            value=("`config` `language` `engine` `cooldown` `auto-drive`\n"
                   "`max-pages` `allow-channel` `reset` *(Manage Server)*"), inline=False)
        embed.add_field(name="👤 `/pref` — Your Preferences",
            value="`language` `engine` `compact` `drive-notify` `view`", inline=False)
        embed.add_field(name="📈 `/usage` · `/stats` · `/quota`",
            value="Usage breakdown, bot stats, and daily quota.", inline=False)
        embed.add_field(name="🔧 OCR Engines",
            value=("⚡ **Tesseract** — fast, offline\n"
                   "🎯 **EasyOCR** — best for Korean/Chinese\n"
                   "🔮 **Manga-OCR** — best for Japanese manga\n"
                   "✨ **Claude Vision** — perfect accuracy on all fonts\n"
                   "*Auto-fallback chain if engine unavailable*"), inline=False)
        embed.add_field(name="📤 Output Formats",
            value="📄 TXT · 📝 Markdown · 📊 JSON · 🎬 SRT subtitles\n"
                  "☁️ Auto Google Drive for files > 8MB", inline=False)
        embed.add_field(name="🔗 Links",
            value="📨 **Invite Bot** — use `/invite` · "
                  "💬 **Support** — use `/support` · "
                  "📋 **Changelog** — use `/changelog`", inline=False)
        embed.set_footer(text=f"Manga OCR Bot v3.1 • {now.strftime('%B')} {now.year}")
        return embed

    def _build_changelog(self) -> discord.Embed:
        now = datetime.now()
        embed = discord.Embed(
            title="📋 Changelog — Manga OCR Bot",
            color=0x6C63FF, timestamp=now)
        embed.add_field(name="✨ v3.1 — Current",
            value=("• Added **Claude Vision** engine — perfect accuracy on decorative fonts\n"
                   "• Added **SRT / Markdown / JSON** output formats for `/zip`\n"
                   "• Fixed EasyOCR bubble ordering (top-to-bottom)\n"
                   "• Fixed Tesseract PSM 6→11 for scattered manga text\n"
                   "• Added per-user OCR engine preference\n"
                   "• Daily data backup to bot owner DM\n"
                   "• Lazy ZIP image loading (lower RAM usage)"), inline=False)
        embed.add_field(name="🔧 v3.0",
            value=("• ZIP chapter processing with parallel OCR\n"
                   "• Google Drive auto-upload\n"
                   "• DeepL + Google Translate integration\n"
                   "• Per-server quotas and cooldowns\n"
                   "• Leaderboard and feedback system"), inline=False)
        embed.add_field(name="🌱 v2.0",
            value=("• Multi-language support (JP/KR/CN/EN/AR)\n"
                   "• EasyOCR + Manga-OCR engines added\n"
                   "• Image preprocessing pipeline"), inline=False)
        embed.set_footer(text=f"Manga OCR Bot v3.1 • {now.strftime('%B')} {now.year}")
        return embed

    @app_commands.command(name="help", description="📖 Full guide to all bot commands")
    async def help_slash(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_embed(), ephemeral=True)

    @app_commands.command(name="changelog", description="📋 What's new in each version")
    async def changelog_slash(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self._build_changelog(), ephemeral=True)

    @commands.command(name="help", aliases=["h", "commands"])
    async def help_prefix(self, ctx: commands.Context):
        await ctx.send(embed=self._build_embed())


async def setup(bot):
    await bot.add_cog(HelpMenu(bot))
