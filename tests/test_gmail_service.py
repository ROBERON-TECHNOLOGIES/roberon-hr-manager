import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import gmail_service


class GmailTests(unittest.TestCase):
    def test_payslip_has_one_recipient_and_exact_pdf(self):
        service = MagicMock()
        request = service.users.return_value.messages.return_value.send
        request.return_value.execute.return_value = {'id':'test-message-id'}
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'test.pdf'
            pdf.write_bytes(b'%PDF-fictional-test')
            with patch.object(gmail_service, 'connect_gmail', return_value=service):
                result = gmail_service.send_payslip_email('demo@example.test','Demo','October 2026',pdf)
            self.assertEqual(result['id'],'test-message-id')
            kwargs = request.call_args.kwargs
            self.assertEqual(kwargs['userId'],'me')
            message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(kwargs['body']['raw']))
            self.assertEqual(message['To'],'demo@example.test')
            self.assertEqual(message['Subject'],'Payslip - October 2026')
            attachment, = message.iter_attachments()
            self.assertEqual(attachment.get_payload(decode=True),pdf.read_bytes())
            request.return_value.execute.assert_called_once_with(num_retries=0)

    def test_connection_test_contains_no_payslip(self):
        service = MagicMock()
        request = service.users.return_value.messages.return_value.send
        request.return_value.execute.return_value = {'id':'test-id'}
        with patch.object(gmail_service,'connect_gmail',return_value=service):
            gmail_service.send_test_email('demo@example.test')
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(request.call_args.kwargs['body']['raw']))
        self.assertEqual(list(message.iter_attachments()),[])
        self.assertIn('connection test',message['Subject'])

    def test_missing_credentials_and_invalid_recipient(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(gmail_service,'APP_DIR',Path(tmp)):
            self.assertEqual(gmail_service.gmail_setup_status(),'missing_credentials')
            with self.assertRaises(gmail_service.GmailSetupError):
                gmail_service.connect_gmail()
        with patch.object(gmail_service,'connect_gmail') as connect:
            with self.assertRaises(ValueError):
                gmail_service.send_test_email('demo@example.test\nBcc: another@example.test')
            connect.assert_not_called()

    def test_uncertain_send_is_not_retried(self):
        service = MagicMock()
        request = service.users.return_value.messages.return_value.send
        request.return_value.execute.side_effect = TimeoutError('test timeout')
        with patch.object(gmail_service,'connect_gmail',return_value=service):
            with self.assertRaises(TimeoutError):
                gmail_service.send_test_email('demo@example.test')
        request.return_value.execute.assert_called_once_with(num_retries=0)
