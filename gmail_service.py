"""Optional Gmail API delivery. The browser draft flow does not depend on this."""
import base64
import os
from email.message import EmailMessage
from pathlib import Path

from gmail_compose import email_content

APP_DIR = Path(__file__).resolve().parent
SCOPES = ['https://www.googleapis.com/auth/gmail.send']


class GmailSetupError(Exception):
    pass


def gmail_setup_status():
    if not (APP_DIR / 'credentials.json').is_file():
        return 'missing_credentials'
    if not (APP_DIR / 'token.json').is_file():
        return 'needs_sign_in'
    return 'authorization_saved'


def connect_gmail():
    """Authorize the local desktop app; request send permission only."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GmailSetupError('Install the packages in requirements.txt first.') from exc

    credentials_file = APP_DIR / 'credentials.json'
    token_file = APP_DIR / 'token.json'
    if not credentials_file.is_file():
        raise GmailSetupError('Add credentials.json using the Automatic Gmail setup steps in Gmail help.')
    creds = None
    try:
        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
                creds = flow.run_local_server(port=0, timeout_seconds=120, prompt='select_account consent')
            # Store secrets with owner-only access; never print tokens in UI/logs.
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as token:
                token.write(creds.to_json())
            token_file.chmod(0o600)
        return build('gmail', 'v1', credentials=creds, cache_discovery=False)
    except Exception as exc:
        raise GmailSetupError('Google authorization failed or expired. Check the OAuth setup, '
                              'test-user access and credentials. Reconnect after removing token.json '
                              'if Google has revoked access. The manual Gmail option is still available.') from exc


def _send(to_email, subject, body, pdf_path=None, *, pdf_bytes=None, filename=None, service=None):
    if any(char in to_email for char in '\r\n,;') or '@' not in to_email:
        raise ValueError('Enter one valid recipient email address.')
    message = EmailMessage()
    message['To'] = to_email
    message['Subject'] = subject
    message.set_content(body)
    if pdf_path is not None:
        pdf_path = Path(pdf_path)
        message.add_attachment(pdf_path.read_bytes(), maintype='application', subtype='pdf', filename=pdf_path.name)
    if pdf_bytes is not None:
        message.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename=filename)
    service = service if service is not None else connect_gmail()
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')
    # Never retry sends automatically: a timeout may follow successful delivery.
    result = service.users().messages().send(userId='me', body={'raw': raw}).execute(num_retries=0)
    if not result.get('id'):
        raise RuntimeError('Gmail did not return a message ID. Check Sent before trying again.')
    return result


def send_payslip_email(to_email, employee_name, period, pdf_path):
    subject, body = email_content(employee_name, period)
    return _send(to_email, subject, body, pdf_path)


def send_test_email(to_email):
    """Non-payroll connection test; never includes employee data or attachments."""
    return _send(to_email, 'Roberon HR Manager - Gmail connection test',
                 'This is a test email from Roberon HR Manager. Automatic Gmail sending is working.\n'
                 'No employee information or payslip is included.')


def send_prepared_email(to_email, subject, body, pdf_bytes, filename, service):
    """Send the exact bytes and text displayed in the immutable bulk review."""
    return _send(to_email, subject, body, pdf_bytes=pdf_bytes, filename=filename, service=service)
