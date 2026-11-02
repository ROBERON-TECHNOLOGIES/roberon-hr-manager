"""Browser-only Gmail drafts and local PDF downloads; no API or credentials."""
import base64
import html
import json
from urllib.parse import urlencode


def email_content(employee_name, period):
    return f"Payslip - {period}", f"""Dear {employee_name},

Please find attached your salary payslip for {period}.

If you have any questions regarding your payslip, please contact the HR Department.

Regards,
HR Department
Roberon Technologies LLP"""


def compose_url(to_email, subject, body):
    return 'https://mail.google.com/mail/?' + urlencode({
        'view': 'cm', 'fs': '1', 'to': to_email, 'su': subject, 'body': body,
    })


def download_and_compose_html(pdf_bytes, filename, url):
    """Native new-tab link keeps opening tied to a user click (no popup timer).

    Data stays in this component until the user clicks. Gmail receives only
    the recipient, subject and body, never the PDF through the compose URL.
    """
    payload = base64.b64encode(pdf_bytes).decode('ascii')
    safe_filename = json.dumps(filename).replace('<', '\\u003c')
    return f'''<!doctype html><html><head><style>
    body {{margin:0;font:14px Arial,sans-serif;}}
    a {{display:inline-block;background:#c8102e;color:white;text-decoration:none;
        padding:13px 20px;border-radius:8px;font-weight:600;}}
    p {{color:#666;font-size:12px;margin:10px 0;}}
    </style></head><body>
    <a id="compose" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Download PDF &amp; open Gmail</a>
    <p id="status">Then drag the downloaded PDF into Gmail and click Send.</p>
    <script>
    document.getElementById('compose').addEventListener('click', function () {{
        try {{
            const bytes = Uint8Array.from(atob('{payload}'), c => c.charCodeAt(0));
            const blobUrl = URL.createObjectURL(new Blob([bytes], {{type:'application/pdf'}}));
            const download = document.createElement('a');
            download.href = blobUrl;
            download.download = {safe_filename};
            document.body.appendChild(download);
            download.click();
            download.remove();
            setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
            document.getElementById('status').textContent = 'Download requested. Attach the PDF in Gmail before sending.';
        }} catch (error) {{
            document.getElementById('status').textContent = 'Use Download generated PDF below, then attach it in Gmail.';
        }}
    }});
    </script></body></html>'''
