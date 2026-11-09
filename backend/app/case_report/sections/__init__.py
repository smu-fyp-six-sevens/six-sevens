"""The report's parts, each a function returning its flowables: the header, the analysis sections and the case's."""

from .analysis import _additional_documents_section, _cross_document_section, _risk_section, _summary_section
from .case import _checklist_section, _documents_section, _notes_section
from .header import _analysis_status_banner, _stat_cards, _title_block

__all__ = [
    "_additional_documents_section",
    "_analysis_status_banner",
    "_checklist_section",
    "_cross_document_section",
    "_documents_section",
    "_notes_section",
    "_risk_section",
    "_stat_cards",
    "_summary_section",
    "_title_block",
]
