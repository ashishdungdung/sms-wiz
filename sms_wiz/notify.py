"""Notification policy: what deserves an interruption.

The classifier already decides what a message *is*. This module decides whether
the user should be interrupted, and it is deliberately stingy:

* **critical** — money is at risk right now: a pre-debit that will bounce, an OTP
  someone else triggered, a payment that failed. Pushes, even at 2 a.m.
* **warning** — needs attention today, but not this second: refund overdue,
  card bill due in 2 days. Pushes in the day, joins the digest at night.
* **digest** — everything worth knowing, nothing worth a buzz: vouchers saved,
  duplicates suppressed, messages quarantined. A weekly line is enough.
* **silent** — bookkeeping: nothing reaches the user at all.

Phishing is deliberately **digest**, not push. A push trains the user to tap; a
line in a weekly digest ("4 messages quarantined, all fake KYC links") trains
them to check. And a notification is never the only record: every event stays in
the timeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .analytics import IST

#: (severity, default channel) per alert kind produced by the pipeline.
ALERT_POLICY: dict[str, tuple[str, str]] = {
    "debit_shortfall": ("critical", "push"),
    "high_risk_otp": ("critical", "push"),
    "payment_failed": ("critical", "push"),
    "refund_overdue": ("warning", "push"),
    "refund_amount_mismatch": ("warning", "digest"),
    "bill_due_today": ("warning", "push"),
    "phishing_suspect": ("warning", "digest"),
    "suspicious_link": ("warning", "digest"),
    "possible_duplicate": ("info", "digest"),
    "duplicate_suppressed": ("info", "digest"),
    "refund_matched": ("info", "digest"),
    "refund_tracking_started": ("info", "digest"),
    "voucher_saved": ("info", "digest"),
    "needs_review": ("info", "digest"),
    "debit_covered": ("info", "silent"),
    "debit_balance_unknown": ("info", "digest"),
}

#: Quiet hours in IST. Critical still pushes; everything else waits.
QUIET_START = 22
QUIET_END = 7

#: Same (kind, subject) within this window collapses into one notification.
DEDUPE_WINDOW = timedelta(hours=6)


@dataclass
class Notification:
    kind: str
    severity: str
    channel: str
    title: str
    body: str
    event_id: Optional[str] = None
    dedupe_key: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(IST))
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "channel": self.channel,
            "title": self.title,
            "body": self.body,
            "event_id": self.event_id,
            "dedupe_key": self.dedupe_key,
            "at": self.at.isoformat(),
            "data": self.data,
        }


def _titles(kind: str) -> str:
    return {
        "debit_shortfall": "Auto-debit may bounce",
        "high_risk_otp": "An OTP you did not ask for",
        "phishing_suspect": "Quarantined a scam message",
        "suspicious_link": "Check before tapping that link",
        "refund_matched": "Refund received",
        "refund_tracking_started": "Refund being tracked",
        "refund_amount_mismatch": "Refund is short",
        "refund_overdue": "Refund is late",
        "voucher_saved": "Voucher saved",
        "duplicate_suppressed": "Duplicate ignored",
        "possible_duplicate": "Possible duplicate",
        "needs_review": "One message needs a look",
        "debit_covered": "Auto-debit covered",
        "debit_balance_unknown": "Auto-debit coming",
        "bill_due_today": "Bill due today",
    }.get(kind, kind.replace("_", " ").capitalize())


def in_quiet_hours(when: Optional[datetime] = None) -> bool:
    when = (when or datetime.now(IST)).astimezone(IST)
    hour = when.hour
    return hour >= QUIET_START or hour < QUIET_END


_RANK = {"info": 0, "warning": 1, "critical": 2}


def channel_for(kind: str, severity: str, when: Optional[datetime] = None) -> str:
    """The *policy* picks the channel, not the severity.

    Severity only decides whether quiet hours apply. That separation is what
    stops a critical phishing alert from becoming a 2 a.m. push: the kind is
    severe, the channel is still the digest.
    """
    _policy_severity, default_channel = ALERT_POLICY.get(kind, ("info", "digest"))
    if default_channel == "silent":
        return "silent"
    if in_quiet_hours(when) and severity != "critical":
        return "digest"
    return default_channel


def from_alert(alert: dict[str, Any], event_id: Optional[str] = None, when: Optional[datetime] = None) -> Notification:
    kind = str(alert.get("kind") or "unknown")
    policy_severity, _channel = ALERT_POLICY.get(kind, ("info", "digest"))
    alert_severity = str(alert.get("severity") or "info")
    # Never understate severity in the digest; the channel is separate.
    severity = max((policy_severity, alert_severity), key=lambda s: _RANK.get(s, 0))
    when = when or datetime.now(IST)
    subject = (alert.get("data") or {}).get("subject") or ""
    return Notification(
        kind=kind,
        severity=severity,
        channel=channel_for(kind, severity, when),
        title=_titles(kind),
        body=str(alert.get("message") or "").strip(),
        event_id=event_id,
        dedupe_key=f"{kind}:{subject}",
        at=when,
        data=dict(alert.get("data") or {}),
    )


def plan(
    alerts: Iterable[dict[str, Any]],
    event_id: Optional[str] = None,
    when: Optional[datetime] = None,
    existing: Iterable[Notification] = (),
) -> list[Notification]:
    """Turn pipeline alerts into delivery decisions, deduped against history."""
    default_when = when or datetime.now(IST)
    history = list(existing)
    out: list[Notification] = []
    for alert in alerts:
        notification = from_alert(alert, event_id=event_id, when=_alert_time(alert, default_when))
        if notification.channel == "silent":
            continue
        if _is_duplicate(notification, history + out):
            continue
        out.append(notification)
    return out


def _alert_time(alert: dict[str, Any], fallback: datetime) -> datetime:
    """Prefer the alert's own timestamp: quiet hours are about *when it happened*."""
    raw = alert.get("created_at") or alert.get("at")
    if isinstance(raw, datetime):
        return raw.astimezone(IST) if raw.tzinfo else raw.replace(tzinfo=IST)
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw))
            return parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)
        except ValueError:
            pass
    return fallback


def _is_duplicate(notification: Notification, history: Iterable[Notification]) -> bool:
    for previous in history:
        if previous.kind != notification.kind:
            continue
        if abs(previous.at - notification.at) > DEDUPE_WINDOW:
            continue
        if notification.dedupe_key and previous.dedupe_key == notification.dedupe_key:
            return True
        if notification.event_id and previous.event_id == notification.event_id:
            return True
    return False


def digest(notifications: Iterable[Notification], period: str = "week") -> dict[str, Any]:
    """One readable summary. Counts first, the sharpest item second."""
    items = [n for n in notifications if n.channel in {"digest", "push"}]
    by_kind: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for item in items:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        by_severity[item.severity] = by_severity.get(item.severity, 0) + 1

    def rank(n: Notification) -> tuple[int, datetime]:
        order = {"critical": 0, "warning": 1, "info": 2}
        return (order.get(n.severity, 3), -n.at.timestamp())

    top = sorted(items, key=rank)[:5]
    parts = []
    if by_kind.get("phishing_suspect"):
        count = by_kind["phishing_suspect"]
        parts.append(f"{count} scam message{'s' if count > 1 else ''} quarantined")
    if by_kind.get("debit_shortfall"):
        parts.append("an auto-debit that may bounce")
    if by_kind.get("voucher_saved"):
        count = by_kind["voucher_saved"]
        parts.append(f"{count} voucher{'s' if count > 1 else ''} saved")
    if by_kind.get("duplicate_suppressed"):
        count = by_kind["duplicate_suppressed"]
        parts.append(f"{count} duplicate{'s' if count > 1 else ''} ignored")
    headline = "This " + period + ": " + (", ".join(parts) if parts else "nothing needs you")
    return {
        "period": period,
        "count": len(items),
        "by_kind": by_kind,
        "by_severity": by_severity,
        "headline": headline,
        "items": [n.as_dict() for n in top],
    }


def quiet_hours_summary(notifications: Iterable[Notification]) -> dict[str, Any]:
    """What was held back overnight, so nothing is lost silently."""
    held = [n for n in notifications if in_quiet_hours(n.at) and n.channel != "push"]
    return {"held": len(held), "kinds": sorted({n.kind for n in held})}
