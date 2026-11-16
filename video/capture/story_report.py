"""The film's case report: the backend's real report renderer (app.case_report.render) fed the story case GLDB-02.
Run from backend/: python3 ../video/capture/story_report.py OUT_DIR  (writes GLDB-02 Case Report.pdf and page PNGs)."""
import subprocess, sys
from datetime import datetime, timezone
sys.path.insert(0, ".")
from app import case_report
from app.case_pipeline.models import AnalysisSummary, CaseAnalysis, CrossDocFinding, FindingCategory, SummaryReason
from app.case_report import ReportData
from app.models import (AdditionalDocument, AdditionalDocuments, CaseDocumentsStatus, CaseNote, CaseRisk, CaseSummary,
                        ContributingFinding, DocumentRecord, DocumentStatus)

out = sys.argv[1]
NOW = datetime(2026, 10, 27, 2, 12, tzinfo=timezone.utc)
JAN = "Anchorpoint_BankStatement_Jan2026.pdf"
BIZ = "Anchorpoint_BusinessProfile.pdf"
summary = CaseSummary(id="case-1", greenlink_case_id="GLDB-02", company_name="Anchorpoint Insurance Brokers Pte. Ltd.",
                      created_at=NOW, updated_at=NOW, documents_status=CaseDocumentsStatus.NO_DOCUMENTS, status="finalised",
                      fulfilled_requirements=["id_document", "bank_statement", "certificate_of_incorporation"])
def doc(i, f, t, st=DocumentStatus.DETECTED):
    return DocumentRecord(id=i, case_id="case-1", filename=f, document_type=t, status=st, uploaded_at=NOW)
documents = [doc("d1", JAN, "bank_statement"), doc("d2", BIZ, "certificate_of_incorporation"),
             doc("d3", "Anchorpoint_BankStatement_Dec2025_scan.pdf", "bank_statement"),
             doc("d4", "Anchorpoint_IRAS_NOA.pdf", "notice_of_assessment"), doc("d5", "Director_NRIC_front.jpg", "nric"),
             doc("d6", "Credit_Bureau_Report_8027.pdf", "credit_report")]
f = lambda **k: ContributingFinding(**k)
analysis = CaseAnalysis(
    run_id="run-1", case_id="case-1", status="complete", fingerprint="f", started_at=NOW, finished_at=NOW, stale=False,
    findings=[CrossDocFinding(id="x1", hypothesis_id="h1", severity="high", category=FindingCategory.CROSS_DOCUMENT_VARIANCE,
        summary="The registered address differs between the business profile and the bank statement",
        rationale='The business profile gives 6 Serangoon North Avenue 5, #02-11; the statement gives 18 Cross Street, #07-01.')],
    structured_summary=AnalysisSummary(
        headline="High risk: the January bank statement's closing balance was edited",
        reasons=[SummaryReason(text="The closing balance 812,400.00 does not reconcile with the month's transactions (182,400.00)", finding_id="f1", document_id="d1"),
                 SummaryReason(text="The closing balance is set in a different weight from the rest of the statement", finding_id="f2", document_id="d1")],
        would_help="A bank-certified January 2026 statement, and proof of the registered address."),
    risk=CaseRisk(state="scored", level="High", score=72, points=31, documents_scanned=20, documents_total=20,
        reasons=["Rated High from the combined severity of 4 finding(s)."],
        contributing_findings=[
            f(document_id="d1", filename=JAN, document_type="bank_statement", finding_id="f1", fingerprint="f1", tool="bank_statement_totals_tool", severity="high",
              summary="Closing balance 812,400.00 does not reconcile: the transactions give 182,400.00.", rationale="The balance is four times what the transactions support.", points=12),
            f(document_id="d1", filename=JAN, document_type="bank_statement", finding_id="f2", fingerprint="f2", tool="font_consistency_tool", severity="medium",
              summary="The closing balance is set in a different weight from the rest of the statement.", rationale="Only the figure that matters differs.", points=4),
            f(document_id="", filename="", finding_id="x1", fingerprint="x1", severity="high", source="cross_document",
              category="cross_document_variance", summary="Registered address differs between the business profile and the bank statement", rationale="", points=12)]),
    additional_documents=AdditionalDocuments(items=[
        AdditionalDocument(title="Bank-certified January 2026 statement", why=f"Confirms the closing balance printed on {JAN}, straight from the bank.", source="finding", affects_score=True, score_if_supports=40, level_if_supports="Medium"),
        AdditionalDocument(title="Proof of registered address", why="Settles which address is the company's.", source="finding", affects_score=True, score_if_supports=46, level_if_supports="Medium")],
        current_score=72, current_level="High", best_case_score=20, best_case_level="Low"),
    clean_checks=["spelling_anomaly_tool"], consistent_checks=[])
notes = [CaseNote(id="n1", case_id="case-1", author="officer", body="Metadata finding dismissed: the bank exports statements through its own PDF tool. Requested a bank-certified January statement before any decision.", created_at=NOW)]
data = ReportData(summary=summary, documents=documents, latest=analysis, completed=analysis, notes=notes, generated_by="officer", generated_at=NOW)
pdf = case_report.render(data)
path = f"{out}/GLDB-02 Case Report.pdf"
open(path, "wb").write(pdf)
subprocess.run(["pdftoppm", "-r", "150", "-png", path, f"{out}/report-page"], check=True)
print(path)
