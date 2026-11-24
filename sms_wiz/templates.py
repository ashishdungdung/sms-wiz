"""Per-bank message templates.

Why templates when regex extraction already works
-------------------------------------------------
Indian banks each use one or two rigid sentence shapes for months at a time.
Knowing the shape buys three things the generic extractors cannot:

1. **Field accuracy.** ``A/c XX4092`` vs ``Card ending 4092`` vs
   ``A/c no. XXXXXXXX1234`` — the template says which one this bank means.
2. **An audit trail.** Every event records ``analysis.template``, so a wrong
   parse points at one template instead of "the extractor".
3. **A phishing signal.** A DLT header claiming HDFC, with money language, and
   *no* HDFC template matching, is a message pretending to be a bank it is not.
   That is a weak signal on its own (score +0.10) and only helps when combined
   with others — see ``forensics.py``.

Adding a bank is a data change: copy a neighbour, adjust the sender prefix and
the sentence, add one real (redacted) message to ``tests/test_templates.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

#: Named groups we understand. Anything else in a pattern is ignored.
FIELDS = ("amount", "last4", "merchant", "balance", "reference", "direction")

_NUM = r"(?P<amount>[\d,]+(?:\.\d{1,2})?)"
#: The mask before a card/account tail is written many ways: XX4092, X4092,
#: ****4092, *4092, "ending with 4092", "no. 4092". All of them mean the same.
_LAST4 = r"(?:XX+|X|xx+|\*+|ending\s+(?:with\s+)?|no\.?\s*)?(?P<last4>\d{4})\b"


@dataclass(frozen=True)
class BankTemplate:
    id: str
    bank: str
    senders: tuple[str, ...]
    pattern: re.Pattern[str]
    kind: str  # debit | credit | bill | mandate | card | any
    note: str = ""


@dataclass
class TemplateHit:
    template_id: str
    bank: str
    kind: str
    fields: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.9
    template_ids: tuple[str, ...] = ()

    @property
    def amount(self) -> Optional[float]:
        raw = self.fields.get("amount")
        if not raw:
            return None
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            return None

    @property
    def last4(self) -> Optional[str]:
        return self.fields.get("last4")

    @property
    def balance(self) -> Optional[float]:
        raw = self.fields.get("balance")
        if not raw:
            return None
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            return None

    @property
    def merchant(self) -> Optional[str]:
        raw = self.fields.get("merchant")
        return re.sub(r"\s+", " ", raw).strip(" .*-") if raw else None

    def as_dict(self) -> dict[str, object]:
        return {
            "template": self.template_id,
            "templates": list(self.template_ids or (self.template_id,)),
            "bank": self.bank,
            "kind": self.kind,
            "confidence": self.confidence,
            **{k: v for k, v in self.fields.items() if v},
        }


# --------------------------------------------------------------------------
# The templates
# --------------------------------------------------------------------------

_RAW_TEMPLATES: tuple[tuple[str, str, tuple[str, ...], str, str, str], ...] = (
    # -- HDFC --------------------------------------------------------------
    (
        "hdfc.upi_debit",
        "HDFC Bank",
        ("HDFCBK",),
        rf"Rs\.?\s*{_NUM}\s+debited\s+from\s+(?:your\s+)?(?:A/c|account)\s*{_LAST4}"
        r"(?:.*?\bto\s+(?:VPA\s+)?(?P<merchant>[A-Za-z0-9@._\- ]{3,40}?)\s*\()?",
        "debit",
        "Rs.X debited from A/c XX1234 to VPA name (UPI Ref ...)",
    ),
    (
        "hdfc.balance",
        "HDFC Bank",
        ("HDFCBK",),
        r"(?:Avl\s*Bal|Available\s+Balance)\s*:?\s*Rs\.?\s*(?P<balance>[\d,]+(?:\.\d{1,2})?)",
        "any",
        "Avl Bal: Rs.5,000.00",
    ),
    (
        "hdfc.credit",
        "HDFC Bank",
        ("HDFCBK",),
        rf"Rs\.?\s*{_NUM}\s+credited\s+to\s+(?:your\s+)?(?:A/c|account)\s*{_LAST4}",
        "credit",
        "Rs.X credited to your A/c XX1234",
    ),
    (
        "hdfc.card_due",
        "HDFC Bank",
        ("HDFCBK",),
        r"Total\s+(?:amount\s+)?due\s+(?:is\s+)?Rs\.?\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)",
        "bill",
        "Total due Rs 24,560.00",
    ),
    # -- ICICI -------------------------------------------------------------
    (
        "icici.upi_debit",
        "ICICI Bank",
        ("ICICIB",),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}"
        r"(?:.*?\bto\s+(?P<merchant>[A-Za-z0-9@._\- ]{3,40}?)\s+on)?",
        "debit",
        "Rs X debited from a/c XX7781 to swiggy@icici on ...",
    ),
    (
        "icici.credit",
        "ICICI Bank",
        ("ICICIB",),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?credited\s+to\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "credit",
        "Rs X credited to a/c XX7781",
    ),
    (
        "icici.card_payment",
        "ICICI Bank",
        ("ICICIB",),
        rf"Payment\s+of\s+Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?(?:received\s+)?towards\s+your\s+"
        rf"(?:ICICI\s+)?Credit\s+Card\s*{_LAST4}",
        "card",
        "Payment of Rs X towards your Credit Card XX4092",
    ),
    # -- SBI ---------------------------------------------------------------
    (
        "sbi.debit",
        "State Bank of India",
        ("SBIN", "SBIINB", "ATMSBI"),
        rf"(?:Your\s+)?(?:A/c|account)\s*(?:no\.?\s*)?(?:XX+X*)?{_LAST4}\s+(?:is\s+)?debited\s+"
        rf"(?:by|for|with)\s+Rs\.?\s*{_NUM}",
        "debit",
        "Your A/c no. XXXXXXXX1234 is debited by Rs.1,200.00",
    ),
    (
        "sbi.credit",
        "State Bank of India",
        ("SBIN", "SBIINB"),
        rf"(?:Your\s+)?(?:A/c|account)\s*(?:no\.?\s*)?(?:XX+X*)?{_LAST4}\s+(?:is\s+)?credited\s+"
        rf"(?:by|with)\s+Rs\.?\s*{_NUM}",
        "credit",
        "Your A/c no. XXXXXXXX1234 is credited by Rs.95,000.00",
    ),
    (
        "sbi.upi",
        "State Bank of India",
        ("SBIN", "SBIINB"),
        rf"Rs\.?\s*{_NUM}\s+debited\s+from\s+your\s+account\s+ending\s+{_LAST4}"
        r"(?:\s+to\s+(?P<merchant>[A-Za-z0-9@._\- ]{3,40}?))?(?:\s+on|\.|$)",
        "debit",
        "Rs.500 debited from your account ending 1234 to name",
    ),
    # -- Axis --------------------------------------------------------------
    (
        "axis.upi_debit",
        "Axis Bank",
        ("AXISBK", "UTIB"),
        rf"Rs\.?\s*{_NUM}\s+debited\s+from\s+(?:A/c|a/c|account)\s*{_LAST4}"
        r"(?:\s+on\s+[\d\-/A-Za-z]+\s+to\s+(?P<merchant>[A-Za-z0-9@._\- ]{3,40}?))?(?:\s+UPI|\s*\.|$)",
        "debit",
        "Rs 249 debited from A/c XX1234 on 25-02-25 to name",
    ),
    (
        "axis.card_offer",
        "Axis Bank",
        ("AXISBK",),
        r"credit\s+limit\s+(?:upgrade|increase|enhancement)\s+(?:up\s+to\s+|to\s+)?Rs\.?\s*(?P<amount>[\d,]+)",
        "any",
        "Get a credit limit upgrade up to Rs 5,00,000",
    ),
    # -- Kotak -------------------------------------------------------------
    (
        "kotak.upi_debit",
        "Kotak Mahindra Bank",
        ("KOTAKB", "KKBK"),
        rf"Sent\s+Rs\.?\s*{_NUM}\s+from\s+(?:your\s+)?Kotak\s+(?:Bank\s+)?(?:A/c|AC)\s*{_LAST4}",
        "debit",
        "Sent Rs.500 from Kotak Bank AC X1234",
    ),
    (
        "kotak.nach",
        "Kotak Mahindra Bank",
        ("KOTAKB", "KKBK"),
        rf"NACH\s+Mandate\s*:?\s*Rs\.?\s*{_NUM}.*?(?:A/c|AC)\s*{_LAST4}",
        "mandate",
        "NACH Mandate: Rs 9,999 ... A/c XX5566",
    ),
    # -- IDFC First --------------------------------------------------------
    (
        "idfc.debit",
        "IDFC First Bank",
        ("IDFCFB",),
        rf"(?:INR|Rs\.?)\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+from\s+(?:your\s+)?(?:a/c|A/c)\s*{_LAST4}",
        "debit",
        "INR 1,299.00 has been debited from your a/c XX1234",
    ),
    # -- Yes Bank ----------------------------------------------------------
    (
        "yes.debit",
        "Yes Bank",
        ("YESBK",),
        rf"(?:INR|Rs\.?)\s*{_NUM}\s+debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "INR 500 debited from a/c XX1234",
    ),
    # -- PNB / Canara / BoB / Union (PSU formats) --------------------------
    (
        "pnb.debit",
        "Punjab National Bank",
        ("PNB", "PUNB"),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+(?:from\s+)?(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs 2,000 debited from your a/c XX1234",
    ),
    # -- IndusInd / Federal / RBL / AU ------------------------------------
    (
        "indusind.debit",
        "IndusInd Bank",
        ("INDUS", "INDB"),
        rf"Rs\.?\s*{_NUM}\s+debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs 4,500 debited from your a/c XX1234",
    ),
    (
        "rbl.debit",
        "RBL Bank",
        ("RBLBK", "RATN"),
        rf"Rs\.?\s*{_NUM}\s+debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs 1,499 debited from a/c XX1234",
    ),
    # -- Cards (issuer-agnostic shapes) ------------------------------------
    (
        "card.spend",
        "card",
        ("SBICRD", "HDFCCR", "ICICIC", "AXISCR", "KOTAKC", "AMEX", "RBLCR", "IDFCCR", "FEDCRD", "INDUSC"),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?(?:spent|used)\s+(?:on|at)\s+"
        r"(?P<merchant>[A-Za-z0-9&*.\- ]{3,40}?)(?=\s+using|\s+on\s|\s+at\s|\s*\.|$)",
        "debit",
        "Rs 1,200 spent on SWIGGY using SBI Card XX4433",
    ),
    (
        "card.ending",
        "card",
        ("SBICRD", "HDFCCR", "ICICIC", "AXISCR", "KOTAKC", "AMEX", "RBLCR", "IDFCCR", "FEDCRD", "INDUSC"),
        r"\bCard\s*(?:no\.?\s*|ending\s+(?:with\s+)?|XX+|X)?(?P<last4>\d{4})\b",
        "any",
        "SBI Card XX4433 -> card last4 4433",
    ),
    (
        "card.emi_conversion",
        "card",
        ("SBICRD", "HDFCCR", "ICICIC", "AXISCR", "KOTAKC"),
        r"EMI\s+(?:conversion|plan)\s+of\s+Rs\.?\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)",
        "any",
        "EMI conversion of Rs 5,000",
    ),
    (
        "hdfc.sent",
        "HDFC Bank",
        ("HDFCBK",),
        rf"Sent\s+Rs\.?\s*{_NUM}\s+(?:from\s+)?(?:HDFC\s+Bank\s+)?(?:A/C|A/c|account)\s*{_LAST4}\s+to\s+(?P<merchant>[A-Za-z][A-Za-z .]{{2,40}}?)\s+on\s",
        "debit",
        "Sent Rs.450.00 From HDFC Bank A/C *5678 to <name> on 13-10-26 via UPI",
    ),
    # -- More PSU and private banks ----------------------------------------
    (
        "sbi.upi_debit",
        "State Bank of India",
        ("SBIN",),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}\s+(?:by|for|to)"
        r"\s+(?P<merchant>[A-Za-z0-9@._\- ]{3,40}?)(?:\s+on|\s*\.|\s*\()",
        "debit",
        "Rs.500 debited from your a/c XX1234 to VPA x@ybl",
    ),
    (
        "sbi.credit_balance",
        "State Bank of India",
        ("SBIN",),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?credited\s+to\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "credit",
        "Rs.1,000 credited to your a/c XX1234 by UPI",
    ),
    (
        "axis.debit",
        "Axis Bank",
        ("AXISBK",),
        rf"(?:INR|Rs\.?)\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+from\s+(?:your\s+)?(?:A/c|a/c|account)\s*{_LAST4}",
        "debit",
        "INR 1,200 debited from A/c XX1234",
    ),
    (
        "axis.credit",
        "Axis Bank",
        ("AXISBK",),
        rf"(?:INR|Rs\.?)\s*{_NUM}\s+(?:has\s+been\s+)?credited\s+to\s+(?:your\s+)?(?:A/c|a/c|account)\s*{_LAST4}",
        "credit",
        "INR 5,000 credited to A/c XX1234",
    ),
    (
        "indus.debit",
        "IndusInd Bank",
        ("INDUSB",),
        rf"(?:INR|Rs\.?)\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+(?:from|to)\s+(?:a/c|account|your\s+a/c)\s*{_LAST4}",
        "debit",
        "INR 2,000 debited from a/c XX1234",
    ),
    (
        "bandhan.debit",
        "Bandhan Bank",
        ("BANDHN",),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.300 debited from your a/c XX1234",
    ),
    # -- Payment banks and small finance banks ----------------------------
    (
        "ippb.debit",
        "India Post Payments Bank",
        ("IPPBIN",),
        rf"Rs\.?\s*{_NUM}\s+debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.100 debited from your IPPB a/c XX1234",
    ),
    (
        "paytmb.debit",
        "Paytm Payments Bank",
        ("PAYTMB",),
        rf"(?:Rs\.?|INR)\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.199 debited from your Paytm Bank a/c XX1234",
    ),
    (
        "airtelpb.debit",
        "Airtel Payments Bank",
        ("AIRTPB",),
        rf"(?:Rs\.?|INR)\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+from\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.99 debited from your Airtel Payments Bank a/c XX1234",
    ),
    # -- Transfers by rail (NEFT / IMPS / RTGS) ---------------------------
    (
        "bank.neft_credit",
        "bank",
        ("SBIN", "HDFCBK", "ICICIB", "AXISBK", "KOTAKB", "PNBBNK", "BOBBAR", "CNRABK", "UBIN", "IDFCFB", "YESBNK", "INDUSB"),
        rf"(?:NEFT|IMPS|RTGS)\s*(?:CR|CREDIT)?[^.]{{0,40}}?Rs\.?\s*{_NUM}\s+credited\s+(?:to\s+)?(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "credit",
        "NEFT CR-ACME-Rs 25,000 credited to A/c XX1234",
    ),
    (
        "bank.neft_debit",
        "bank",
        ("SBIN", "HDFCBK", "ICICIB", "AXISBK", "KOTAKB", "PNBBNK", "BOBBAR", "CNRABK", "UBIN", "IDFCFB", "YESBNK", "INDUSB"),
        rf"(?:NEFT|IMPS|RTGS)\s*(?:DR|DEBIT)?[^.]{{0,40}}?Rs\.?\s*{_NUM}\s+debited\s+(?:from\s+)?(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "NEFT DR-Rs 10,000 debited from A/c XX1234",
    ),
    (
        "bank.atm_withdrawal",
        "bank",
        ("SBIN", "HDFCBK", "ICICIB", "AXISBK", "KOTAKB", "PNBBNK", "BOBBAR", "CNRABK", "UBIN", "IDFCFB", "YESBNK", "INDUSB", "FEDBNK", "RBLBNK"),
        rf"(?:Rs\.?|INR)\s*{_NUM}\s+(?:has\s+been\s+)?withdrawn\s+(?:from\s+)?(?:your\s+)?(?:a/c|account|card)\s*{_LAST4}"
        r"(?:\s+at\s+(?P<merchant>[A-Za-z0-9 .\-]{3,40}?))?(?:\s+on|\s*\.|$)",
        "debit",
        "Rs 2,000 withdrawn from A/c XX1234 at ATM",
    ),
    (
        "bank.cheque",
        "bank",
        ("SBIN", "HDFCBK", "ICICIB", "AXISBK", "KOTAKB", "PNBBNK"),
        rf"(?:cheque|chq)\s*(?:no\.?\s*)?(?:[0-9]{{6}})?[^.]{{0,30}}?Rs\.?\s*{_NUM}[^.]{{0,30}}?(?:debited|cleared|paid)",
        "debit",
        "Cheque 123456 for Rs.5,000 debited",
    ),
    # -- Cards: POS, online, EMI conversions ------------------------------
    (
        "card.pos_spend",
        "card",
        ("SBICRD", "HDFCCR", "ICICIC", "AXISCR", "KOTAKC", "AMEXCR", "YESCRD", "RBLCRD", "IDFCCR", "FEDCRD", "INDUCR", "HSBCCR", "SCCARD", "CITICR", "AUCRED", "ONECRD"),
        rf"(?:Rs\.?|INR)\s*{_NUM}\s+(?:has\s+been\s+)?(?:spent|debited|used)\s+(?:on|at|for)\s+"
        r"(?:(?:your\s+)?card\s*)?(?P<merchant>[A-Za-z0-9&*.\- ]{3,40}?)(?=\s+on\s|\s+using|\s*\|\s*|\s*\.|$)",
        "debit",
        "Rs 1,200 spent at CROMA on your card",
    ),
    (
        "card.available_limit",
        "card",
        ("SBICRD", "HDFCCR", "ICICIC", "AXISCR", "KOTAKC", "AMEXCR", "YESCRD", "RBLCRD", "IDFCCR", "FEDCRD", "INDUCR", "HSBCCR", "AUCRED", "ONECRD"),
        r"(?:Avl(?:able)?\s+(?:credit\s+)?limit|Available\s+Limit)\s*(?:is|:)?\s*(?:Rs\.?|INR)\s*(?P<balance>[\d,]+(?:\.\d{1,2})?)",
        "any",
        "Available limit Rs.46,501.00",
    ),
    (
        "card.minimum_due",
        "card",
        ("SBICRD", "HDFCCR", "ICICIC", "AXISCR", "KOTAKC", "AMEXCR", "YESCRD", "RBLCRD", "IDFCCR", "FEDCRD", "INDUCR", "HSBCCR", "ONECRD"),
        r"Minimum\s+(?:amount\s+)?due\s*(?:is|:)?\s*(?:Rs\.?|INR)\s*(?P<minimum>[\d,]+(?:\.\d{1,2})?)",
        "bill",
        "Minimum due Rs.620.00",
    ),
    (
        "card.emi_conversion_done",
        "card",
        ("SBICRD", "HDFCCR", "ICICIC", "AXISCR", "KOTAKC"),
        rf"(?:converted|EMI)\s+of\s+Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?(?:converted\s+)?(?:into\s+)?(?:an?\s+)?(?:EMI|equated\s+monthly)",
        "any",
        "Rs 5,000 converted into EMI of 6 months",
    ),
    # -- Demat / broking --------------------------------------------------
    (
        "broker.trade_executed",
        "broker",
        ("ZERODH", "GROWW", "UPSTOX", "ANGEL1", "FIVEPS", "MOTILA", "SHAREK", "DHANIN", "FYERS", "KOTAKS", "ICICID", "HDFCSL", "SBICAP", "IIFLSC", "PAYTMN", "AXISDL"),
        r"\b(?:bought|sold|purchased|order\s+executed|order\s+for)\b[^.]{0,60}?(?P<quantity>[\d,]+)\s*(?:qty|shares?|units?)?"
        r"[^.]{0,40}?(?:of\s+|@\s*|at\s+)(?:Rs\.?\s*)?(?P<price>[\d,]+(?:\.\d{1,2})?)",
        "any",
        "Bought 10 shares of INFY at 1,450.00",
    ),
    (
        "broker.isin",
        "broker",
        ("CDSLIN", "NSDLIN", "ZERODH", "GROWW", "UPSTOX", "ANGEL1", "ICICID", "HDFCSL", "KOTAKS"),
        r"\bISIN\s*:?\s*(?P<isin>IN[E0-9][A-Z0-9]{10})\b",
        "any",
        "ISIN INE009A01021",
    ),
    (
        "broker.dividend",
        "broker",
        ("CDSLIN", "NSDLIN", "CAMSN", "KFINT", "ZERODH", "GROWW", "HDFCSL", "ICICID"),
        rf"\bdividend\s+of\s+(?:Rs\.?|INR)\s*{_NUM}[^.]{{0,40}}?(?:credited|paid|for\s+(?P<merchant>[A-Za-z0-9& .\-]{{3,40}}))",
        "credit",
        "Dividend of Rs 120 credited for INFOSYS",
    ),
    (
        "broker.ipo_allotment",
        "broker",
        ("CDSLIN", "NSDLIN", "ZERODH", "GROWW", "UPSTOX", "ANGEL1", "ICICID", "HDFCSL", "KOTAKS", "SBICAP"),
        r"\b(?:allotted|allotment)\b[^.]{0,60}?(?P<quantity>[\d,]+)\s*(?:shares?|units?)[^.]{0,40}?(?:of\s+)?(?P<merchant>[A-Za-z0-9& .\-]{3,40})",
        "any",
        "Allotted 15 shares of XYZ IPO",
    ),
    (
        "broker.sip",
        "broker",
        ("ZERODH", "GROWW", "KUVERA", "COININ", "CAMSN", "KFINT", "MFUTIL", "NEXTBILLION"),
        rf"\bSIP\b[^.]{{0,40}}?(?:Rs\.?|INR)\s*{_NUM}[^.]{{0,60}}?(?:for\s+|in\s+)?(?P<merchant>[A-Za-z0-9& .\-]{{3,40}}?)(?=\s+debited|\s*\.|\s*\(|$)",
        "debit",
        "SIP of Rs 5,000 for Parag Parikh Flexi Cap debited",
    ),
    (
        "broker.units_allotted",
        "broker",
        ("ZERODH", "GROWW", "KUVERA", "COININ", "CAMSN", "KFINT", "MFUTIL"),
        r"\b(?P<quantity>[\d,.]+)\s+units?\s+(?:of\s+)?(?P<merchant>[A-Za-z0-9& .\-]{3,50}?)\s+(?:allotted|credited|purchased)",
        "any",
        "12.345 units of Parag Parikh Flexi Cap allotted",
    ),
    # -- Travel -----------------------------------------------------------
    (
        "travel.irctc_ticket",
        "IRCTC",
        ("IRCTCS", "IRCTC"),
        r"(?:PNR|pNr)\s*:?\s*(?P<pnr>\d{10})[^.]{0,80}?(?:train|booked|confirmed|ticket)",
        "any",
        "PNR 1234567890 train 12627 booked",
    ),
    (
        "travel.bus_ticket",
        "travel",
        ("REDBUS", "ABHIBU", "PAYTMTR", "IXIGOT", "MMYTRI", "GOIBBO"),
        r"(?:ticket|booking|bus)\s*(?:is\s+)?(?:confirmed|booked|successful)[^.]{0,60}?(?:\bPNR\b|\bticket\s*(?:id|no))?\s*:?\s*(?P<pnr>[A-Z0-9]{6,12})?",
        "any",
        "Your bus ticket is confirmed. PNR ABC1234567",
    ),
    (
        "travel.flight_ticket",
        "travel",
        ("INDIGO", "AIRIND", "VISTAR", "SPICEJ", "AKASA", "GOFIRST", "AAHANA", "MMYTRI", "GOIBBO", "CLEART", "YATRA"),
        r"(?:PNR|booking\s+(?:id|reference))\s*:?\s*(?P<pnr>[A-Z0-9]{6})\b",
        "any",
        "PNR ABC123 confirmed",
    ),
    (
        "travel.hotel_booking",
        "travel",
        ("OYOIN", "MMYTRI", "GOIBBO", "CLEART", "TREEBO", "FABHOT", "AIRBNB"),
        r"(?:booking|reservation)\s*(?:id|no|confirmed)?\s*:?\s*(?P<pnr>[A-Z0-9\-]{6,14})",
        "any",
        "Booking ID OYOB1234567 confirmed",
    ),
    # -- Telecom / utilities ---------------------------------------------
    (
        "telco.recharge",
        "telecom",
        ("JIO", "AIRTEL", "VODAFN", "BSNL", "JIOFBR", "XSTRM", "TATAPL"),
        rf"(?:Rs\.?|INR)\s*{_NUM}\s+(?:has\s+been\s+)?(?:recharged|credited|added|paid)\s+(?:to|for|in)\s+(?:your\s+)?"
        r"(?P<merchant>[A-Za-z0-9& .\-]{3,40}?)(?:\s+(?:number|mobile|account)|\s*\.|\s*$)",
        "debit",
        "Rs.299 recharged to your Jio number",
    ),
    (
        "telco.bill_due",
        "telecom",
        ("JIO", "AIRTEL", "VODAFN", "BSNL", "JIOFBR", "XSTRM", "ACTFB", "HATHWY", "EXCITL"),
        r"(?:bill|amount)\s+(?:of\s+)?(?:Rs\.?|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s+(?:is\s+)?due\s+on\s*(?P<due>[\d\-/A-Za-z ]{6,14})",
        "bill",
        "Your bill of Rs.999 is due on 05-03-2026",
    ),
    (
        "utility.bill_payment",
        "utility",
        ("BESCOM", "MSEDCL", "TANGED", "ADILGT", "INDANE", "HPGAS", "BHARAT", "BWSSB"),
        rf"(?:Rs\.?|INR)\s*{_NUM}\s+(?:has\s+been\s+)?(?:received|paid|credited)\s+(?:towards|for)\s+(?:your\s+)?(?P<merchant>[A-Za-z0-9& .\-]{3,40}?)\s+(?:bill|account|consumer)",
        "credit",
        "Rs.1,200 received towards your BESCOM bill",
    ),
    # -- PSU / SFB shapes not covered above -------------------------------
    (
        "canara.debit",
        "Canara Bank",
        ("CNRABK",),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+(?:to|from)\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.600 debited to your a/c XX1234",
    ),
    (
        "bob.debit",
        "Bank of Baroda",
        ("BOBBAR",),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?Dr(?:awn)?\.?\s+(?:from|to)\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.900 Dr. from a/c XX1234",
    ),
    (
        "union.debit",
        "Union Bank of India",
        ("UBIN",),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+(?:in|from|to)\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.600 debited in your a/c XX1234",
    ),
    (
        "federal.debit",
        "Federal Bank",
        ("FEDBNK", "FEDCRD"),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+(?:from|to)\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.1,500 debited from your a/c XX1234",
    ),
    (
        "au.debit",
        "AU Small Finance Bank",
        ("AUSFB", "AUCRED"),
        rf"Rs\.?\s*{_NUM}\s+(?:has\s+been\s+)?debited\s+(?:from|to)\s+(?:your\s+)?(?:a/c|account)\s*{_LAST4}",
        "debit",
        "Rs.400 debited from your a/c XX1234",
    ),
    # -- Banks added from the public message corpus ------------------------
    # These were the institutions with no shape of their own: every message
    # fell through to the generic extractors, which is where field accuracy
    # goes first (``A/c XXXXXXXX1234`` vs ``Card ending 1234``). One real,
    # redacted message per bank lives in
    # ``tests/fixtures/bank_templates/public_messages.json``.
    (
        "boi.debit_with",
        "Bank of India",
        ("BOIIND",),
        rf"your\s+A/c\s*(?:no\.?\s*)?(?:XX+X*)?(?P<last4>\d{{4}})\s+has\s+been\s+debited\s+with\s+"
        rf"Rs\.?\s*{_NUM}",
        "debit",
        "Your A/c XXXXXXXX1234 has been debited with Rs.1,500.00",
    ),
    (
        "boi.credit_with",
        "Bank of India",
        ("BOIIND",),
        rf"your\s+A/c\s*(?:no\.?\s*)?(?:XX+X*)?(?P<last4>\d{{4}})\s+has\s+been\s+credited\s+with\s+"
        rf"Rs\.?\s*{_NUM}",
        "credit",
        "Your A/c XXXXXXXX1234 has been credited with Rs.5,000.00",
    ),
    (
        "cbi.debit_by",
        "Central Bank of India",
        ("CBIN",),
        rf"(?:your\s+)?A/c\s*(?:no\.?\s*)?(?:XX+X*)?(?P<last4>\d{{4}})\s+debited\s+by\s+Rs\.?\s*{_NUM}",
        "debit",
        "Your A/c XXXXXXXX1234 debited by Rs.2,000.00 on 24-11-26",
    ),
    (
        "cbi.credit_by",
        "Central Bank of India",
        ("CBIN",),
        rf"(?:your\s+)?A/c\s*(?:no\.?\s*)?(?:XX+X*)?(?P<last4>\d{{4}})\s+credited\s+by\s+Rs\.?\s*{_NUM}",
        "credit",
        "Your A/c XXXXXXXX1234 credited by Rs.12,000.00",
    ),
    (
        "citi.card_used_for",
        "Citibank India",
        ("CITIBN",),
        rf"(?:Citibank|Citi)\s+Card\s+ending\s+(?:with\s+)?(?P<last4>\d{{4}})\s+has\s+been\s+used\s+for\s+"
        rf"Rs\.?\s*{_NUM}"
        r"(?:\s+at\s+(?P<merchant>[A-Za-z0-9][A-Za-z0-9 .&_\-]{2,38}?))?(?:\s+on\s+[\d\-A-Za-z]+|\.|$)",
        "debit",
        "Your Citibank Card ending 1234 has been used for Rs 1,299.00 at AMAZON",
    ),
    (
        "dbs.account_debited",
        "DBS Bank India",
        ("DBSBNK",),
        rf"DBS\s+Bank\s+Account\s*(?:no\.?\s*)?(?:XX+)?(?P<last4>\d{{4}})\s+has\s+been\s+debited\s+by\s+"
        rf"(?:INR|Rs\.?)\s*{_NUM}",
        "debit",
        "Your DBS Bank Account XX1234 has been debited by INR 1,500.00",
    ),
    (
        "dcb.debited_from",
        "DCB Bank",
        ("DCBBNK",),
        rf"Rs\.?\s*{_NUM}\s+has\s+been\s+debited\s+from\s+your\s+DCB\s+Bank\s+A/c\s*(?:XX+)?(?P<last4>\d{{4}})",
        "debit",
        "Rs.500.00 has been debited from your DCB Bank A/c XX1234",
    ),
    (
        "cub.debited_for",
        "City Union Bank",
        ("CUBANK",),
        rf"Your\s+A/c\s*(?:No\.?\s*)?(?:XX+)?(?P<last4>\d{{4}})\s+debited\s+for\s+Rs\.?\s*{_NUM}",
        "debit",
        "Your A/c No XX1234 debited for Rs.1,000.00 on 24/11/2026",
    ),
    (
        "bom.debited_by",
        "Bank of Maharashtra",
        ("BOMIND",),
        rf"Your\s+A/c\s*(?:no\.?\s*)?(?:XX+)?(?P<last4>\d{{4}})\s+has\s+been\s+debited\s+by\s+Rs\.?\s*{_NUM}",
        "debit",
        "Your A/c XX1234 has been debited by Rs.500.00",
    ),
    (
        "amex.charged",
        "American Express",
        ("AMEXIN",),
        rf"American\s+Express\s+Card\s+ending\s+(?:with\s+)?(?P<last4>\d{{4}})\s+has\s+been\s+charged\s+"
        rf"Rs\.?\s*{_NUM}"
        r"(?:\s+at\s+(?P<merchant>[A-Za-z0-9][A-Za-z0-9 .&_\-]{2,38}?))?(?:\s+on\s+[\d\-A-Za-z]+|\.|$)",
        "debit",
        "Your American Express Card ending 3005 has been charged Rs 2,500.00 at AMAZON",
    ),
    # -- Wallets / instruments --------------------------------------------
    (
        "fastag.recharge",
        "FASTag",
        ("HDFCBK", "ICICIB", "AXISBK", "PAYTM"),
        rf"FASTag\s+recharge\s+of\s+Rs\.?\s*{_NUM}\s+(?:is\s+)?successful",
        "any",
        "FASTag recharge of Rs 500 successful",
    ),
)

TEMPLATES: tuple[BankTemplate, ...] = tuple(
    BankTemplate(id=tid, bank=bank, senders=senders, pattern=re.compile(pattern, re.IGNORECASE), kind=kind, note=note)
    for tid, bank, senders, pattern, kind, note in _RAW_TEMPLATES
)

TEMPLATE_BY_ID: dict[str, BankTemplate] = {t.id: t for t in TEMPLATES}
BANK_NAMES: tuple[str, ...] = tuple(sorted({t.bank for t in TEMPLATES}))


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


_OPERATOR_CODES = {"AD", "AX", "VM", "VK", "TM", "BT", "BM", "BW", "JD", "JK", "GX", "TA", "TD", "CP"}


def sender_prefix(sender: str) -> Optional[str]:
    """``AD-HDFCBK-T`` -> ``HDFCBK``, ``SBICRD-T`` -> ``SBICRD``, ``57575`` -> ``57575``.

    A header is usually operator / DLT entity / circle, but some banks send only
    two parts. A two-character first part is an operator code; anything longer is
    the bank's own entity code and must not be skipped.
    """
    raw = sender.strip().upper()
    parts = [p for p in re.split(r"[-_\s]+", raw) if p]
    if not parts:
        return None
    if len(parts) >= 3 and len(parts[0]) <= 3:
        return parts[1]
    if len(parts) == 2:
        return parts[1] if (parts[0] in _OPERATOR_CODES or len(parts[0]) <= 2) else parts[0]
    return parts[0]


def candidates_for(sender: str) -> list[BankTemplate]:
    prefix = sender_prefix(sender)
    if not prefix:
        return []
    return [t for t in TEMPLATES if prefix in t.senders]


def match(text: str, sender: str) -> TemplateHit | None:
    """Merge every template that fires for this sender into one hit.

    A single HDFC message often carries a debit *and* a balance sentence; two
    templates, one event. Fields are merged in template order, so the more
    specific sentence wins, and every matched id is recorded for auditing.
    """
    pools = [candidates_for(sender), [t for t in TEMPLATES if t.bank == "card"]]
    seen: set[str] = set()
    hits: list[tuple[BankTemplate, dict[str, str]]] = []
    for pool in pools:
        for template in pool:
            if template.id in seen:
                continue
            seen.add(template.id)
            found = template.pattern.search(text)
            if not found:
                continue
            fields = {k: v for k, v in found.groupdict().items() if v}
            if fields:
                hits.append((template, fields))
    if not hits:
        return None

    merged: dict[str, str] = {}
    for _template, fields in hits:
        for key, value in fields.items():
            merged.setdefault(key, value)

    primary = hits[0][0]
    confidence = max(0.92 if t.kind != "any" else 0.85 for t, _ in hits)
    return TemplateHit(
        template_id=primary.id,
        bank=primary.bank,
        kind=primary.kind,
        fields=merged,
        confidence=confidence,
        template_ids=tuple(t.id for t, _ in hits),
    )


def bank_of(sender: str) -> Optional[str]:
    """Which bank does this DLT prefix claim to be, if any?"""
    pool = candidates_for(sender)
    return pool[0].bank if pool else None


def expected_but_missing(text: str, sender: str) -> bool:
    """A known bank's header with money language but no template of its own.

    This is deliberately *only* a signal: legacy templates lag behind format
    changes, so on its own it must never quarantine anything.
    """
    pool = candidates_for(sender)
    if not pool:
        return False
    if not re.search(r"(?:Rs\.?|INR)\s*[\d,]+", text, re.IGNORECASE):
        return False
    return not any(t.pattern.search(text) for t in pool)


def catalog() -> list[dict]:
    """Machine-readable template list for the app's diagnostics screen."""
    return [
        {
            "id": t.id,
            "bank": t.bank,
            "senders": list(t.senders),
            "kind": t.kind,
            "note": t.note,
        }
        for t in TEMPLATES
    ]
