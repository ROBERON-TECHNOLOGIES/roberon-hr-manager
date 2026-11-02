# Roberon HR Manager

Local Streamlit app for employee records, salary calculations, payslip PDFs and Gmail drafts.

## Run on macOS

```bash
cd roberon_hr_manager
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

For a first install, create the environment with `python3 -m venv .venv` first.
Open the local URL printed in Terminal (normally http://localhost:8501).
Restart Streamlit after updating this version to load the payroll schema changes.

## Process salary and send from Gmail

1. Select an employee, pay period and payment date in **Process Salary**.
2. Enter **Gross salary**, **Deduction amount** (zero if none), and a reason for any deduction.
   Net payable is gross salary less deductions. Review it before generating.
3. Generate and review the PDF. Bank details are hidden unless explicitly selected.
4. Click **Download PDF & open Gmail**. This requests a PDF download and opens Gmail
   with To, Subject and message filled in. No API credentials are required.
5. Use the company Gmail account. Drag the matching PDF from Downloads into the
   draft, wait for upload, review the recipient/attachment, and click Send in Gmail.
6. Click **I sent this payslip in Gmail** in the app to record your confirmation.
   Opening Gmail never marks a payslip as sent automatically.

Browser settings control the download location and whether a new tab can open.
If blocked, use **Download generated PDF** and **Open Gmail only (fallback)** separately.
The application cannot attach a local file to Gmail or verify delivery with this flow.
The earlier API helper remains available in source, but the app does not call it.

## PDF design and payroll assumptions

One A4 page in red, white and black, with separate earnings and deductions cards,
Indian currency grouping and prominent net pay. Gross salary is currently a single
basic salary component; deductions are one amount with an operator-entered reason.
Long fields shrink and may be shortened with an ellipsis to preserve alignment.
Internal notes and employee email addresses are not printed on the PDF.
A fictional example is at `../output/pdf/roberon_sample_payslip.pdf`.
Existing PDFs are unchanged; new generation uses the approved design.
The app records operator-entered payroll; it does not verify bank transfers.

## Data and upgrades

Employee and payroll records stay in `roberon_hr.db`; PDFs stay in `payslips/`.
Back up the database securely. The additive upgrade preserves existing rows, adds
gross salary/deduction fields, and treats old payroll as gross=net with zero deductions.
Generation saves a payroll record, so use a temporary database for dummy payroll.

## Checks

With the environment active:

```bash
pip install pymupdf  # optional development dependency for PDF checks
python -m unittest discover -s tests -v
```

## Optional automatic Gmail sending

The existing download-and-Gmail draft flow remains available and needs no setup.
For automatic PDF attachment/sending, follow **Gmail help > Automatic Gmail setup**:
create a Desktop OAuth client with Gmail API enabled, save its download as
`credentials.json`, connect the company account and send a non-payroll connection
test to yourself. A `token.json` authorization file is stored locally with owner-only
permissions. Both credential files remain ignored by git. Only `gmail.send` is requested.

Under a generated payslip, expand **Optional: send automatically through Gmail**,
review the recipient/PDF, confirm and click **Send PDF automatically**. Generation
alone never sends mail. A successful API response marks the record Sent; this means
Gmail accepted the message, not proof of inbox delivery. Previously sent records
cannot be sent again through that button. Sends are never automatically retried:
if a connection fails after submission, check Gmail Sent before attempting manual delivery.

Google's standard API usage has no additional charge within quotas; mailbox sending
limits still apply. See the linked Google documentation in Gmail help. External OAuth
apps in Testing can require authorization again after seven days. This local Desktop
OAuth flow expects the browser and Streamlit server on the same machine.

## Bulk Payroll

Use **Bulk Payroll** for sheet-style entry. Active employees are prefilled; tick
**Include**, enter gross salary, deductions and a reason, and add rows for new IDs.
You can paste cells from Excel/Sheets or download/import a CSV using the provided
column headings. Pay period and payment date apply to all selected rows.

Click **Review all payslips** to validate the entire sheet and prepare a saved batch.
Every selected employee's recipient, subject, message and PDF is displayed inline
on the same page. The PDF preview is rendered from the exact attachment bytes.
Review creates no employee/payroll records and sends no mail. Editing selected rows
or dates disables sending until you review again. Bank details are always excluded
in bulk mode. Existing employee directory details are not overwritten by sheet edits;
new employee IDs are registered when that row is sent.

At the bottom, confirm the review and click **Send all reviewed payslips**. The
connected Gmail account sends separate messages, one PDF per person. Payroll and
batch statuses are persisted in SQLite. Previously sent or uncertain employee/period
payments are skipped to avoid duplicates; this also checks individual payroll history.
Gmail acceptance means Sent, not proof of inbox delivery. A network/provider error
stops sending and marks that row **Check Gmail**. Check Gmail Sent manually for any
**Check Gmail** or **Sending** row; these rows are never retried automatically.
Remaining **Ready** rows can be sent by another click after checking the results.
Use **Saved review batches > Load saved batch** to recover a batch after a reload.

Batch snapshots include recipient/message data and PDF bytes in the local database,
so protect backups as payroll records. PyMuPDF is now a runtime dependency for inline
previews; install updated `requirements.txt` when upgrading another environment.

## Start with a double click

In Finder, double-click **Start Roberon HR Manager.command** in the parent project
folder. It starts Streamlit in the background and opens http://localhost:8501 in
your default browser. If the app is already running, it opens the existing app.
You may close the Terminal window; the service keeps running until the computer
shuts down or the process is stopped. Run the launcher again after restarting your Mac.
Keep the launcher beside the `roberon_hr_manager` folder; you can create a Finder
alias on your Desktop for convenience. Startup errors are logged to
`roberon_hr_manager/.launcher.log`.

To stop the background service, double-click **Stop Roberon HR Manager.command**
beside the Start launcher. It gracefully stops this project's Streamlit server;
saved employees, payroll, PDFs and Gmail authorization remain intact. Closing the
browser alone does not stop the service. Use the Start launcher to open it again.

## Free cloud deployment

This app is a Streamlit WebSocket application backed by SQLite. For reliable operation,
the website should be hosted on Streamlit Community Cloud or another service that keeps
a server process running. Vercel is optimized for serverless websites and does not provide
persistent storage for the current SQLite database or local `payslips/` folder.

Recommended hobby setup:

1. Keep the application code in a GitHub repository.
2. Deploy it to Streamlit Community Cloud for a free public or private website link.
3. Store employee/payroll data in your Google Sheets or in a cloud database.
4. Store payslip PDF files in a dedicated Google Drive folder.
5. Store Gmail OAuth credentials only in Streamlit Cloud secrets; never commit them.

Cloud storage is not automatic yet. Before deployment, migrate the local database and PDFs
to Drive/Sheets; sharing a Drive or Sheets folder alone does not make the app use it.
Google credentials must be created and granted to the exact Drive/Sheets resources.

For the initial deployment:

```bash
streamlit run app.py
```

Select the repository `app.py` entry point and add `requirements.txt` automatically.
The automatic Gmail flow will need adjustment for a hosted OAuth redirect; the manual
browser-Gmail flow works without server-side credentials and is suitable for monthly use.

## Google Sheets and Drive setup

The cloud version mirrors the existing SQLite app to Google without rewriting every screen.
When configured it stores:

- `roberon_hr.db` in the Drive folder
- generated PDFs in the Drive folder
- employees in the `Employees` Google Sheet tab
- payroll records in the `Payroll` Google Sheet tab

1. Enable Google Drive API and Google Sheets API in Google Cloud Console.
2. Create a service account and download its JSON key.
3. Run:

   ```bash
   .venv/bin/python cloud_setup.py --service-account /secure/path/service-account.json
   ```

   The command prints the Drive folder ID and Google Sheet ID.
4. In Streamlit Community Cloud, add these secrets:

   ```toml
   GOOGLE_SERVICE_ACCOUNT = """<complete service-account JSON>"""
   GOOGLE_DRIVE_FOLDER_ID = "<printed folder ID>"
   GOOGLE_SHEET_ID = "<printed sheet ID>"
   ```

5. Deploy `app.py` from the repository.
6. Use **Gmail help > Back up database, records and PDFs now** to upload existing local data.

Never commit a service-account JSON key, `credentials.json`, `token.json`, or
`.streamlit/secrets.toml`. Keep the service account limited to the dedicated Drive
folder and Sheet.
