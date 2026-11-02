import sqlite3
from pathlib import Path
import cloud_storage

DB_PATH = Path(__file__).parent / "roberon_hr.db"

def _sync_after_change():
    try:
        cloud_storage.sync_to_cloud()
    except Exception:
        # Local operation remains usable when cloud credentials/resources are unavailable.
        pass

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    if not DB_PATH.exists():
        cloud_storage.sync_from_cloud()
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            employee_id TEXT NOT NULL UNIQUE,
            designation TEXT,
            email TEXT NOT NULL,
            bank_account TEXT,
            ifsc TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS payroll (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_db_id INTEGER NOT NULL,
            pay_period TEXT NOT NULL,
            net_pay REAL NOT NULL,
            payment_date TEXT NOT NULL,
            notes TEXT,
            email_status TEXT NOT NULL DEFAULT 'Not sent',
            email_error TEXT,
            pdf_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(employee_db_id) REFERENCES employees(id)
        )""")

        # Additive, idempotent migration; existing employee/payroll rows remain intact.
        columns = {row[1] for row in con.execute("PRAGMA table_info(payroll)")}
        for name, definition in (("gross_pay", "REAL"), ("deduction_amount", "REAL NOT NULL DEFAULT 0"),
                                 ("deduction_reason", "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                con.execute(f"ALTER TABLE payroll ADD COLUMN {name} {definition}")
        con.execute("UPDATE payroll SET gross_pay=net_pay WHERE gross_pay IS NULL")
    cloud_storage.sync_from_cloud()

def add_employee(name, employee_id, designation, email, bank_account, ifsc):
    with connect() as con:
        con.execute("""INSERT INTO employees
            (name, employee_id, designation, email, bank_account, ifsc)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (name, employee_id, designation, email, bank_account, ifsc))
    _sync_after_change()

def get_employees(active_only=True):
    with connect() as con:
        query = "SELECT * FROM employees"
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY name"
        return [dict(r) for r in con.execute(query).fetchall()]

def save_payroll(employee_db_id, pay_period, net_pay, payment_date, notes, email_status, pdf_path, *, gross_pay=None,
                 deduction_amount=0, deduction_reason=""):
    with connect() as con:
        cur = con.execute("""INSERT INTO payroll
            (employee_db_id, pay_period, net_pay, payment_date, notes, email_status, pdf_path,
             gross_pay, deduction_amount, deduction_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (employee_db_id, pay_period, net_pay, payment_date, notes, email_status, pdf_path,
             net_pay if gross_pay is None else gross_pay, deduction_amount, deduction_reason))
        record_id = cur.lastrowid
    _sync_after_change()
    return record_id

def update_pdf_path(record_id, path):
    with connect() as con:
        con.execute("UPDATE payroll SET pdf_path=? WHERE id=?", (path, record_id))
    _sync_after_change()

def mark_email(record_id, status, error):
    with connect() as con:
        con.execute("UPDATE payroll SET email_status=?, email_error=? WHERE id=?",
                    (status, error, record_id))
    _sync_after_change()

def get_payroll():
    with connect() as con:
        rows = con.execute("""SELECT p.*, e.name, e.employee_id, e.email
            FROM payroll p JOIN employees e ON e.id=p.employee_db_id
            ORDER BY p.created_at DESC""").fetchall()
        return [dict(r) for r in rows]
