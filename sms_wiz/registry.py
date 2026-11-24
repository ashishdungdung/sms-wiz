"""Static knowledge: Indian sender IDs, brand domains, categories, routing table.

Everything here is data, deliberately kept apart from logic so it can be
reviewed, extended and corrected without touching the parser. Unknown sender
codes are *not* guessed — they come back as unknown with ``needs_review``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import AppleAction, AppleSubAction, EntityKind, Intent, Retention


@dataclass(frozen=True)
class Entity:
    code: str
    name: str
    kind: EntityKind
    domains: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


def _bank(code: str, name: str, domains: tuple[str, ...], aliases: tuple[str, ...] = ()) -> Entity:
    return Entity(code, name, EntityKind.BANK, domains, aliases)


def _fin(code: str, name: str, domains: tuple[str, ...], aliases: tuple[str, ...] = ()) -> Entity:
    return Entity(code, name, EntityKind.FINTECH, domains, aliases)


def _govt(code: str, name: str, domains: tuple[str, ...], aliases: tuple[str, ...] = ()) -> Entity:
    return Entity(code, name, EntityKind.GOVT, domains, aliases)


def _brand(code: str, name: str, domains: tuple[str, ...], aliases: tuple[str, ...] = ()) -> Entity:
    return Entity(code, name, EntityKind.BRAND, domains, aliases)


def _telco(code: str, name: str, domains: tuple[str, ...], aliases: tuple[str, ...] = ()) -> Entity:
    return Entity(code, name, EntityKind.TELECOM, domains, aliases)


ENTITIES: tuple[Entity, ...] = (
    # --- Large private banks -------------------------------------------------
    _bank("HDFCBK", "HDFC Bank", ("hdfcbank.com", "hdfc.com"), ("HDFC",)),
    _bank("HDFCCR", "HDFC Bank Cards", ("hdfcbank.com", "hdfc.com"), ("HDFC CARD", "HDFC CREDIT CARD")),
    _bank("ICICIB", "ICICI Bank", ("icicibank.com",), ("ICICI",)),
    _bank("ICICIC", "ICICI Bank Cards", ("icicibank.com",), ("ICICI CARD", "ICICI CREDIT CARD")),
    _bank("AXISBK", "Axis Bank", ("axisbank.com", "axisbank.co.in"), ("AXIS",)),
    _bank("AXISCR", "Axis Bank Cards", ("axisbank.com",), ("AXIS CARD",)),
    _bank("KOTAKB", "Kotak Mahindra Bank", ("kotak.com",), ("KOTAK",)),
    _bank("KOTAKC", "Kotak Cards", ("kotak.com",), ("KOTAK CARD",)),
    _bank("IDFCFB", "IDFC FIRST Bank", ("idfcfirstbank.com",), ("IDFC FIRST", "IDFC")),
    _bank("IDFCCR", "IDFC FIRST Cards", ("idfcfirstbank.com",), ("IDFC CARD",)),
    _bank("INDUSB", "IndusInd Bank", ("indusind.com",), ("INDUSIND",)),
    _bank("YESBNK", "YES Bank", ("yesbank.in",), ("YES BANK",)),
    _bank("FEDBNK", "Federal Bank", ("federalbank.co.in",), ("FEDERAL BANK",)),
    _bank("RBLBNK", "RBL Bank", ("rblbank.com",), ("RBL",)),
    _bank("RBLCRD", "RBL Cards", ("rblbank.com",), ("RBL CARD",)),
    _bank("BANDHN", "Bandhan Bank", ("bandhanbank.com",), ("BANDHAN",)),
    _bank("CITIBN", "Citibank India", ("citibank.co.in", "citibank.com"), ("CITI",)),
    _bank("SCBANK", "Standard Chartered", ("sc.com",), ("STANDARD CHARTERED", "STANCHART")),
    _bank("HSBCIN", "HSBC India", ("hsbc.co.in", "hsbc.com"), ("HSBC",)),
    _bank("DBSBNK", "DBS Bank India", ("dbs.com",), ("DBS",)),
    _bank("AMEXIN", "American Express", ("americanexpress.com", "amex.in"), ("AMEX",)),
    # --- PSU banks -----------------------------------------------------------
    _bank("SBIN", "State Bank of India", ("onlinesbi.sbi", "sbi.co.in", "onlinesbi.com"), ("SBI",)),
    _bank("SBICRD", "SBI Card", ("sbicard.com", "onlinesbi.sbi"), ("SBI CARD", "SBI CREDIT CARD")),
    # --- Insurers and NBFCs (they send premium/EMI mail from their own DLT IDs)
    _brand("LICIND", "LIC", ("licindia.in",), ("LIC", "LIFE INSURANCE CORPORATION")),
    _brand("LICOFI", "LIC", ("licindia.in",), ("LIC OF INDIA",)),
    _brand("HDFCLF", "HDFC Life", ("hdfclife.com",), ("HDFC LIFE",)),
    _brand("ICICIP", "ICICI Prudential", ("iciciprulife.com",), ("ICICI PRUDENTIAL", "ICICI PRU")),
    _brand("SBILIF", "SBI Life", ("sbilife.co.in",), ("SBI LIFE",)),
    _brand("MAXLIF", "Max Life", ("maxlifeinsurance.com",), ("MAX LIFE",)),
    _brand("TATAAIA", "Tata AIA", ("tataaia.com",), ("TATA AIA",)),
    _brand("STARHL", "Star Health", ("starhealth.in",), ("STAR HEALTH",)),
    _brand("NIVABU", "Niva Bupa", ("nivabupa.com",), ("NIVA BUPA",)),
    _brand("GODIGIT", "Go Digit", ("godigit.com",), ("GO DIGIT", "DIGIT")),
    _brand("BAJAJF", "Bajaj Finance", ("bajajfinserv.in",), ("BAJAJ FINANCE", "BAJAJ FINSERV")),
    _brand("TATCAP", "Tata Capital", ("tatacapital.com",), ("TATA CAPITAL",)),
    _brand("MUTHOT", "Muthoot Finance", ("muthootfinance.com",), ("MUTHOOT",)),
    _brand("GROWW", "Groww", ("groww.in",), ("GROWW", "NEXTBILLION")),
    _brand("ZERODH", "Zerodha", ("zerodha.com",), ("ZERODHA",)),
    _brand("CDSLIN", "CDSL", ("cdslindia.com",), ("CDSL",)),
    _brand("NSDLIN", "NSDL", ("nsdl.co.in",), ("NSDL",)),
    _brand("IRCTCS", "IRCTC", ("irctc.co.in",), ("IRCTC",)),
    _brand("VAHAN", "Parivahan", ("parivahan.gov.in", "vahan.parivahan.gov.in"), ("PARIVAHAN", "VAHAN", "RTO")),
    _brand("NETFLX", "Netflix", ("netflix.com",), ("NETFLIX",)),
    _brand("SPOTFY", "Spotify", ("spotify.com",), ("SPOTIFY",)),
    _brand("AMAZON", "Amazon", ("amazon.in", "amazon.com"), ("AMAZON", "AMZN")),
    _brand("FLIPKT", "Flipkart", ("flipkart.com",), ("FLIPKART",)),
    _brand("MYNTRA", "Myntra", ("myntra.com",), ("MYNTRA",)),
    _brand("ZOMATO", "Zomato", ("zomato.com",), ("ZOMATO", "ETERNAL")),
    _brand("SWIGGY", "Swiggy", ("swiggy.in", "swiggy.com"), ("SWIGGY", "BUNDL")),
    _brand("JIO", "Jio", ("jio.com",), ("JIO", "RELIANCE JIO")),
    _brand("AIRTEL", "Airtel", ("airtel.in",), ("AIRTEL", "BHARTI")),
    _bank("PNBBNK", "Punjab National Bank", ("pnbindia.in",), ("PNB",)),
    _bank("BOIIND", "Bank of India", ("bankofindia.co.in",), ("BANK OF INDIA",)),
    _bank("CNRABK", "Canara Bank", ("canarabank.com",), ("CANARA",)),
    _bank("UBIN", "Union Bank of India", ("unionbankofindia.co.in",), ("UNION BANK",)),
    _bank("BOBBAR", "Bank of Baroda", ("bankofbaroda.in",), ("BOB", "BANK OF BARODA")),
    _bank("IOBANK", "Indian Overseas Bank", ("iob.in",), ("IOB",)),
    _bank("PSBBNK", "Punjab & Sind Bank", ("psbindia.com",), ()),
    _bank("BOMIND", "Bank of Maharashtra", ("bankofmaharashtra.in",), ()),
    _bank("CBIN", "Central Bank of India", ("centralbankofindia.co.in",), ()),
    _bank("IPPBIN", "India Post Payments Bank", ("ippbonline.in", "indiapost.gov.in"), ("IPPB", "INDIA POST")),
    # --- Small finance / co-operative ---------------------------------------
    _bank("AUSFB", "AU Small Finance Bank", ("aubank.in",), ("AU BANK", "AU SMALL")),
    _bank("EQITAS", "Equitas Small Finance Bank", ("equitasbank.com",), ("EQUITAS",)),
    _bank("UJJIVN", "Ujjivan Small Finance Bank", ("ujjivansfb.in",), ("UJJIVAN",)),
    _bank("JANABK", "Jana Small Finance Bank", ("janabank.com",), ("JANA",)),
    _bank("SVCBNK", "SVC Co-operative Bank", ("svcbank.com",), ("SVC",)),
    _bank("SRWTBK", "Saraswat Co-operative Bank", ("saraswatbank.com",), ("SARASWAT",)),
    _bank("CSMSBK", "Cosmos Co-operative Bank", ("cosmosbank.com",), ("COSMOS",)),
    # --- Fintechs, UPI apps, BNPL -------------------------------------------
    _fin("PHONPE", "PhonePe", ("phonepe.com",), ("PHONEPE",)),
    _fin("PAYTM", "Paytm", ("paytm.com", "paytmbank.com"), ("PAYTM",)),
    _fin("GPAYIN", "Google Pay", ("pay.google.com",), ("GOOGLE PAY", "GPAY")),
    _fin("CREDIN", "CRED", ("cred.club",), ("CRED",)),
    _fin("SLICE", "Slice", ("sliceit.com",), ("SLICE",)),
    _fin("SIMPL", "Simpl", ("getsimpl.com",), ("SIMPL",)),
    _fin("LZPAY", "LazyPay", ("lazypay.in",), ("LAZYPAY",)),
    _fin("JUPITR", "Jupiter", ("jupiter.money",), ("JUPITER",)),
    _fin("FIBNK", "Fi Money", ("fi.money",), ("FI MONEY",)),
    _fin("NAVIIN", "Navi", ("navi.com",), ("NAVI",)),
    _fin("GROWW", "Groww", ("groww.in",), ("GROWW",)),
    _fin("ZERODH", "Zerodha", ("zerodha.com",), ("ZERODHA",)),
    _fin("AMAZNP", "Amazon Pay", ("amazon.in", "amazonpay.in"), ("AMAZON PAY",)),
    _fin("MOBIKW", "MobiKwik", ("mobikwik.com",), ("MOBIKWIK",)),
    _fin("FREECH", "Freecharge", ("freecharge.in",), ("FREECHARGE",)),
    # --- Government / utilities ---------------------------------------------
    _govt("VAHAN", "Parivahan / Vahan", ("parivahan.gov.in", "vahan.parivahan.gov.in"), ("PARIVAHAN", "VAHAN", "RTO")),
    _govt("ECHALN", "Parivahan e-Challan", ("echallan.parivahan.gov.in", "parivahan.gov.in"), ("E-CHALLAN", "ECHALLAN")),
    _govt("ITDEPT", "Income Tax Department", ("incometax.gov.in",), ("INCOME TAX",)),
    _govt("EPFOIN", "EPFO", ("epfindia.gov.in",), ("EPFO", "PF")),
    _govt("UIDAI", "UIDAI / Aadhaar", ("uidai.gov.in",), ("UIDAI", "AADHAAR")),
    _govt("PASPRT", "Passport Seva", ("passportindia.gov.in",), ("PASSPORT",)),
    _govt("DGLCKR", "DigiLocker", ("digilocker.gov.in",), ("DIGILOCKER",)),
    _govt("GSTN", "GST Network", ("gst.gov.in",), ("GST",)),
    _govt("BESCOM", "BESCOM", ("bescom.karnataka.gov.in", "bescom.org"), ("BESCOM",)),
    _govt("MSEDCL", "MSEDCL (Mahavitaran)", ("mahadiscom.in",), ("MSEDCL", "MAHADISCOM")),
    _govt("TANGED", "TANGEDCO", ("tangedco.gov.in",), ("TANGEDCO",)),
    _govt("ADILGT", "Adani Electricity", ("adanielectricity.com",), ("ADANI ELECTRICITY",)),
    _govt("IRCTC", "IRCTC", ("irctc.co.in",), ("IRCTC",)),
    _govt("INDANE", "Indane Gas (IOCL)", ("indane.co.in", "iocl.com"), ("INDANE",)),
    _govt("HPGAS", "HP Gas (HPCL)", ("hindustanpetroleum.com", "myhpng.in"), ("HP GAS",)),
    _govt("BHARAT", "Bharat Gas (BPCL)", ("ebharatgas.com",), ("BHARAT GAS",)),
    # --- E-commerce / transport / health brands ------------------------------
    _brand("AMAZON", "Amazon", ("amazon.in", "amazon.com"), ("AMAZON",)),
    _brand("FLPKRT", "Flipkart", ("flipkart.com",), ("FLIPKART",)),
    _brand("MYNTRA", "Myntra", ("myntra.com",), ("MYNTRA",)),
    _brand("AJIO", "AJIO", ("ajio.com",), ("AJIO",)),
    _brand("NYKAA", "Nykaa", ("nykaa.com",), ("NYKAA",)),
    _brand("LNSKRT", "Lenskart", ("lenskart.com",), ("LENSKART",)),
    _brand("SWIGGY", "Swiggy", ("swiggy.com", "swiggy.in"), ("SWIGGY",)),
    _brand("ZOMATO", "Zomato", ("zomato.com",), ("ZOMATO",)),
    _brand("BLINKT", "Blinkit", ("blinkit.com",), ("BLINKIT",)),
    _brand("ZEPTO", "Zepto", ("zepto.co", "zeptonow.com"), ("ZEPTO",)),
    _brand("BIGBSK", "BigBasket", ("bigbasket.com",), ("BIGBASKET",)),
    _brand("DMART", "DMart", ("dmart.in",), ("DMART", "D-MART")),
    _brand("TATACL", "Tata CLiQ", ("tatacliq.com",), ("TATA CLIQ",)),
    _brand("BMSHOW", "BookMyShow", ("bookmyshow.com",), ("BOOKMYSHOW",)),
    _brand("MMYTRI", "MakeMyTrip", ("makemytrip.com",), ("MAKEMYTRIP",)),
    _brand("GOIBBO", "Goibibo", ("goibibo.com",), ("GOIBIBO",)),
    _brand("IXIGO", "ixigo", ("ixigo.com",), ("IXIGO",)),
    _brand("INDIGO", "IndiGo", ("goindigo.in",), ("INDIGO", "INDIGO AIRLINES")),
    _brand("AIRIND", "Air India", ("airindia.com", "airindia.in"), ("AIR INDIA",)),
    _brand("UBERIN", "Uber India", ("uber.com",), ("UBER",)),
    _brand("OLAIND", "Ola", ("olacabs.com", "olaelectric.com"), ("OLA",)),
    _brand("RAPIDO", "Rapido", ("rapido.bike",), ("RAPIDO",)),
    _brand("BLUDAR", "Blue Dart", ("bluedart.com",), ("BLUE DART", "BLUEDART")),
    _brand("DELHIV", "Delhivery", ("delhivery.com",), ("DELHIVERY",)),
    _brand("DTDCIN", "DTDC", ("dtdc.in",), ("DTDC",)),
    _brand("1MG", "Tata 1mg", ("1mg.com",), ("1MG", "TATA 1MG")),
    _brand("APOLLO", "Apollo Pharmacy", ("apollopharmacy.in", "apollohospitals.com"), ("APOLLO",)),
    _brand("PHTMEZ", "PharmEasy", ("pharmeasy.in",), ("PHARMEASY",)),
    _brand("LALPT", "Dr Lal PathLabs", ("lalpathlabs.com",), ("LAL PATHLABS", "DR LAL")),
    _brand("METROP", "Metropolis", ("metropolisindia.com",), ("METROPOLIS",)),
    _brand("THYRO", "Thyrocare", ("thyrocare.com",), ("THYROCARE",)),
    _brand("PRACTO", "Practo", ("practo.com",), ("PRACTO",)),
    _brand("STARHL", "Star Health", ("starhealth.in",), ("STAR HEALTH",)),
    _brand("NIVABP", "Niva Bupa", ("nivabupa.com",), ("NIVA BUPA",)),
    _brand("BAJAJF", "Bajaj Finserv", ("bajajfinserv.in",), ("BAJAJ FINANCE", "BAJAJ FINSErv")),
    _brand("TATACP", "Tata Capital", ("tatacapital.com",), ("TATA CAPITAL",)),
    _brand("HDBFIN", "HDB Financial", ("hdbfs.com",), ("HDB FINANCIAL",)),
    _brand("MUTHUT", "Muthoot Finance", ("muthootfinance.com",), ("MUTHOOT",)),
    _brand("NETMED", "Netmeds", ("netmeds.com",), ("NETMEDS",)),
    _brand("CROMA", "Croma", ("croma.com",), ("CROMA",)),
    _brand("RELDIG", "Reliance Digital", ("reliancedigital.in",), ("RELIANCE DIGITAL",)),
    _brand("VIJAYS", "Vijay Sales", ("vijaysales.com",), ("VIJAY SALES",)),
    _brand("HOTSTR", "Disney+ Hotstar", ("hotstar.com", "jiohotstar.com"), ("HOTSTAR", "JIOHOTSTAR")),
    _brand("NETFLX", "Netflix", ("netflix.com",), ("NETFLIX",)),
    _brand("SPOTFY", "Spotify", ("spotify.com",), ("SPOTIFY",)),
    _brand("JIOCIN", "JioCinema", ("jiocinema.com",), ("JIOCINEMA",)),
    _brand("ZOMTRA", "Zomato Gold", ("zomato.com",), ("ZOMATO GOLD",)),
    # --- Telecom -------------------------------------------------------------
    _telco("JIO", "Jio", ("jio.com",), ("JIO", "RELIANCE JIO")),
    _telco("AIRTEL", "Airtel", ("airtel.in",), ("AIRTEL",)),
    _telco("VODAFN", "Vi (Vodafone Idea)", ("myvi.in", "vodafoneidea.com"), ("VODAFONE", "VI ") ),
    _telco("BSNL", "BSNL", ("bsnl.co.in",), ("BSNL",)),
)

ENTITIES_BY_CODE: dict[str, Entity] = {e.code: e for e in ENTITIES}

ALIAS_INDEX: dict[str, Entity] = {}
for _e in ENTITIES:
    ALIAS_INDEX[_e.name.upper()] = _e
    for _a in _e.aliases:
        ALIAS_INDEX.setdefault(_a.strip().upper(), _e)

DOMAIN_INDEX: dict[str, Entity] = {}
for _e in ENTITIES:
    for _d in _e.domains:
        DOMAIN_INDEX[_d] = _e

#: Link shorteners commonly abused in Indian SMS phishing.
SHORTENERS: frozenset[str] = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "rb.gy",
        "cutt.ly",
        "is.gd",
        "shorturl.at",
        "rebrand.ly",
        "tiny.cc",
        "ow.ly",
        "s.id",
        "gg.gg",
        "clck.ru",
        "shorturl.ae",
        "wa.link",
        "tr.ee",
        "lnkd.in",
        "bit.do",
        "soo.gd",
        "urlz.fr",
    }
)

#: TLDs that show up almost exclusively in throwaway phishing domains.
SUSPICIOUS_TLDS: frozenset[str] = frozenset(
    {"xyz", "top", "click", "link", "shop", "site", "online", "live", "icu", "buzz", "rest", "fun", "cf", "tk", "ml", "ga", "gq", "work", "loan", "zip", "mov"}
)

CATEGORIES: tuple[str, ...] = (
    "Food & Dining",
    "Groceries & Quick Commerce",
    "Shopping",
    "Travel & Commute",
    "Fuel & FASTag",
    "Utilities & Bills",
    "Healthcare",
    "Entertainment & Subscriptions",
    "Investments",
    "Taxes & Fines",
    "Education",
    "P2P Transfer",
    "Uncategorized",
)


@dataclass(frozen=True)
class Route:
    action: AppleAction
    subaction: AppleSubAction
    retention: Retention
    safe_to_bulk_delete: bool
    rationale: str


#: Intent -> Apple routing. ``None`` sub-action means "broad folder only".
ROUTING: dict[Intent, Route] = {
    Intent.EXPENSE: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.KEEP_FOREVER, False, "money movement, audit trail"),
    Intent.INCOME: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.KEEP_FOREVER, False, "money movement, audit trail"),
    Intent.CC_BILL_PAYMENT: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.KEEP_FOREVER, False, "transfer; keep for reconciliation"),
    Intent.SELF_TRANSFER: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.KEEP_FOREVER, False, "transfer; keep for reconciliation"),
    Intent.REFUND_INITIATED: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.KEEP_FOREVER, False, "open refund claim"),
    Intent.REFUND_CREDITED: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.KEEP_FOREVER, False, "closes a refund claim"),
    Intent.CASH_WITHDRAWAL: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.EXPIRE_30D, False, "cash withdrawal record"),
    Intent.INVESTMENT: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.KEEP_FOREVER, False, "tax / portfolio record"),
    Intent.INSTRUMENT_LOAD: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.EXPIRE_30D, False, "wallet/FASTag load receipt"),
    Intent.FASTAG_TOLL: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_FINANCE, Retention.EXPIRE_30D, False, "toll receipt, short-lived"),
    Intent.UPCOMING_DEBIT: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_REMINDERS, Retention.DELETE_AFTER_EVENT, False, "pre-debit notice; keep until the debit happens"),
    Intent.BILL_DUE: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_REMINDERS, Retention.DELETE_AFTER_EVENT, False, "due date reminder"),
    Intent.OTP: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_REMINDERS, Retention.EXPIRE_24H, True, "expires in minutes; Apple has no OTP sub-action"),
    Intent.VOUCHER_ISSUED: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_REWARDS, Retention.KEEP_FOREVER, False, "has monetary value; the app tracks expiry"),
    Intent.LOGISTICS: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_ORDERS, Retention.DELETE_AFTER_EVENT, True, "dead once delivered"),
    Intent.GOVT_NOTICE: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_PUBLIC_SERVICES, Retention.KEEP_FOREVER, False, "legal record"),
    Intent.HEALTH: Route(AppleAction.TRANSACTION, AppleSubAction.TRANSACTIONAL_HEALTH, Retention.KEEP_FOREVER, False, "medical record"),
    Intent.BANK_UPSELL: Route(AppleAction.PROMOTION, AppleSubAction.PROMOTIONAL_OFFERS, Retention.EXPIRE_30D, True, "marketing from a bank is still marketing"),
    Intent.COMMERCIAL_PROMO: Route(AppleAction.PROMOTION, AppleSubAction.PROMOTIONAL_COUPONS, Retention.EXPIRE_7D, True, "promotional bulk"),
    Intent.PHISHING_SUSPECT: Route(AppleAction.JUNK, AppleSubAction.NONE, Retention.EXPIRE_24H, True, "quarantine suspicious sender"),
    Intent.UNKNOWN: Route(AppleAction.ALLOW, AppleSubAction.NONE, Retention.REVIEW, False, "not enough evidence; leave in inbox"),
}

#: What the iOS app should declare in its capabilities response.
#: Apple allows five transactional and five promotional sub-actions; iOS 18
#: has been reported to show only two folders when more are declared, so keep
#: this list short and stable.
DECLARED_TRANSACTIONAL: tuple[AppleSubAction, ...] = (
    AppleSubAction.TRANSACTIONAL_FINANCE,
    AppleSubAction.TRANSACTIONAL_REMINDERS,
    AppleSubAction.TRANSACTIONAL_ORDERS,
    AppleSubAction.TRANSACTIONAL_REWARDS,
    AppleSubAction.TRANSACTIONAL_PUBLIC_SERVICES,
)
DECLARED_PROMOTIONAL: tuple[AppleSubAction, ...] = (
    AppleSubAction.PROMOTIONAL_OFFERS,
    AppleSubAction.PROMOTIONAL_COUPONS,
)


def effective_subaction(sub: AppleSubAction) -> AppleSubAction:
    """Return the sub-action only if iOS will actually render it.

    Apple silently discards a sub-action the app did not declare, falling back
    to the broad folder. We model that so the API never promises a folder the
    phone cannot show.
    """
    if sub in DECLARED_TRANSACTIONAL or sub in DECLARED_PROMOTIONAL:
        return sub
    return AppleSubAction.NONE


#: High-value merchant / legal-entity names seen in Indian bank SMS.
#: These are seeds; the store learns more and user corrections always win.
SEED_MERCHANTS: tuple[tuple[str, str, str], ...] = (
    ("AMAZON", "Amazon", "Shopping"),
    ("FLIPKART", "Flipkart", "Shopping"),
    ("MYNTRA", "Myntra", "Shopping"),
    ("SWIGGY", "Swiggy", "Food & Dining"),
    ("NETFLIX", "Netflix", "Entertainment & Subscriptions"),
    ("BUNDL TECHNOLOGIES", "Swiggy", "Food & Dining"),
    ("SCOOTSY LOGISTICS", "Swiggy Instamart", "Groceries & Quick Commerce"),
    ("SWIGGY LIMITED", "Swiggy", "Food & Dining"),
    ("ETERNAL", "Zomato", "Food & Dining"),
    ("ZOMATO", "Zomato", "Food & Dining"),
    ("BLINK COMMERCE", "Blinkit", "Groceries & Quick Commerce"),
    ("KIRANAKART", "Zepto", "Groceries & Quick Commerce"),
    ("ZEPTO", "Zepto", "Groceries & Quick Commerce"),
    ("AVENUE SUPERMARTS", "DMart", "Groceries & Quick Commerce"),
    ("INNOVATIVE RETAIL", "BigBasket", "Groceries & Quick Commerce"),
    ("SUPERMARKET GROCERY", "BigBasket", "Groceries & Quick Commerce"),
    ("INTERGLOBE AVIATION", "IndiGo", "Travel & Commute"),
    ("MAKEMYTRIP", "MakeMyTrip", "Travel & Commute"),
    ("IRCTC", "IRCTC", "Travel & Commute"),
    ("UBER INDIA", "Uber", "Travel & Commute"),
    ("ANI TECHNOLOGIES", "Ola", "Travel & Commute"),
    ("RAPIRO", "Rapido", "Travel & Commute"),
    ("FSN E-COMMERCE", "Nykaa", "Shopping"),
    ("MYNTRA DESIGNS", "Myntra", "Shopping"),
    ("FLIPKART INTERNET", "Flipkart", "Shopping"),
    ("AMAZON PAY INDIA", "Amazon Pay", "Shopping"),
    ("AMAZON SELLER SERVICES", "Amazon", "Shopping"),
    ("LENSKART SOLUTIONS", "Lenskart", "Shopping"),
    ("CROMA", "Croma", "Shopping"),
    ("RELIANCE RETAIL", "Reliance Retail", "Shopping"),
    ("NEXTBILLION TECHNOLOGY", "Groww", "Investments"),
    ("ZERODHA BROKING", "Zerodha", "Investments"),
    ("INDIAN CLEARING CORP", "BSE StarMF (SIP)", "Investments"),
    ("CAMS", "CAMS (mutual funds)", "Investments"),
    ("KFIN TECHNOLOGIES", "KFintech (mutual funds)", "Investments"),
    ("CDSL", "CDSL", "Investments"),
    ("NSDL", "NSDL", "Investments"),
    ("BAJAJ FINANCE", "Bajaj Finance", "Uncategorized"),
    ("BAJAJ FINSERV", "Bajaj Finserv", "Uncategorized"),
    ("TATA CAPITAL", "Tata Capital", "Uncategorized"),
    ("HDB FINANCIAL", "HDB Financial", "Uncategorized"),
    ("MUTHOOT", "Muthoot Finance", "Uncategorized"),
    ("HUNGERBOX", "HungerBox", "Food & Dining"),
    ("NETFLIX", "Netflix", "Entertainment & Subscriptions"),
    ("SPOTIFY", "Spotify", "Entertainment & Subscriptions"),
    ("HOTSTAR", "Disney+ Hotstar", "Entertainment & Subscriptions"),
    ("GOOGLE", "Google", "Entertainment & Subscriptions"),
    ("APPLE", "Apple", "Entertainment & Subscriptions"),
    ("OPENAI", "OpenAI", "Entertainment & Subscriptions"),
    ("APOLLO PHARMACY", "Apollo Pharmacy", "Healthcare"),
    ("TATA 1MG", "Tata 1mg", "Healthcare"),
    ("PHARMEASY", "PharmEasy", "Healthcare"),
    ("LAL PATHLABS", "Dr Lal PathLabs", "Healthcare"),
    ("PRACTO", "Practo", "Healthcare"),
    ("INDANE", "Indane Gas", "Utilities & Bills"),
    ("BESCOM", "BESCOM", "Utilities & Bills"),
    ("MSEDCL", "MSEDCL", "Utilities & Bills"),
    ("JIO", "Jio", "Utilities & Bills"),
    ("AIRTEL", "Airtel", "Utilities & Bills"),
    ("PARIVAHAN", "Parivahan e-Challan", "Taxes & Fines"),
    ("ECHALLAN", "Parivahan e-Challan", "Taxes & Fines"),
)
