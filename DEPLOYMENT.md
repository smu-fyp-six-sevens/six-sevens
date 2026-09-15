# Deploying to Cloud Run — step by step

This walks through getting the app live on Google Cloud, assuming you've
never done this before. Budget ~30-45 minutes the first time. Every command
below is meant to be copy-pasted as-is (replacing the placeholders in
`ALL_CAPS`) — you shouldn't need to guess anything.

**What actually happens once this is set up:** the deploy workflow is
**manual-only** — pushing commits to `main` never touches the live
services, so you can keep developing without breaking what's already
deployed. Only clicking "Run workflow" on GitHub triggers it: a GitHub
Actions robot builds the app into two Docker images, uploads them to Google
Cloud, and starts them running as two "Cloud Run services" (one for the
backend, one for the frontend), each getting its own public URL. This is a
one-shot deploy, not a fancy multi-service setup yet — that's intentional
for now.

---

## Part A — one-time Google Cloud setup

You'll run these commands in a terminal on your own machine (PowerShell is
fine), because the first-time login opens a browser window that this
assistant can't do on your behalf.

### A1. Install the Google Cloud CLI (`gcloud`)

You don't have it installed yet. Easiest way on Windows:

1. Go to `https://cloud.google.com/sdk/docs/install` and download the
   Windows installer, **or** if you have `winget`, run in PowerShell:
   ```powershell
   winget install Google.CloudSDK
   ```
2. Run the installer, accept the defaults. When it finishes it offers to
   open a new terminal and run `gcloud init` — let it.
3. Close and reopen your terminal so the `gcloud` command is on your PATH.
4. Check it worked:
   ```powershell
   gcloud --version
   ```
   You should see version numbers printed, not "command not found".

### A2. Sign in

```powershell
gcloud auth login
```

This opens a browser window — sign in with the Google account tied to your
Cloud Console. Once it says "You are now logged in", come back to the
terminal.

### A3. Pick (or create) a project

A "project" is Google Cloud's container for everything you deploy. If you
already created one when you set up your console account:

```powershell
gcloud projects list
```

Find the `PROJECT_ID` column value for the one you want to use, and note it
down — you'll need it repeatedly below.

If you don't have one yet, create one (project IDs must be globally unique,
so add some random digits if the plain name is taken):

```powershell
gcloud projects create YOUR_NEW_PROJECT_ID --name="Fraud Detection Demo"
```

Either way, set it as your active project for every command below:

```powershell
gcloud config set project YOUR_PROJECT_ID
```

### A4. Enable billing on the project

Cloud Run and Artifact Registry require a billing account linked to the
project, even if you stay within the free tier (Cloud Run has a generous
always-free monthly quota — for a demo like this you're unlikely to be
charged, but Google still requires a card on file).

1. Go to `https://console.cloud.google.com/billing` in your browser.
2. If you don't have a billing account, click "Create account" and follow
   the prompts (needs a payment method).
3. Go to `https://console.cloud.google.com/billing/linkedaccount?project=YOUR_PROJECT_ID`
   (swap in your real project ID) and link your billing account to this
   project if it isn't already.

### A5. Turn on the Google Cloud services this needs

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com iam.googleapis.com
```

This can take a minute or two the first time. It'll return to the prompt
with no output when done — no output here means success.

### A6. Create a "service account" for GitHub Actions to deploy with

A service account is a non-human identity your GitHub Actions robot will
log in as, instead of using your personal Google login. This keeps your
own credentials out of GitHub entirely.

```powershell
gcloud iam service-accounts create github-deployer --display-name="GitHub Actions deployer"
```

### A7. Give that service account permission to do its job

Each command below grants one specific permission — deploying to Cloud Run,
pushing Docker images, and being allowed to launch a Cloud Run revision.
Run all three (PowerShell: paste them one at a time).

```powershell
$PROJECT_ID = gcloud config get-value project
$SA_EMAIL = "github-deployer@$PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/run.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/artifactregistry.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/iam.serviceAccountUser"
```

Each command should print a big block of JSON ending in a line with your
service account's email in it — that's confirmation the permission was
added, not an error.

### A8. Create a login key for that service account

This generates a file — think of it like a password, in file form — that
GitHub Actions will use to authenticate as `github-deployer`.

```powershell
gcloud iam service-accounts keys create github-deployer-key.json --iam-account=$SA_EMAIL
```

This creates `github-deployer-key.json` in whatever folder your terminal is
currently in. **Treat this file as a password.** You'll paste its contents
into GitHub in Part B, then delete it — do not commit it, email it, or
leave it lying around.

Open it in a text editor (or VS Code) so you can copy its full contents in
the next part:

```powershell
notepad github-deployer-key.json
```

---

## Part B — GitHub setup

### B1. Add secrets to the repo

Secrets are encrypted values GitHub stores for your repo that the deploy
workflow can use, but that nobody (including you, after saving) can view
again through the website — only use inside a workflow run.

1. Open the repo on github.com.
2. Click **Settings** (top nav of the repo, not your account settings).
3. In the left sidebar, click **Secrets and variables → Actions**.
4. Click the green **New repository secret** button.
5. Type the **Name** exactly as shown in the table below, paste the
   **Value**, click **Add secret**.
6. Repeat for every row in the table. Yes, all of them — the workflow will
   fail with a clear error telling you which one's missing if you skip one
   that doesn't have a default.

| Secret name | Where the value comes from |
|---|---|
| `GCP_SA_KEY` | The **entire contents** of `github-deployer-key.json` from step A8 — open it, select all, copy, paste the whole JSON blob as the value. |
| `GCP_PROJECT_ID` | Your project ID from step A3. |
| `GCP_REGION` | Optional — skip this one if you're fine with the default (`asia-southeast1`, i.e. Singapore). |
| `SUPABASE_URL` | Open your local `backend/.env` file (or root `.env` if you use docker-compose) and copy this line's value. |
| `SUPABASE_PUBLISHABLE_KEY` | Same `.env` file. |
| `SUPABASE_STORAGE_BUCKET` | Optional — skip unless you changed it from the default `documents`. |
| `DIFY_BASE_URL` | Optional — skip unless you use something other than Dify Cloud. |
| `DIFY_API_KEY` | Same `.env` file. |
| `DIFY_CLASSIFIER_API_KEY` | Same `.env` file. |
| `DIFY_CLASSIFIER_WORKFLOW_ID` | Same `.env` file. |
| `DIFY_EXTRACTION_WORKFLOW_ID` | Same `.env` file. |
| `DIFY_EXTRACTION_API_KEY` | Same `.env` file. |
| `AUTH_USERNAME` | Make this up — it's the shared login username you'll hand out to whoever you're sharing the demo with. |
| `AUTH_PASSWORD` | Make this up too — the shared login password. |
| `AUTH_SECRET_KEY` | Any long random string, used only to sign login tokens (nobody types this in). In PowerShell: `-join ((48..57)+(97..122)|Get-Random -Count 40 |%{[char]$_})` generates one, or just mash your keyboard for 40 characters. |

Once you've added all of them: go back to **Settings → Secrets and
variables → Actions** and count the rows — you should see 15 (or however
many you didn't skip).

**Now delete `github-deployer-key.json` from your computer** — its job is
done, it's already safely stored (encrypted) in the `GCP_SA_KEY` secret.

```powershell
Remove-Item github-deployer-key.json
```

---

## Part C — run the deployment

### C1. Trigger it

This only runs when you ask it to — pushing commits does nothing on its
own. On github.com, go to the **Actions** tab → click **Deploy to Cloud
Run** in the left sidebar → click the **Run workflow** dropdown (top right)
→ make sure the branch is `main` (or whichever branch you want deployed)
→ click the green **Run workflow** button.

### C2. Watch it run

Click into the run that just appeared (it'll have a yellow dot while
running). Click the `deploy` job to expand it and watch each step. The
first run takes longer (~5-8 minutes) since it also creates the Artifact
Registry repository; later runs are faster.

- 🟢 Green checkmark on every step = it worked.
- 🔴 Red X on a step = something failed — click that step to expand the
  log and read the error. See Troubleshooting below for the common ones.

### C3. Get your URLs

When the run finishes successfully, scroll to the bottom of that run's page
for the **Summary** — it prints both URLs, e.g.:

```
### Deployed
- Frontend: https://fyp-frontend-xxxxxxxxxx.asia-southeast1.run.app
- Backend: https://fyp-backend-xxxxxxxxxx.asia-southeast1.run.app
```

Open the **Frontend** URL in a browser — you should land on the app's
login page. Log in with the `AUTH_USERNAME`/`AUTH_PASSWORD` you chose in
Part B. Share that same URL + credential with whoever you want to see the
app; you don't need to repeat any of this setup for them.

---

## Troubleshooting

- **"billing account not configured" / `FAILED_PRECONDITION`** — go back
  to step A4, billing isn't linked to the project yet.
- **`PERMISSION_DENIED` on an `add-iam-policy-binding` command** — you're
  not an Owner/IAM Admin on this project, or you ran it against the wrong
  project. Check `gcloud config get-value project` matches what you
  intended.
- **Workflow fails at "Deploy backend to Cloud Run" with a permission
  error** — usually means one of the three `add-iam-policy-binding`
  commands in A7 didn't complete, or `GCP_SA_KEY` in GitHub doesn't match
  the service account you granted roles to. Re-check both.
- **Workflow fails with `GCP_PROJECT_ID` or similar "not found"** — a
  GitHub secret name is misspelled. Names must match the table exactly,
  including capitalization.
- **Frontend loads but shows a network error / can't reach backend** — the
  backend deploy step likely failed earlier in the same run; scroll up in
  that run's log to find the real error rather than debugging the frontend.
- Stuck on something not listed here? Paste the red error text from the
  failed step and we can debug it together.

---

## Notes / known limitations

- Both services are `--allow-unauthenticated` at the Cloud Run level (no
  Google login required to reach them) — access control is entirely the
  app's own `/login` page. Don't put real customer data through this
  deployment.
- No custom domain — you get the default `*.run.app` URLs.
- The backend's CORS allowlist matches any hostname Cloud Run might hand
  the frontend service (regex on the stable service name, not one exact
  resolved URL — see `ALLOWED_ORIGIN_REGEX` in the workflow), so it keeps
  working even if you delete and recreate the frontend service and its
  `*.run.app` URL changes.
- Cold starts: RapidOCR/onnxruntime make the backend image relatively
  heavy; the first request after idle may take a few seconds longer.
- To rotate the shared login credential later, just update the
  `AUTH_USERNAME`/`AUTH_PASSWORD` secrets in GitHub and re-run the
  workflow (Part C1) — no code changes needed.
