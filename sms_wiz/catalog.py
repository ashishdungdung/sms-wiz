"""Merchant catalog for Indian SMS.

Why a catalog separate from the learned dictionary
--------------------------------------------------
Indian bank SMS print the *legal* entity, not the brand: ``KIRANAKART
TECHNOLOGIES`` is Zepto, ``BUNDL TECHNOLOGIES`` is Swiggy, ``ETERNAL`` is
Zomato. A seed list has to be curated once, reviewed like code, and kept
separate from things users teach the app — so this module holds the curated
part and :mod:`sms_wiz.merchants` holds the learning.

Rules for this file
-------------------
1. **A key is what appears in the SMS**, upper-case, as printed. Aliases cover
   the variants we have actually seen.
2. **No guessing by similarity.** Every alias is explicit. A wrong brand costs
   the user trust in every category total.
3. **Categories come from the taxonomy below**, so analytics can roll up
   "essentials vs discretionary" without a second mapping.

Everything here is data. Nothing here decides an intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# --------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    slug: str
    label: str
    group: str
    essential: bool
    note: str = ""


#: The groups exist so a monthly report can answer "how much was unavoidable?"
#: without arguing about the category list.
CATEGORIES: tuple[Category, ...] = (
    Category("groceries", "Groceries & Quick Commerce", "Essentials", True, "food at home, staples"),
    Category("food", "Food & Dining", "Lifestyle", False, "eating out, delivery"),
    Category("utilities", "Utilities & Bills", "Essentials", True, "power, water, gas, broadband, mobile"),
    Category("transport", "Transport & Fuel", "Essentials", True, "cab, metro, fuel, toll"),
    Category("health", "Healthcare", "Essentials", True, "pharmacy, diagnostics, hospital"),
    Category("education", "Education", "Essentials", True, "school fees, courses, books"),
    Category("rent", "Rent & Housing", "Essentials", True, "rent, maintenance, society dues"),
    Category("shopping", "Shopping", "Lifestyle", False, "apparel, electronics, home"),
    Category("entertainment", "Entertainment & Subscriptions", "Lifestyle", False, "streaming, games, events"),
    Category("travel", "Travel & Commute", "Lifestyle", False, "flights, trains, hotels"),
    Category("personal_care", "Personal Care", "Lifestyle", False, "salon, cosmetics, fitness"),
    Category("investments", "Investments", "Money", False, "SIP, stocks, IPO, gold"),
    Category("insurance", "Insurance", "Money", False, "life, health, motor premiums"),
    Category("loans", "Loans & EMI", "Money", False, "EMI, interest, processing fees"),
    Category("fees", "Bank Fees & Charges", "Money", True, "SMS charges, AMC, forex markup"),
    Category("taxes", "Taxes & Fines", "Money", True, "challan, income tax, property tax"),
    Category("donations", "Donations", "Lifestyle", False, "religious, charity"),
    Category("p2p", "Transfers to People", "Money", False, "UPI to an individual"),
    Category("uncategorized", "Uncategorized", "Other", False, "needs review; never silently bucketed"),
)

CATEGORY_BY_SLUG: dict[str, Category] = {c.slug: c for c in CATEGORIES}
CATEGORY_BY_LABEL: dict[str, Category] = {c.label: c for c in CATEGORIES}

#: Labels used by the older event schema are mapped here so stored rows and new
#: rows roll up together instead of splitting a category in two.
LABEL_ALIASES: dict[str, str] = {
    "Groceries & Quick Commerce": "groceries",
    "Food & Dining": "food",
    "Utilities & Bills": "utilities",
    "Travel & Commute": "travel",
    "Shopping": "shopping",
    "Entertainment & Subscriptions": "entertainment",
    "Healthcare": "health",
    "Investments": "investments",
    "Taxes & Fines": "taxes",
    "Uncategorized": "uncategorized",
    "Transport": "transport",
    "Fuel": "transport",
    "Insurance": "insurance",
    "Loans": "loans",
    "Fees & Charges": "fees",
}


def category_slug(label: str | None) -> str:
    """Map any stored label (old or new) to a taxonomy slug."""
    if not label:
        return "uncategorized"
    if label in LABEL_ALIASES:
        return LABEL_ALIASES[label]
    lowered = label.strip().lower().replace(" & ", "_").replace(" ", "_")
    if lowered in CATEGORY_BY_SLUG:
        return lowered
    return "uncategorized"


def is_essential(label: str | None) -> bool:
    return CATEGORY_BY_SLUG[category_slug(label)].essential


# --------------------------------------------------------------------------
# Merchants
# --------------------------------------------------------------------------

#: ``(shown_in_sms, clean_name, category_slug, aliases)``.
#: Aliases are extra keys inserted into the dictionary, pointing at the same
#: clean name and category.
MERCHANTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # -- quick commerce / groceries ---------------------------------------
    ("KIRANAKART", "Zepto", "groceries", ("KIRANAKART TECHNOLOGIES", "ZEPTO")),
    ("BLINK COMMERCE", "Blinkit", "groceries", ("BLINKIT", "BLINKIT COMMERCE")),
    ("BUNDL TECHNOLOGIES", "Swiggy", "food", ("SWIGGY", "SWIGGY LIMITED")),
    ("SCOOTSY LOGISTICS", "Swiggy Instamart", "groceries", ("SWIGGY INSTAMART", "INSTAMART")),
    ("ETERNAL", "Zomato", "food", ("ZOMATO", "ZOMATO LIMITED", "ETERNAL LIMITED")),
    ("AVENUE SUPERMARTS", "DMart", "groceries", ("DMART", "D MART")),
    ("INNOVATIVE RETAIL", "BigBasket", "groceries", ("BIGBASKET", "BIG BASKET")),
    ("SUPERMARKET GROCERY", "BigBasket", "groceries", ()),
    ("JIOMART", "JioMart", "groceries", ("RELIANCE RETAIL",)),
    ("COUNTRY DELIGHT", "Country Delight", "groceries", ()),
    ("LICIOUS", "Licious", "groceries", ("LICIOUS FOODS",)),
    ("FRESHTOHOME", "FreshToHome", "groceries", ("FRESH TO HOME",)),
    ("MILKBASKET", "Milkbasket", "groceries", ()),
    ("BBDaily", "BBdaily", "groceries", ("BBD DAILY", "BIGBASKET DAILY")),
    ("SPENCERS", "Spencers", "groceries", ()),
    # -- food -------------------------------------------------------------
    ("HUNGERBOX", "HungerBox", "food", ()),
    ("DOMINOS", "Domino's", "food", ("JUBILANT FOODWORKS", "DOMINO S")),
    ("PIZZA HUT", "Pizza Hut", "food", ("SAPPHIRE FOODS",)),
    ("BURGER KING", "Burger King", "food", ()),
    ("KFC", "KFC", "food", ("DEVYANI INTERNATIONAL",)),
    ("MCDONALDS", "McDonald's", "food", ("HARDCASTLE", "WESTLIFE FOODWORLD")),
    ("STARBUCKS", "Starbucks", "food", ("TATA STARBUCKS",)),
    ("THIRD WAVE", "Third Wave Coffee", "food", ()),
    ("BLUE TOKAI", "Blue Tokai", "food", ()),
    ("CHAIPOINT", "Chaayos", "food", ("CHAAYOS",)),
    ("EATCLUB", "EatClub", "food", ()),
    ("REBEL FOODS", "Rebel Foods", "food", ("FAASOS", "BEHROUZ", "OVENSTORY")),
    ("BIRYANI BLUES", "Biryani Blues", "food", ()),
    ("BARBEQUE NATION", "Barbeque Nation", "food", ()),
    # -- shopping ---------------------------------------------------------
    ("AMAZON PAY INDIA", "Amazon Pay", "shopping", ("AMAZON PAY",)),
    ("AMAZON SELLER SERVICES", "Amazon", "shopping", ("AMAZON", "AMZN")),
    ("FLIPKART INTERNET", "Flipkart", "shopping", ("FLIPKART",)),
    ("MYNTRA DESIGNS", "Myntra", "shopping", ("MYNTRA",)),
    ("FSN E COMMERCE", "Nykaa", "personal_care", ("NYKAA",)),
    ("LENSKART SOLUTIONS", "Lenskart", "shopping", ("LENSKART",)),
    ("CROMA", "Croma", "shopping", ("TATA CROMA",)),
    ("RELIANCE DIGITAL", "Reliance Digital", "shopping", ()),
    ("VIJAY SALES", "Vijay Sales", "shopping", ()),
    ("TATA CLIQ", "Tata Cliq", "shopping", ()),
    ("AJIO", "AJIO", "shopping", ("RELIANCE LIFESTYLE")),
    ("MEESHO", "Meesho", "shopping", ()),
    ("SNAPDEAL", "Snapdeal", "shopping", ()),
    ("DECATHLON", "Decathlon", "shopping", ()),
    ("IKEA", "IKEA", "shopping", ()),
    ("PEPPERFRY", "Pepperfry", "shopping", ()),
    ("URBAN LADDER", "Urban Ladder", "shopping", ()),
    ("FIRSTCRY", "FirstCry", "shopping", ("BRAINBEES")),
    ("HOPSCOTCH", "Hopscotch", "shopping", ()),
    ("NYKAA FASHION", "Nykaa Fashion", "shopping", ()),
    ("TITAN", "Titan", "shopping", ("FASTTRACK",)),
    ("KALYAN JEWELLERS", "Kalyan Jewellers", "shopping", ()),
    ("TANISHQ", "Tanishq", "shopping", ()),
    # -- transport --------------------------------------------------------
    ("UBER INDIA", "Uber", "transport", ("UBER",)),
    ("ANI TECHNOLOGIES", "Ola", "transport", ("OLA", "OLA CABS")),
    ("RAPIRO", "Rapido", "transport", ("RAPIDO",)),
    ("INTERGLOBE AVIATION", "IndiGo", "travel", ("INDIGO",)),
    ("MAKEMYTRIP", "MakeMyTrip", "travel", ("MAKEMYTRIP INDIA",)),
    ("IRCTC", "IRCTC", "travel", ("INDIAN RAILWAY CATERING",)),
    ("GOIBIBO", "Goibibo", "travel", ()),
    ("CLEARTRIP", "Cleartrip", "travel", ()),
    ("IXIGO", "ixigo", "travel", ()),
    ("REDBUS", "RedBus", "travel", ("IBIBO GROUP")),
    ("OYO", "OYO", "travel", ("ORAVEL STAYS")),
    ("AIRBNB", "Airbnb", "travel", ()),
    ("INDIGO PAINTS", "Indigo Paints", "shopping", ()),
    ("HP PETROL", "HP Petrol Pump", "transport", ("HPCL", "HINDUSTAN PETROLEUM")),
    ("INDIAN OIL", "Indian Oil", "transport", ("IOCL", "INDIANOIL")),
    ("BHARAT PETROLEUM", "Bharat Petroleum", "transport", ("BPCL",)),
    ("SHELL", "Shell", "transport", ()),
    ("NAYARA ENERGY", "Nayara Energy", "transport", ()),
    ("FASTAG", "FASTag", "transport", ("NETC FASTAG", "NPCI FASTAG")),
    ("METRO RAIL", "Metro Rail", "transport", ("DMRC", "BMRCL", "MUMBAI METRO")),
    ("BEST BUS", "BEST", "transport", ()),
    ("PARKING", "Parking", "transport", ()),
    # -- utilities --------------------------------------------------------
    ("JIO", "Jio", "utilities", ("RELIANCE JIO", "JIO PLATFORMS", "MYJIO")),
    ("AIRTEL", "Airtel", "utilities", ("BHARTI AIRTEL", "AIRTEL PREPAID", "AIRTEL POSTPAID")),
    ("VODAFONE IDEA", "Vi", "utilities", ("VODAFONE", "IDEA CELLULAR")),
    ("BSNL", "BSNL", "utilities", ()),
    ("ACT FIBERNET", "ACT Fibernet", "utilities", ("ACT BROADBAND",)),
    ("HATHWAY", "Hathway", "utilities", ()),
    ("EXCITEL", "Excitel", "utilities", ()),
    ("BESCOM", "BESCOM", "utilities", ("BANGALORE ELECTRICITY",)),
    ("MSEDCL", "MSEDCL", "utilities", ("MAHARASHTRA STATE ELECTRICITY", "MAHADISCOM")),
    ("TATA POWER", "Tata Power", "utilities", ("TPDDL", "TATA POWER DELHI")),
    ("ADANI ELECTRICITY", "Adani Electricity", "utilities", ("ADANI POWER",)),
    ("CESC", "CESC", "utilities", ()),
    ("INDANE", "Indane Gas", "utilities", ("INDIAN OIL GAS",)),
    ("HP GAS", "HP Gas", "utilities", ("HP GAS AGENCY",)),
    ("BHARAT GAS", "Bharat Gas", "utilities", ()),
    ("MUNICIPAL CORPORATION", "Municipal Corporation", "taxes", ("BBMP", "MCD", "BMC")),
    ("BESCOM WATER", "Water Board", "utilities", ("BWSSB", "DELHI JAL BOARD")),
    # -- entertainment ----------------------------------------------------
    ("NETFLIX", "Netflix", "entertainment", ("NETFLIX INDIA",)),
    ("SPOTIFY", "Spotify", "entertainment", ("SPOTIFY INDIA",)),
    ("HOTSTAR", "Disney+ Hotstar", "entertainment", ("DISNEY HOTSTAR", "JIOHOTSTAR")),
    ("PRIME VIDEO", "Prime Video", "entertainment", ("AMAZON PRIME",)),
    ("SONYLIV", "SonyLIV", "entertainment", ()),
    ("ZEE5", "ZEE5", "entertainment", ()),
    ("GOOGLE", "Google", "entertainment", ("GOOGLE PLAY", "GOOGLE INDIA")),
    ("APPLE", "Apple", "entertainment", ("APPLE MEDIA", "ITUNES")),
    ("OPENAI", "OpenAI", "entertainment", ("OPENAI LLC",)),
    ("STEAM", "Steam", "entertainment", ("VALVE")),
    ("PLAYSTATION", "PlayStation", "entertainment", ("SONY PLAYSTATION")),
    ("BOOKMYSHOW", "BookMyShow", "entertainment", ("BIGTREE")),
    ("PVR", "PVR INOX", "entertainment", ("PVR CINEMAS", "INOX")),
    ("CINEPOLIS", "Cinepolis", "entertainment", ()),
    ("HOTSTAR SPORTS", "Disney+ Hotstar", "entertainment", ()),
    # -- health / personal care ------------------------------------------
    ("APOLLO PHARMACY", "Apollo Pharmacy", "health", ("APOLLO 24",)),
    ("TATA 1MG", "Tata 1mg", "health", ("TATA 1 MG",)),
    ("PHARMEASY", "PharmEasy", "health", ("API HOLDINGS",)),
    ("LAL PATHLABS", "Dr Lal PathLabs", "health", ("DR LAL PATH",)),
    ("METROPOLIS", "Metropolis", "health", ("METROPOLIS HEALTHCARE",)),
    ("THYROCARE", "Thyrocare", "health", ()),
    ("PRACTO", "Practo", "health", ()),
    ("CULT FIT", "cult.fit", "personal_care", ("CUREFIT", "CULTFIT")),
    ("URBAN COMPANY", "Urban Company", "personal_care", ("URBANCLAP")),
    ("NATURAL SALON", "Naturals Salon", "personal_care", ()),
    ("ENRICH SALON", "Enrich Salon", "personal_care", ()),
    ("NYKAA MAN", "Nykaa Man", "personal_care", ()),
    ("AMAZON FRESH", "Amazon Fresh", "groceries", ()),
    # -- money ------------------------------------------------------------
    ("NEXTBILLION TECHNOLOGY", "Groww", "investments", ("GROWW",)),
    ("ZERODHA BROKING", "Zerodha", "investments", ("ZERODHA", "ZERODHA COIN")),
    ("UPSTOX", "Upstox", "investments", ("RKSV SECURITIES",)),
    ("KUVERA", "Kuvera", "investments", ()),
    ("INDMONEY", "INDmoney", "investments", ()),
    ("ANGEL ONE", "Angel One", "investments", ("ANGEL BROKING",)),
    ("ICICI DIRECT", "ICICI Direct", "investments", ()),
    ("HDFC SECURITIES", "HDFC Securities", "investments", ()),
    ("KOTAK SECURITIES", "Kotak Securities", "investments", ()),
    ("INDIAN CLEARING CORP", "BSE StarMF (SIP)", "investments", ("BSE STARMF",)),
    ("CAMS", "CAMS", "investments", ("COMPUTER AGE MANAGEMENT",)),
    ("KFIN TECHNOLOGIES", "KFintech", "investments", ("KFINTECH", "KARVY")),
    ("CDSL", "CDSL", "investments", ("CENTRAL DEPOSITORY",)),
    ("NSDL", "NSDL", "investments", ("NATIONAL SECURITIES DEPOSITORY",)),
    ("SOVEREIGN GOLD", "Sovereign Gold Bond", "investments", ("SOVEREIGN GOLD BOND",)),
    ("TICKERTAPE", "Tickertape", "investments", ()),
    ("BAJAJ FINANCE", "Bajaj Finance", "loans", ("BAJAJ FINANCE LIMITED",)),
    ("BAJAJ FINSERV", "Bajaj Finserv", "loans", ("BAJAJ FINSERV LIMITED",)),
    ("TATA CAPITAL", "Tata Capital", "loans", ()),
    ("HDB FINANCIAL", "HDB Financial", "loans", ()),
    ("MUTHOOT", "Muthoot Finance", "loans", ("MUTHOOT FINANCE",)),
    ("IIFL", "IIFL", "loans", ("INDIA INFOLINE",)),
    ("LENDINGKART", "Lendingkart", "loans", ()),
    ("NAVI", "Navi", "loans", ("NAVI TECHNOLOGIES", "SAHIL CHAUDHARY")),
    ("KREDITBEE", "KreditBee", "loans", ()),
    ("MONEYVIEW", "Moneyview", "loans", ()),
    ("SLICE", "Slice", "loans", ("SLICEIT", "SLICE PAY")),
    ("UNI CARDS", "Uni", "loans", ("UNI PAY",)),
    ("ONE CARD", "OneCard", "loans", ("ONECARD", "FPL TECHNOLOGIES")),
    ("CRED", "CRED", "fees", ("DREAMPLUG", "CRED CLUB")),
    ("JUPITER", "Jupiter", "fees", ("JUPITER MONEY", "AMICA FINANCIAL")),
    ("FI MONEY", "Fi Money", "fees", ("EPIFI",)),
    ("PHONEPE", "PhonePe", "fees", ("PHONEPE PRIVATE",)),
    ("GOOGLE PAY", "Google Pay", "fees", ("GPAY", "TEZ")),
    ("PAYTM", "Paytm", "fees", ("ONE97", "PAYTM PAYMENTS")),
    ("BHIM", "BHIM UPI", "fees", ()),
    ("AMAZON PAY UPI", "Amazon Pay", "fees", ()),
    ("WHATSAPP PAY", "WhatsApp Pay", "fees", ()),
    ("LIC", "LIC", "insurance", ("LIFE INSURANCE CORPORATION", "LIC OF INDIA")),
    ("HDFC LIFE", "HDFC Life", "insurance", ()),
    ("ICICI PRUDENTIAL", "ICICI Prudential", "insurance", ()),
    ("SBI LIFE", "SBI Life", "insurance", ()),
    ("MAX LIFE", "Max Life", "insurance", ()),
    ("TATA AIA", "Tata AIA", "insurance", ()),
    ("STAR HEALTH", "Star Health", "insurance", ("STAR HEALTH ALLIED",)),
    ("NIVA BUPA", "Niva Bupa", "insurance", ("MAX BUPA",)),
    ("ACKO", "Acko", "insurance", ()),
    ("DIGIT", "Go Digit", "insurance", ("GO DIGIT", "DIGIT INSURANCE")),
    ("POLICYBAZAAR", "PolicyBazaar", "insurance", ("ETECH ACES",)),
    ("PARIVAHAN", "Parivahan e-Challan", "taxes", ("ECHALLAN", "E CHALLAN")),
    ("INCOME TAX", "Income Tax", "taxes", ("ITR", "TDS", "CPC")),
    ("GST", "GST", "taxes", ("GSTN",)),
    ("EPFO", "EPFO", "investments", ("PROVIDENT FUND",)),
    ("ISKCON", "ISKCON", "donations", ("ISKCON TEMPLE",)),
    ("TIRUPATI", "Tirumala Tirupati", "donations", ("TIRUMALA",)),
    ("SABARIMALA", "Sabarimala", "donations", ()),
    ("GIVE INDIA", "GiveIndia", "donations", ()),
    ("MILAAP", "Milaap", "donations", ()),
    # -- school / rent / misc --------------------------------------------
    ("SCHOOL FEES", "School Fees", "education", ("TUITION", "ACADEMY")),
    ("BYJUS", "BYJU'S", "education", ("THINK AND LEARN",)),
    ("UNACADEMY", "Unacademy", "education", ()),
    ("VEDANTU", "Vedantu", "education", ()),
    ("COURSERA", "Coursera", "education", ()),
    ("UDEMY", "Udemy", "education", ()),
    ("NOBROKER", "NoBroker", "rent", ()),
    ("MYGATE", "MyGate", "rent", ("MY GATE",)),
    ("SOCIETY MAINTENANCE", "Society Maintenance", "rent", ("MAINTENANCE",)),
    ("RENT", "Rent", "rent", ()),
)


def iter_seed_rows() -> Iterable[tuple[str, str, str]]:
    """Flatten the catalog into ``(raw_key, clean_name, category_label)`` rows.

    Aliases become their own keys pointing at the same clean name, which is what
    the dictionary's longest-key-wins lookup needs.
    """
    for raw, clean, slug, aliases in MERCHANTS:
        label = CATEGORY_BY_SLUG[slug].label
        yield raw, clean, label
        if isinstance(aliases, str):  # a single alias without a trailing comma
            aliases = (aliases,)
        for alias in aliases:
            alias = alias.strip()
            if alias:
                yield alias, clean, label


#: UPI handles that identify a merchant without a readable name:
#: ``kiranakart@axisbank``, ``zomato@paytm``, ``jio@ybl``.
VPA_HINTS: dict[str, tuple[str, str]] = {
    "kiranakart": ("Zepto", "Groceries & Quick Commerce"),
    "zepto": ("Zepto", "Groceries & Quick Commerce"),
    "blinkit": ("Blinkit", "Groceries & Quick Commerce"),
    "swiggy": ("Swiggy", "Food & Dining"),
    "swiggyinstamart": ("Swiggy Instamart", "Groceries & Quick Commerce"),
    "zomato": ("Zomato", "Food & Dining"),
    "bigbasket": ("BigBasket", "Groceries & Quick Commerce"),
    "jiomart": ("JioMart", "Groceries & Quick Commerce"),
    "dmart": ("DMart", "Groceries & Quick Commerce"),
    "countrydelight": ("Country Delight", "Groceries & Quick Commerce"),
    "licious": ("Licious", "Groceries & Quick Commerce"),
    "uber": ("Uber", "Travel & Commute"),
    "olacabs": ("Ola", "Travel & Commute"),
    "rapido": ("Rapido", "Travel & Commute"),
    "irctc": ("IRCTC", "Travel & Commute"),
    "indigo": ("IndiGo", "Travel & Commute"),
    "amazonpay": ("Amazon Pay", "Shopping"),
    "amazon": ("Amazon", "Shopping"),
    "flipkart": ("Flipkart", "Shopping"),
    "myntra": ("Myntra", "Shopping"),
    "nykaa": ("Nykaa", "Personal Care"),
    "meesho": ("Meesho", "Shopping"),
    "lenskart": ("Lenskart", "Shopping"),
    "netflix": ("Netflix", "Entertainment & Subscriptions"),
    "spotify": ("Spotify", "Entertainment & Subscriptions"),
    "hotstar": ("Disney+ Hotstar", "Entertainment & Subscriptions"),
    "googleplay": ("Google", "Entertainment & Subscriptions"),
    "jiorecharge": ("Jio", "Utilities & Bills"),
    "jio": ("Jio", "Utilities & Bills"),
    "airtel": ("Airtel", "Utilities & Bills"),
    "vi": ("Vi", "Utilities & Bills"),
    "bescom": ("BESCOM", "Utilities & Bills"),
    "tata power": ("Tata Power", "Utilities & Bills"),
    "groww": ("Groww", "Investments"),
    "zerodha": ("Zerodha", "Investments"),
    "upstox": ("Upstox", "Investments"),
    "kuvera": ("Kuvera", "Investments"),
    "cred": ("CRED", "Bank Fees & Charges"),
    "jupiter": ("Jupiter", "Bank Fees & Charges"),
    "fi": ("Fi Money", "Bank Fees & Charges"),
    "slice": ("Slice", "Loans & EMI"),
    # NB: PSP handles (@okhdfcbank, @ybl, @paytm) are deliberately absent.
    # They identify the *provider*, not the payee, so mapping them would turn a
    # transfer to a person into a "merchant" and corrupt category totals.
}


def merchant_count() -> int:
    return len({raw for raw, _, _ in iter_seed_rows()})


def category_of_clean(clean: str) -> str | None:
    """Reverse lookup for a clean brand name (used by the card/subscription views)."""
    for raw, name, slug, _aliases in MERCHANTS:
        if name == clean:
            return CATEGORY_BY_SLUG[slug].label
    return None
