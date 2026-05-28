# Hardcoded because Plaid does not expose the PFC taxonomy via API.
# Source: plaid.com/docs/api/products/transactions/#personal-finance-category-taxonomy

import re

PFC_TAXONOMY: dict[str, list[str]] = {
    "FOOD_AND_DRINK": [
        "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR",
        "FOOD_AND_DRINK_COFFEE",
        "FOOD_AND_DRINK_FAST_FOOD",
        "FOOD_AND_DRINK_GROCERIES",
        "FOOD_AND_DRINK_RESTAURANT",
        "FOOD_AND_DRINK_VENDING_MACHINES",
        "FOOD_AND_DRINK_OTHER_FOOD_AND_DRINK",
    ],
    "TRANSPORTATION": [
        "TRANSPORTATION_BIKES_AND_SCOOTERS",
        "TRANSPORTATION_GAS",
        "TRANSPORTATION_PARKING",
        "TRANSPORTATION_PUBLIC_TRANSIT",
        "TRANSPORTATION_TAXIS_AND_RIDE_SHARES",
        "TRANSPORTATION_TOLLS",
        "TRANSPORTATION_OTHER_TRANSPORTATION",
    ],
    "TRAVEL": [
        "TRAVEL_FLIGHTS",
        "TRAVEL_LODGING",
        "TRAVEL_RENTAL_CARS",
        "TRAVEL_OTHER_TRAVEL",
    ],
    "RENT_AND_UTILITIES": [
        "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
        "RENT_AND_UTILITIES_INTERNET_AND_CABLE",
        "RENT_AND_UTILITIES_RENT",
        "RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT",
        "RENT_AND_UTILITIES_TELEPHONE",
        "RENT_AND_UTILITIES_WATER",
        "RENT_AND_UTILITIES_OTHER_UTILITIES",
    ],
    "LOAN_PAYMENTS": [
        "LOAN_PAYMENTS_CAR_PAYMENT",
        "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
        "LOAN_PAYMENTS_MORTGAGE_PAYMENT",
        "LOAN_PAYMENTS_PERSONAL_LOAN_PAYMENT",
        "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT",
        "LOAN_PAYMENTS_OTHER_PAYMENT",
    ],
    "GENERAL_MERCHANDISE": [
        "GENERAL_MERCHANDISE_BOOKSTORES_AND_NEWSSTANDS",
        "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES",
        "GENERAL_MERCHANDISE_CONVENIENCE_STORES",
        "GENERAL_MERCHANDISE_DEPARTMENT_STORES",
        "GENERAL_MERCHANDISE_DISCOUNT_STORES",
        "GENERAL_MERCHANDISE_ELECTRONICS",
        "GENERAL_MERCHANDISE_GIFTS_AND_NOVELTIES",
        "GENERAL_MERCHANDISE_OFFICE_SUPPLIES",
        "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
        "GENERAL_MERCHANDISE_PET_SUPPLIES",
        "GENERAL_MERCHANDISE_SPORTING_GOODS",
        "GENERAL_MERCHANDISE_SUPERSTORES",
        "GENERAL_MERCHANDISE_TOBACCO_AND_VAPE",
        "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE",
    ],
    "GENERAL_SERVICES": [
        "GENERAL_SERVICES_ACCOUNTING_AND_FINANCIAL_PLANNING",
        "GENERAL_SERVICES_AUTOMOTIVE",
        "GENERAL_SERVICES_CHILDCARE",
        "GENERAL_SERVICES_CONSULTING_AND_LEGAL",
        "GENERAL_SERVICES_EDUCATION",
        "GENERAL_SERVICES_INSURANCE",
        "GENERAL_SERVICES_POSTAGE_AND_SHIPPING",
        "GENERAL_SERVICES_STORAGE",
        "GENERAL_SERVICES_OTHER_GENERAL_SERVICES",
    ],
    "ENTERTAINMENT": [
        "ENTERTAINMENT_CASINOS_AND_GAMBLING",
        "ENTERTAINMENT_MUSIC_AND_AUDIO",
        "ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS",
        "ENTERTAINMENT_TV_AND_MOVIES",
        "ENTERTAINMENT_VIDEO_GAMES",
        "ENTERTAINMENT_OTHER_ENTERTAINMENT",
    ],
    "PERSONAL_CARE": [
        "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS",
        "PERSONAL_CARE_HAIR_AND_BEAUTY",
        "PERSONAL_CARE_LAUNDRY_AND_DRY_CLEANING",
        "PERSONAL_CARE_OTHER_PERSONAL_CARE",
    ],
    "MEDICAL": [
        "MEDICAL_DENTAL_CARE",
        "MEDICAL_EYE_CARE",
        "MEDICAL_NURSING_CARE",
        "MEDICAL_PHARMACIES_AND_SUPPLEMENTS",
        "MEDICAL_PRIMARY_CARE",
        "MEDICAL_VETERINARY_SERVICES",
        "MEDICAL_OTHER_MEDICAL",
    ],
    "HOME_IMPROVEMENT": [
        "HOME_IMPROVEMENT_FURNITURE",
        "HOME_IMPROVEMENT_HARDWARE",
        "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE",
        "HOME_IMPROVEMENT_SECURITY",
        "HOME_IMPROVEMENT_OTHER_HOME_IMPROVEMENT",
    ],
    "BANK_FEES": [
        "BANK_FEES_ATM_FEES",
        "BANK_FEES_FOREIGN_TRANSACTION_FEES",
        "BANK_FEES_INSUFFICIENT_FUNDS",
        "BANK_FEES_INTEREST_CHARGE",
        "BANK_FEES_OVERDRAFT_FEES",
        "BANK_FEES_OTHER_BANK_FEES",
    ],
    "GOVERNMENT_AND_NON_PROFIT": [
        "GOVERNMENT_AND_NON_PROFIT_DONATIONS",
        "GOVERNMENT_AND_NON_PROFIT_GOVERNMENT_DEPARTMENTS_AND_AGENCIES",
        "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT",
        "GOVERNMENT_AND_NON_PROFIT_OTHER_GOVERNMENT_AND_NON_PROFIT",
    ],
    "TRANSFER_OUT": [
        "TRANSFER_OUT_ACCOUNT_TRANSFER",
        "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS",
        "TRANSFER_OUT_SAVINGS",
        "TRANSFER_OUT_WITHDRAWAL",
        "TRANSFER_OUT_OTHER_TRANSFER_OUT",
    ],
    "TRANSFER_IN": [
        "TRANSFER_IN_ACCOUNT_TRANSFER",
        "TRANSFER_IN_CASH_ADVANCES_AND_LOANS",
        "TRANSFER_IN_DEPOSIT",
        "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS",
        "TRANSFER_IN_SAVINGS",
        "TRANSFER_IN_OTHER_TRANSFER_IN",
    ],
    "INCOME": [
        "INCOME_DIVIDENDS",
        "INCOME_INTEREST_EARNED",
        "INCOME_RETIREMENT_PENSION",
        "INCOME_TAX_REFUND",
        "INCOME_UNEMPLOYMENT",
        "INCOME_WAGES",
        "INCOME_OTHER_INCOME",
    ],
}

CATEGORY_COLORS: dict[str, str] = {
    "FOOD_AND_DRINK": "#ef4444",
    "TRANSPORTATION": "#f97316",
    "GENERAL_MERCHANDISE": "#f59e0b",
    "ENTERTAINMENT": "#eab308",
    "GENERAL_SERVICES": "#84cc16",
    "RENT_AND_UTILITIES": "#22c55e",
    "PERSONAL_CARE": "#14b8a6",
    "MEDICAL": "#06b6d4",
    "TRAVEL": "#3b82f6",
    "LOAN_PAYMENTS": "#6366f1",
    "HOME_IMPROVEMENT": "#8b5cf6",
    "BANK_FEES": "#a855f7",
    "GOVERNMENT_AND_NON_PROFIT": "#ec4899",
    "TRANSFER_OUT": "#78716c",
    "TRANSFER_IN": "#a8a29e",
    "INCOME": "#10b981",
    "UNKNOWN": "#64748b",
}
DEFAULT_COLOR: str = CATEGORY_COLORS["UNKNOWN"]


# Excluded from spending totals but kept as recategorize targets.
EXCLUDED_CATEGORIES: set[str] = {"INCOME", "TRANSFER_IN"}


ALL_PRIMARIES: list[str] = [
    "INCOME",
    "TRANSFER_IN",
    "TRANSFER_OUT",
    "FOOD_AND_DRINK",
    "TRANSPORTATION",
    "TRAVEL",
    "RENT_AND_UTILITIES",
    "LOAN_PAYMENTS",
    "GENERAL_MERCHANDISE",
    "GENERAL_SERVICES",
    "ENTERTAINMENT",
    "PERSONAL_CARE",
    "MEDICAL",
    "HOME_IMPROVEMENT",
    "BANK_FEES",
    "GOVERNMENT_AND_NON_PROFIT",
]


_VALID_DETAILED: set[str] = {
    code for codes in PFC_TAXONOMY.values() for code in codes
}
_PRIMARY_BY_DETAILED: dict[str, str] = {
    code: primary for primary, codes in PFC_TAXONOMY.items() for code in codes
}


def is_valid_detailed(code: str) -> bool:
    return code in _VALID_DETAILED


def is_valid_primary(code: str) -> bool:
    return code in PFC_TAXONOMY


def primary_of(code: str) -> str | None:
    return _PRIMARY_BY_DETAILED.get(code)


# 3-item lists need explicit Oxford comma; underscores don't carry that info.
_DETAILED_OVERRIDES: dict[str, str] = {
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": "Beer, Wine, and Liquor",
    "ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS":
        "Sporting Events, Amusement Parks, and Museums",
}


# .title() butchers these acronyms.
_ACRONYM_FIXUPS = {"Tv": "TV", "Atm": "ATM"}
_ACRONYM_RE = re.compile(r"\b(" + "|".join(_ACRONYM_FIXUPS) + r")\b")


def _titlecase(s: str) -> str:
    out = s.replace("_", " ").title().replace(" And ", " and ")
    return _ACRONYM_RE.sub(lambda m: _ACRONYM_FIXUPS[m.group(1)], out)


def humanize_primary(primary: str) -> str:
    return _titlecase(primary)


def humanize_detailed(detailed: str, primary: str | None = None) -> str:
    if detailed in _DETAILED_OVERRIDES:
        return _DETAILED_OVERRIDES[detailed]
    primary = primary or primary_of(detailed) or ""
    if primary and detailed.startswith(primary + "_"):
        rest = detailed[len(primary) + 1 :]
    else:
        rest = detailed
    # Collapse "OTHER_*" tails to plain "OTHER" — reads better.
    if rest.startswith("OTHER_"):
        rest = "OTHER"
    return _titlecase(rest)
