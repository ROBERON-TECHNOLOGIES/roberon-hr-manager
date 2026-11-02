import streamlit as st
import cloud_storage
import pandas as pd
from datetime import date
from pathlib import Path
from db import init_db, add_employee, get_employees, save_payroll, get_payroll, mark_email
import importlib
import payslip
from gmail_compose import email_content, compose_url, download_and_compose_html
import streamlit.components.v1 as components
from gmail_service import gmail_setup_status, connect_gmail, send_test_email, send_payslip_email, GmailSetupError
from decimal import Decimal, ROUND_HALF_UP
from bulk_ui import render_bulk_page

APP_DIR = Path(__file__).parent
PAYSLIP_DIR = APP_DIR / "payslips"
PAYSLIP_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title="Roberon HR Manager", page_icon="🧾", layout="wide")
init_db()

st.title("ROBERON TECHNOLOGIES LLP")
st.caption("HR Manager · Employee records · Payslips · Email dispatch")

tab_dash, tab_emp, tab_pay, tab_bulk, tab_history, tab_setup = st.tabs(
    ["Dashboard", "Employees", "Process Salary", "Bulk Payroll", "Payslip History", "Gmail help"]
)

with tab_dash:
    employees = get_employees(active_only=True)
    records = get_payroll()
    this_month = date.today().strftime("%B %Y")
    month_records = [r for r in records if r["pay_period"] == this_month]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active employees", len(employees))
    c2.metric("Payslips this month", len(month_records))
    c3.metric("Net pay this month", f"₹{sum(r['net_pay'] for r in month_records):,.2f}")
    c4.metric("Emails confirmed sent this month", sum(1 for r in month_records if r["email_status"] == "Sent"))
    st.info("Start by adding employee records. Then process salary payments for the relevant month.")

with tab_emp:
    st.subheader("Add employee")
    with st.form("employee_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        name = col1.text_input("Full name *")
        emp_id = col2.text_input("Employee ID *")
        designation = col1.text_input("Designation", value="Student Intern")
        email = col2.text_input("Personal email address *")
        bank = col1.text_input("Bank account number (optional)")
        ifsc = col2.text_input("IFSC code (optional)")
        submitted = st.form_submit_button("Save employee")
        if submitted:
            if not name.strip() or not emp_id.strip() or not email.strip():
                st.error("Name, Employee ID and email are required.")
            elif "@" not in email or "." not in email.split("@")[-1]:
                st.error("Enter a valid email address.")
            else:
                try:
                    add_employee(name.strip(), emp_id.strip(), designation.strip(), email.strip(), bank.strip(), ifsc.strip())
                    st.success(f"Employee {name} added.")
                except Exception as e:
                    st.error(f"Could not save employee: {e}")

    st.subheader("Employee directory")
    all_employees = get_employees(active_only=False)
    if all_employees:
        df = pd.DataFrame(all_employees)
        st.dataframe(df.drop(columns=["created_at"], errors="ignore"), use_container_width=True, hide_index=True)
    else:
        st.caption("No employees registered yet.")

with tab_pay:
    st.subheader("Process salary payment")
    st.caption("New PDFs use the approved red, white and black design. Generate a new PDF to update an older payslip.")
    employees = get_employees(active_only=True)
    if not employees:
        st.warning("Add at least one employee before processing salary.")
    else:
        employee_map = {f'{e["name"]} · {e["employee_id"]}': e for e in employees}
        selected_label = st.selectbox("Employee", list(employee_map.keys()))
        emp = employee_map[selected_label]
        today = date.today()
        col1, col2, col3 = st.columns(3)
        pay_month = col1.selectbox("Pay period", [
            date(today.year, m, 1).strftime("%B %Y") for m in range(1, 13)
        ], index=today.month - 1)
        amount = col2.number_input("Gross salary (₹)", min_value=0.0, step=100.0, format="%.2f")
        payment_date = col3.date_input("Payment date", value=today)
        deduction_amount = st.number_input("Deduction amount (₹)", min_value=0.0, step=100.0, format="%.2f")
        deduction_reason = st.text_input("Deduction reason", placeholder="For example: Loss of pay")
        net_amount = float((Decimal(str(amount)) - Decimal(str(deduction_amount))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP))
        st.metric("Net payable", payslip.format_inr(net_amount))
        bank_display = st.checkbox("Show bank account and IFSC on payslip", value=False)
        notes = st.text_input("Optional note (not printed on payslip)")
        if st.button("Generate payslip PDF", type="primary"):
            if amount <= 0:
                st.error("Enter an amount greater than zero.")
            elif deduction_amount > amount:
                st.error("Deductions cannot exceed gross salary.")
            elif deduction_amount > 0 and not deduction_reason.strip():
                st.error("Enter a reason for the deduction.")
            else:
                try:
                    record_id = save_payroll(
                        emp["id"], pay_month, net_amount, payment_date.isoformat(),
                        notes, "Not sent", "", gross_pay=float(amount),
                        deduction_amount=float(deduction_amount), deduction_reason=deduction_reason.strip()
                    )
                    pdf_path = PAYSLIP_DIR / f"Payslip_{pay_month.replace(' ', '_')}_{record_id}.pdf"
                    # Streamlit can retain imported modules across script reruns.
                    # Load the current template when explicitly generating a new PDF.
                    importlib.reload(payslip).create_payslip(emp, pay_month, float(amount), payment_date, pdf_path,
                                   include_bank=bank_display, deduction_amount=deduction_amount,
                                   deduction_reason=deduction_reason.strip())
                    # Store path after successful generation
                    from db import update_pdf_path
                    update_pdf_path(record_id, str(pdf_path))
                    try:
                        cloud_storage.upload_pdf(pdf_path)
                        cloud_storage.sync_to_cloud()
                    except Exception:
                        pass
                    st.session_state["latest_pdf"] = str(pdf_path)
                    st.session_state["latest_record_id"] = record_id
                    st.session_state["latest_employee"] = emp
                    st.session_state["latest_period"] = pay_month
                    st.session_state["latest_amount"] = net_amount
                    st.success("Payslip generated.")
                except Exception as e:
                    st.error(f"Generation failed: {e}")

    if st.session_state.get("latest_pdf") and Path(st.session_state["latest_pdf"]).exists():
        p = Path(st.session_state["latest_pdf"])
        st.download_button("Download generated PDF", data=p.read_bytes(), file_name=p.name, mime="application/pdf")
        emp = st.session_state["latest_employee"]
        period = st.session_state["latest_period"]
        subject, body = email_content(emp['name'], period)
        url = compose_url(emp['email'], subject, body)
        st.markdown("#### Prepare Gmail message")
        st.write(f"**To:** {emp['email']}")
        st.write(f"**Subject:** {subject}")
        st.text(body)
        components.html(download_and_compose_html(p.read_bytes(), p.name, url), height=100)
        st.link_button("Open Gmail only (fallback)", url)
        st.caption(f"Attach {p.name} from Downloads. Use the company Gmail account. "
                   "Your browser may ask where to save the PDF. The app cannot attach it or verify sending.")
        if st.button("I sent this payslip in Gmail", key=f"sent_{st.session_state['latest_record_id']}"):
            mark_email(st.session_state["latest_record_id"], "Sent", "Manually confirmed by operator")
            st.success("Marked as sent based on your confirmation.")

        with st.expander("Optional: send automatically through Gmail"):
            st.caption("Attaches this PDF automatically using the Google account you connected. "
                       "The download-and-Gmail option above stays available.")
            setup_ready = gmail_setup_status() == 'authorization_saved'
            if not setup_ready:
                st.info("Connect your company Google account in Gmail help first.")
            record = next((r for r in get_payroll() if r['id'] == st.session_state['latest_record_id']), None)
            already_sent = record is not None and record['email_status'] == 'Sent'
            if already_sent:
                st.success("This payslip is already marked as sent.")
            confirmed = st.checkbox("I reviewed this recipient and PDF and want to send it now.",
                                    key=f"auto_confirm_{st.session_state['latest_record_id']}")
            if st.button("Send PDF automatically", disabled=not setup_ready or not confirmed or already_sent,
                         key=f"auto_send_{st.session_state['latest_record_id']}"):
                try:
                    with st.spinner("Sending payslip through Gmail..."):
                        result = send_payslip_email(emp['email'], emp['name'], period, p)
                except GmailSetupError as exc:
                    st.error(str(exc))
                except Exception:
                    mark_email(st.session_state['latest_record_id'], 'Check Gmail',
                               'API send not confirmed. Check Sent before retrying.')
                    st.error("Sending was not confirmed. Check Gmail Sent before retrying to avoid duplicates. "
                             "The manual download-and-Gmail option is still available.")
                else:
                    try:
                        mark_email(st.session_state['latest_record_id'], 'Sent', '')
                    except Exception:
                        st.warning("Gmail accepted the message, but its status could not be saved. "
                                   "Do not resend; check Gmail Sent.")
                    else:
                        st.success("Gmail accepted the email with the payslip attached. See Gmail Sent for the message.")
                        st.rerun()

with tab_bulk:
    render_bulk_page(PAYSLIP_DIR)

with tab_history:
    st.subheader("Payslip and payment history")
    records = get_payroll()
    if records:
        df = pd.DataFrame(records)
        st.dataframe(df[["pay_period", "name", "employee_id", "gross_pay", "deduction_amount", "net_pay", "payment_date", "email_status", "pdf_path"]], use_container_width=True, hide_index=True)
        st.caption("A generated payslip is not proof of bank transfer. Keep your bank/payment confirmation separately.")
    else:
        st.caption("No payroll records yet.")

with tab_setup:
    st.subheader("Cloud data and payslip storage")
    cloud_status = cloud_storage.status()
    if cloud_status == "configured":
        st.success("Google Drive and Sheets cloud storage are configured.")
    elif cloud_status == "needs_resource_ids":
        st.warning("Google credentials are detected. Add the Drive folder ID and Sheet ID to Streamlit secrets.")
    else:
        st.info("Cloud storage is not configured yet. The app will continue using local SQLite and PDFs.")
    if st.button("Back up database, records and PDFs now", disabled=cloud_status != "configured"):
        try:
            if cloud_storage.sync_to_cloud():
                st.success("Database, Google Sheets tabs and payslip PDFs were backed up.")
            else:
                st.error("Cloud backup could not be completed.")
        except Exception as exc:
            st.error(f"Cloud backup failed: {exc}")
    st.divider()
    st.subheader("Send with Gmail - no API setup")
    st.markdown("""
1. Generate and review the payslip in **Process Salary**.
2. Click **Download PDF & open Gmail**. The PDF downloads and a Gmail draft opens
   with the employee's address, subject and message filled in.
3. Check that you are using the company Gmail account.
4. Drag the matching PDF from Downloads into the draft, wait for the attachment
   to upload, review it and click **Send** in Gmail.
5. Return here and click **I sent this payslip in Gmail** to update its status.

No Google Cloud project, API credentials or app password is required.
If the browser blocks the download or new tab, use **Download generated PDF**
and **Open Gmail only (fallback)** separately. Existing PDFs keep their original design.
    """)

    st.divider()
    st.subheader("Automatic Gmail setup (optional)")
    st.markdown("""
This alternative attaches and sends the PDF directly. The manual flow above stays available.

1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project and enable **Gmail API**.
2. Configure the OAuth consent screen. For an external app in Testing, add your company Google account as a test user.
3. Create an **OAuth client ID** of type **Desktop app**. Download its JSON to this app folder as **credentials.json**.
4. Click **Connect company Gmail**, choose the company account and allow sending permission.
5. Send a connection test to your own email below. Then use **Send PDF automatically** under the generated payslip.

Standard Gmail API usage is available at no additional cost, within Google's quotas.
It is **not unlimited**: normal Gmail account sending limits and anti-spam rules still apply.
External OAuth apps in Testing may require sign-in again after seven days.

[Google API quotas](https://developers.google.com/workspace/gmail/api/reference/quota) ·
[Personal Gmail limits](https://support.google.com/mail/answer/22839) ·
[Workspace limits](https://knowledge.workspace.google.com/admin/gmail/gmail-sending-limits-in-google-workspace)
    """)
    setup_status = gmail_setup_status()
    st.caption({
        'missing_credentials': 'Not configured: credentials.json is missing.',
        'needs_sign_in': 'Credentials found. Connect your Google account next.',
        'authorization_saved': 'Authorization saved. Send a test to verify it still works.',
    }[setup_status])
    if st.button("Connect company Gmail", disabled=setup_status == 'missing_credentials'):
        try:
            with st.spinner("Complete Google sign-in in the browser (up to two minutes)..."):
                connect_gmail()
            st.success("Google authorization succeeded. Send a connection test below.")
        except GmailSetupError as exc:
            st.error(str(exc))
    test_recipient = st.text_input("Your own email for the connection test")
    if st.button("Send connection test email", disabled=gmail_setup_status() != 'authorization_saved'):
        try:
            send_test_email(test_recipient.strip())
            st.success("Gmail accepted the test message. Check your inbox and Sent folder.")
        except (GmailSetupError, ValueError) as exc:
            st.error(str(exc))
        except Exception:
            st.error("Test delivery was not confirmed. Check Gmail Sent before retrying.")
