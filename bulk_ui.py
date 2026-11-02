"""Sheet entry and a continuous review page for bulk payslips."""
from datetime import date
from io import BytesIO

import pandas as pd
import streamlit as st
import pymupdf

import db
from bulk_payroll import init_bulk_db, validate_rows, fingerprint, prepare_batch, load_batch, send_batch
from gmail_compose import compose_url
from gmail_service import GmailSetupError, gmail_setup_status
from payslip import format_inr

COLUMNS = ['Include','Employee ID','Name','Email','Designation','Gross salary','Deductions','Deduction reason']


def initial_sheet():
    return pd.DataFrame([{'Include': False, 'Employee ID': emp['employee_id'], 'Name': emp['name'],
                          'Email': emp['email'], 'Designation': emp.get('designation') or '',
                          'Gross salary': 0.0, 'Deductions': 0.0, 'Deduction reason': ''}
                         for emp in db.get_employees()], columns=COLUMNS).astype(
                             {'Include': bool, 'Gross salary': float, 'Deductions': float})


def render_bulk_page(output_dir):
    init_bulk_db()
    st.subheader('Bulk payroll')
    st.caption('Fill or paste rows into the sheet, review every email and payslip below, then send the batch. '
               'Each person receives a separate email with only their own PDF. Bank details are excluded.')
    with db.connect() as con:
        batches = con.execute('SELECT id,created_at FROM bulk_batches ORDER BY created_at DESC LIMIT 30').fetchall()
    if batches:
        labels = {row['id']: f"{row['created_at']} / {row['id'][:8]}" for row in batches}
        saved = st.selectbox('Saved review batches', list(labels), format_func=labels.get)
        if st.button('Load saved batch'):
            items = load_batch(saved)
            first = items[0]['data']
            # Parse the original pay period rather than assuming payment month equals salary month.
            from datetime import datetime
            st.session_state['bulk_period_date'] = datetime.strptime(first['period'], '%B %Y').date()
            st.session_state['bulk_payment_date'] = date.fromisoformat(first['payment_date'])
            st.session_state['bulk_seed'] = pd.DataFrame([{
                'Include': True, 'Employee ID': item['data']['employee']['employee_id'],
                'Name': item['data']['employee']['name'], 'Email': item['data']['employee']['email'],
                'Designation': item['data']['employee']['designation'],
                'Gross salary': float(item['data']['gross']), 'Deductions': float(item['data']['deduction']),
                'Deduction reason': item['data']['reason']} for item in items], columns=COLUMNS)
            st.session_state['bulk_editor_version'] = st.session_state.get('bulk_editor_version',0)+1
            st.session_state['bulk_batch_id'] = saved
            st.session_state['bulk_review_fingerprint'] = fingerprint([item['data'] for item in items])
    col1,col2 = st.columns(2)
    period_date = col1.date_input('Bulk salary month', value=date.today().replace(day=1), key='bulk_period_date')
    payment_date = col2.date_input('Bulk payment date', value=date.today(), key='bulk_payment_date')
    period = period_date.strftime('%B %Y')
    if 'bulk_seed' not in st.session_state:
        st.session_state['bulk_seed'] = initial_sheet()
    if st.button('Reload employee directory into sheet'):
        st.session_state['bulk_seed'] = initial_sheet()
        st.session_state['bulk_editor_version'] = st.session_state.get('bulk_editor_version',0)+1
    version = st.session_state.get('bulk_editor_version',0)
    sheet = st.data_editor(st.session_state['bulk_seed'], num_rows='dynamic', hide_index=True,
        key=f'bulk_sheet_{version}', use_container_width=True,
        column_config={
            'Include': st.column_config.CheckboxColumn(default=False),
            'Gross salary': st.column_config.NumberColumn(min_value=0, step=100, format='%.2f',default=0),
            'Deductions': st.column_config.NumberColumn(min_value=0, step=100, format='%.2f',default=0),
            'Email': st.column_config.TextColumn(width='large'),
            'Name': st.column_config.TextColumn(width='medium')})
    st.caption('Tick Include for the rows to send. Add new employees in the blank row, or paste cells from Excel/Sheets. '
               'New employee IDs are registered when sending. Edits here apply to this batch; existing directory details stay unchanged.')
    st.download_button('Download sheet as CSV',sheet.to_csv(index=False).encode(),
                       file_name='bulk_payroll.csv',mime='text/csv',key='bulk_csv_download')
    uploaded = st.file_uploader('Or import a CSV with the same column headings',type=['csv'],key='bulk_csv')
    if st.button('Load CSV into sheet',disabled=uploaded is None):
        try:
            imported = pd.read_csv(BytesIO(uploaded.getvalue()),dtype={'Employee ID':str},keep_default_na=False)
            if any(column not in imported.columns for column in COLUMNS):
                raise ValueError('Use the column headings from Download sheet as CSV.')
            included = imported['Include'].astype(str).str.lower().str.strip()
            if not included.isin(['true','false','1','0','']).all():
                raise ValueError('Include must contain True or False.')
            imported['Include'] = included.isin(['true','1'])
            for column in ('Gross salary','Deductions'):
                imported[column] = pd.to_numeric(imported[column],errors='raise').astype(float)
            st.session_state['bulk_seed'] = imported[COLUMNS]
            st.session_state['bulk_editor_version'] = version+1
            st.rerun()
        except (ValueError, pd.errors.ParserError):
            st.error('CSV could not be loaded. Use the downloaded template headings and numeric salary/deduction columns.')
    snapshots = None
    try:
        snapshots = validate_rows(sheet.fillna('').to_dict('records'),period,payment_date)
    except ValueError as exc:
        validation_error = str(exc)
    if st.button('Review all payslips',type='primary'):
        if snapshots is None:
            st.error(validation_error)
        else:
            try:
                with st.spinner('Preparing every payslip for review...'):
                    st.session_state['bulk_batch_id'] = prepare_batch(snapshots)
                    st.session_state['bulk_review_fingerprint'] = fingerprint(snapshots)
            except Exception:
                st.error('Could not prepare the review. No emails were sent.')
    batch_id = st.session_state.get('bulk_batch_id')
    if not batch_id:
        return
    items = load_batch(batch_id)
    current = snapshots is not None and fingerprint(snapshots) == st.session_state.get('bulk_review_fingerprint')
    if not current:
        st.warning('The sheet or dates changed. Click Review all payslips again before sending. '
                   'The review below shows the previously prepared batch.')
    st.divider()
    st.subheader('Review every email and payslip')
    st.caption(f'Batch {batch_id[:8]} / {len(items)} recipients. Scroll down to Send all reviewed payslips.')
    st.dataframe(pd.DataFrame([{
        'Employee': item['data']['employee']['name'], 'To': item['data']['employee']['email'],
        'Gross': format_inr(item['data']['gross']), 'Deductions': format_inr(item['data']['deduction']),
        'Net payable': format_inr(item['data']['net']), 'Status': item['status']} for item in items]),
        hide_index=True,use_container_width=True)
    for index,item in enumerate(items,1):
        data = item['data']
        st.markdown(f'#### {index}. {data["employee"]["name"]}')
        st.write(f'Employee ID: {data["employee"]["employee_id"]} · Status: {item["status"]}')
        if item['error']:
            st.warning(item['error'])
        mail,pdf = st.columns([1,1.3])
        with mail:
            st.write('**To:**',data['employee']['email'])
            st.write('**Subject:**',data['subject'])
            st.text(data['body'])
            filename = f"Payslip_{batch_id[:8]}_{item['id']}.pdf"
            st.download_button('Download this payslip',item['pdf'],file_name=filename,
                               mime='application/pdf',key=f'bulk_download_{item["id"]}')
            st.link_button('Open this email in Gmail',compose_url(data['employee']['email'],data['subject'],data['body']))
        with pdf:
            # Rasterize the exact immutable attachment, avoiding embedded PDF viewers and nested scrolling.
            with pymupdf.open(stream=item['pdf'],filetype='pdf') as document:
                preview = document[0].get_pixmap(matrix=pymupdf.Matrix(1.3,1.3)).tobytes('png')
            st.image(preview,caption='Exact PDF attachment preview',use_container_width=True)
        st.divider()
    ready = sum(item['status']=='Ready' for item in items)
    sent = sum(item['status']=='Sent' for item in items)
    st.write(f'**{ready} ready · {sent} sent · {len(items)-ready-sent} skipped or needing attention**')
    confirmed = st.checkbox('I reviewed all recipients, amounts, email messages and PDF previews above.',
                            key=f'bulk_confirm_{batch_id}_{st.session_state.get("bulk_review_fingerprint","")}')
    configured = gmail_setup_status() == 'authorization_saved'
    if not configured:
        st.info('Connect your company Gmail account in Gmail help before bulk sending.')
    if st.button('Send all reviewed payslips',type='primary',disabled=not current or not confirmed or not ready or not configured,
                 key=f'bulk_send_{batch_id}'):
        progress = st.progress(0.0,text='Sending individual emails...')
        try:
            send_batch(batch_id,output_dir,progress=lambda value: progress.progress(value,text='Sending individual emails...'))
        except GmailSetupError as exc:
            st.error(str(exc))
        except Exception:
            st.error('Sending stopped. Reload this saved batch to see statuses. Check Gmail Sent for any Sending rows before resending.')
        else:
            st.rerun()
    st.caption('Sent emails are skipped on subsequent clicks. If delivery is uncertain, sending stops; '
               'check Gmail Sent before any manual resend. Remaining Ready rows can be sent on the next click.')
