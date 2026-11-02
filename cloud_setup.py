"""Create the Google Drive folder and Sheets workbook used by the cloud app."""
import argparse
import json
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-account", required=True, type=Path)
    parser.add_argument("--folder-id", default="")
    parser.add_argument("--sheet-id", default="")
    args = parser.parse_args()

    info = json.loads(args.service_account.read_text())
    credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)

    folder_id = args.folder_id.strip()
    if not folder_id:
        query = (
            "mimeType='application/vnd.google-apps.folder' "
            "and name='Roberon HR Manager' and trashed=false"
        )
        result = drive.files().list(q=query, fields="files(id)", pageSize=10).execute()
        if result.get("files"):
            folder_id = result["files"][0]["id"]
        else:
            folder_id = drive.files().create(
                body={"name": "Roberon HR Manager",
                      "mimeType": "application/vnd.google-apps.folder"},
                fields="id",
            ).execute()["id"]

    sheet_id = args.sheet_id.strip()
    if not sheet_id:
        sheet_id = drive.files().create(
            body={"name": "Roberon HR Manager Data",
                  "mimeType": "application/vnd.google-apps.spreadsheet",
                  "parents": [folder_id]},
            fields="id",
        ).execute()["id"]

    print("GOOGLE_DRIVE_FOLDER_ID =", folder_id)
    print("GOOGLE_SHEET_ID =", sheet_id)
    print("Share Drive folder with service-account email:", info["client_email"])
    print("Sheet access is granted automatically because it is created by the service account.")


if __name__ == "__main__":
    main()
