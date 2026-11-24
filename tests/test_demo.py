"""The demo corpus is a regression test, not a brochure.

33 messages covering every rail, script and trap the app claims to handle. If a
classifier change breaks a real-world shape, this file fails before the demo
looks wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sms_wiz import analytics, cards, notify, subscriptions
from sms_wiz.cli import SAMPLE_MESSAGES, _fmt_date
from sms_wiz.db import connect
from sms_wiz.models import IngestRequest
from sms_wiz.pipeline import Ingestor
from sms_wiz.store import list_events

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("demo") / "demo.db")
    ingestor = Ingestor(db_path)
    now = datetime(2026, 11, 24, 10, 0, tzinfo=IST)
    responses = []
    for index, sample in enumerate(SAMPLE_MESSAGES, 1):
        body = sample["body"].format(
            d0=_fmt_date(now, -1),
            d1=_fmt_date(now, 0),
            d2=_fmt_date(now, 2),
            d5=_fmt_date(now, 3),
            d30=_fmt_date(now, 30),
            m7=_fmt_date(now, -7),
            m30=_fmt_date(now, -30),
        )
        responses.append(
            ingestor.ingest(
                IngestRequest(sender=sample["sender"], body=body, received_at=now + timedelta(minutes=index), source="import")
            )
        )
    connection = connect(db_path)
    try:
        rows = list_events(connection, limit=500)
    finally:
        connection.close()
    return {"responses": responses, "rows": rows, "now": now}


class TestCorpusShape:
    def test_at_least_thirty_messages(self):
        assert len(SAMPLE_MESSAGES) >= 30

    def test_every_message_has_a_name_sender_and_body(self):
        for sample in SAMPLE_MESSAGES:
            assert sample["name"] and sample["sender"] and sample["body"]

    def test_every_message_produces_one_event(self, demo):
        assert len(demo["rows"]) == len(SAMPLE_MESSAGES)

    def test_nothing_crashes_and_nothing_is_unclassified(self, demo):
        assert all(r["intent"] != "unknown" for r in demo["rows"])

    def test_most_messages_need_no_human_review(self, demo):
        review = [r for r in demo["rows"] if r["needs_review"]]
        assert len(review) <= 3


class TestLedgerInvariants:
    def test_spend_is_the_expected_number(self, demo):
        # Not ₹14,092: the Netflix charge says "on <30 days ago>", so
        # it posted in October and belongs to October. The message arrived in
        # November; the money did not move in November.
        # ₹13,443: the broadband debit (₹1,335) is ordinary spending, while the
        # ticket, the IPO block and the trade confirmation add nothing.
        overview = analytics.overview(demo["rows"], month="2026-11")
        assert overview["month"]["spend"] == pytest.approx(13443.0)

    def test_income_and_refunds_are_separate_from_spend(self, demo):
        overview = analytics.overview(demo["rows"], month="2026-11")
        # Salary 85,000 plus a 1,200 dividend: a dividend is income, not a refund.
        assert overview["month"]["income"] == pytest.approx(86200.0)
        assert overview["month"]["refunds"] == pytest.approx(1850.0)

    def test_excluded_money_splits_by_reason(self, demo):
        excluded = {}
        for row in demo["rows"]:
            if row["ledger_treatment"] != "expense" and row["amount_value"]:
                excluded[row["ledger_treatment"]] = excluded.get(row["ledger_treatment"], 0) + row["amount_value"]
        assert excluded.get("internal_transfer", 0) >= 18000
        assert excluded.get("investment", 0) > 0
        assert excluded.get("cash_withdrawal", 0) == pytest.approx(2000.0)

    def test_no_event_is_counted_twice(self, demo):
        counted = [r for r in demo["rows"] if r["counts_toward_spend"]]
        assert len({r["event_id"] for r in counted}) == len(counted)
        spend = sum(r["amount_value"] for r in counted)
        assert spend == pytest.approx(14092.0)

    def test_card_spend_is_not_a_bill(self, demo):
        croma = [r for r in demo["rows"] if (r["merchant_clean"] or "") == "Croma"]
        assert croma and croma[0]["intent"] == "expense"

    def test_government_notice_never_adds_to_spend(self, demo):
        challans = [r for r in demo["rows"] if r["intent"] == "govt_notice"]
        assert challans
        assert not any(r["counts_toward_spend"] for r in challans)


class TestSafetyAndViews:
    def test_two_messages_are_quarantined_as_phishing(self, demo):
        phishing = [r for r in demo["rows"] if r["intent"] == "phishing_suspect"]
        assert len(phishing) == 2
        assert all(r["apple_action"] == "junk" for r in phishing)

    def test_card_statement_is_reconciled_to_its_bank(self, demo):
        states = cards.build(demo["rows"])
        sbi = [c for c in states if c.last4 == "4433"]
        assert sbi and sbi[0].bank == "SBI Card"
        assert sbi[0].statement_amount == pytest.approx(12480.0)

    def test_subscriptions_have_no_unknown_billers_left(self, demo):
        series = subscriptions.build(demo["rows"], today=demo["now"].date())
        assert not [s for s in series if s.name == "Unknown biller"]

    def test_notification_plan_keeps_pushes_rare(self, demo):
        alerts = []
        for response in demo["responses"]:
            alerts.extend(a.model_dump() for a in response.alerts)
        planned = notify.plan(alerts, when=demo["now"])
        pushes = [n for n in planned if n.channel == "push"]
        assert 1 <= len(pushes) <= 3
        assert len(planned) < len(alerts), "some alerts must be deduped or silenced"

    def test_digest_reads_like_a_sentence(self, demo):
        alerts = []
        for response in demo["responses"]:
            alerts.extend(a.model_dump() for a in response.alerts)
        headline = notify.digest(notify.plan(alerts, when=demo["now"]))["headline"]
        assert headline.startswith("This week:")
        assert "scam message" in headline
