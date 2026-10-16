"""External validation: a document's facts against outside references.

* runner.py            run_external_validation, the one entry point the tool and the endpoint call
* registry_lookups/    live lookups: ACRA, OneMap, HDB, and MOM's public holidays
* sg_reference/        the published tables: CPF, GST, IRAS tax, bank identifiers, public holidays
* reference_checks/    the checks that read those tables (registered as group="external")
* giro.py              GIRO payments dated on days no bank processes GIRO

Which checks run is fixed by the document type. A calculation on the document's own figures is a
numerical check instead (numerical_validation/).
"""
