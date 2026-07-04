# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deterministic, locale-aware YO!chat question classification."""

from __future__ import annotations

import re
import unicodedata

from .locales import normalize_locale


QUESTION_PUNCTUATION = frozenset({"?", "？", "؟", "՞", ";", "፧"})
_TRAILING_CLOSERS = frozenset("\"'”’»）)]}")
_URL_OR_CODE = re.compile(r"(?:https?://|`[^`]*`)", re.IGNORECASE)
_INTERROGATIVES: dict[str, tuple[str, ...]] = {
    "ar": ("هل", "ماذا", "لماذا", "كيف", "متى", "أين", "من"),
    "de": ("wer", "was", "wann", "wo", "warum", "wie", "welche", "ist", "sind", "kann"),
    "en": ("who", "what", "when", "where", "why", "how", "which", "is", "are", "can", "could", "would", "should", "do", "does", "did"),
    "es": ("quién", "qué", "cuándo", "dónde", "por qué", "cómo", "cuál", "puede", "es"),
    "fr": ("qui", "quoi", "quand", "où", "pourquoi", "comment", "quel", "est-ce", "peut"),
    "he": ("מי", "מה", "מתי", "איפה", "למה", "איך", "האם"),
    "hi": ("कौन", "क्या", "कब", "कहाँ", "क्यों", "कैसे"),
    "it": ("chi", "cosa", "quando", "dove", "perché", "come", "quale", "può"),
    "ja": ("誰", "何", "いつ", "どこ", "なぜ", "どう", "ですか", "ますか"),
    "ko": ("누구", "무엇", "언제", "어디", "왜", "어떻게", "인가", "할까"),
    "nl": ("wie", "wat", "wanneer", "waar", "waarom", "hoe", "welke", "kan", "is"),
    "pl": ("kto", "co", "kiedy", "gdzie", "dlaczego", "jak", "który", "czy"),
    "pt-BR": ("quem", "o que", "quando", "onde", "por que", "como", "qual", "pode"),
    "ru": ("кто", "что", "когда", "где", "почему", "как", "какой", "можно", "ли"),
    "th": ("ใคร", "อะไร", "เมื่อไหร่", "ที่ไหน", "ทำไม", "อย่างไร", "ไหม"),
    "tr": ("kim", "ne", "ne zaman", "nerede", "neden", "nasıl", "hangi", "mı", "mi"),
    "vi": ("ai", "gì", "khi nào", "ở đâu", "tại sao", "như thế nào", "có thể"),
    "zh-Hans": ("谁", "什么", "何时", "哪里", "为什么", "怎么", "是否", "能否"),
    "zh-Hant": ("誰", "什麼", "何時", "哪裡", "為什麼", "怎麼", "是否", "能否"),
}


def chat_message_is_question(body: str, locale: str) -> bool:
    """Classify direct questions without treating URL/code/quoted punctuation as intent."""
    raw_text = str(body or "").strip()
    text = unicodedata.normalize("NFKC", raw_text)
    if not text or _URL_OR_CODE.fullmatch(text):
        return False
    if text[0] in "\"'“‘«([{":
        return False
    tail = text
    raw_tail = raw_text
    while tail and tail[-1] in _TRAILING_CLOSERS:
        tail = tail[:-1].rstrip()
    while raw_tail and raw_tail[-1] in _TRAILING_CLOSERS:
        raw_tail = raw_tail[:-1].rstrip()
    if ((tail and tail[-1] in QUESTION_PUNCTUATION) or (raw_tail and raw_tail[-1] in QUESTION_PUNCTUATION)) and not _URL_OR_CODE.search(tail[max(0, len(tail) - 256):]):
        return True
    locale_code = normalize_locale(locale)
    lowered = text.casefold()
    for prefix in _INTERROGATIVES.get(locale_code, _INTERROGATIVES["en"]):
        candidate = unicodedata.normalize("NFKC", prefix).casefold()
        if lowered == candidate or lowered.startswith(candidate + " "):
            return True
        if locale_code in {"ja", "ko", "th", "zh-Hans", "zh-Hant"} and lowered.startswith(candidate):
            return True
    return False
