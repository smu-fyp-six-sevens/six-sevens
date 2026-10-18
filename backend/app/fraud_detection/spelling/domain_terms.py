# domain_terms.py
"""
Seed allow-list of legal/financial/corporate terminology and acronyms (AC2)
that SymSpell's general-English dictionary doesn't recognise, so
spelling_anomaly.py doesn't flag them as candidates. This is a starting
list informed by the document types in extraction_prompts.py, not an
authoritative company glossary — extend it as real false positives turn up.

Checked case-insensitively (see spelling_anomaly.py), so casing here doesn't
matter beyond readability.
"""

_RAW_DOMAIN_TERMS: set[str] = {
    # KYC / AML / regulatory acronyms
    "kyc", "aml", "cdd", "edd", "fatca", "crs", "tin", "uen", "nric", "fin",
    "gst", "sgd", "usd", "sfc", "mas", "acra",
    # Corporate entity types / suffixes
    "pte", "ltd", "llc", "llp", "inc", "corp", "plc", "sa", "gmbh", "kg",
    # Honorifics/titles that aren't in a general English dictionary
    "mr", "mrs", "ms", "mx", "dr", "esq", "jr", "sr", "prof",
    # Legal/financial terminology
    "escrow", "indemnify", "indemnification", "indemnitee", "indemnitor",
    "collateral", "covenant", "amortization", "amortisation", "arrears",
    "encumbrance", "encumbered", "lien", "guarantor", "guarantee",
    "beneficiary", "beneficiaries", "debenture", "novation", "subrogation",
    "jurisdiction", "arbitration", "notarised", "notarized", "apostille",
    "affidavit", "deponent", "attestation", "witnesseth", "recital",
    "whereas", "hereinafter", "hereto", "hereunder", "thereof", "thereto",
    "counterparty", "counterparties", "underwriter", "underwriting",
    "disbursement", "remittance", "chargeable", "forfeiture", "forfeit",
    "insolvency", "receivership", "liquidator", "liquidation",
    "incorporation", "incorporated", "shareholder", "shareholders",
    "director", "directorship", "signatory", "signatories", "custodian",
    "trustee", "settlor", "grantor", "mortgagee", "mortgagor",
    # Document/field terminology used elsewhere in this codebase
    "constitution", "incumbency", "declarant", "rtf",
    "Maybank", "DBS", "OCBC", "UOB", "Citi", "HSBC","XXXX", "CIMB",
    "StandardChartered", "StandardCharteredBank", "GLDB", "NFE", "NFFE",
    "FATCA", "GIIN", "IRS", "https", "www", "oecd", "paynow","eStatements",
    "bizfile", "mytax",
    # Bank statement shorthand ("Txn Charges", "Funds Trf", "Inward Cr")
    "txn", "txns", "trf", "xfer", "cr", "chq", "acct", "accts", "amt",
    "bal", "ibg", "giro", "atm", "atms", "pmt",
    # Singapore address shorthand (cross_document/address_matching.py's _ADDRESS_ABBREVIATIONS, plus
    # the common Malay street words), and the MSF ComLink+ scheme named on credit reports
    "blk", "bldg", "ave", "av", "cres", "ctrl", "cwealth", "jln", "ln", "lwr", "nth", "sth", "upp",
    "ter", "rd", "pk", "lor", "bt", "kg", "tg", "comlink",
    # Finance/legal words missing from SymSpell's general dictionary
    "nonfinancial", "drawdown", "drawdowns", "undrawn", "annum", "payables",
    "receivables", "lodgment", "lodgments", "unformatted", "chargee",
    "chargees", "chargor", "endeavor", "megastore",
}

# Lowercased once here rather than trusting every entry above to already be
# lowercase — spelling_anomaly.py only lowercases the word it's checking,
# not this set, so a mixed-case entry (e.g. "DBS") would silently never
# match anything and just sit there doing nothing. This is what actually
# makes the "checked case-insensitively" claim below true.
DOMAIN_TERMS: set[str] = {term.lower() for term in _RAW_DOMAIN_TERMS}
