"""The four banks' published identifiers, from each bank's own documents.

Used by the bank-statement identifier and account-number checks (reference_checks/bank.py).
"""

from __future__ import annotations

import re
from typing import Any

# Canonical bank name -> how it's named on statements, and its ACRA
# registration number (UEN), each from the bank's own published
# documents (checked Oct 2026):
#   DBS        dbs.com.sg account application forms ("DBS Bank Ltd Co. Reg. No: 196800306E")
#   OCBC       OCBC's SGX announcements ("Co. Reg. no.: 193200032W")
#   UOB        uob.com.sg terms & conditions ("Co. Reg. No. 193500026Z")
#   Green Link glbank.com MAS Notice 637 disclosures ("UEN: 202115950G")
# SWIFT BIC (first 8 characters; an 11-character form only adds a branch,
# "XXX" = head office): DBS from dbs.com.sg's SWIFT code page, OCBC from
# its Telegraphic Transfer Guide, UOB from its International Network
# listing; Green Link's isn't on its own site, so it's taken from the
# SWIFT directory as republished by Wise and XE.
# GST registration numbers are deliberately absent: none could be
# confirmed from an official source, and a guessed one would flag
# genuine statements. BS.BANK_IDENTIFIERS only compares fields present here.
# account_digits (BS.ACCOUNT_NUMBER_FORMAT): DBS 10 and POSB 9 from DBS's
# "Bank & Branch Codes" page; OCBC "10 or 12 digits" from OCBC's help page;
# UOB 10 from OCBC's funds-transfer tips PDF. Green Link publishes none.
# All four registration numbers also match ACRA's own register
# (data.gov.sg "Entities Registered with ACRA").
BANK_IDENTIFIERS: dict[str, dict] = {
    # POSB is a DBS brand: same legal entity, UEN and BIC (DBS's SWIFT page
    # gives "DBS & POSB" one code).
    # gst_no: an IRAS GST number of the pre-UEN format, not the UEN; real DBS
    # statements print "Co. Reg. No. 196800306E GST Registration No:
    # MR-8500180-3". OCBC's, UOB's and GLDB's GST numbers couldn't be
    # confirmed from a reliable source (IRAS's register needs a CAPTCHA or an
    # API key), so they're left out and their GST check doesn't run.
    "DBS Bank Ltd": {
        "aliases": ("dbs", "posb", "development bank of singapore"),
        "registration_no": "196800306E", "gst_no": "MR-8500180-3", "bic": "DBSSSGSG",
        "account_digits": (9, 10),
    },
    "Oversea-Chinese Banking Corporation Limited": {
        "aliases": ("ocbc", "oversea-chinese banking", "oversea chinese banking"),
        "registration_no": "193200032W", "bic": "OCBCSGSG", "account_digits": (10, 12),
    },
    "United Overseas Bank Limited": {
        "aliases": ("uob", "united overseas bank"), "registration_no": "193500026Z",
        "bic": "UOVBSGSG", "account_digits": (10,),
    },
    "Green Link Digital Bank Pte. Ltd.": {
        "aliases": ("green link digital bank", "gldb"), "registration_no": "202115950G",
        "bic": "GLDTSGSG",
    },
}


def bank_reference(bank_name: Any) -> dict | None:
    """The published identifiers of the bank a statement names, or None for a bank not listed."""
    name = re.sub(r"[^a-z ]", " ", str(bank_name or "").lower())
    name = " ".join(name.split())
    for reference in BANK_IDENTIFIERS.values():
        for alias in reference["aliases"]:
            alias_norm = " ".join(re.sub(r"[^a-z ]", " ", alias).split())
            if re.search(rf"\b{re.escape(alias_norm)}\b", name):
                return reference
    return None
