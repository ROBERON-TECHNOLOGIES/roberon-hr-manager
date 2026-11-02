"""Full salary flow against isolated data, without opening or sending Gmail."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
from streamlit.testing.v1 import AppTest


class AppTests(unittest.TestCase):
    def test_deduction_generation_and_manual_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, 'DB_PATH', Path(tmp) / 'test.db'):
            db.init_db()
            db.add_employee('Demo Employee','DEMO-TEST','Engineer','demo@example.test','','')
            source = (Path(__file__).resolve().parents[1] / 'app.py').read_text()
            source = source.replace('PAYSLIP_DIR = APP_DIR / "payslips"',
                                    f'PAYSLIP_DIR = Path({str(Path(tmp) / "payslips")!r})')
            app = AppTest.from_string(source).run()
            self.assertFalse(app.exception)
            next(x for x in app.number_input if x.label == 'Gross salary (₹)').set_value(10000)
            next(x for x in app.number_input if x.label == 'Deduction amount (₹)').set_value(1250)
            next(x for x in app.text_input if x.label == 'Deduction reason').set_value('Loss of pay')
            next(x for x in app.button if x.label == 'Generate payslip PDF').click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            row = db.get_payroll()[0]
            self.assertEqual((row['gross_pay'],row['deduction_amount'],row['net_pay']), (10000,1250,8750))
            self.assertEqual(row['email_status'], 'Not sent')
            self.assertTrue(Path(row['pdf_path']).exists())
            import pymupdf
            with pymupdf.open(row['pdf_path']) as doc:
                self.assertIn('Rs. 8,750.00',doc[0].get_text())
            self.assertIn('mail.google.com', app.get('iframe')[0].proto.srcdoc)
            next(x for x in app.button if x.label == 'I sent this payslip in Gmail').click().run()
            self.assertEqual(db.get_payroll()[0]['email_status'],'Sent')
            next(x for x in app.number_input if x.label == 'Deduction amount (₹)').set_value(20000)
            next(x for x in app.button if x.label == 'Generate payslip PDF').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertEqual(len(db.get_payroll()),1)

    def test_optional_automatic_send_and_fallback(self):
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as tmp, patch.object(db, 'DB_PATH', Path(tmp)/'test.db'):
                db.init_db()
                db.add_employee('Demo','DEMO','Engineer','demo@example.test','','')
                pdf = Path(tmp)/'sample.pdf'
                pdf.write_bytes(b'%PDF-test')
                record = db.save_payroll(1,'October 2026',1000,'2026-11-01','','Not sent',str(pdf))
                source = (Path(__file__).resolve().parents[1]/'app.py').read_text()
                source = source.replace('PAYSLIP_DIR = APP_DIR / "payslips"',f'PAYSLIP_DIR = Path({str(Path(tmp)/"payslips")!r})')
                with patch('gmail_service.gmail_setup_status',return_value='authorization_saved'), patch('gmail_service.send_payslip_email') as send:
                    send.return_value = {'id':'test-id'}
                    if failed:
                        send.side_effect = TimeoutError('test')
                    app = AppTest.from_string(source)
                    for key, value in dict(latest_pdf=str(pdf),latest_record_id=record,
                        latest_employee=db.get_employees()[0],latest_period='October 2026',latest_amount=1000).items():
                        app.session_state[key] = value
                    app.run()
                    button = next(x for x in app.button if x.label=='Send PDF automatically')
                    self.assertTrue(button.disabled)
                    next(x for x in app.checkbox if x.label.startswith('I reviewed this recipient')).check().run()
                    next(x for x in app.button if x.label=='Send PDF automatically').click().run()
                    self.assertFalse(app.exception)
                    send.assert_called_once_with('demo@example.test','Demo','October 2026',pdf)
                    self.assertEqual(db.get_payroll()[0]['email_status'],'Check Gmail' if failed else 'Sent')
                    self.assertIn('Download PDF',app.get('iframe')[0].proto.srcdoc)
                    if not failed:
                        self.assertTrue(next(x for x in app.button if x.label=='Send PDF automatically').disabled)
