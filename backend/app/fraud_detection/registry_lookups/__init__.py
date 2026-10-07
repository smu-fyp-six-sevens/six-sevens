"""Checks a document's facts against Singapore's public registries, live.

* http.py        cached GETs with rate-limit backoff, shared by the clients
* datagov.py     data.gov.sg datastore search (ACRA and HDB datasets)
* acra.py        ACRA entity lookup by UEN, and the entity checks
* onemap.py      OneMap postal code search (SLA)
* hdb.py         HDB block lookup: is the block residential or commercial
* addresses.py   address checks: postal code exists, street matches, residential block
* mas.py         MAS Financial Institutions Directory: licensed banks
* models.py      result types
* runner.py      runs the applicable lookups for one document

Every lookup degrades to a skip with the reason (registry unreachable, rate limited)
rather than a finding: a registry being down is never evidence about the document.
"""

from .models import RegistryFinding, RegistryResult
from .runner import verify_against_registries

__all__ = ["RegistryFinding", "RegistryResult", "verify_against_registries"]
