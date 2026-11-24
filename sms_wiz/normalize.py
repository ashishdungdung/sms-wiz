"""Multilingual Indian SMS normalisation.

Indian banks, state utilities and government portals routinely send SMS in
Devanagari, Tamil, Telugu, Kannada, Malayalam, Gujarati, Bengali, Gurmukhi or
Odia — often mixed with English, and often with *regional numerals*.

Doing this deterministically, before any regex or model runs, matters for three
reasons:

* a 12-digit UPI RRN written in Devanagari must deduplicate against the same
  payment written in ASCII by PhonePe, or the ledger double-counts;
* ``₹``, ``रु``, ``ரூ``, ``రూ`` all mean rupees and the amount parser shouldn't
  care which one arrived;
* it keeps the output canonical: internal enum values are always ASCII English,
  whatever the input script.

No model, no network, no ambiguity. This runs inside the iOS filter extension's
budget as easily as it runs on the server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: First code point of each script's digit zero (0-9 are contiguous from there).
INDIC_DIGIT_BLOCKS: dict[str, int] = {
    "devanagari": 0x0966,  # Hindi, Marathi, Sanskrit
    "bengali": 0x09E6,
    "gurmukhi": 0x0A66,  # Punjabi
    "gujarati": 0x0AE6,
    "oriya": 0x0B66,
    "tamil": 0x0BE6,
    "telugu": 0x0C66,
    "kannada": 0x0CE6,
    "malayalam": 0x0D66,
}

DIGIT_TABLE: dict[int, int] = {}
for _start in INDIC_DIGIT_BLOCKS.values():
    for _offset in range(10):
        DIGIT_TABLE[_start + _offset] = ord("0") + _offset

#: Currency markers in the scripts we see on Indian SMS.
_CURRENCY = re.compile(
    # Longest alternative first: "रुपये" must win over "रु", or the tail
    # ("पये") is left behind and the amount parser sees a stray word.
    r"(?:₹|रुपये|रूपये|রুপি|টাকা|रु\.?|ரூ\.?|రూ\.?|ರೂ\.?|രൂ\.?|રૂ\.?|ରୁ\.?|ਰੁ\.?|৳|INR)\s*",
    re.IGNORECASE,
)

#: High-value phrases, longest first so multi-word forms win.
#: These are *safety net* translations: they let the existing English rule chain
#: work unchanged instead of maintaining a second rule chain per language.
PHRASE_MAP: tuple[tuple[str, str], ...] = (
    # Hindi / Marathi
    ("उपलब्ध शेष", "available balance"),
    ("शेष राशि", "balance"),
    ("खाते से डेबिट", "debited from account"),
    ("खाते से काटे", "debited from account"),
    ("खाते में जमा", "credited to account"),
    ("निकासी", "withdrawal"),
    ("डेबिट", "debited"),
    ("क्रेडिट", "credited"),
    ("जमा", "credited"),
    ("बैलेंस", "balance"),
    ("शिल्लक", "balance"),
    ("भुगतान", "payment"),
    ("खर्च", "spent"),
    ("ओटीपी", "OTP"),
    ("गुप्त कोड", "OTP"),
    ("सत्यापन कोड", "verification code"),
    ("मैंडेट", "mandate"),
    ("ऑटोपे", "autopay"),
    ("लाभार्थी", "beneficiary"),
    ("आधार", "Aadhaar"),
    ("चालान", "challan"),
    ("जुर्माना", "fine"),
    ("खाते", "account"),
    ("खाता", "account"),
    # Tamil
    ("பற்று", "debit"),
    ("வரவு", "credit"),
    ("இருப்பு", "balance"),
    # Telugu
    ("డెబిట్", "debit"),
    ("క్రెడిట్", "credit"),
    ("బ్యాలెన్స్", "balance"),
    ("ఖాతా", "account"),
    # Kannada
    ("ಡೆಬಿಟ್", "debit"),
    ("ಕ್ರೆಡಿಟ್", "credit"),
    ("ಬ್ಯಾಲೆನ್ಸ್", "balance"),
    ("ಖಾತೆ", "account"),
    # Malayalam
    ("ഡെബിറ്റ്", "debit"),
    ("ക്രെഡിറ്റ്", "credit"),
    ("ബാലൻസ്", "balance"),
    # Gujarati
    ("ઉપલબ્ધ બેલેન્સ", "available balance"),
    ("ડેબિટ", "debit"),
    ("ક્રેડિટ", "credit"),
    ("બેલેન્સ", "balance"),
    ("ખાતું", "account"),
    # Bengali
    ("ডেবিট", "debit"),
    ("ক্রেডিট", "credit"),
    ("ব্যালেন্স", "balance"),
    ("অ্যাকাউন্ট", "account"),
    # Punjabi
    ("ਡੈਬਿਟ", "debit"),
    ("ਕ੍ਰੈਡਿਟ", "credit"),
    ("ਬਕਾਇਆ", "balance"),
)

#: Words that identify which script(s) arrived — useful for the UI and for
#: knowing which languages still need work.
SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("gurmukhi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF),
    ("oriya", 0x0B00, 0x0B7F),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
)


@dataclass
class NormalizedSMS:
    text: str
    scripts: list[str] = field(default_factory=list)
    digit_scripts: list[str] = field(default_factory=list)
    translations: int = 0

    @property
    def is_multilingual(self) -> bool:
        return bool(self.scripts)


def detect_scripts(raw: str) -> list[str]:
    found: list[str] = []
    for char in raw:
        code = ord(char)
        for name, start, end in SCRIPT_RANGES:
            if start <= code <= end:
                if name not in found:
                    found.append(name)
                break
    return found


def normalize_text(raw: str) -> NormalizedSMS:
    """Return ASCII-digit, rupee-normalised text plus what we changed."""
    result = NormalizedSMS(text=raw, scripts=detect_scripts(raw))

    # 1. Digits: "२,५००" -> "2,500", "५१२३४५६७८९०१" -> a deduplicatable RRN.
    translated = raw.translate(DIGIT_TABLE)
    if translated != raw:
        result.digit_scripts = result.scripts
    text = translated

    # 2. Currency markers -> "Rs " so one amount regex covers every script.
    text, currency_hits = _CURRENCY.subn("Rs ", text)
    result.translations += currency_hits

    # 3. Phrase normalisation, longest first.
    for phrase, english in PHRASE_MAP:
        if phrase in text:
            text = text.replace(phrase, english)
            result.translations += 1

    result.text = text
    return result
