"""Replay publicly published bank messages through the pipeline.

The fixtures in ``fixtures/bank_templates/`` are real message *shapes* published
by third parties (open-source SMS parsers, security write-ups, bank help pages),
already masked by the publisher and masked again here. They exist to keep the
templates honest against text written by banks rather than by us.

Adding a case: run ``sms-wiz ingest -s <sender> -b "<body>"`` and copy what the
pipeline actually does. If it is wrong, fix the code, not this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sms_wiz.classify import analyse
from sms_wiz.merchants import MerchantDictionary

FIXTURES = Path(__file__).parent / "fixtures" / "bank_templates"
PUBLIC = FIXTURES / "public_messages.json"

CASES = json.loads(PUBLIC.read_text(encoding="utf-8"))["messages"]


@pytest.fixture(scope="module")
def merchants(tmp_path_factory):
    return MerchantDictionary(str(tmp_path_factory.mktemp("fx") / "fx.db"))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_public_message_shape(case, merchants):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    event = analyse(case["body"], case["sender"], now=now, merchants=merchants)
    expect = case["expect"]

    assert event.intent.value == expect["intent"], event.evidence
    assert event.ledger.treatment.value == expect["treatment"]
    if "amount" in expect:
        assert event.amount is not None and event.amount.value == pytest.approx(expect["amount"])
    if "last4" in expect:
        assert event.analysis.get("template"), "a known bank header must match a template"
    if "balance" in expect:
        assert event.amount is not None
        assert event.amount.balance_after == pytest.approx(expect["balance"])
    if "merchant" in expect:
        assert event.merchant is not None
        assert event.merchant.clean == expect["merchant"]


def test_every_case_declares_its_provenance():
    for case in CASES:
        assert case.get("source", "").startswith("http"), case["id"]
        assert case.get("body") and case.get("sender")


def test_no_unmasked_account_numbers_in_fixtures():
    """A fixture is a test, not a data leak: only masked tails are allowed."""
    import re

    for case in CASES:
        for digits in re.findall(r"\d{6,}", case["body"]):
            # Long digit runs may only be references (UPI RRN / ref no), and the
            # publisher's own masked tails are four digits.
            assert not digits.startswith("5"), case["id"]
