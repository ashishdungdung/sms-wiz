"""Replay every shared fixture in ``contracts/fixtures/`` through the real pipeline.

These are the acceptance tests for the whole brain. Each fixture is a JSON file
that the iOS package also reads (``ios/Packages/SMSCore/Tests``), so a change
that breaks the handshake fails on the Python side even if nobody opens Xcode.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from sms_wiz.models import IngestRequest
from sms_wiz.pipeline import Ingestor

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "contracts" / "fixtures"
FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ingest(ingestor: Ingestor, payload: dict):
    return ingestor.ingest(
        IngestRequest(
            sender=payload["sender"],
            body=payload["body"],
            received_at=datetime.fromisoformat(payload["received_at"]),
            source="manual",
        )
    )


def test_fixtures_exist():
    assert len(FIXTURES) >= 9, "the fixture corpus must not silently shrink"


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_fixture_matches_expectations(path: Path, tmp_path):
    fixture = _load(path)
    expect = fixture["expect"]
    ingestor = Ingestor(str(tmp_path / "fixture.db"))

    for prior in fixture.get("prior", []):
        _ingest(ingestor, prior)

    response = _ingest(ingestor, fixture["input"])
    event = response.event

    # Intent is the headline answer.
    assert event.intent.value == expect["intent"], (
        f"{fixture['id']}: intent {event.intent.value} != {expect['intent']} "
        f"(evidence: {[e.rule for e in event.evidence]})"
    )

    # iOS routing.
    if "apple_action" in expect:
        assert event.routing.apple_action.value == expect["apple_action"]
    if "apple_subaction" in expect:
        assert event.routing.apple_subaction.value == expect["apple_subaction"]
    if "retention" in expect:
        assert event.routing.retention.value == expect["retention"]

    # Money.
    if "ledger_treatment" in expect:
        assert event.ledger.treatment.value == expect["ledger_treatment"]
    if "counts_toward_spend" in expect:
        assert event.ledger.counts_toward_spend is expect["counts_toward_spend"]
    if "amount" in expect:
        assert event.amount is not None, f"{fixture['id']}: amount not extracted"
        assert event.amount.value == pytest.approx(expect["amount"])

    # Details.
    if "merchant_clean" in expect:
        assert event.merchant is not None, f"{fixture['id']}: merchant not resolved"
        assert event.merchant.clean == expect["merchant_clean"]
    if "account_last4" in expect:
        assert event.institution is not None, f"{fixture['id']}: sender not resolved to an entity"
        assert event.institution.account_last4 == expect["account_last4"]

    # Confidence contract: uncertain messages are flagged, never guessed.
    if "confidence_needs_review" in expect:
        assert event.confidence.needs_review is expect["confidence_needs_review"]

    # Dedup.
    if "dedup_status" in expect:
        assert event.dedup.status == expect["dedup_status"]

    # Flags and alerts are additive by design, so we assert inclusion only.
    for flag in expect.get("flags_include", []):
        assert flag in event.flags, f"{fixture['id']}: missing flag {flag}, got {event.flags}"
    alert_kinds = [alert.kind for alert in response.alerts]
    for kind in expect.get("alerts_include", []):
        assert kind in alert_kinds, f"{fixture['id']}: missing alert {kind}, got {alert_kinds}"

    # Multilingual messages must report which scripts they carried.
    for script in expect.get("scripts_include", []):
        assert script in event.analysis.get("scripts", []), (
            f"{fixture['id']}: script {script} not recorded, got {event.analysis.get('scripts')}"
        )
