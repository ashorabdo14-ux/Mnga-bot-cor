# 🔍 Manga/Manhwa OCR Bot — v3.1

> بوت Discord احترافي لاستخراج وترجمة النصوص من المانجا والمانهوا  
> Professional Discord bot for OCR + translation of Manga, Manhwa, Manhua

---

## ✨ الميزات الكاملة | Full Features

| الميزة | الأمر |
|--------|-------|
| 🔍 استخراج نص صورة واحدة | `/ocr` |
| 📦 معالجة فصل كامل (ZIP) | `/zip` |
| 🌍 ترجمة تلقائية | `/translate` + `/ocr-translate` |
| 🔬 مقارنة محركَين OCR | `/ocr-compare` |
| 🔗 OCR من URL مباشر | `/ocr-url` |
| 📋 صيغ مخرجات متعددة | `/ocr-format` (TXT/JSON/MD/SRT) |
| 📐 تصحيح الإمالة | `/ocr-deskew` |
| 🎯 مؤشر جودة OCR | `/ocr-confidence` |
| 📜 سجل الفحوصات | `/history` (مع أزرار تصفح) |
| 🏆 لوحة المتصدرين | `/leaderboard` + `/my-rank` |
| 📊 إحصائيات مفصلة | `/usage` + `/stats` |
| ⚙️ لوحة إعدادات تفاعلية | `/settings-panel` |
| 📊 حصص يومية | `/quota` + `/quota-set` |
| 💬 إرسال تقييم | `/feedback` |
| ☁️ Google Drive | رفع تلقائي، تجاوز حد Discord |
| 🛡️ إدارة المشرفين | `/mod blacklist-user/guild/broadcast/reload/backup` |
| 🔄 Hot-reload | `/mod reload cogs.ocr_commands` |
| 💾 نسخ احتياطية تلقائية | كل 24 ساعة تلقائياً |
| 🌐 Health check | HTTP server لـ Railway |

---

## 🚀 التثبيت | Installation

```bash
git clone https://github.com/your-username/manga-ocr-bot.git
cd manga-ocr-bot
pip install -r requirements.txt
cp .env.example .env   # Add your tokens
python bot.py
```

## 🛤️ Railway Deployment

1. Push to GitHub
2. Connect repo on [railway.app](https://railway.app)
3. Add environment variables:

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Your Discord bot token |
| `GOOGLE_CREDENTIALS_JSON` | Google Service Account JSON |
| `GDRIVE_FOLDER_ID` | Google Drive folder ID |
| `WEBHOOK_LOG_URL` | Discord webhook for error logs (optional) |
| `DEEPL_API_KEY` | DeepL API key for better translation (optional) |

---

## 📋 جميع الأوامر | All Commands

### OCR
| Command | Description |
|---------|-------------|
| `/ocr` | Single image OCR with optional confidence score + thumbnail |
| `/zip` | Full chapter ZIP with parallel processing + optional translation |
| `/ocr-compare` | Compare 2 engines side-by-side |
| `/ocr-url` | OCR from direct image URL |
| `/ocr-format` | Output as TXT / JSON / Markdown / SRT |
| `/ocr-deskew` | Fix tilted image then OCR |
| `/ocr-confidence` | Score all 3 engines on one image |
| `!ocr [lang]` | Prefix: scan attached image |
| `!ocr-bulk [lang]` | Prefix: scan up to 5 images |

### Translation
| Command | Description |
|---------|-------------|
| `/translate` | Translate last OCR result or custom text |
| `/ocr-translate` | OCR + translate in one step |

### Stats & Social
| Command | Description |
|---------|-------------|
| `/stats` | Bot-wide statistics |
| `/usage` | Server usage breakdown |
| `/history` | Your recent scans (paginated) |
| `/leaderboard` | Top scanners in server |
| `/my-rank` | Your personal rank |
| `/quota` | Daily scan quota status |
| `/feedback` | Rate the bot |

### Settings (Admin)
| Command | Description |
|---------|-------------|
| `/settings-panel` | Interactive button panel |
| `/server config/language/engine/cooldown/...` | Server settings |
| `/pref language/engine/compact/...` | Personal preferences |
| `/quota-set` | Set daily page limit |

### Owner Only
| Command | Description |
|---------|-------------|
| `/mod blacklist-user` | Block a user |
| `/mod blacklist-guild` | Block a server |
| `/mod broadcast` | Send to all servers |
| `/mod reload` | Hot-reload a cog |
| `/mod backup` | Download data ZIP |
| `/bot-status` | Full runtime status |

---

## 🔧 OCR Engines

| Engine | Speed | Accuracy | Best for |
|--------|-------|----------|----------|
| ⚡ Tesseract | Fast | Medium | Simple text, offline |
| 🎯 EasyOCR | Medium | High | Korean, Chinese |
| 🔮 Manga-OCR | Slow | Excellent | Japanese manga |
| ✨ Claude Vision | Slow | Perfect | Decorative/bold fonts |

*Auto-fallback chain: claude_vision → manga_ocr → easyocr → tesseract*

---

## 📁 Project Structure

```
manga-ocr-bot/
├── bot.py                      # Entry point + daily backup
├── core/
│   ├── config.py               # Settings, Queue, Cache, StatsBuffer, DriveRateLimiter
│   ├── translator.py           # Google Translate + DeepL
│   └── image_utils.py          # Deskew, formatters, confidence, thumbnail
├── cogs/
│   ├── ocr_commands.py         # /ocr, !ocr, !ocr-bulk, /history + pagination
│   ├── zip_handler.py          # /zip with parallel processing + translation
│   ├── translate_cmd.py        # /translate, /ocr-translate
│   ├── advanced_ocr.py         # /ocr-compare, /ocr-url, /ocr-format, /ocr-deskew
│   ├── admin_settings.py       # /server, /pref, /bot-status
│   ├── moderation.py           # /mod blacklist/broadcast/reload/backup
│   ├── leaderboard.py          # /leaderboard, /my-rank, /feedback
│   ├── quota.py                # /quota, /quota-set, daily reset
│   ├── usage.py                # /usage, /settings-panel (buttons)
│   ├── stats.py                # /stats
│   ├── gdrive_uploader.py      # Google Drive integration
│   ├── health.py               # HTTP health check server
│   └── help_menu.py            # /help
├── data/                       # Auto-generated JSON storage
├── Dockerfile
├── railway.toml
├── nixpacks.toml
└── requirements.txt
```

---

*Built with ❤️ | Version 3.1 | 2025*
