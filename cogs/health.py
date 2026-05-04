"""
Health Check Cog (FIX #7)
Runs a tiny HTTP server on port 8080.
Railway pings this to know the bot is alive — prevents idle kills.
"""

import discord
from discord.ext import commands, tasks
import asyncio, logging, os
from datetime import datetime
from aiohttp import web

log = logging.getLogger("Health")

PORT = int(os.getenv("PORT", "8080"))


class HealthCog(commands.Cog, name="Health"):

    def __init__(self, bot):
        self.bot   = bot
        self._app  = web.Application()
        self._app.router.add_get("/",       self._handle_root)
        self._app.router.add_get("/health", self._handle_health)
        self._runner = None
        self._start_server.start()

    async def _handle_root(self, request):
        return web.Response(text="Manga OCR Bot is running ✅")

    async def _handle_health(self, request):
        now = datetime.now()
        payload = {
            "status":  "ok",
            "bot":     self.bot.user.name if self.bot.user else "connecting",
            "guilds":  len(self.bot.guilds),
            "latency": f"{self.bot.latency*1000:.1f}ms",
            "time":    now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return web.json_response(payload)

    @tasks.loop(count=1)
    async def _start_server(self):
        try:
            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            site = web.TCPSite(self._runner, "0.0.0.0", PORT)
            await site.start()
            log.info(f"✅ Health server on port {PORT}")
        except Exception as e:
            log.warning(f"Health server failed to start: {e}")

    @_start_server.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    async def cog_unload(self):
        self._start_server.cancel()
        if self._runner:
            await self._runner.cleanup()


async def setup(bot):
    await bot.add_cog(HealthCog(bot))
