"""Multilingual normalisation is a code path, not a promise.

These tests pin the three things that actually matter for an India-only SMS
brain reading 22 scheduled languages:

1. regional numerals become ASCII, so amounts and UPI RRNs parse and dedup;
2. currency markers collapse to one token, so one amount regex is enough;
3. the *facts* survive (script names, translation counts) so the UI can say
   "read from Hindi" instead of pretending everything was English.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sms_wiz import extract
from sms_wiz.classify import analyse
from sms_wiz.models import IngestRequest
from sms_wiz.normalize import INDIC_DIGIT_BLOCKS, detect_scripts, normalize_text
from sms_wiz.pipeline import Ingestor

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2025, 3, 1, 10, 0, tzinfo=IST)

DIGIT_SAMPLES = {
    "devanagari": ("०१२३४५६७८९", "0123456789"),
    "bengali": ("০১২৩৪৫৬৭৮৯", "0123456789"),
    "gurmukhi": ("੦੧੨੩੪੫੬੭੮੯", "0123456789"),
    "gujarati": ("૦૧૨૩૪૫૬૭૮૯", "0123456789"),
    "oriya": ("୦୧୨୩୪୫୬୭୮୯", "0123456789"),
    "tamil": ("௦௧௨௩௪௫௬௭௮௯", "0123456789"),
    "telugu": ("౦౧౨౩౪౫౬౭౮౯", "0123456789"),
    "kannada": ("೦೧೨೩೪೫೬೭೮೯", "0123456789"),
    "malayalam": ("൦൧൨൩൪൫൬൭൮൯", "0123456789"),
}


@pytest.mark.parametrize("script", sorted(INDIC_DIGIT_BLOCKS))
def test_every_regional_digit_block_becomes_ascii(script):
    source, expected = DIGIT_SAMPLES[script]
    result = normalize_text(source)
    assert result.text == expected
    assert script in result.digit_scripts
    assert script in result.scripts


@pytest.mark.parametrize(
    "marker",
    ["₹", "रु.", "रुपये", "ரூ.", "రూ.", "રૂ.", "ରୁ.", "ਰੁ.", "৳", "INR"],
)
def test_currency_markers_collapse_to_rs(marker):
    result = normalize_text(f"{marker} 1,200 debited")
    assert result.text.startswith("Rs 1,200"), result.text
    assert result.text == "Rs 1,200 debited"


def test_long_currency_word_is_not_partially_eaten():
    """'रुपये' must not leave 'पये' behind after 'रु' matches first."""
    assert normalize_text("रुपये 500").text == "Rs 500"


def test_plain_english_is_untouched():
    body = "Rs.1,450.00 debited from A/c XX4092 to VPA KIRANAKART@axisbank (UPI Ref 505612345678)."
    result = normalize_text(body)
    assert result.text == body
    assert result.scripts == []
    assert result.digit_scripts == []
    assert result.translations == 0
    assert result.is_multilingual is False


def test_normalisation_is_idempotent():
    body = "आपके खाते XX1094 से रु २,५००.०० SWIGGY को डेबिट"
    once = normalize_text(body)
    twice = normalize_text(once.text)
    assert twice.text == once.text


def test_mixed_script_message_reports_all_scripts():
    body = "Your ખાતું 1234: रु 500 डेबिट. बैलेंस: రూ. 900"
    result = normalize_text(body)
    # Order is first appearance in the message, not alphabetical.
    assert result.scripts == ["gujarati", "devanagari", "telugu"]
    assert result.is_multilingual is True


def test_unknown_indic_words_are_left_alone():
    """We translate known phrases; we never transliterate or mangle the rest."""
    body = "कृपया प्रतीक्षा करें"
    result = normalize_text(body)
    assert "कृपया" in result.text
    assert detect_scripts(result.text) == ["devanagari"]


HINDI_DEBIT = (
    "प्रिय ग्राहक, आपके खाते XX1094 से रु २,५००.०० दिनांक 25-02-25 को SWIGGY को "
    "डेबिट किए गए हैं (UPI Ref ५१२३४५६७८९०१). उपलब्ध शेष: रु ८,२००.००"
)


def test_hindi_message_extracts_amount_balance_reference_and_last4():
    text = extract.normalize(HINDI_DEBIT)
    balance = extract.find_balance(text)
    assert balance[0] == pytest.approx(8200.0)
    assert extract.find_reference(text)[0] == "512345678901"
    assert extract.find_last4(text) == "1094"

    balance_hit = extract.find_balance(text)
    assert balance_hit is not None
    amounts = extract.find_amounts(text, skip_spans=(balance_hit[1],))
    assert 2500.0 in [round(hit.value, 2) for hit in amounts]


def test_hindi_message_classifies_as_expense(merchants):
    event = analyse(HINDI_DEBIT, "VM-SBIN-T", now=NOW, merchants=merchants)
    assert event.intent.value == "expense"
    assert event.amount.value == pytest.approx(2500.0)
    assert event.merchant is not None and event.merchant.clean == "Swiggy"
    assert event.analysis["scripts"] == ["devanagari"]
    assert event.analysis["multilingual"] is True
    assert event.analysis["translations"] > 0


def test_otp_phrase_in_hindi_is_recognised():
    body = "आपका ओटीपी ४८२९१७ है। किसी को न बताएं।"
    event = analyse(body, "VM-SBIN-T", now=NOW)
    assert event.intent.value == "otp"
    assert event.analysis["scripts"] == ["devanagari"]


TAMIL_BALANCE = "உங்கள் கணக்கு XX3312 இல் இருப்பு ரூ. 1,204.50 உள்ளது."
TELUGU_BALANCE = "మీ ఖాతా XX7788 బ్యాలెన్స్ రూ. 8,900.00."


@pytest.mark.parametrize(
    ("body", "script", "expected"),
    [
        (TAMIL_BALANCE, "tamil", 1204.50),
        (TELUGU_BALANCE, "telugu", 8900.00),
    ],
)
def test_south_indian_balance_phrases_parse(body, script, expected):
    text = extract.normalize(body)
    assert extract.find_balance(text)[0] == pytest.approx(expected)
    result = normalize_text(body)
    assert result.scripts == [script]


def test_hindi_rrn_dedups_against_ascii_rrn(tmp_path):
    """The whole point of digit normalisation: one payment, one ledger row."""
    ingestor = Ingestor(str(tmp_path / "ml.db"))

    first = ingestor.ingest(
        IngestRequest(sender="AD-HDFCBK-T", body=(
            "Rs.2,500.00 debited from A/c XX1094 on 25-02-25 to VPA SWIGGY@ybl "
            "(UPI Ref 512345678901). Avl Bal: Rs.8,200.00"
        ), received_at=NOW)
    )
    assert first.event.dedup.status == "unique"
    assert first.event.ledger.counts_toward_spend is True

    second = ingestor.ingest(
        IngestRequest(sender="VM-SBIN-T", body=HINDI_DEBIT, received_at=NOW)
    )
    assert second.event.dedup.status == "suppressed_duplicate"
    assert second.event.ledger.counts_toward_spend is True  # treatment unchanged
    assert "duplicate_suppressed" in [alert.kind for alert in second.alerts]
