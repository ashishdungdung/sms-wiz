"""Notification policy: stingy by design, and honest about quiet hours."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from sms_wiz import notify
from sms_wiz.analytics import IST


def day() -> datetime:
    return datetime(2025, 3, 1, 11, 0, tzinfo=IST)


def night() -> datetime:
    return datetime(2025, 3, 1, 2, 0, tzinfo=IST)


def alert(kind: str, severity: str = "info", subject: str = "", message: str = "") -> dict:
    return {"kind": kind, "severity": severity, "message": message, "data": {"subject": subject}}


class TestChannels:
    def test_money_at_risk_pushes_even_at_night(self):
        notification = notify.from_alert(alert("debit_shortfall", "critical"), when=night())
        assert notification.channel == "push"
        assert notification.severity == "critical"

    def test_warnings_wait_for_morning(self):
        notification = notify.from_alert(alert("refund_overdue", "warning"), when=night())
        assert notification.channel == "digest"

    def test_phishing_is_never_a_push(self):
        # A push trains people to tap; the digest trains them to read.
        notification = notify.from_alert(alert("phishing_suspect", "critical"), when=day())
        assert notification.channel == "digest"
        assert notification.severity == "critical"

    def test_bookkeeping_is_silent(self):
        assert notify.channel_for("debit_covered", "info", day()) == "silent"

    def test_unknown_kind_defaults_to_digest_not_dropped(self):
        notification = notify.from_alert(alert("something_new"), when=day())
        assert notification.channel == "digest"

    @pytest.mark.parametrize("hour,quiet", [(21, False), (22, True), (2, True), (6, True), (7, False)])
    def test_quiet_hours_boundaries(self, hour, quiet):
        when = datetime(2025, 3, 1, hour, 0, tzinfo=IST)
        assert notify.in_quiet_hours(when) is quiet


class TestPlanning:
    def test_silent_notifications_never_reach_the_user(self):
        planned = notify.plan([alert("debit_covered"), alert("duplicate_suppressed")], when=day())
        assert [n.kind for n in planned] == ["duplicate_suppressed"]

    def test_same_subject_within_six_hours_is_deduped(self):
        first = notify.plan([alert("voucher_saved", subject="Amazon")], when=day())
        second = notify.plan(
            [alert("voucher_saved", subject="Amazon")],
            when=day() + timedelta(hours=2),
            existing=first,
        )
        assert second == []

    def test_same_subject_after_the_window_is_a_new_notification(self):
        first = notify.plan([alert("voucher_saved", subject="Amazon")], when=day())
        later = notify.plan(
            [alert("voucher_saved", subject="Amazon")],
            when=day() + timedelta(hours=7),
            existing=first,
        )
        assert len(later) == 1

    def test_different_kinds_are_never_deduped_together(self):
        planned = notify.plan(
            [alert("voucher_saved", subject="Amazon"), alert("refund_matched", subject="Amazon")],
            when=day(),
        )
        assert len(planned) == 2

    def test_duplicates_inside_one_batch_collapse(self):
        planned = notify.plan([alert("voucher_saved", subject="X"), alert("voucher_saved", subject="X")], when=day())
        assert len(planned) == 1


class TestDigest:
    def test_headline_uses_counts_and_plain_words(self):
        notifications = notify.plan(
            [
                alert("phishing_suspect", "critical", subject="a"),
                alert("phishing_suspect", "critical", subject="b"),
                alert("debit_shortfall", "critical", subject="c"),
                alert("voucher_saved", subject="d"),
            ],
            when=day(),
        )
        data = notify.digest(notifications)
        assert "2 scam messages quarantined" in data["headline"]
        assert "an auto-debit that may bounce" in data["headline"]
        assert "1 voucher saved" in data["headline"]
        assert data["count"] == 4

    def test_empty_week_says_nothing_needs_you(self):
        assert notify.digest([])["headline"].endswith("nothing needs you")

    def test_items_are_ranked_by_severity_then_recency(self):
        notifications = notify.plan(
            [alert("voucher_saved", subject="a"), alert("debit_shortfall", "critical", subject="b")],
            when=day(),
        )
        data = notify.digest(notifications)
        assert data["items"][0]["kind"] == "debit_shortfall"

    def test_quiet_hours_summary_reports_what_was_held(self):
        held = [
            notify.from_alert(alert("refund_overdue", "warning"), when=night()),
            notify.from_alert(alert("debit_shortfall", "critical"), when=night()),
        ]
        summary = notify.quiet_hours_summary(held)
        assert summary["held"] == 1
        assert summary["kinds"] == ["refund_overdue"]

    def test_every_alert_kind_the_pipeline_emits_has_a_policy(self):
        from sms_wiz import pipeline

        emitted = {
            "debit_shortfall",
            "debit_covered",
            "debit_balance_unknown",
            "high_risk_otp",
            "phishing_suspect",
            "suspicious_link",
            "duplicate_suppressed",
            "possible_duplicate",
            "refund_tracking_started",
            "refund_matched",
            "refund_overdue",
            "voucher_saved",
        }
        # If the pipeline learns a new alert and nobody sets a policy, it would
        # silently default to digest; assert the known set is covered.
        assert emitted <= set(notify.ALERT_POLICY)
        assert hasattr(pipeline, "Ingestor")
