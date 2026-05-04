"""
Translator Module
Auto-translate OCR results using free Google Translate API (no key needed)
+ DeepL if API key provided
"""

import asyncio, logging, re
from typing import Optional

log = logging.getLogger("Translator")

TARGET_LANG_OPTIONS = [
    ("🇸🇦 Arabic",     "ar"),
    ("🇬🇧 English",    "en"),
    ("🇫🇷 French",     "fr"),
    ("🇩🇪 German",     "de"),
    ("🇪🇸 Spanish",    "es"),
    ("🇷🇺 Russian",    "ru"),
    ("🇵🇹 Portuguese", "pt"),
    ("🇮🇩 Indonesian", "id"),
    ("🇹🇷 Turkish",    "tr"),
]

SOURCE_DETECT = {
    "jpn": "ja", "kor": "ko", "chi_sim": "zh-CN",
    "chi_tra": "zh-TW", "eng": "en", "ara": "ar", "auto": None,
}


async def translate_text(text: str, target_lang: str, source_lang: Optional[str] = None) -> Optional[str]:
    """
    Translate text. Tries DeepL first (if key set), then Google free API.
    Returns translated string or None on failure.
    """
    import os
    text = text.strip()
    if not text or len(text) < 3:
        return None
    # Don't translate if source == target
    if source_lang and source_lang[:2].lower() == target_lang[:2].lower():
        return text

    deepl_key = os.getenv("DEEPL_API_KEY", "").strip()
    if deepl_key:
        result = await _deepl_translate(text, target_lang, source_lang, deepl_key)
        if result:
            return result

    return await _google_free_translate(text, target_lang, source_lang)


async def _deepl_translate(text: str, target: str, source: Optional[str], key: str) -> Optional[str]:
    try:
        import aiohttp
        # DeepL uses uppercase lang codes
        tgt = target.upper().replace("-", "_")
        payload = {"text": [text[:4000]], "target_lang": tgt}
        if source:
            payload["source_lang"] = source.upper()[:2]
        url = "https://api-free.deepl.com/v2/translate" if key.endswith(":fx") else "https://api.deepl.com/v2/translate"
        headers = {"Authorization": f"DeepL-Auth-Key {key}", "Content-Type": "application/json"}
        import json
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, data=json.dumps(payload), timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    return data["translations"][0]["text"]
    except Exception as e:
        log.warning(f"DeepL failed: {e}")
    return None


async def _google_free_translate(text: str, target: str, source: Optional[str]) -> Optional[str]:
    """Uses Google Translate free endpoint (no API key)."""
    try:
        import aiohttp, urllib.parse, json
        # Chunk long text
        chunks = _chunk_text(text, 4500)
        results = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            for chunk in chunks:
                src_param = source or "auto"
                encoded = urllib.parse.quote(chunk)
                url = (f"https://translate.googleapis.com/translate_a/single"
                       f"?client=gtx&sl={src_param}&tl={target}&dt=t&q={encoded}")
                async with s.get(url) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        translated = "".join(seg[0] for seg in data[0] if seg[0])
                        results.append(translated)
                    else:
                        return None
        return "\n".join(results)
    except Exception as e:
        log.warning(f"Google Translate failed: {e}")
        return None


def _chunk_text(text: str, max_len: int) -> list:
    """Split text into chunks at sentence boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 <= max_len:
            current += line + "\n"
        else:
            if current: chunks.append(current.strip())
            current = line + "\n"
    if current: chunks.append(current.strip())
    return chunks or [text[:max_len]]
