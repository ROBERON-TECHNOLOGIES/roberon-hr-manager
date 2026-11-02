"""Optional Google Drive and Google Sheets mirror for the Streamlit cloud deployment."""
import csv
import io
import json
import sqlite3
import tempfile
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "roberon_hr.db"
PAYSLIP_DIR = APP_DIR / "payslips"


def get_config():
    try:
        import streamlit as st
        service_account = st.secrets.get("GOOGLE_SERVICE_ACCOUNT")
        oauth_client = st.secrets.get("GOOGLE_OAUTH_CLIENT_JSON")
        oauth_token = st.secrets.get("GOOGLE_OAUTH_TOKEN_JSON")
        folder_id = st.secrets.get("GOOGLE_DRIVE_FOLDER_ID", "")
        sheet_id = st.secrets.get("GOOGLE_SHEET_ID", "")
    except Exception:
        service_account = None
        oauth_client = None
        oauth_token = None
        folder_id = ""
        sheet_id = ""
    if isinstance(service_account, dict):
        service_account = dict(service_account)
    elif isinstance(service_account, str):
        try:
            service_account = json.loads(service_account)
        except json.JSONDecodeError:
            service_account = None
    if service_account:
        return {"mode": "service_account", "service_account": service_account,
                "folder_id": str(folder_id).strip(), "sheet_id": str(sheet_id).strip()}

    if isinstance(oauth_client, str):
        try:
            oauth_client = json.loads(oauth_client)
        except json.JSONDecodeError:
            oauth_client = None
    if isinstance(oauth_token, str):
        try:
            oauth_token = json.loads(oauth_token)
        except json.JSONDecodeError:
            oauth_token = None
    if oauth_client and oauth_token:
        if "installed" in oauth_client:
            oauth_client = oauth_client["installed"]
        oauth_credentials = dict(oauth_client)
        oauth_credentials.update(oauth_token)
        return {"mode": "oauth", "oauth_credentials": oauth_credentials,
                "folder_id": str(folder_id).strip(), "sheet_id": str(sheet_id).strip()}

    local_credentials = APP_DIR / "credentials.json"
    local_token = APP_DIR / "token.json"
    if local_credentials.is_file() and local_token.is_file():
        client = json.loads(local_credentials.read_text())
        if "installed" in client:
            client = client["installed"]
        token = json.loads(local_token.read_text())
        oauth_credentials = dict(client)
        oauth_credentials.update(token)
        return {"mode": "oauth", "oauth_credentials": oauth_credentials,
                "folder_id": str(folder_id).strip(), "sheet_id": str(sheet_id).strip()}
    return None


def _credentials(config, scopes):
    if config["mode"] == "service_account":
        from google.oauth2.service_account import Credentials
        return Credentials.from_service_account_info(config["service_account"], scopes=scopes)
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_info(config["oauth_credentials"], scopes=scopes)


def status():
    config = get_config()
    if not config:
        return "not_configured"
    if config["folder_id"] and config["sheet_id"]:
        return "configured"
    return "needs_resource_ids"


def _drive_service(config):
    from googleapiclient.discovery import build
    credentials = _credentials(config, ["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _find_or_create_folder(service, name="Roberon HR Manager"):
    query = (
        "mimeType='application/vnd.google-apps.folder' and "
        f"name='{name.replace(chr(39), chr(92) + chr(39))}' and trashed=false"
    )
    result = service.files().list(q=query, spaces="drive", fields="files(id, name)", pageSize=10).execute()
    if result.get("files"):
        return result["files"][0]["id"]
    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    return service.files().create(body=metadata, fields="id").execute()["id"]


def ensure_resources():
    """Create a Drive folder and Sheet when their IDs are not configured."""
    config = get_config()
    if not config:
        raise RuntimeError("Google service-account secrets are not configured.")
    service = _drive_service(config)
    folder_id = config["folder_id"] or _find_or_create_folder(service)
    sheet_id = config["sheet_id"]
    if not sheet_id:
        metadata = {"name": "Roberon HR Manager Data",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "parents": [folder_id]}
        sheet_id = service.files().create(body=metadata, fields="id").execute()["id"]
    return folder_id, sheet_id


def _upload_bytes(service, folder_id, filename, data, mime_type):
    from googleapiclient.http import MediaIoBaseUpload
    query = (
        f"name='{filename.replace(chr(39), chr(92) + chr(39))}' and "
        f"'{folder_id}' in parents and trashed=false"
    )
    existing = service.files().list(q=query, spaces="drive", fields="files(id)", pageSize=5).execute()
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
    if existing.get("files"):
        file_id = existing["files"][0]["id"]
        return service.files().update(fileId=file_id, media_body=media, fields="id").execute()["id"]
    metadata = {"name": filename, "parents": [folder_id]}
    return service.files().create(body=metadata, media_body=media, fields="id").execute()["id"]


def _download_file(service, folder_id, filename):
    from googleapiclient.http import MediaIoBaseDownload
    query = (
        f"name='{filename.replace(chr(39), chr(92) + chr(39))}' and "
        f"'{folder_id}' in parents and trashed=false"
    )
    result = service.files().list(q=query, spaces="drive", fields="files(id)", pageSize=5).execute()
    files = result.get("files", [])
    if not files:
        return False
    output = io.BytesIO()
    request = service.files().get_media(fileId=files[0]["id"])
    downloader = MediaIoBaseDownload(output, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    data = output.getvalue()
    target = DB_PATH if filename == "roberon_hr.db" else None
    if target is not None:
        target.write_bytes(data)
    elif filename.startswith("payslips/"):
        PAYSLIP_DIR.mkdir(parents=True, exist_ok=True)
        (PAYSLIP_DIR / Path(filename).name).write_bytes(data)
    return True


def _replace_sheet_rows(config, sheet_id, title, rows):
    import gspread
    credentials = _credentials(config, [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(sheet_id)
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=title, rows=max(100, len(rows)), cols=20)
    values = [list(row) for row in rows]
    if values:
        worksheet.clear()
        worksheet.update(values=values)


def sync_to_cloud():
    """Upload the database, generated PDFs, payroll sheet and employee sheet."""
    config = get_config()
    if not config:
        return False
    folder_id = config["folder_id"]
    sheet_id = config["sheet_id"]
    if not folder_id or not sheet_id:
        return False
    service = _drive_service(config)
    _upload_bytes(service, folder_id, "roberon_hr.db", DB_PATH.read_bytes(), "application/x-sqlite3")

    if PAYSLIP_DIR.exists():
        for pdf in PAYSLIP_DIR.glob("*.pdf"):
            _upload_bytes(service, folder_id, pdf.name, pdf.read_bytes(), "application/pdf")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    employees = [dict(row) for row in con.execute("SELECT * FROM employees ORDER BY name").fetchall()]
    payroll = [dict(row) for row in con.execute(
        """SELECT p.*, e.name, e.employee_id, e.email
           FROM payroll p JOIN employees e ON e.id=p.employee_db_id
           ORDER BY p.created_at DESC""").fetchall()]
    con.close()

    employee_columns = ["id", "name", "employee_id", "designation", "email", "bank_account", "ifsc",
                        "active", "created_at"]
    _replace_sheet_rows(config, sheet_id, "Employees",
                        [employee_columns] + [[row.get(column, "") for column in employee_columns]
                                              for row in employees])
    payroll_columns = ["id", "employee_db_id", "name", "employee_id", "email", "pay_period", "gross_pay",
                       "deduction_amount", "deduction_reason", "net_pay", "payment_date", "notes",
                       "email_status", "email_error", "pdf_path", "created_at"]
    _replace_sheet_rows(config, sheet_id, "Payroll",
                        [payroll_columns] + [[row.get(column, "") for column in payroll_columns]
                                             for row in payroll])
    return True


def sync_from_cloud():
    """Restore the database before a cloud app instance begins handling requests."""
    config = get_config()
    if not config or not config["folder_id"]:
        return False
    service = _drive_service(config)
    if _download_file(service, config["folder_id"], "roberon_hr.db"):
        restored = True
    else:
        restored = False
    PAYSLIP_DIR.mkdir(parents=True, exist_ok=True)
    if restored:
        for pdf in PAYSLIP_DIR.glob("*.pdf"):
            pdf.unlink()
        query = (
            f"'{config['folder_id']}' in parents and "
            "mimeType='application/pdf' and trashed=false"
        )
        result = service.files().list(q=query, spaces="drive",
                                      fields="files(id,name)", pageSize=1000).execute()
        for record in result.get("files", []):
            request = service.files().get_media(fileId=record["id"])
            output = io.BytesIO()
            from googleapiclient.http import MediaIoBaseDownload
            downloader = MediaIoBaseDownload(output, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            (PAYSLIP_DIR / Path(record["name"]).name).write_bytes(output.getvalue())
    return restored


def upload_pdf(pdf_path):
    config = get_config()
    if not config or not config["folder_id"]:
        return False
    pdf_path = Path(pdf_path)
    service = _drive_service(config)
    _upload_bytes(service, config["folder_id"], pdf_path.name,
                  pdf_path.read_bytes(), "application/pdf")
    return True
