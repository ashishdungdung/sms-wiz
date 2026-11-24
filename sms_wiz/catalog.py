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
    Category("utilities", "Utilities & Bills", "Essentials", True, "power, water, gas"),
    Category("telecom", "Mobile & Broadband", "Essentials", True, "mobile plan, fibre, DTH"),
    Category("brokerage", "Brokerage & Demat", "Money", False, "stock, MF and IPO transactions"),
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
    "Mobile & Broadband": "telecom",
    "Brokerage & Demat": "brokerage",
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
    ("INDIGO PAINTS", "Indigo Paints", "shopping", ()),
    ("HP PETROL", "HP Petrol Pump", "transport", ("HPCL", "HINDUSTAN PETROLEUM")),
    ("INDIAN OIL", "Indian Oil", "transport", ("IOCL", "INDIANOIL")),
    ("BHARAT PETROLEUM", "Bharat Petroleum", "transport", ("BPCL",)),
    ("SHELL", "Shell", "transport", ()),
    ("NAYARA ENERGY", "Nayara Energy", "transport", ()),
    ("METRO RAIL", "Metro Rail", "transport", ("MUMBAI METRO", "METRO CARD")),
    ("BEST BUS", "BEST", "transport", ()),
    # -- utilities --------------------------------------------------------
    ("JIO", "Jio", "telecom", ("RELIANCE JIO", "JIO PLATFORMS", "MYJIO")),
    ("AIRTEL", "Airtel", "telecom", ("BHARTI AIRTEL", "AIRTEL PREPAID", "AIRTEL POSTPAID")),
    ("VODAFONE IDEA", "Vi", "telecom", ("VODAFONE", "IDEA CELLULAR")),
    ("BSNL", "BSNL", "telecom", ()),
    ("ACT FIBERNET", "ACT Fibernet", "telecom", ("ACT BROADBAND",)),
    ("HATHWAY", "Hathway", "telecom", ()),
    ("EXCITEL", "Excitel", "telecom", ()),
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
    ("NEXTBILLION TECHNOLOGY", "Groww", "brokerage", ("GROWW",)),
    ("ZERODHA BROKING", "Zerodha", "brokerage", ("ZERODHA", "ZERODHA COIN")),
    ("UPSTOX", "Upstox", "brokerage", ("RKSV SECURITIES",)),
    ("KUVERA", "Kuvera", "brokerage", ()),
    ("INDMONEY", "INDmoney", "brokerage", ()),
    ("ANGEL ONE", "Angel One", "brokerage", ("ANGEL BROKING",)),
    ("ICICI DIRECT", "ICICI Direct", "brokerage", ()),
    ("HDFC SECURITIES", "HDFC Securities", "brokerage", ()),
    ("KOTAK SECURITIES", "Kotak Securities", "brokerage", ()),
    ("INDIAN CLEARING CORP", "BSE StarMF (SIP)", "brokerage", ("BSE STARMF",)),
    ("CAMS", "CAMS", "investments", ("COMPUTER AGE MANAGEMENT",)),
    ("KFIN TECHNOLOGIES", "KFintech", "investments", ("KFINTECH", "KARVY")),
    ("CDSL", "CDSL", "investments", ("CENTRAL DEPOSITORY",)),
    ("NSDL", "NSDL", "investments", ("NATIONAL SECURITIES DEPOSITORY",)),
    ("SOVEREIGN GOLD", "Sovereign Gold Bond", "investments", ("SOVEREIGN GOLD BOND",)),
    ("TICKERTAPE", "Tickertape", "brokerage", ()),
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

    # ======================================================================
    # Brokers and depositories (the demat half of the ledger)
    # ======================================================================
    ("ZERODHA BROKING", "Zerodha", "brokerage", ("ZERODHA", "ZERODHA COIN", "COIN")),
    ("NEXTBILLION", "Groww", "brokerage", ("GROWW", "GROWW MUTUAL FUND")),
    ("RKSV SECURITIES", "Upstox", "brokerage", ("UPSTOX",)),
    ("ANGEL ONE", "Angel One", "brokerage", ("ANGEL BROKING", "ANGELONE")),
    ("5PAISA", "5paisa", "brokerage", ("FIVE PAISA", "IIFL SECURITIES")),
    ("MOTILAL OSWAL", "Motilal Oswal", "brokerage", ("MOSL", "MOTILAL OSWAL FINANCIAL")),
    ("SHAREKHAN", "Sharekhan", "brokerage", ("SHAREKHAN BNP",)),
    ("MONEYLICIOUS", "Dhan", "brokerage", ("DHAN",)),
    ("FYERS", "Fyers", "brokerage", ("FYERS SECURITIES",)),
    ("PAYTM MONEY", "Paytm Money", "brokerage", ("PAYTMMONEY",)),
    ("INDMONEY", "INDmoney", "brokerage", ("IND MONEY",)),
    ("KUVERA", "Kuvera", "brokerage", ("KUVERA TECHNOLOGIES",)),
    ("SMALLCASE", "smallcase", "brokerage", ("SMALLCASE TECHNOLOGIES",)),
    ("GEOJIT", "Geojit", "brokerage", ("GEOJIT FINANCIAL",)),
    ("RELIGARE", "Religare Broking", "brokerage", ("RELIGARE SECURITIES",)),
    ("AXIS DIRECT", "Axis Direct", "brokerage", ("AXIS SECURITIES",)),
    ("SBI SECURITIES", "SBI Securities", "brokerage", ("SBICAP SECURITIES",)),
    ("IIFL SECURITIES", "IIFL Securities", "brokerage", ("IIFL",)),
    ("KOTAK NEO", "Kotak Neo", "brokerage", ("KOTAK SECURITIES",)),
    ("ZEBPAY", "Zebpay", "brokerage", ("ZEB PAY",)),
    ("COINDCX", "CoinDCX", "brokerage", ("COIN DCX",)),
    ("WAZIRX", "WazirX", "brokerage", ("WAZIR X",)),
    ("COINSWITCH", "CoinSwitch", "brokerage", ("COINSWITCH KUBER",)),
    ("MUDREX", "Mudrex", "brokerage", ("MUDREX TECHNOLOGIES",)),
    # depositories, exchanges, RTAs
    ("CDSL", "CDSL", "investments", ("CENTRAL DEPOSITORY SERVICES",)),
    ("NSDL", "NSDL", "investments", ("NATIONAL SECURITIES DEPOSITORY",)),
    ("CAMS", "CAMS", "investments", ("COMPUTER AGE MANAGEMENT SERVICES",)),
    ("KFIN TECHNOLOGIES", "KFintech", "investments", ("KFINTECH", "KARVY", "KARVY COMPUTERSHARE")),
    ("MF UTILITIES", "MF Utilities", "investments", ("MFU", "MF UTILITIES INDIA")),
    ("BSE STARMF", "BSE StarMF", "investments", ("BSE STARMFP", "STAR MF")),
    ("NATIONAL STOCK EXCHANGE", "NSE", "investments", ("NSE", "NSEIL")),
    ("BSE LIMITED", "BSE", "investments", ("BSE", "BOMBAY STOCK EXCHANGE")),
    ("MULTI COMMODITY EXCHANGE", "MCX", "brokerage", ("MCX",)),
    # AMCs (SIP debits print the AMC, not the broker)
    ("SBI MUTUAL FUND", "SBI Mutual Fund", "investments", ("SBI MF", "SBIMF")),
    ("HDFC MUTUAL FUND", "HDFC Mutual Fund", "investments", ("HDFC MF", "HDFC AMC")),
    ("ICICI PRUDENTIAL MUTUAL", "ICICI Prudential MF", "investments", ("ICICI PRUDENTIAL AMC", "ICICI MF")),
    ("NIPPON INDIA", "Nippon India MF", "investments", ("NIPPON INDIA MUTUAL FUND", "RELIANCE MUTUAL FUND")),
    ("AXIS MUTUAL FUND", "Axis Mutual Fund", "investments", ("AXIS MF", "AXIS AMC")),
    ("KOTAK MUTUAL FUND", "Kotak Mutual Fund", "investments", ("KOTAK MF", "KOTAK AMC")),
    ("PPFAS", "PPFAS Mutual Fund", "investments", ("PARAG PARIKH", "PARAG PARIKH FLEXI CAP")),
    ("UTI MUTUAL FUND", "UTI Mutual Fund", "investments", ("UTI MF", "UTI AMC")),
    ("FRANKLIN TEMPLETON", "Franklin Templeton", "investments", ("FRANKLIN INDIA",)),
    ("EDELWEISS MUTUAL FUND", "Edelweiss MF", "investments", ("EDELWEISS MF", "EDELWEISS AMC")),
    ("MIRAE ASSET", "Mirae Asset", "investments", ("MIRAE ASSET MUTUAL FUND", "MIRAE MF")),
    ("DSP MUTUAL FUND", "DSP Mutual Fund", "investments", ("DSP MF", "DSP BLACKROCK")),
    ("ADITYA BIRLA SUN LIFE", "Aditya Birla SL MF", "investments", ("ABSL MF", "ADITYA BIRLA MUTUAL FUND")),
    ("TATA MUTUAL FUND", "Tata Mutual Fund", "investments", ("TATA MF", "TATA ASSET MANAGEMENT")),
    ("QUANT MUTUAL FUND", "Quant Mutual Fund", "investments", ("QUANT MF",)),
    ("GROWW AMC", "Groww MF", "investments", ("GROWW MUTUAL FUND",)),
    # gold / bonds / small savings
    ("SOVEREIGN GOLD BOND", "Sovereign Gold Bond", "investments", ("SGB", "SOVEREIGN GOLD")),
    ("NATIONAL PENSION", "NPS", "investments", ("NPS TRUST", "PENSION FUND")),
    ("RBI RETAIL DIRECT", "RBI Retail Direct", "investments", ("RETAIL DIRECT", "GOVT SECURITIES")),

    # ======================================================================
    # Mobile, fibre, DTH — the bill that arrives every month
    # ======================================================================
    ("JIO", "Jio", "telecom", ("RELIANCE JIO", "JIO PLATFORMS", "MYJIO", "JIO PREPAID", "JIO POSTPAID")),
    ("JIOFIBER", "JioFiber", "telecom", ("JIO FIBER", "JIO AIRFIBER", "JIO AIR FIBER", "JIOFIBER BROADBAND")),
    ("AIRTEL", "Airtel", "telecom", ("BHARTI AIRTEL", "AIRTEL PREPAID", "AIRTEL POSTPAID", "AIRTEL XSTREAM")),
    ("AIRTEL XSTREAM FIBER", "Airtel Xstream Fiber", "telecom", ("XSTREAM FIBER", "AIRTEL FIBER", "AIRTEL BLACK")),
    ("VODAFONE IDEA", "Vi", "telecom", ("VODAFONE", "IDEA CELLULAR", "VI POSTPAID", "VI PREPAID")),
    ("BSNL", "BSNL", "telecom", ("BSNL POSTPAID", "BSNL BROADBAND", "BHARAT FIBER", "BSNL FIBER")),
    ("ACT FIBERNET", "ACT Fibernet", "telecom", ("ACT BROADBAND", "ATRIA CONVERGENCE")),
    ("HATHWAY", "Hathway", "telecom", ("HATHWAY CABLE", "HATHWAY BROADBAND")),
    ("EXCITEL", "Excitel", "telecom", ("EXCITEL BROADBAND",)),
    ("YOU BROADBAND", "YOU Broadband", "telecom", ("YOU TELECOM",)),
    ("RAILWIRE", "RailWire", "telecom", ("RAIL WIRE",)),
    ("GTPL", "GTPL", "telecom", ("GTPL KCBPL", "GTPL BROADBAND")),
    ("KERALA VISION", "Kerala Vision", "telecom", ("KERALAVISION",)),
    ("ASIANET", "Asianet", "telecom", ("ASIANET BROADBAND", "ASIANET CABLE")),
    ("TATA PLAY", "Tata Play", "telecom", ("TATA SKY", "TATASKY", "TATA PLAY FIBER")),
    ("DISH TV", "Dish TV", "telecom", ("DISHTV", "DISH TV INDIA")),
    ("D2H", "d2h", "telecom", ("VIDEOCON D2H", "D2H DTH")),
    ("SUN DIRECT", "Sun Direct", "telecom", ("SUNDIRECT",)),
    ("ORTEL", "Ortel", "telecom", ("ORTEL COMMUNICATIONS",)),
    ("AMAZON FIBER", "Amazon Fiber", "telecom", ("AMAZON FIBRE",)),
    ("FREECHARGE", "Freecharge", "telecom", ("FREECHARGE PAYMENT",)),
    ("MOBIKWIK", "MobiKwik", "telecom", ("MOBIKWIK WALLET",)),

    # ======================================================================
    # Power, water, gas — the other monthly bill
    # ======================================================================
    ("BESCOM", "BESCOM", "utilities", ("BANGALORE ELECTRICITY", "BESCOM BENGALURU")),
    ("MSEDCL", "MSEDCL", "utilities", ("MAHARASHTRA STATE ELECTRICITY", "MAHADISCOM", "MAHAVITARAN")),
    ("TANGEDCO", "TANGEDCO", "utilities", ("TAMIL NADU ELECTRICITY", "TNEB")),
    ("ADANI ELECTRICITY", "Adani Electricity", "utilities", ("ADANI ELECTRICITY MUMBAI", "ADANI POWER")),
    ("TATA POWER", "Tata Power", "utilities", ("TPDDL", "TATA POWER DELHI", "TATA POWER DDL")),
    ("BSES RAJDHANI", "BSES Rajdhani", "utilities", ("BRPL", "BSES RAJDHANI POWER")),
    ("BSES YAMUNA", "BSES Yamuna", "utilities", ("BYPL", "BSES YAMUNA POWER")),
    ("TORRENT POWER", "Torrent Power", "utilities", ("TORRENT POWER LTD",)),
    ("CESC", "CESC", "utilities", ("CESC LIMITED", "CALCUTTA ELECTRIC")),
    ("KSEB", "KSEB", "utilities", ("KERALA STATE ELECTRICITY", "KSEB LIMITED")),
    ("PSPCL", "PSPCL", "utilities", ("PUNJAB STATE POWER",)),
    ("UPPCL", "UPPCL", "utilities", ("UTTAR PRADESH POWER", "LESA")),
    ("APSPDCL", "APSPDCL", "utilities", ("ANDHRA PRADESH SOUTHERN POWER",)),
    ("TGSPDCL", "TGSPDCL", "utilities", ("TELANGANA SOUTHERN POWER", "TSSPDCL")),
    ("MESCOM", "MESCOM", "utilities", ("MANGALORE ELECTRICITY",)),
    ("BWSSB", "BWSSB", "utilities", ("BANGALORE WATER", "WATER BOARD BENGALURU")),
    ("DELHI JAL BOARD", "Delhi Jal Board", "utilities", ("DJB",)),
    ("CMC WATER", "CMC Water", "utilities", ("CHENNAI METRO WATER",)),
    ("INDANE GAS", "Indane Gas", "utilities", ("INDIAN OIL GAS", "INDANE LPG", "IOCL GAS")),
    ("HP GAS", "HP Gas", "utilities", ("HP GAS AGENCY", "HPCL GAS", "HP LPG")),
    ("BHARAT GAS", "Bharat Gas", "utilities", ("BPCL GAS", "BHARAT LPG")),
    ("GUJARAT GAS", "Gujarat Gas", "utilities", ("GUJARAT GAS LIMITED", "GGL")),
    ("MAHANAGAR GAS", "Mahanagar Gas", "utilities", ("MGL", "MAHANAGAR GAS LTD")),
    ("INDRAPRASTHA GAS", "Indraprastha Gas", "utilities", ("IGL", "INDRAPRASTHA GAS LTD")),
    ("ADANI TOTAL GAS", "Adani Total Gas", "utilities", ("ADANI GAS", "ATGL")),
    ("TORRENT GAS", "Torrent Gas", "utilities", ("TORRENT GAS LIMITED",)),
    ("MUNICIPAL CORPORATION", "Municipal Corporation", "taxes", ("BBMP", "MCD", "BMC", "PROPERTY TAX")),
    ("GOVT TREASURY", "Government Treasury", "taxes", ("TREASURY", "PAY GOVT")),

    # ======================================================================
    # Travel: rail, bus, air, hotel
    # ======================================================================
    ("IRCTC", "IRCTC", "travel", ("INDIAN RAILWAY CATERING", "IRCTC RAIL", "INDIAN RAILWAYS")),
    ("REDBUS", "RedBus", "travel", ("IBIBO GROUP", "RED BUS")),
    ("ABHIBUS", "AbhiBus", "travel", ("ABHI BUS",)),
    ("PAYTM TRAVEL", "Paytm Travel", "travel", ("PAYTM FLIGHTS", "PAYTM BUS")),
    ("IXIGO", "ixigo", "travel", ("IXIGO TRAINS", "CONFIRMTKT")),
    ("MAKEMYTRIP", "MakeMyTrip", "travel", ("MAKEMYTRIP INDIA", "MMT")),
    ("GOIBIBO", "Goibibo", "travel", ("GO IBIBO",)),
    ("CLEARTRIP", "Cleartrip", "travel", ("CLEAR TRIP",)),
    ("YATRA", "Yatra", "travel", ("YATRA ONLINE",)),
    ("INTERGLOBE AVIATION", "IndiGo", "travel", ("INDIGO", "6E")),
    ("AIR INDIA", "Air India", "travel", ("AIRINDIA", "AIR INDIA EXPRESS")),
    ("VISTARA", "Vistara", "travel", ("TATA SIA",)),
    ("SPICEJET", "SpiceJet", "travel", ("SPICE JET", "SG AIRLINE")),
    ("AKASA AIR", "Akasa Air", "travel", ("AKASA", "QP AIRLINE")),
    ("GO FIRST", "Go First", "travel", ("GOAIR", "GO AIR")),
    ("AIRASIA INDIA", "AirAsia India", "travel", ("AIR ASIA", "AIX CONNECT")),
    ("OYO", "OYO", "travel", ("ORAVEL STAYS", "OYO ROOMS")),
    ("TREEBO", "Treebo", "travel", ("TREEBO HOTELS",)),
    ("FABHOTELS", "FabHotels", "travel", ("FAB HOTELS",)),
    ("AIRBNB", "Airbnb", "travel", ("AIRBNB INDIA",)),
    ("TAJ HOTELS", "Taj Hotels", "travel", ("IHCL", "INDIAN HOTELS")),
    ("MARRIOTT", "Marriott", "travel", ("MARRIOTT INDIA",)),
    ("ITC HOTELS", "ITC Hotels", "travel", ("ITC WELCOMHOTEL",)),
    ("LEMON TREE", "Lemon Tree", "travel", ("LEMON TREE HOTELS",)),
    ("DMRC", "Delhi Metro", "transport", ("DELHI METRO", "DMRC RECHARGE")),
    ("BMRCL", "Namma Metro", "transport", ("NAMMA METRO", "BMRCL RECHARGE")),
    ("BLUSMART", "BluSmart", "transport", ("BLU SMART",)),
    ("ZOOMCAR", "Zoomcar", "transport", ("ZOOM CAR",)),
    ("REVV", "Revv", "transport", ("REVV SELF DRIVE",)),
    ("JIO BP", "Jio-bp", "transport", ("JIOBP", "RELIANCE BP")),
    ("FASTAG", "FASTag", "transport", ("NETC FASTAG", "NPCI FASTAG", "PAYTM FASTAG", "ICICI FASTAG")),
    ("TOLL PLAZA", "Toll Plaza", "transport", ("NHAI TOLL", "TOLL PAYMENT")),
    ("PARKING", "Parking", "transport", ("PARKPLUS", "PARKING FEE")),

    # ======================================================================
    # Deliveries
    # ======================================================================
    ("DELHIVERY", "Delhivery", "shopping", ("DELHIVERY LIMITED",)),
    ("BLUE DART", "Blue Dart", "shopping", ("BLUEDART", "BLUE DART EXPRESS")),
    ("DTDC", "DTDC", "shopping", ("DTDC COURIER",)),
    ("EKART", "Ekart", "shopping", ("EKART LOGISTICS",)),
    ("XPRESSBEES", "XpressBees", "shopping", ("XPRESS BEES",)),
    ("ECOM EXPRESS", "Ecom Express", "shopping", ("ECOMEXPRESS",)),
    ("SHADOWFAX", "Shadowfax", "shopping", ("SHADOW FAX",)),
    ("INDIA POST", "India Post", "shopping", ("SPEED POST", "DEPARTMENT OF POSTS")),

    # ======================================================================
    # Gaming, crypto, and the rest of the discretionary tail
    # ======================================================================
    ("DREAM11", "Dream11", "entertainment", ("DREAM 11", "SPORTSPL",)),
    ("MPL", "MPL", "entertainment", ("MOBILE PREMIER LEAGUE",)),
    ("GAMES24X7", "Games24x7", "entertainment", ("RUMMYCIRCLE", "MY11CIRCLE")),
    ("ZUpee", "Zupee", "entertainment", ("ZUPEE GAMES",)),
    ("WINZO", "WinZO", "entertainment", ("WINZO GAMES",)),
    ("POKERBAAZI", "PokerBaazi", "entertainment", ("POKER BAAZI",)),
    ("TATA PLAY BINGE", "Tata Play Binge", "entertainment", ("BINGE",)),
    ("YOUTUBE", "YouTube", "entertainment", ("GOOGLE YOUTUBE", "YT PREMIUM")),
    ("AUDIBLE", "Audible", "entertainment", ("AUDIBLE INDIA",)),
    ("KINDLE", "Kindle", "entertainment", ("AMAZON KINDLE",)),
    ("JIOCINEMA", "JioCinema", "entertainment", ("JIO CINEMA",)),
    ("SUNNXT", "Sun NXT", "entertainment", ("SUN NXT",)),
    ("AHA", "aha", "entertainment", ("AHA MAXX",)),

    # ======================================================================
    # Health, education, food, shopping additions
    # ======================================================================
    ("APOLLO HOSPITALS", "Apollo Hospitals", "health", ("APOLLO HOSPITAL",)),
    ("FORTIS", "Fortis", "health", ("FORTIS HEALTHCARE",)),
    ("MAX HEALTHCARE", "Max Healthcare", "health", ("MAX HOSPITAL",)),
    ("MANIPAL HOSPITALS", "Manipal Hospitals", "health", ("MANIPAL HEALTH",)),
    ("NARAYANA HEALTH", "Narayana Health", "health", ("NARAYANA HRUDAYALAYA",)),
    ("ASTER", "Aster", "health", ("ASTER DM",)),
    ("KIMS", "KIMS", "health", ("KIMS HOSPITALS",)),
    ("RAINBOW HOSPITAL", "Rainbow Hospital", "health", ("RAINBOW CHILDREN",)),
    ("PHYSICS WALLAH", "Physics Wallah", "education", ("PW", "PHYSICSWALLAH")),
    ("AAKASH", "Aakash", "education", ("AAKASH BYJUS", "AESL")),
    ("UPGRAD", "upGrad", "education", ("UP GRAD",)),
    ("SRI CHAITANYA", "Sri Chaitanya", "education", ("SRI CHAITANYA TECHNO",)),
    ("BARRON'S", "Barron's", "education", ("BARRONS",)),
    ("BARISTA", "Barista", "food", ("BARISTA COFFEE",)),
    ("CAFE COFFEE DAY", "Cafe Coffee Day", "food", ("CCD",)),
    ("WOW MOMO", "Wow! Momo", "food", ("WOWMOMO",)),
    ("BIRYANI BY KILO", "Biryani By Kilo", "food", ("BBK",)),
    ("THIRD WAVE COFFEE", "Third Wave", "food", ("THIRDWAVE",)),
    ("NATURES BASKET", "Nature's Basket", "groceries", ("NATURES BASKET",)),
    ("RELIANCE FRESH", "Reliance Fresh", "groceries", ("RELIANCE SMART", "SMART BAZAAR")),
    ("MORE RETAIL", "More", "groceries", ("MORE MEGASTORE",)),
    ("SPAR", "SPAR", "groceries", ("SPAR HYPERMARKET",)),
    ("METRO CASH", "Metro Cash & Carry", "groceries", ("METRO CASH AND CARRY",)),
    ("SAREGAMA", "Saregama", "entertainment", ("CARVAAN",)),
    ("MALABAR GOLD", "Malabar Gold", "shopping", ("MALABAR GOLD AND DIAMONDS",)),
    ("JOYALUKKAS", "Joyalukkas", "shopping", ("JOY ALUKKAS",)),
    ("PC JEWELLER", "PC Jeweller", "shopping", ("PCJ",)),
    ("BAJAJ ELECTRONICS", "Bajaj Electronics", "shopping", ("BAJAJ ELECTRONICS INDIA",)),
    ("SANGEETHA MOBILES", "Sangeetha Mobiles", "shopping", ("SANGEETHA",)),
    ("POORVIKA", "Poorvika", "shopping", ("POORVIKA MOBILES",)),
    ("POTHYS", "Pothys", "shopping", ("POTHYS SILKS",)),
    ("KALYAN SILKS", "Kalyan Silks", "shopping", ("KALYAN SILKS LTD",)),
)


def _flatten() -> dict[str, tuple[str, str]]:
    """``key -> (clean_name, category_label)``, last definition wins.

    A key can legitimately be written twice: ``JIO`` first appeared under
    Utilities before mobile became its own category, and ``ZERODHA`` under
    Investments before broking did. Rather than leave the resolution order to
    insertion luck, the *later* block wins — the catalog is read top to bottom
    as a history, so the newest, most specific entry is the true one.
    """
    resolved: dict[str, tuple[str, str]] = {}
    for raw, clean, slug, aliases in MERCHANTS:
        label = CATEGORY_BY_SLUG[slug].label
        if isinstance(aliases, str):  # a single alias without a trailing comma
            aliases = (aliases,)
        resolved[raw] = (clean, label)
        for alias in aliases:
            alias = alias.strip()
            if alias:
                resolved[alias] = (clean, label)
    return resolved


def duplicate_keys() -> list[str]:
    """Keys defined more than once. Reported by ``cli coverage``, not an error."""
    seen: set[str] = set()
    dupes: list[str] = []
    for raw, _clean, _slug, aliases in MERCHANTS:
        names = [raw, *((aliases,) if isinstance(aliases, str) else aliases)]
        for name in names:
            key = name.strip()
            if not key:
                continue
            if key in seen and key not in dupes:
                dupes.append(key)
            seen.add(key)
    return dupes


def iter_seed_rows() -> Iterable[tuple[str, str, str]]:
    """Flatten the catalog into unique ``(raw_key, clean_name, category_label)``.

    Aliases become their own keys pointing at the same clean name, which is what
    the dictionary's longest-key-wins lookup needs. Duplicate keys are resolved
    once here so the store never depends on insertion order.
    """
    for raw, (clean, label) in _flatten().items():
        yield raw, clean, label


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
    "jiorecharge": ("Jio", "Mobile & Broadband"),
    "jio": ("Jio", "Mobile & Broadband"),
    "airtel": ("Airtel", "Mobile & Broadband"),
    "vi": ("Vi", "Mobile & Broadband"),
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
    # Broadband, DTH and carrier handles are single words in a VPA even when the
    # brand has two ("ACTFIBERNET@axisbank"), so they need their own keys.
    "actfibernet": ("ACT Fibernet", "Mobile & Broadband"),
    "actbroadband": ("ACT Fibernet", "Mobile & Broadband"),
    "jiofiber": ("JioFiber", "Mobile & Broadband"),
    "airtelxstream": ("Airtel Xstream Fiber", "Mobile & Broadband"),
    "excitel": ("Excitel", "Mobile & Broadband"),
    "hathway": ("Hathway", "Mobile & Broadband"),
    "youbroadband": ("YOU Broadband", "Mobile & Broadband"),
    "gtpl": ("GTPL", "Mobile & Broadband"),
    "tatasky": ("Tata Play", "Mobile & Broadband"),
    "tataplay": ("Tata Play", "Mobile & Broadband"),
    "dishtv": ("Dish TV", "Mobile & Broadband"),
    "d2h": ("d2h", "Mobile & Broadband"),
    "sundirect": ("Sun Direct", "Mobile & Broadband"),
    "keralavision": ("Kerala Vision", "Mobile & Broadband"),
    "railwire": ("RailWire", "Mobile & Broadband"),
    "asianet": ("Asianet", "Mobile & Broadband"),
    "bsnl": ("BSNL", "Mobile & Broadband"),
    "mtnl": ("MTNL", "Mobile & Broadband"),
    # NB: PSP handles (@okhdfcbank, @ybl, @paytm) are deliberately absent.
    # They identify the *provider*, not the payee, so mapping them would turn a
    # transfer to a person into a "merchant" and corrupt category totals.
}


#: Brand names, not keys: the list a reviewer can read and count. Declared here
#: so "23 brokers" is a fact about code rather than an estimate from a category
#: that also holds exchanges, depositories and mutual-fund platforms.
STOCK_BROKERS: tuple[str, ...] = (
    "5paisa",
    "Angel One",
    "Axis Direct",
    "Dhan",
    "Fyers",
    "Geojit",
    "Groww",
    "HDFC Securities",
    "ICICI Direct",
    "IIFL Securities",
    "INDmoney",
    "Kotak Neo",
    "Motilal Oswal",
    "Paytm Money",
    "Religare Broking",
    "SBI Securities",
    "Sharekhan",
    "Upstox",
    "Zerodha",
    "smallcase",
    "BSE StarMF (SIP)",
    "Kuvera",
    # "Coin" is not a broker: it is Zerodha's mutual-fund alias, already covered.
)

#: Mobile operators that bill and recharge by SMS. Broadband ISPs and DTH are
#: counted separately because their message shapes differ (no data balance).
MOBILE_OPERATORS: tuple[str, ...] = ("Airtel", "Jio", "Vi", "BSNL", "MTNL")

#: Exchanges and depositories: real institutions in the demat path, but not
#: brokers, so they are never added to the broker count.
MARKET_INFRASTRUCTURE: tuple[str, ...] = ("NSE", "BSE", "MCX", "CDSL", "NSDL")


def brands_in(label: str) -> set[str]:
    """Distinct clean brand names in one category label."""
    return {clean for _raw, clean, found in iter_seed_rows() if found == label}


def coverage() -> dict[str, object]:
    """Countable coverage for ``cli coverage`` and the tests.

    ``brands`` counts distinct clean names; ``keys`` counts raw spellings and
    aliases, which is what the dictionary actually indexes. Both are reported so
    neither number can be quietly presented as the other.
    """
    by_category: dict[str, dict[str, int]] = {}
    for label, meta in CATEGORY_BY_LABEL.items():
        names = brands_in(label)
        if not names:
            continue
        by_category[label] = {"brands": len(names), "keys": 0}
    for _raw, _clean, label in iter_seed_rows():
        if label in by_category:
            by_category[label]["keys"] += 1
    # Brokers are filed under two categories (a broker's SIP debit lands in
    # Investments), so the check spans both rather than pretending one is the
    # whole list.
    demat_brands = brands_in("Brokerage & Demat") | brands_in("Investments")
    from .merchants import matchable, normalize_key  # local: merchants imports catalog

    normalised = {normalize_key(raw) for raw, _clean, _label in iter_seed_rows()}
    return {
        "categories": len(CATEGORIES),
        "keys": merchant_count(),
        # The dictionary stores normalised keys, so "Swiggy" and "SWIGGY FOOD"
        # collapse. Report the number a user can actually match against, not the
        # number of lines in the source table.
        "distinct_keys": len(normalised),
        "matchable_keys": sum(1 for key in normalised if matchable(key)),
        "duplicate_keys": len(duplicate_keys()),
        "brands": len({clean for _raw, clean, _slug in iter_seed_rows()}),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1]["brands"])),
        "brokers_declared": len(STOCK_BROKERS),
        "brokers_present": sum(1 for name in STOCK_BROKERS if name in demat_brands),
        "mobile_operators_present": sum(
            1 for name in MOBILE_OPERATORS if name in brands_in("Mobile & Broadband")
        ),
        "market_infrastructure_present": sum(
            1 for name in MARKET_INFRASTRUCTURE if name in demat_brands
        ),
    }


def merchant_count() -> int:
    return len({raw for raw, _, _ in iter_seed_rows()})


def category_of_clean(clean: str) -> str | None:
    """Reverse lookup for a clean brand name (used by the card/subscription views)."""
    for raw, name, slug, _aliases in MERCHANTS:
        if name == clean:
            return CATEGORY_BY_SLUG[slug].label
    return None
