# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Loan officers at Green Link Digital Bank (GLDB), primary.** They handle corporate loan applications: create a case for an applicant company, upload its documents, review and approve each one, judge the detection findings, and run the case analysis. Their job is to decide whether a case's documents can be trusted before it moves on in loan origination.
- **Admins and supervisors.** They watch detection accuracy and case flow on the dashboard. For now, whoever creates a case decides whether finding reviews are switched on for it.
- **The GLDB sponsor and FYP assessors.** They judge the product in demos and reports, so the app also has to make its reasoning legible to people who are not using it day to day.

## Product Purpose

GLDB Fraud Review automates the forgery-check stage of GLDB's loan origination workflow. It is a Final Year Project built with GLDB. Officers upload a case's documents. The system reads, classifies and extracts each one, scans approved documents for tampering and inconsistencies, then compares documents across the case and scores its risk.

Success means officers catch forged or inconsistent documents with less manual checking, and trust the findings enough to act on them. Detection precision and recall are measured from officers' own verdicts.

Credit scoring and offer generation are out of scope.

## Positioning

An agentic forgery check that shows its working. A lead agent and three specialist agents (layout and rendering, content validation, digital authenticity) run deterministic forensic tools. The agents judge the tools' candidate findings but cannot invent findings or change a tool's facts. Hard breaches can't be dismissed. Every finding, kept or dismissed, is shown with its reasoning. The officer's verdict is the source of truth and feeds the accuracy figures.

## Operating Context

- **Flow:** a case moves through stages derived from its documents and analysis: Awaiting Documents, Processing Documents, Pending Review, Missing Documents, Detecting Fraud, Ready to Analyse, Pending Evaluation, Ready to Finalise, Finalised, with Failed possible at any point. Only Finalised is permanent, and a finalised case is view-only.
- **Documents:** PDF, PNG or JPEG, up to 25 MB each and 20 per batch. They include bank statements, NRICs, certificates of incumbency, payslips, notices of assessment and balance sheets.
- **Officer actions:** edit extracted fields, correct a document's type, approve or reject documents, retry failures, mark findings correct or wrong, report missed issues, leave notes on a case or document, run the case analysis, and download a case report.
- **Live updates:** case status arrives live over a WebSocket while background work runs.
- **Devices:** not decided. It has not been established whether officers mainly use large office screens, laptops or phones.

## Capabilities and Constraints

- **Stack:** React with TypeScript (Vite) on the frontend. FastAPI and LangGraph on the backend, with Supabase for data and storage and the Claude API for model calls.
- **Accounts:** named officer accounts with sign-in and no roles. There is an internal-use notice: "authorised officers only".
- **Risk score:** exists only as the output of a case analysis run. A case never analysed reads "Not scored yet", and a run made before its documents changed is marked out of date.
- **No false "clean":** a check that could not run is never shown as clean.
- **Protected attributes:** a person's race, sex, nationality and date of birth are compared only in code and never shown to a model.
- **Feature switches:** case analysis can be switched off with `CASE_ANALYSIS_ENABLED`.
- **Terminology:** case, document, finding, flag, missed issue, verdict (correct or wrong), case analysis, risk level (Low, Medium, High, Critical), and the stage names above.

## Brand Commitments

- **Name:** the product is "GLDB Fraud Review".
- **Brand assets:** no GLDB brand guidelines or assets are required. The app's own look is its identity.

## Evidence on Hand

- **Test documents:** generated in `../documents_generated` and `../document_templates`. Tampered variants are made by `backend/app/injection`.
- **Regression report:** `../docs/regression/REGRESSION_REPORT.md`.
- **What doesn't exist:** there is no labelled test set, and no real customer data, testimonials or usage figures. Do not fabricate any of these.

## Product Principles

1. **Officers decide.** The system raises and explains findings, and the officer's verdict is final and is what accuracy is measured against.
2. **Show the reasoning.** Every finding, score and dismissal can be traced to the tool, document and reasoning behind it.
3. **Never overstate certainty.** Unrun checks, out-of-date runs and failures are said plainly, never shown as clean.
4. **Fairness by construction.** Protected attributes stay out of model reasoning and out of the score.
5. **Officers' time is scarce.** Each screen should get an officer to the next decision with as little re-checking as possible.
