"""Persistent reviewed batches: immutable PDFs, atomic claims, no send retries."""
import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import db
import cloud_storage
from gmail_compose import email_content
from gmail_service import connect_gmail, send_prepared_email
from payslip import create_payslip


def init_bulk_db():
    with db.connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS bulk_batches (
            id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        con.execute('''CREATE TABLE IF NOT EXISTS bulk_items (
            id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, snapshot TEXT NOT NULL,
            pdf BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'Ready',
            payroll_id INTEGER, message_id TEXT, error TEXT NOT NULL DEFAULT '')''')


def validate_rows(rows, period, payment_date):
    result, seen = [], set()
    if not period.strip():
        raise ValueError('Choose a pay period.')
    for index, row in enumerate(rows, 1):
        if not row.get('Include', False):
            continue
        values = {key: str(row.get(key) or '').strip() for key in
                  ('Employee ID', 'Name', 'Email', 'Designation', 'Deduction reason')}
        if any(values[key].lower() in ('', 'nan', 'none') for key in ('Employee ID', 'Name', 'Email')):
            raise ValueError(f'Row {index}: employee ID, name and email are required.')
        if values['Employee ID'].casefold() in seen:
            raise ValueError(f'Row {index}: employee ID is repeated in this sheet.')
        seen.add(values['Employee ID'].casefold())
        if not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', values['Email']):
            raise ValueError(f'Row {index}: enter one valid email address.')
        if any('\n' in val or '\r' in val for val in values.values()):
            raise ValueError(f'Row {index}: fields must be single-line values.')
        try:
            gross = Decimal(str(row.get('Gross salary', 0)))
            deduction = Decimal(str(row.get('Deductions', 0)))
            if not gross.is_finite() or not deduction.is_finite() or gross <= 0 or deduction < 0 or deduction > gross:
                raise ValueError
            gross, deduction = [v.quantize(Decimal('.01'), rounding=ROUND_HALF_UP) for v in (gross, deduction)]
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError(f'Row {index}: enter positive gross salary and deductions between zero and gross.') from None
        if deduction and not values['Deduction reason']:
            raise ValueError(f'Row {index}: add a deduction reason.')
        employee = {'employee_id': values['Employee ID'], 'name': values['Name'],
                    'email': values['Email'], 'designation': values['Designation']}
        subject, body = email_content(employee['name'], period)
        result.append(dict(employee=employee, gross=str(gross), deduction=str(deduction),
                           net=str(gross-deduction), reason=values['Deduction reason'],
                           period=period, payment_date=payment_date.isoformat(), subject=subject, body=body))
    if not result:
        raise ValueError('Select Include for at least one employee.')
    return result


def fingerprint(snapshots):
    return hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest()


def load_batch(batch_id):
    with db.connect() as con:
        rows = con.execute('SELECT * FROM bulk_items WHERE batch_id=? ORDER BY id', (batch_id,)).fetchall()
    return [dict(row, data=json.loads(row['snapshot'])) for row in rows]


def prepare_batch(snapshots):
    digest = fingerprint(snapshots)
    with db.connect() as con:
        existing = con.execute('SELECT id FROM bulk_batches WHERE fingerprint=?', (digest,)).fetchone()
    if existing:
        return existing['id']
    # Render everything before storing any batch, so an invalid PDF cannot produce a partial review.
    documents = []
    with TemporaryDirectory() as tmp:
        for index, item in enumerate(snapshots):
            path = create_payslip(item['employee'], item['period'], item['gross'],
                                  date.fromisoformat(item['payment_date']), Path(tmp)/f'{index}.pdf',
                                  deduction_amount=item['deduction'], deduction_reason=item['reason'])
            documents.append(path.read_bytes())
    batch_id = uuid4().hex
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT id FROM bulk_batches WHERE fingerprint=?', (digest,)).fetchone()
        if existing:
            return existing['id']
        con.execute('INSERT INTO bulk_batches(id,fingerprint) VALUES (?,?)', (batch_id,digest))
        for item, pdf in zip(snapshots, documents):
            con.execute('INSERT INTO bulk_items(batch_id,snapshot,pdf) VALUES (?,?,?)',
                        (batch_id,json.dumps(item),pdf))
    cloud_storage.sync_to_cloud()
    return batch_id


def send_batch(batch_id, output_dir, progress=None):
    """Send only Ready rows. Persist Sending before network I/O; uncertainty is never retried."""
    service = connect_gmail()  # Fail authorization before claiming or creating payroll.
    items = load_batch(batch_id)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(items):
        if item['status'] != 'Ready':
            continue
        data = item['data']
        emp = data['employee']
        filename = f"Payslip_{batch_id[:8]}_{item['id']}.pdf"
        path = output_dir/filename
        path.write_bytes(item['pdf'])
        with db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute('SELECT status FROM bulk_items WHERE id=?', (item['id'],)).fetchone()
            if current['status'] != 'Ready':
                continue
            # Guard against another batch or the individual flow having already sent this period.
            duplicate = con.execute('''SELECT 1 FROM payroll p JOIN employees e ON e.id=p.employee_db_id
                WHERE lower(e.employee_id)=lower(?) AND p.pay_period=?
                AND p.email_status IN ('Sent','Sending','Check Gmail')''',
                (emp['employee_id'],data['period'])).fetchone()
            if duplicate:
                con.execute("UPDATE bulk_items SET status='Skipped',error=? WHERE id=?",
                            ('Already sent or awaiting delivery confirmation for this employee and period.',item['id']))
                continue
            employee = con.execute('SELECT id,active FROM employees WHERE lower(employee_id)=lower(?)',
                                   (emp['employee_id'],)).fetchone()
            if employee and not employee['active']:
                con.execute("UPDATE bulk_items SET status='Skipped',error='Employee is inactive.' WHERE id=?",(item['id'],))
                continue
            if not employee:
                cursor = con.execute('INSERT INTO employees(name,employee_id,designation,email) VALUES (?,?,?,?)',
                                     (emp['name'],emp['employee_id'],emp['designation'],emp['email']))
                employee_id = cursor.lastrowid
            else:
                employee_id = employee['id']
            cursor = con.execute('''INSERT INTO payroll(employee_db_id,pay_period,net_pay,payment_date,notes,
                email_status,pdf_path,gross_pay,deduction_amount,deduction_reason)
                VALUES (?,?,?,?,?,'Sending',?,?,?,?)''',
                (employee_id,data['period'],float(data['net']),data['payment_date'],'Bulk payroll',str(path),
                 float(data['gross']),float(data['deduction']),data['reason']))
            payroll_id = cursor.lastrowid
            con.execute("UPDATE bulk_items SET status='Sending',payroll_id=? WHERE id=?",(payroll_id,item['id']))
        try:
            response = send_prepared_email(emp['email'],data['subject'],data['body'],item['pdf'],filename,service)
        except Exception:
            with db.connect() as con:
                con.execute("UPDATE bulk_items SET status='Check Gmail',error=? WHERE id=?",
                            ('Delivery uncertain. Check Gmail Sent before any manual resend.',item['id']))
                con.execute("UPDATE payroll SET email_status='Check Gmail' WHERE id=?",(payroll_id,))
            # Stop on provider/network failure; later Ready rows are safe to send on a subsequent click.
            break
        else:
            with db.connect() as con:
                con.execute("UPDATE bulk_items SET status='Sent',message_id=? WHERE id=?",(response['id'],item['id']))
                con.execute("UPDATE payroll SET email_status='Sent' WHERE id=?",(payroll_id,))
        if progress:
            progress((index+1)/len(items))
    cloud_storage.sync_to_cloud()
    return load_batch(batch_id)
