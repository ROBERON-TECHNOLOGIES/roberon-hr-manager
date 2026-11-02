import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import db
import bulk_payroll as bulk


def rows():
    return [dict(Include=True, **{'Employee ID':f'DEMO-{i}','Name':f'Demo {i}',
            'Email':f'demo{i}@example.test','Designation':'Engineer','Gross salary':10000,
            'Deductions':1250,'Deduction reason':'Loss of pay'}) for i in (1,2)]


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(db,'DB_PATH',Path(self.tmp.name)/'test.db')
        self.patch.start()
        db.init_db()
        bulk.init_bulk_db()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def prepare(self):
        return bulk.prepare_batch(bulk.validate_rows(rows(),'October 2026',date(2026,11,1)))

    def test_validate_before_any_generation(self):
        for key,value in [('Email','a@example.test,b@example.test'),('Gross salary',float('nan')),
                          ('Deductions',10001),('Deduction reason',''),('Name','')]:
            invalid = rows()
            invalid[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                bulk.validate_rows(invalid,'October 2026',date(2026,11,1))
        repeated = rows()
        repeated[1]['Employee ID'] = repeated[0]['Employee ID'].lower()
        with self.assertRaises(ValueError):
            bulk.validate_rows(repeated,'October 2026',date.today())

    def test_review_is_persistent_and_does_not_send_or_create_payroll(self):
        first = self.prepare()
        self.assertEqual(self.prepare(),first)
        self.assertEqual(db.get_payroll(),[])
        self.assertEqual(db.get_employees(),[])
        items = bulk.load_batch(first)
        self.assertEqual(len(items),2)
        import pymupdf
        for item in items:
            with pymupdf.open(stream=item['pdf'],filetype='pdf') as pdf:
                text = pdf[0].get_text()
                self.assertIn(item['data']['employee']['name'],text)
                self.assertIn('Rs. 8,750.00',text)

    def test_exact_reviewed_attachments_and_no_repeat_send(self):
        batch = self.prepare()
        reviewed = bulk.load_batch(batch)
        with patch.object(bulk,'connect_gmail',return_value=MagicMock()), patch.object(bulk,'send_prepared_email',return_value={'id':'fake-id'}) as send:
            items = bulk.send_batch(batch,Path(self.tmp.name)/'pdfs')
            self.assertEqual([item['status'] for item in items],['Sent','Sent'])
            self.assertEqual(send.call_count,2)
            for item,call in zip(reviewed,send.call_args_list):
                self.assertEqual(call.args[0],item['data']['employee']['email'])
                self.assertEqual(call.args[1],item['data']['subject'])
                self.assertEqual(call.args[2],item['data']['body'])
                self.assertEqual(call.args[3],item['pdf'])
            bulk.send_batch(batch,Path(self.tmp.name)/'pdfs')
            self.assertEqual(send.call_count,2)
        self.assertEqual(len(db.get_payroll()),2)
        self.assertEqual(len(db.get_employees()),2)

    def test_uncertain_delivery_stops_batch_and_is_not_retried(self):
        batch = self.prepare()
        with patch.object(bulk,'connect_gmail',return_value=MagicMock()), patch.object(bulk,'send_prepared_email',side_effect=TimeoutError) as send:
            items = bulk.send_batch(batch,Path(self.tmp.name)/'pdfs')
            self.assertEqual([item['status'] for item in items],['Check Gmail','Ready'])
            send.assert_called_once()
        with patch.object(bulk,'connect_gmail',return_value=MagicMock()), patch.object(bulk,'send_prepared_email',return_value={'id':'fake-id'}) as send:
            items = bulk.send_batch(batch,Path(self.tmp.name)/'pdfs')
            self.assertEqual([item['status'] for item in items],['Check Gmail','Sent'])
            self.assertEqual(send.call_args.args[0],'demo2@example.test')

    def test_duplicate_period_is_skipped_even_across_batches(self):
        batch = self.prepare()
        with patch.object(bulk,'connect_gmail',return_value=MagicMock()), patch.object(bulk,'send_prepared_email',return_value={'id':'fake-id'}) as send:
            bulk.send_batch(batch,Path(self.tmp.name)/'pdfs')
            changed = rows()
            changed[0]['Gross salary'] = 20000
            new = bulk.prepare_batch(bulk.validate_rows(changed,'October 2026',date(2026,11,1)))
            items = bulk.send_batch(new,Path(self.tmp.name)/'pdfs')
            self.assertEqual([item['status'] for item in items],['Skipped','Skipped'])
            self.assertEqual(send.call_count,2)

    def test_bulk_sheet_review_and_send_in_streamlit(self):
        import pandas as pd
        from streamlit.testing.v1 import AppTest
        source = (Path(__file__).resolve().parents[1]/'app.py').read_text()
        source = source.replace('PAYSLIP_DIR = APP_DIR / "payslips"',
                                f'PAYSLIP_DIR = Path({str(Path(self.tmp.name)/"pdfs")!r})')
        with patch('bulk_ui.gmail_setup_status',return_value='authorization_saved'), patch.object(bulk,'connect_gmail',return_value=MagicMock()), patch.object(bulk,'send_prepared_email',return_value={'id':'fake-id'}) as send:
            app = AppTest.from_string(source)
            app.session_state['bulk_seed'] = pd.DataFrame(rows())
            app.run()
            self.assertFalse(app.exception)
            next(x for x in app.button if x.label=='Review all payslips').click().run()
            self.assertFalse(app.exception)
            send.assert_not_called()
            batch = app.session_state['bulk_batch_id']
            self.assertEqual(len(bulk.load_batch(batch)),2)
            self.assertTrue(next(x for x in app.button if x.label=='Send all reviewed payslips').disabled)
            next(x for x in app.checkbox if x.label.startswith('I reviewed all recipients')).check().run()
            self.assertFalse(next(x for x in app.button if x.label=='Send all reviewed payslips').disabled)
            # Editing the salary month invalidates review even when the confirmation was checked.
            next(x for x in app.date_input if x.label=='Bulk salary month').set_value(date(2026,9,1)).run()
            self.assertTrue(next(x for x in app.button if x.label=='Send all reviewed payslips').disabled)
            next(x for x in app.button if x.label=='Review all payslips').click().run()
            next(x for x in app.checkbox if x.label.startswith('I reviewed all recipients')).check().run()
            next(x for x in app.button if x.label=='Send all reviewed payslips').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_count,2)
            self.assertTrue(next(x for x in app.button if x.label=='Send all reviewed payslips').disabled)

    def test_failed_authorization_keeps_rows_ready_and_no_payroll(self):
        from gmail_service import GmailSetupError
        batch = self.prepare()
        with patch.object(bulk,'connect_gmail',side_effect=GmailSetupError('test authorization error')):
            with self.assertRaises(GmailSetupError):
                bulk.send_batch(batch,Path(self.tmp.name)/'pdfs')
        self.assertEqual([item['status'] for item in bulk.load_batch(batch)],['Ready','Ready'])
        self.assertEqual(db.get_payroll(),[])
