"""
Currencies supported by the Coffee Shop Management System.
Structure: (ISO_4217_code, name, symbol, decimal_places, country_hint)
"""

# East African currencies
EAST_AFRICAN_CURRENCIES = [
    ("BIF", "Burundian Franc", "FBu", 0, "Burundi"),
    ("KMF", "Comorian Franc", "CF", 0, "Comoros"),
    ("DJF", "Djiboutian Franc", "Fdj", 0, "Djibouti"),
    ("ERN", "Eritrean Nakfa", "Nfk", 2, "Eritrea"),
    ("ETB", "Ethiopian Birr", "Br", 2, "Ethiopia"),
    ("KES", "Kenyan Shilling", "KSh", 2, "Kenya"),
    ("MGA", "Malagasy Ariary", "Ar", 0, "Madagascar"),
    ("MUR", "Mauritian Rupee", "₨", 2, "Mauritius"),
    ("RWF", "Rwandan Franc", "FRw", 0, "Rwanda"),
    ("SCR", "Seychellois Rupee", "₨", 2, "Seychelles"),
    ("SOS", "Somali Shilling", "Sh", 2, "Somalia"),
    ("SSP", "South Sudanese Pound", "£", 2, "South Sudan"),
    ("SDG", "Sudanese Pound", "ج.س.", 2, "Sudan"),
    ("TZS", "Tanzanian Shilling", "TSh", 2, "Tanzania"),
    ("UGX", "Ugandan Shilling", "USh", 0, "Uganda"),
]

# Central African currencies
CENTRAL_AFRICAN_CURRENCIES = [
    ("AOA", "Angolan Kwanza", "Kz", 2, "Angola"),
    ("XAF", "Central African CFA Franc", "FCFA", 0, "Central Africa (CEMAC)"),
    ("CDF", "Congolese Franc", "FC", 2, "DR Congo"),
]

# West African currencies
WEST_AFRICAN_CURRENCIES = [
    ("XOF", "West African CFA Franc", "CFA", 0, "West Africa (UEMOA)"),
    ("CVE", "Cape Verdean Escudo", "$", 2, "Cabo Verde"),
    ("GMD", "Gambian Dalasi", "D", 2, "The Gambia"),
    ("GHS", "Ghanaian Cedi", "₵", 2, "Ghana"),
    ("GNF", "Guinean Franc", "FG", 0, "Guinea"),
    ("LRD", "Liberian Dollar", "$", 2, "Liberia"),
    ("MRU", "Mauritanian Ouguiya", "UM", 2, "Mauritania"),
    ("NGN", "Nigerian Naira", "₦", 2, "Nigeria"),
    ("SLE", "Sierra Leonean Leone", "Le", 2, "Sierra Leone"),
]

# Other African currencies
OTHER_AFRICAN_CURRENCIES = [
    ("DZD", "Algerian Dinar", "د.ج", 2, "Algeria"),
    ("BWP", "Botswana Pula", "P", 2, "Botswana"),
    ("EGP", "Egyptian Pound", "£", 2, "Egypt"),
    ("SZL", "Swazi Lilangeni", "E", 2, "Eswatini"),
    ("LSL", "Lesotho Loti", "L", 2, "Lesotho"),
    ("LYD", "Libyan Dinar", "ل.د", 3, "Libya"),
    ("MWK", "Malawian Kwacha", "MK", 2, "Malawi"),
    ("MAD", "Moroccan Dirham", "د.م.", 2, "Morocco"),
    ("MZN", "Mozambican Metical", "MT", 2, "Mozambique"),
    ("NAD", "Namibian Dollar", "N$", 2, "Namibia"),
    ("ZAR", "South African Rand", "R", 2, "South Africa"),
    ("TND", "Tunisian Dinar", "د.ت", 3, "Tunisia"),
    ("ZMW", "Zambian Kwacha", "ZK", 2, "Zambia"),
    ("ZWG", "Zimbabwe Gold", "ZiG", 2, "Zimbabwe"),
]

# Major international currencies
INTERNATIONAL_CURRENCIES = [
    ("USD", "US Dollar", "$", 2, "United States"),
    ("EUR", "Euro", "€", 2, "European Union"),
    ("CNY", "Chinese Yuan Renminbi", "¥", 2, "China"),
    ("GBP", "Pound Sterling", "£", 2, "United Kingdom"),
    ("AED", "UAE Dirham", "د.إ", 2, "United Arab Emirates"),
    ("CHF", "Swiss Franc", "Fr", 2, "Switzerland"),
    ("JPY", "Japanese Yen", "¥", 0, "Japan"),
    ("CAD", "Canadian Dollar", "$", 2, "Canada"),
    ("AUD", "Australian Dollar", "$", 2, "Australia"),
    ("INR", "Indian Rupee", "₹", 2, "India"),
]

ALL_CURRENCIES = (
    EAST_AFRICAN_CURRENCIES
    + CENTRAL_AFRICAN_CURRENCIES
    + WEST_AFRICAN_CURRENCIES
    + OTHER_AFRICAN_CURRENCIES
    + INTERNATIONAL_CURRENCIES
)