# How to run the success metrics

We measure the system on synthetic loan cases whose answers we already know. Each generated document comes with a truth file that says what it prints and, if it's forged, what was changed. You upload a case through the app, the agents process it, and a script compares what they produced with the truth files.

The report covers:

| Metric | What it measures |
|---|---|
| Classification accuracy | Documents given the right type |
| Extraction accuracy | Key fields read correctly |
| Risk score accuracy | Cases given the expected risk level (Low, Medium, High or Critical) |
| Anomaly detection accuracy | Forged documents that were flagged |
| False positive rate | Clean documents flagged, and clean cases rated above Low |

There are two parts. **Part A** is uploading a case, and anyone can do it in the app. **Part B** is fetching and scoring, which needs the repo set up.

---

## Part A: Upload a case (in the app)

### 1. Pick a case and get its files

The documents are in the shared Drive folders (the same as `FYP/v18_generated_documents` and `FYP/v18_fraud_documents`).

- **Clean case:** upload every file in `v18_generated_documents/<case>`.
- **Fraud case:** upload every file in `v18_generated_documents/<case>`, **except** use the copy from `v18_fraud_documents/<case>` for each file that's there.

| Case | Expected risk | Files from `v18_fraud_documents` |
|---|---|---|
| C002 | Critical | `KFF_COI_scan.pdf`, `NRIC_3844_front.jpg`, `KestrelFreightForwarding_NOA_YA2025.pdf` |
| C003 | High | `LIS_Constitution.pdf`, `LIS_BalanceSheet_Audited.pdf` |
| C008 | High | `ACRA_CertIncorp_201651600K.pdf`, `CreditReport_CBS_20260808.pdf` |
| C011 | Critical | `SimTH_Passport_scan.jpg`, `TayYS_Salary_Slip_20260925.pdf`, `SP_Utilities_TMS.pdf`, `POR_NRIC_20260925.pdf` |
| C015 | Critical | `eStmt_download_630.pdf`, `POR_UtilityBill_20260904.png`, `Rajesh_Salary_Slip_20260904.pdf` |
| C018 | High | `eStmt_download_233.pdf`, `FATCA_CRS_Entity_95509.pdf` |
| C020 | High | `LoanFacility_17299_1.pdf` |
| C023 | High | `ACRA_Incumbency_20260808.pdf` |
| C024 | Medium | `ACRA_CertIncorp_201065233R.pdf` |
| C025 | Medium | `ACRA_CertIncorp_201013138R.pdf` |
| C028 | Medium | `FS_VPIS_2025.pdf` |
| C030 | High | `ACRA_CertIncorp_201078782M.pdf`, `NOA_YA2025_201078782M.pdf` |

Every other case (C001, C004–C007, C009, C010, C012–C014, C016, C017, C019, C021, C022, C026, C027, C029) is clean and expected Low.

### 2. Create the case and upload

- Create a new case in the app. Any name works: the script recognises the case from its filenames.
- Upload all of the case's files. **Don't rename any of them.**

### 3. Approve every document

Wait for every document to finish processing, then approve each one.

| If approval says… | Do this |
|---|---|
| "Low-confidence fields must be reviewed and saved first" | Open the fields and **save them without changing anything** |
| "Confirm the document type first" | Confirm the type the system picked |
| "Required fields are missing or empty" | Delete the document and **upload the same file again** (a known bug, see Troubleshooting) |

Try not to correct any fields. If you have to, that's fine: the script uses the audit trail to put back what the agents originally read, so they are still scored on their own work.

### 4. Let the case analysis finish

The analysis starts on its own once every approved document has been scanned. Wait until the case shows a risk level. If you re-uploaded a document after that, run the analysis again from the case page.

### 5. Send the case number

Send the case number from the app (for example **GLDB-12**) to whoever runs Part B, with which case it was (for example C011). The case number is enough on its own; the case ID is just a check.

---

## Part B: Fetch and score (needs the repo)

### One-time setup

1. Pull `main`.
2. Set up the backend as in the main README. From `backend/`:
   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements-dev.lock
   .venv/bin/pip install --no-deps -e .
   ```
3. Make sure `backend/.env` has the team's usual backend settings: `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `AUTH_USERS`, `AUTH_SECRET_KEY` and the rest. They must point at **the same Supabase project the cases were uploaded to**, or the script won't find the cases. Ask whoever set up the project if you don't have them.
4. Add these three lines to `backend/.env`:
   ```
   METRICS_USERNAME=<your app username> (e.g. bob)
   METRICS_PASSWORD=<your app password> (e.g. bobs-password)
   METRICS_API=http://localhost:8000
   ```

The script only *reads* from the app. It never changes a case, a document or the database.

### Every time: run it

You need **two terminals**: one runs the backend, the other runs the scripts.

**Terminal 1: start the backend and leave it running.** From `backend/`:

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

Wait until it prints `Application startup complete`. Don't close this terminal until you've finished.

**Terminal 2: fetch the cases, then score them.** From `backend/`:

```bash
.venv/bin/python -m scripts.metrics.fetch_cases GLDB-12 GLDB-13 GLDB-14
.venv/bin/python -m scripts.metrics.score
```

Use the case numbers your teammates sent you (Part A step 5). Each one prints a line like:

```
GLDB-12 -> C008: 16 documents, risk Critical, 5 note(s)
```

That's the mastersheet case it matched, how many documents it took, the risk level, and anything worth knowing (listed under it).

Then open **`metrics-testing/metrics_report.md`** for the results. Every detail is also in `metrics_report.json`.

**When you're done,** stop the backend in Terminal 1 with `Ctrl+C`.

### Choosing which cases get scored

Results pile up: every case you fetch is saved in `metrics-testing/seed-0/results/`, and `score` scores **all of them**, including cases fetched earlier or by someone else. Two ways to score only what you want:

**Start fresh with `--reset`.** It deletes every earlier result before fetching, so the report covers only the cases in this command:

```bash
.venv/bin/python -m scripts.metrics.fetch_cases --reset GLDB-12 GLDB-13
.venv/bin/python -m scripts.metrics.score
```

**Keep everything, but score a few with `--cases`.** Nothing is deleted. Name cases by app number (GLDB-12) or mastersheet case (C008):

```bash
.venv/bin/python -m scripts.metrics.score --cases GLDB-12 C008
```

Use `--reset` before the final full run, so the report has only the cases from that run. Use `--cases` to check one case without losing the rest.

Good to know:

- Fetching a case again replaces its earlier results, which is useful after a re-upload.
- `score` doesn't need the backend, so you can re-run it any time.
- `--reset` only deletes the saved results on your machine. The cases in the app are untouched, so you can fetch them again whenever you like.

### Reading the report

- **Headline table:** the five metrics. This is what goes in our report.
- **Classification / Extraction:** results per document type. For extraction, the misses that only got the field name wrong show up under "any name".
- **Risk:** the expected level against the level the system gave.
- **Anomaly detection:** results for each kind of forgery.
- **False positives:** every wrong finding on a clean document, with the check that raised it.
- **Gaps in the run:** what wasn't measured, such as documents that were never scanned or files that didn't match. Check this before quoting any number.

---

## Troubleshooting

| You see | What it means | What to do |
|---|---|---|
| "Required fields are missing or empty" when approving (often a bank statement's transactions) | Known bug: when the classifier flags a document for review, it's read with a generic field list | Delete it and upload the same file again; it usually reads correctly the second time |
| `fetch_cases`: "doesn't print what the dataset's file prints" | The uploaded file isn't the dataset's version. Most often it's the clean copy of a forged document, or a file from an older download | Upload the right file (Part A step 1) and fetch the case again. Note that every certificate of incorporation was regenerated on 16 Nov, so older downloads of those won't match |
| `fetch_cases`: "the case hasn't been analysed" | No risk level yet | Wait for the analysis or run it from the case page, then fetch again |
| `fetch_cases`: "the case analysis is out of date" | Documents changed after the analysis ran | Run the analysis again, then fetch again |
| `fetch_cases`: "not scanned" | A document was never approved, so detection never ran on it | Approve it, wait for the scan, then fetch again |
| `fetch_cases`: "files that aren't Cxxx's" | Another case's files were uploaded into this case | Remove them, or start a fresh case |
| Lots of "UEN is not in ACRA's register" findings | The backend isn't using the test UEN register | Whoever runs the backend must set `SYNTHETIC_ACRA_REGISTRY=true` in its `.env` and restart it |
| Sign-in fails | Wrong username, password or API address | Check the three `METRICS_` lines in `backend/.env` |
| `Connection refused` when fetching | The backend isn't running | Start it in Terminal 1 (Part B, "Every time: run it") and wait for `Application startup complete` |
| `address already in use` when starting the backend | A backend is already running on port 8000 | Use that one, or stop it with `lsof -ti:8000 \| xargs kill` and start again |
| `fetch_cases`: "looking up GLDB-… failed: 404" | Your backend is connected to a different Supabase project from the one the case was uploaded to | Check `SUPABASE_URL` and the keys in `backend/.env` |

## Things that would break the scores

- **Renaming files.** The script matches files by name.
- **Uploading from a different build.** Regenerating the documents changes them. Only upload from the shared `v18_` folders.
- **Mixing two cases' files** in one app case.
