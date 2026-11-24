"""Core data model for SMS Wiz.

Design rules
------------
1. **Every assertion carries evidence.** A classification without a reason is a
   bug. ``SmsEvent.evidence`` must explain *why* the parser believes something.
2. **Classification is separate from routing.** A bank's loan upsell is routed
   to Promotions while still being recognised as bank-originated. A real debit
   that also contains an EMI pitch stays in Finance and is counted once.
3. **Uncertainty is a first-class output.** Anything below the review
   threshold gets ``needs_review = True`` instead of a confident guess.
4. **Money is never double counted.** The ledger treatment, not the intent,
   decides whether an event enters spend/income totals.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Intent(str, Enum):
    """What the message *is about*."""

    EXPENSE = "expense"
    INCOME = "income"
    REFUND_INITIATED = "refund_initiated"
    REFUND_CREDITED = "refund_credited"
    CC_BILL_PAYMENT = "cc_bill_payment"
    SELF_TRANSFER = "self_transfer"
    CASH_WITHDRAWAL = "cash_withdrawal"
    INSTRUMENT_LOAD = "instrument_load"  # FASTag recharge / wallet / UPI Lite top-up
    FASTAG_TOLL = "fastag_toll"
    INVESTMENT = "investment"
    UPCOMING_DEBIT = "upcoming_debit"  # NACH / e-mandate / AutoPay pre-debit notice
    BILL_DUE = "bill_due"
    OTP = "otp"
    VOUCHER_ISSUED = "voucher_issued"
    LOGISTICS = "logistics"
    TRAVEL = "travel"  # ticket / itinerary: a booking record, not a second payment
    TRADE = "trade"  # demat order execution: shares moved, money moved elsewhere
    DIVIDEND = "dividend"
    IPO = "ipo"  # ASBA application, block, allotment, refund
    CORPORATE_ACTION = "corporate_action"  # bonus, split, rights, buyback
    GOVT_NOTICE = "govt_notice"
    HEALTH = "health"
    BANK_UPSELL = "bank_upsell"  # standalone loan / EMI / insurance pitch
    COMMERCIAL_PROMO = "commercial_promo"
    PHISHING_SUSPECT = "phishing_suspect"
    UNKNOWN = "unknown"


class LedgerTreatment(str, Enum):
    """How (or whether) an event touches money totals."""

    EXPENSE = "expense"
    INCOME = "income"
    REFUND = "refund"
    INTERNAL_TRANSFER = "internal_transfer"
    INSTRUMENT_LOAD = "instrument_load"
    CASH_WITHDRAWAL = "cash_withdrawal"
    INVESTMENT = "investment"
    #: The message is money, but not in rupees. Counting "USD 12.99" as ₹12.99
    #: would understate a card bill by a factor of ~87, so it is excluded from
    #: the rupee totals and surfaced for review until the bank posts the
    #: converted amount in its own SMS.
    FOREIGN_CURRENCY = "foreign_currency"
    NONE = "none"


class Retention(str, Enum):
    KEEP_FOREVER = "keep_forever"
    EXPIRE_30D = "expire_30d"
    EXPIRE_7D = "expire_7d"
    EXPIRE_24H = "expire_24h"
    DELETE_AFTER_EVENT = "delete_after_event"
    REVIEW = "review"


class AppleAction(str, Enum):
    """ILMessageFilterAction — the only four answers iOS accepts."""

    ALLOW = "allow"
    JUNK = "junk"
    PROMOTION = "promotion"
    TRANSACTION = "transaction"


class AppleSubAction(str, Enum):
    """ILMessageFilterSubAction (iOS 16+).

    These are Apple's fixed identifiers. We cannot invent folders or rename
    them; a sub-action only renders if the app declared it in its capabilities
    response (Apple: max five per group).
    """

    NONE = "none"
    TRANSACTIONAL_OTHERS = "transactionalOthers"
    TRANSACTIONAL_FINANCE = "transactionalFinance"
    TRANSACTIONAL_ORDERS = "transactionalOrders"
    TRANSACTIONAL_REMINDERS = "transactionalReminders"
    TRANSACTIONAL_HEALTH = "transactionalHealth"
    TRANSACTIONAL_WEATHER = "transactionalWeather"
    TRANSACTIONAL_CARRIER = "transactionalCarrier"
    TRANSACTIONAL_REWARDS = "transactionalRewards"
    TRANSACTIONAL_PUBLIC_SERVICES = "transactionalPublicServices"
    PROMOTIONAL_OTHERS = "promotionalOthers"
    PROMOTIONAL_OFFERS = "promotionalOffers"
    PROMOTIONAL_COUPONS = "promotionalCoupons"


class EntityKind(str, Enum):
    BANK = "bank"
    FINTECH = "fintech"
    GOVT = "govt"
    BRAND = "brand"
    TELECOM = "telecom"
    PERSONAL_MOBILE = "personal_mobile"
    SHORTCODE = "shortcode"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------


class Amount(BaseModel):
    value: float
    currency: str = "INR"
    raw: str = ""
    direction: Literal["debit", "credit", "none"] = "none"
    balance_after: Optional[float] = None
    credit_limit_after: Optional[float] = None


class SenderInfo(BaseModel):
    raw: str
    normalized: str = ""
    operator: Optional[str] = None
    entity_code: Optional[str] = None
    dlt_suffix: Optional[str] = None  # P / S / T / G
    entity_kind: EntityKind = EntityKind.UNKNOWN
    display_name: Optional[str] = None
    is_known_brand: bool = False
    notes: list[str] = Field(default_factory=list)


class Institution(BaseModel):
    name: str
    kind: EntityKind = EntityKind.UNKNOWN
    account_last4: Optional[str] = None
    card_last4: Optional[str] = None


class Merchant(BaseModel):
    raw: Optional[str] = None
    clean: Optional[str] = None
    category: Optional[str] = None
    source: str = "none"  # seed | learned | llm | user | none
    confidence: float = 0.0


class OtpInfo(BaseModel):
    code: str
    purpose: Literal[
        "payment",
        "mandate_registration",
        "beneficiary_addition",
        "login",
        "delivery_handover",
        "unknown",
    ] = "unknown"
    risk: Literal["low", "medium", "high"] = "low"
    service: Optional[str] = None
    associated_amount: Optional[float] = None


class VoucherInfo(BaseModel):
    brand: Optional[str] = None
    value: Optional[float] = None
    code: Optional[str] = None
    pin: Optional[str] = None
    expiry: Optional[datetime] = None


class ReminderInfo(BaseModel):
    kind: Literal["bill_due", "upcoming_debit", "renewal", "other"] = "other"
    biller: Optional[str] = None
    amount_due: Optional[float] = None
    minimum_due: Optional[float] = None
    due_date: Optional[datetime] = None
    target_account_last4: Optional[str] = None
    autopay: bool = False


class LogisticsInfo(BaseModel):
    mode: Literal["train", "bus", "flight", "hotel", "parcel", "quick_commerce", "cab", "other"] = "other"
    status: Optional[str] = None
    provider: Optional[str] = None
    pnr: Optional[str] = None
    tracking_id: Optional[str] = None
    journey_number: Optional[str] = None
    seat: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    passengers: Optional[int] = None
    event_at: Optional[datetime] = None
    handover_code: Optional[str] = None


class TradeInfo(BaseModel):
    """A demat / mutual-fund event. Deliberately not in rupees.

    The rupee leg (if any) arrives as its own bank message, so these fields
    describe the security, not the payment: quantity, price, ISIN and broker.
    """

    kind: Literal[
        "buy",
        "sell",
        "sip",
        "ipo_apply",
        "ipo_allot",
        "ipo_refund",
        "dividend",
        "bonus",
        "split",
        "rights",
        "buyback",
        "fund_transfer",
        "pledge",
        "other",
    ] = "other"
    symbol: Optional[str] = None
    isin: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    exchange: Optional[str] = None
    broker: Optional[str] = None
    folio: Optional[str] = None
    scheme: Optional[str] = None


class GovInfo(BaseModel):
    kind: Literal["challan", "tax", "epfo", "passport", "vehicle", "utility", "other"] = "other"
    reference: Optional[str] = None
    amount: Optional[float] = None
    vehicle_number: Optional[str] = None
    due_date: Optional[datetime] = None


class LinkInfo(BaseModel):
    url: str
    domain: str
    is_shortener: bool = False
    is_apk: bool = False
    official_for: Optional[str] = None  # brand the domain genuinely belongs to


class Routing(BaseModel):
    apple_action: AppleAction = AppleAction.ALLOW
    apple_subaction: AppleSubAction = AppleSubAction.NONE
    retention: Retention = Retention.REVIEW
    safe_to_bulk_delete: bool = False
    rationale: str = ""


class Confidence(BaseModel):
    score: float = 0.0
    band: Literal["high", "medium", "low"] = "low"
    needs_review: bool = True

    @classmethod
    def from_score(cls, score: float, review_below: float = 0.6) -> "Confidence":
        score = max(0.0, min(1.0, round(score, 3)))
        band = "high" if score >= 0.8 else ("medium" if score >= review_below else "low")
        return cls(score=score, band=band, needs_review=score < review_below)


class DedupInfo(BaseModel):
    status: Literal["unique", "suppressed_duplicate", "possible_duplicate"] = "unique"
    duplicate_of: Optional[str] = None
    reason: str = ""


class Alert(BaseModel):
    severity: Literal["info", "warning", "critical"] = "info"
    kind: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# The event itself
# --------------------------------------------------------------------------


class Ledger(BaseModel):
    treatment: LedgerTreatment = LedgerTreatment.NONE
    counts_toward_spend: bool = False
    counts_as_income: bool = False
    reduces_spend: bool = False
    reason: str = ""

    @classmethod
    def for_intent(cls, intent: Intent) -> "Ledger":
        rules: dict[Intent, tuple[LedgerTreatment, bool, bool, bool, str]] = {
            Intent.EXPENSE: (LedgerTreatment.EXPENSE, True, False, False, "real outflow"),
            Intent.FASTAG_TOLL: (LedgerTreatment.EXPENSE, True, False, False, "toll is real spend"),
            Intent.GOVT_NOTICE: (
                LedgerTreatment.NONE,
                False,
                False,
                False,
                "govt notice: a demand, not a payment; counted only when the payment itself arrives",
            ),
            Intent.INCOME: (LedgerTreatment.INCOME, False, True, False, "money received"),
            Intent.REFUND_CREDITED: (LedgerTreatment.REFUND, False, False, True, "refund reduces earlier spend"),
            Intent.CC_BILL_PAYMENT: (
                LedgerTreatment.INTERNAL_TRANSFER,
                False,
                False,
                False,
                "paying a card bill is a transfer, not new spend (prevents double counting)",
            ),
            Intent.SELF_TRANSFER: (
                LedgerTreatment.INTERNAL_TRANSFER,
                False,
                False,
                False,
                "money moved between the user's own accounts",
            ),
            Intent.INSTRUMENT_LOAD: (
                LedgerTreatment.INSTRUMENT_LOAD,
                False,
                False,
                False,
                "loading FASTag/wallet/UPI Lite is not spending",
            ),
            Intent.CASH_WITHDRAWAL: (
                LedgerTreatment.CASH_WITHDRAWAL,
                False,
                False,
                False,
                "cash withdrawn is still the user's money",
            ),
            Intent.INVESTMENT: (
                LedgerTreatment.INVESTMENT,
                False,
                False,
                False,
                "investment is a transfer of capital, not consumption",
            ),
            Intent.DIVIDEND: (
                LedgerTreatment.INCOME,
                False,
                True,
                False,
                "dividend received is income, and taxable",
            ),
            Intent.TRADE: (
                LedgerTreatment.NONE,
                False,
                False,
                False,
                "an order execution moves shares; the rupees are in the bank/broker message",
            ),
            Intent.IPO: (
                LedgerTreatment.NONE,
                False,
                False,
                False,
                "ASBA blocks and allotments are not spending; the debit, if any, is its own event",
            ),
            Intent.CORPORATE_ACTION: (
                LedgerTreatment.NONE,
                False,
                False,
                False,
                "bonus/split/rights change holdings, not money",
            ),
            Intent.TRAVEL: (
                LedgerTreatment.NONE,
                False,
                False,
                False,
                "a ticket is a booking record; the money is the bank debit for the same amount",
            ),
        }
        if intent in rules:
            treatment, spend, income, reduces, reason = rules[intent]
            return cls(
                treatment=treatment,
                counts_toward_spend=spend,
                counts_as_income=income,
                reduces_spend=reduces,
                reason=reason,
            )
        return cls(reason="not a money movement")


class SmsEvent(BaseModel):
    event_id: str = Field(default_factory=new_id)
    received_at: datetime = Field(default_factory=utcnow)
    source: Literal["filter", "shortcut", "manual", "import"] = "manual"
    sender_raw: str = ""
    body: str = ""
    redacted_body: str = ""

    sender: SenderInfo
    intent: Intent = Intent.UNKNOWN
    domain: str = "other"

    amount: Optional[Amount] = None
    institution: Optional[Institution] = None
    merchant: Optional[Merchant] = None
    otp: Optional[OtpInfo] = None
    voucher: Optional[VoucherInfo] = None
    reminder: Optional[ReminderInfo] = None
    logistics: Optional[LogisticsInfo] = None
    trade: Optional[TradeInfo] = None
    gov: Optional[GovInfo] = None
    links: list[LinkInfo] = Field(default_factory=list)

    ledger: Ledger = Field(default_factory=Ledger)
    routing: Routing = Field(default_factory=Routing)
    confidence: Confidence = Field(default_factory=Confidence)
    dedup: DedupInfo = Field(default_factory=DedupInfo)

    evidence: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    summary: str = ""
    analysis: dict[str, Any] = Field(default_factory=dict)
    raw_ai: Optional[dict[str, Any]] = None


SmsEvent.model_rebuild()


# --------------------------------------------------------------------------
# API payloads
# --------------------------------------------------------------------------


class IngestRequest(BaseModel):
    sender: str
    body: str
    received_at: Optional[datetime] = None
    source: Literal["filter", "shortcut", "manual", "import"] = "manual"


class IngestResponse(BaseModel):
    event: SmsEvent
    alerts: list[Alert] = Field(default_factory=list)
    stored: bool = True
    duplicate_of: Optional[str] = None


class FilterQuery(BaseModel):
    """Payload Apple's filter service sends us (network deferral)."""

    sender: str
    message_body: str


class FilterResponse(BaseModel):
    action: AppleAction
    subAction: AppleSubAction = AppleSubAction.NONE
    #: Only present when the answer is a fallback rather than a decision.
    reason: Optional[str] = None


class MonthSummary(BaseModel):
    period: str
    spend_total: float = 0.0
    income_total: float = 0.0
    refunds_total: float = 0.0
    excluded_total: float = 0.0
    by_category: dict[str, float] = Field(default_factory=dict)
    excluded_reasons: dict[str, float] = Field(default_factory=dict)
    event_count: int = 0
    needs_review_count: int = 0
