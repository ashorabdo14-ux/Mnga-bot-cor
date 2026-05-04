"""
Google Drive Uploader — v3
Fix: asyncio.get_running_loop() + size cap + better error messages
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio, io, os, json, logging
from pathlib import Path
from datetime import datetime
from typing import Optional

log = logging.getLogger("GDrive")

UPLOAD_MAX_MB = 15   # FIX #10: hard cap on upload size


class GDriveUploader(commands.Cog, name="GDrive"):

    def __init__(self, bot):
        self.bot = bot
        self.service = None
        self.folder_id = os.getenv("GDRIVE_FOLDER_ID")
        self._init_drive()

    def _init_drive(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
            if creds_json:
                creds = service_account.Credentials.from_service_account_info(
                    json.loads(creds_json),
                    scopes=["https://www.googleapis.com/auth/drive.file"])
            elif Path(creds_file).exists():
                creds = service_account.Credentials.from_service_account_file(
                    creds_file,
                    scopes=["https://www.googleapis.com/auth/drive.file"])
            else:
                log.warning("⚠️ No Google credentials — Drive upload disabled.")
                return
            self.service = build("drive", "v3", credentials=creds)
            log.info("✅ Google Drive initialized")
        except ImportError:
            log.warning("⚠️ google-api-python-client not installed — Drive disabled.")
        except Exception as e:
            log.error(f"Drive init error: {e}")

    async def upload_text(self, text: str, filename: str) -> Optional[str]:
        if not self.service:
            return None
        # FIX #10: enforce size cap
        size_mb = len(text.encode("utf-8")) / 1024 / 1024
        if size_mb > UPLOAD_MAX_MB:
            log.warning(f"Drive upload rejected: {size_mb:.1f}MB > {UPLOAD_MAX_MB}MB cap")
            return None
        loop = asyncio.get_running_loop()   # FIX #2
        return await loop.run_in_executor(None, self._sync_upload_text, text, filename)

    def _sync_upload_text(self, text: str, filename: str) -> Optional[str]:
        try:
            from googleapiclient.http import MediaIoBaseUpload
            meta = {"name": filename, "mimeType": "text/plain"}
            if self.folder_id:
                meta["parents"] = [self.folder_id]
            media = MediaIoBaseUpload(
                io.BytesIO(text.encode("utf-8")), mimetype="text/plain", resumable=True)
            f = self.service.files().create(body=meta, media_body=media,
                                             fields="id, webViewLink").execute()
            self.service.permissions().create(
                fileId=f["id"], body={"type":"anyone","role":"reader"}).execute()
            link = f.get("webViewLink","")
            log.info(f"✅ Drive upload: {filename}")
            return link
        except Exception as e:
            log.error(f"Drive upload error: {e}")
            return None

    @property
    def is_available(self) -> bool:
        return self.service is not None

    @app_commands.command(name="drive-status", description="☁️ Check Google Drive connection")
    async def drive_status(self, interaction: discord.Interaction):
        now = datetime.now()
        embed = discord.Embed(title="☁️ Google Drive Status",
                               color=0x34A853 if self.is_available else 0xFF4444)
        if self.is_available:
            embed.add_field(name="Status",     value="✅ Connected", inline=True)
            embed.add_field(name="Folder",     value=self.folder_id or "Root", inline=True)
            embed.add_field(name="Max Upload", value=f"{UPLOAD_MAX_MB}MB", inline=True)
            embed.description = "Google Drive is ready to receive OCR results."
        else:
            embed.description = (
                "Google Drive is not configured.\n"
                "Set `GOOGLE_CREDENTIALS_JSON` and `GDRIVE_FOLDER_ID` in Railway Variables.")
        embed.set_footer(text=f"Manga OCR Bot v3 • {now.strftime('%B')} {now.year}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(GDriveUploader(bot))
