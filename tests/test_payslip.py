"""Run with: python -m unittest discover -s tests -v (PyMuPDF for PDF checks)."""
from datetime import date
from pathlib import Path
import tempfile
import unittest

from payslip import create_payslip, format_inr

try:
    import pymupdf
except ImportError:
    pymupdf = None


class PayslipTests(unittest.TestCase):
    def test_indian_currency(self):
        for value, expected in [(0, 'Rs. 0.00'), (1234567.89, 'Rs. 12,34,567.89'),
                                ('99999.995', 'Rs. 1,00,000.00')]:
            self.assertEqual(format_inr(value), expected)

    def test_invalid_salary(self):
        for value in [-1, float('nan'), float('inf'), 'invalid']:
            with self.assertRaises(ValueError):
                create_payslip({}, 'October 2026', value, date(2026, 11, 1), 'unused.pdf')

    @unittest.skipUnless(pymupdf, 'Install PyMuPDF for PDF rendering checks')
    def test_page_bounds_and_bank_privacy(self):
        employee = dict(name='Sample Employee', employee_id='DEMO-001',
                        designation='Software Engineer', email='never-print@example.test',
                        bank_account='9876543210123456', ifsc='TEST0001234')
        with tempfile.TemporaryDirectory() as tmp:
            for bank in (False, True):
                for long in (False, True):
                    data = dict(employee)
                    if long:
                        data.update(name='Very long employee name ' * 15,
                                    designation='Senior engineering designation ' * 15,
                                    employee_id='DEMO-' * 30)
                    path = create_payslip(data, 'October 2026', 1234567.89,
                                          date(2026, 11, 1), Path(tmp) / 'test.pdf', bank)
                    with pymupdf.open(path) as pdf:
                        self.assertEqual(len(pdf), 1)
                        page = pdf[0]
                        self.assertAlmostEqual(page.rect.width, 595.28, places=1)
                        self.assertAlmostEqual(page.rect.height, 841.89, places=1)
                        text = page.get_text()
                        self.assertIn('Rs. 12,34,567.89', text)
                        self.assertNotIn(employee['email'], text)
                        for field in ('bank_account', 'ifsc'):
                            self.assertEqual(employee[field] in text, bank)
                        for block in page.get_text('dict')['blocks']:
                            if 'lines' not in block:
                                continue
                            for line in block['lines']:
                                for span in line['spans']:
                                    self.assertTrue(page.rect.contains(pymupdf.Rect(span['bbox'])))


if __name__ == '__main__':
    unittest.main()
