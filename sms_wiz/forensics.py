"""Link and sender forensics for Indian SMS.

The goal is *evidence*, not certainty. Every signal contributes a named reason
string, and the final score decides between three outcomes:

* ``>= 0.7``  -> quarantine as ``phishing_suspect`` (junk folder, tab still open)
* ``0.45-0.7`` -> allow, but flag for the user to review before acting
* ``< 0.45``  -> no action

We never fetch a link automatically. Unshortening happens only on explicit user
request (out of scope for the offline engine) because fetching a link from the
user's own network is itself a fingerprinting risk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import templates
from .extract import find_urls, is_shortener, is_suspicious_tld, url_domain
from .models import EntityKind, LinkInfo, SenderInfo
from .registry import ALIAS_INDEX, DOMAIN_INDEX

QUARANTINE_THRESHOLD = 0.7
REVIEW_THRESHOLD = 0.45

#: Brands most impersonated on Indian SMS. Deliberately short and curated:
#: matching every brand name in a message body creates false positives.
IMPERSONATION_TARGETS: tuple[str, ...] = (
    "SBI", "STATE BANK", "HDFC", "ICICI", "AXIS", "KOTAK", "PNB", "BANK OF BARODA",
    "PAYTM", "PHONEPE", "GOOGLE PAY", "AMAZON", "FLIPKART", "MYNTRA", "SWIGGY", "ZOMATO",
    "INDIA POST", "INDIAPOST", "PARIVAHAN", "E-CHALLAN", "ECHALLAN", "VAHAN", "RTO",
    "INCOME TAX", "ITR", "EPFO", "UIDAI", "AADHAAR", "PASSPORT", "BESCOM", "MSEDCL",
    "IRCTC", "INDIGO", "LIC", "GST", "NETFLIX", "DELHIVERY", "BLUEDART", "TELECOM",
    "JIO", "AIRTEL", "VODAFONE",
)

#: Language that shows up in coercive scam texts.
THREAT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bwill be (?:blocked|suspended|deactivated|disconnected|terminated)\b", "threatens to block/disconnect"),
    (r"\b(?:blocked|suspended|deactivated|disconnected)\s+(?:today|tonight|within\s+\d+\s*(?:hrs|hours))\b", "urgent deadline"),
    (r"\bkyc\b.{0,30}\b(?:update|expire|pending|suspend)", "KYC expiry pressure"),
    (r"\b(?:last|final)\s+(?:chance|warning|reminder)\b", "final-warning pressure"),
    (r"\b(?:click|tap|open)\s+(?:here|this link|below)\b", "pushes a link tap"),
    (r"\b(?:verify|confirm|update)\s+(?:your\s+)?(?:details|account|card|kyc)\b", "asks to verify credentials"),
    (r"\bapk\b", "asks for an app install"),
    (r"\b(?:lucky\s+winner|lottery|prize\s+of|crore\s+winner)\b", "lottery bait"),
    (r"\b(?:whatsapp|telegram|call)\s+(?:on\s+)?(?:\+?91[\s-]?)?[6-9]\d{9}\b", "pushes contact to a personal number"),
)


@dataclass
class ForensicReport:
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    links: list[LinkInfo] = field(default_factory=list)
    claimed_brands: list[str] = field(default_factory=list)
    suspicious: bool = False
    quarantine: bool = False

    def add(self, weight: float, reason: str) -> None:
        self.score = min(1.0, round(self.score + weight, 3))
        self.reasons.append(reason)


def _brand_mentions(text: str) -> list[str]:
    upper = text.upper()
    found: list[str] = []
    for target in IMPERSONATION_TARGETS:
        if re.search(rf"(?<![A-Z0-9]){re.escape(target)}(?![A-Z0-9])", upper) and target not in found:
            found.append(target)
    return found


def _official_entity_for_alias(name: str):
    return ALIAS_INDEX.get(name.upper())


_IP_HOST = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_BRAND_IN_DOMAIN = re.compile(
    r"(sbi|hdfc|icici|axis|kotak|pnb|bob|canara|union|yesbank|idfc|indus|paytm|phonepe|"
    r"gpay|googlepay|amazon|flipkart|myntra|irctc|lic|epfo|uidai|aadhaar|parivahan|echallan)",
    re.IGNORECASE,
)


def _url_depth_signals(url: str, domain: str) -> list[tuple[float, str]]:
    """Signals that need the URL's own shape, not just its domain."""
    signals: list[tuple[float, str]] = []
    lowered = url.lower()
    host = domain.split("/")[0]

    if _IP_HOST.match(host):
        signals.append((0.3, f"link uses a bare IP address ({host})"))
    if "xn--" in host:
        signals.append((0.25, f"punycode domain can imitate a familiar name ({host})"))
    if "@" in lowered.split("//", 1)[-1].split("/")[0]:
        signals.append((0.2, "link hides its real host behind user-info (user@host)"))
    if host.count(".") >= 4:
        signals.append((0.1, f"link nests an unusual number of subdomains ({host})"))
    if len(host) >= 32:
        signals.append((0.08, "unusually long domain name"))
    if lower_tld := lowered.split("//", 1)[-1].split("/")[0]:
        if lower_tld.count("-") >= 3:
            signals.append((0.12, "domain is stacked with hyphens"))
    # A brand inside a domain that the brand does not own: sbi-kyc-verify.online
    if _BRAND_IN_DOMAIN.search(host) and DOMAIN_INDEX.get(host) is None:
        signals.append((0.18, f"domain mentions a bank/brand it does not own ({host})"))
    return signals


def analyse(text: str, sender: SenderInfo) -> ForensicReport:
    report = ForensicReport()
    links = find_urls(text)

    official_domains: set[str] = set()
    if sender.entity_code:
        entity = ALIAS_INDEX.get((sender.display_name or "").upper())
        if entity:
            official_domains.update(entity.domains)

    has_unregistered_sender = sender.entity_kind in {
        EntityKind.PERSONAL_MOBILE,
        EntityKind.SHORTCODE,
        EntityKind.UNKNOWN,
    }

    for url in links:
        domain = url_domain(url)
        entity = DOMAIN_INDEX.get(domain)
        info = LinkInfo(
            url=url,
            domain=domain,
            is_shortener=is_shortener(domain),
            is_apk=url.lower().endswith(".apk") or ".apk" in url.lower(),
            official_for=entity.name if entity else None,
        )
        report.links.append(info)

        if info.is_apk:
            report.add(0.8, f"link points to an APK download ({url})")
        if info.is_shortener:
            if has_unregistered_sender:
                report.add(0.25, f"shortened link ({domain}) from an unregistered sender")
            else:
                report.add(0.1, f"shortened link ({domain}); verify before tapping")
        if is_suspicious_tld(domain):
            report.add(0.15, f"throwaway top-level domain ({domain})")
        if entity is None:
            report.add(0.05, f"link domain {domain} is not a domain we know")
        for weight, reason in _url_depth_signals(url, domain):
            report.add(weight, reason)

    claimed = _brand_mentions(text)
    report.claimed_brands = claimed

    sender_brand = (sender.display_name or "").upper()
    for brand in claimed:
        entity = _official_entity_for_alias(brand) or _official_entity_for_alias(
            brand.replace("E-CHALLAN", "ECHALLAN").replace(" ", "")
        )
        claims_other_brand = bool(sender_brand) and brand not in sender_brand
        if has_unregistered_sender:
            report.add(0.35, f"an unregistered sender claims to be {brand}")
            break
        if claims_other_brand and entity and entity.kind in {EntityKind.BANK, EntityKind.GOVT}:
            report.add(0.3, f"sender {sender.raw} is registered to {sender.display_name} but the text claims {brand}")
            break

    # The dominant Indian SMS scam signature: an ordinary 10-digit number (or a
    # short code) naming a bank or a government body and asking you to tap a
    # link. Individually each signal is weak; together they are not.
    bank_or_gov_claim = any(
        (entity := _official_entity_for_alias(brand) or _official_entity_for_alias(brand.replace("E-CHALLAN", "ECHALLAN").replace(" ", "")))
        and entity.kind in {EntityKind.BANK, EntityKind.GOVT}
        for brand in claimed
    )
    if links and has_unregistered_sender and bank_or_gov_claim:
        report.add(
            0.3,
            "a link from an unregistered sender that claims to be a bank or government body",
        )

    for pattern, reason in THREAT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            report.add(0.08, reason)

    # A link that no legitimate entity owns, on a message that claims to be a
    # bank or government body, is the single most common Indian SMS phish.
    if links and claimed and not any(link.official_for for link in report.links):
        if sender.entity_kind in {EntityKind.BANK, EntityKind.GOVT}:
            report.add(0.25, "bank/government claim with a link that is not on any official domain")

    # A DLT header that claims a bank, with money language, and none of that
    # bank's message templates matching: the body is not written by the bank.
    # Weak on its own (+0.10) because templates lag behind format changes.
    if sender.entity_kind in {EntityKind.BANK, EntityKind.GOVT} and templates.expected_but_missing(text, sender.raw):
        report.add(0.1, f"header {sender.raw} claims a known institution but the wording matches none of its templates")

    report.score = min(1.0, round(report.score, 3))
    report.suspicious = report.score >= REVIEW_THRESHOLD
    report.quarantine = report.score >= QUARANTINE_THRESHOLD
    return report
