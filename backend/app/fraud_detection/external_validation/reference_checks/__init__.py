"""The static reference checks: a document's figures against a published table.

Importing this package registers them in the numerical registry with group="external", so they are
selected by document type like any other check but run from external_validation's tool.

* cpf.py   payslip CPF rate and ceiling (sg_reference/cpf.py)
* gst.py   GST on a bill (sg_reference/gst.py)
* bank.py  bank identifiers and account number format (sg_reference/banks.py)
* tax.py   a notice of assessment's tax chain (sg_reference/corporate_tax.py)
"""

from . import bank, cpf, gst, tax  # noqa: F401
