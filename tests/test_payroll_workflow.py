import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from datetime import date

import db
from gmail_compose import compose_url, email_content, download_and_compose_html
from payslip import create_payslip


class WorkflowTests(unittest.TestCase):
    def test_legacy_database_migration_preserves_records(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, 'DB_PATH', Path(tmp) / 'legacy.db'):
            with db.connect() as con:
                con.execute('CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, employee_id TEXT, '
                            'designation TEXT, email TEXT, bank_account TEXT, ifsc TEXT, active INTEGER, created_at TEXT)')
                con.execute("INSERT INTO employees VALUES (1,'Demo','TEST','Engineer','demo@example.test','','',1,'original')")
                con.execute('CREATE TABLE payroll (id INTEGER PRIMARY KEY, employee_db_id INTEGER, pay_period TEXT, '
                            'net_pay REAL, payment_date TEXT, notes TEXT, email_status TEXT, email_error TEXT, pdf_path TEXT, created_at TEXT)')
                con.execute("INSERT INTO payroll VALUES (1,1,'October 2026',1000,'2026-11-01','Keep me','Sent','','old.pdf','original')")
            db.init_db()
            db.init_db()
            old = db.get_payroll()[0]
            self.assertEqual((old['net_pay'], old['gross_pay'], old['deduction_amount']), (1000,1000,0))
            self.assertEqual((old['notes'], old['pdf_path'], old['email_status']), ('Keep me','old.pdf','Sent'))
            db.save_payroll(1,'November 2026',950,'2026-11-01','','Not sent','new.pdf',
                            gross_pay=1000,deduction_amount=50,deduction_reason='Loss of pay')
            self.assertEqual(len(db.get_payroll()), 2)
            self.assertEqual(len(db.get_employees()), 1)

    def test_gmail_fields_are_encoded_without_injection(self):
        name = 'Demo & Test <employee>'
        subject, body = email_content(name, 'October 2026')
        url = compose_url('demo+hr@example.test',subject,body)
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params['to'], ['demo+hr@example.test'])
        self.assertEqual(params['su'], [subject])
        self.assertEqual(params['body'], [body])
        markup = download_and_compose_html(b'%PDF-test', '</script>.pdf', url)
        self.assertNotIn('download.download = "</script>', markup)
        self.assertIn('application/pdf', markup)
        self.assertIn('target="_blank"', markup)

    def test_deductions_pdf(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as tmp:
            p = create_payslip({'name':'Demo','employee_id':'TEST'},'October 2026',10000,date(2026,11,1),
                               Path(tmp)/'deductions.pdf',deduction_amount=1250,deduction_reason='Loss of pay')
            with pymupdf.open(p) as pdf:
                self.assertEqual(len(pdf),1)
                text = pdf[0].get_text()
                for expected in ('Rs. 10,000.00','Rs. 1,250.00','Rs. 8,750.00','Loss of pay'):
                    self.assertIn(expected,text)
            for value in (-1,10001,float('nan')):
                with self.assertRaises(ValueError):
                    create_payslip({},'October 2026',10000,date.today(),p,deduction_amount=value)


if __name__ == '__main__':
    unittest.main()
