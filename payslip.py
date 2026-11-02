"""One-page salary payslips; no database or email side effects."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


def format_inr(amount):
    """Format rupees using Indian lakh/crore grouping and two decimal places."""
    value = Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    whole, fraction = f'{abs(value):.2f}'.split('.')
    tail, head = whole[-3:], whole[:-3]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    grouped = ','.join(groups + [tail])
    return f"{'-' if value < 0 else ''}Rs. {grouped}.{fraction}"


def create_payslip(employee, period, amount, payment_date, output_path, include_bank=False, *,
                   deduction_amount=0, deduction_reason="Deductions"):
    """Generate an A4 PDF. Amount is gross salary; optional deductions reduce net pay.

    Bank information requires explicit opt-in. Email and internal notes are never
    printed. The signature and returned Path remain compatible with Streamlit.
    """
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value < 0:
            raise ValueError('Salary must be a finite, non-negative amount.')
        deducted = Decimal(str(deduction_amount))
        if not deducted.is_finite() or deducted < 0 or deducted > value:
            raise ValueError('Deductions must be between zero and gross salary.')
        value = value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        deducted = deducted.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        money = format_inr(value - deducted)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('Salary must be a valid monetary amount.') from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=A4)
    c.setTitle('Salary Payslip | Roberon Technologies LLP')
    c.setAuthor('Roberon Technologies LLP')
    w, h = A4
    left, right = 20 * mm, w - 20 * mm
    width = right - left
    red = colors.HexColor('#C8102E')
    black = colors.HexColor('#18181B')
    muted = colors.HexColor('#626269')
    line = colors.HexColor('#E3E3E7')
    pale = colors.HexColor('#F6F6F7')

    def text(x, top, value, size=10, bold=False, color=black, align='left'):
        c.setFillColor(color)
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        draw = c.drawRightString if align == 'right' else c.drawString
        draw(x, h - top * mm, str(value))

    def rule(top, x=left, end=right, color=line):
        c.setStrokeColor(color)
        c.setLineWidth(0.6)
        c.line(x, h - top * mm, end, h - top * mm)

    def box(x, top, bw, height, color):
        c.setFillColor(color)
        c.rect(x, h - (top + height) * mm, bw, height * mm, fill=1, stroke=0)

    def fitted(x, top, value, max_width, size=10, bold=False, color=black, align='left'):
        value = ' '.join(str(value or '-').split())
        font = 'Helvetica-Bold' if bold else 'Helvetica'
        while size > 7 and stringWidth(value, font, size) > max_width:
            size = max(7, size - 0.25)
        if stringWidth(value, font, size) > max_width:
            while value and stringWidth(value + '...', font, size) > max_width:
                value = value[:-1]
            value += '...'
        text(x, top, value, size, bold, color, align)

    def card(x, top, bw, height, fill=colors.white, border=line, radius=4):
        c.setFillColor(fill)
        c.setStrokeColor(border)
        c.setLineWidth(0.6)
        c.roundRect(x, h - (top + height) * mm, bw, height * mm,
                    radius * mm, fill=1, stroke=1)

    # Restrained brand treatment: a red terminal on a simple text wordmark.
    box(left, 17, 11 * mm, 1.5, red)
    text(left, 34, 'ROBER', 28, True)
    text(left + stringWidth('ROBER', 'Helvetica-Bold', 28), 34, 'ON', 28, True, red)
    text(left, 42, 'ROBERON TECHNOLOGIES LLP', 8, True, muted)
    text(right, 27, 'SALARY PAYSLIP', 9, True, red, 'right')
    fitted(right, 38, period, 72 * mm, 17, True, align='right')
    rule(51)

    text(left, 64, 'EMPLOYEE OVERVIEW', 8, True, muted)
    text(right, 64, 'PRIVATE & CONFIDENTIAL', 7, color=muted, align='right')
    card(left, 70, width, 49)
    col_width = (width - 24 * mm) / 2
    col2 = left + width / 2 + 5 * mm
    fields = [
        (left + 7 * mm, 81, 'EMPLOYEE NAME', employee['name']),
        (col2, 81, 'EMPLOYEE ID', employee['employee_id']),
        (left + 7 * mm, 101, 'DESIGNATION', employee.get('designation')),
        (col2, 101, 'PAYMENT DATE', payment_date.strftime('%d %B %Y')),
    ]
    for x, top, label, val in fields:
        text(x, top, label, 7, color=muted)
        fitted(x, top + 8, val, col_width, 11, True)

    # A compact hero card keeps the amount dominant without overwhelming the page.
    card(left, 127, width, 34, black, black)
    box(left + 7 * mm, 135, 8 * mm, 1, red)
    text(left + 7 * mm, 145, 'NET SALARY', 10, True, colors.white)
    text(left + 7 * mm, 153, 'For the current pay period', 8, color=colors.HexColor('#C7C7CC'))
    fitted(right - 7 * mm, 148, money, 91 * mm, 27, True, colors.white, 'right')

    def salary_table(x, title, row_label, row_amount, total_label):
        tw = (width - 6 * mm) / 2
        card(x, 171, tw, 56)
        text(x + 5 * mm, 182, title, 10, True)
        rule(187, x + 5 * mm, x + tw - 5 * mm)
        text(x + 5 * mm, 195, 'COMPONENT', 6.5, True, muted)
        text(x + tw - 5 * mm, 195, 'AMOUNT / INR', 6.5, True, muted, 'right')
        fitted(x + 5 * mm, 205, row_label, 25 * mm, 9)
        fitted(x + tw - 5 * mm, 205, row_amount, tw - 31 * mm, 9, align='right')
        rule(212, x + 5 * mm, x + tw - 5 * mm)
        text(x + 5 * mm, 221, total_label, 7, True, muted)
        fitted(x + tw - 5 * mm, 221, row_amount, tw - 37 * mm, 10, True, align='right')

    salary_table(left, 'Earnings', 'Basic salary', format_inr(value), 'GROSS TOTAL')
    salary_table(left + (width + 6 * mm) / 2, 'Deductions', (deduction_reason or 'Deductions') if deducted else 'None', format_inr(deducted), 'TOTAL DEDUCTED')

    card(left, 234, width, 20, colors.HexColor('#FFF5F6'), colors.HexColor('#F3DADD'), 3)
    text(left + 6 * mm, 243, 'TOTAL NET PAYABLE', 9, True)
    text(left + 6 * mm, 249, 'Gross earnings less deductions', 7, color=muted)
    fitted(right - 6 * mm, 247, money, 85 * mm, 18, True, red, 'right')

    if include_bank and (employee.get('bank_account') or employee.get('ifsc')):
        fitted(left, 263, f"Account: {employee.get('bank_account') or '-'}", width / 2 - 5 * mm, 8)
        fitted(right, 263, f"IFSC: {employee.get('ifsc') or '-'}", width / 2 - 5 * mm, 8, align='right')

    rule(272)
    text(left, 279, 'ROBERON TECHNOLOGIES LLP', 7.5, True)
    text(right, 279, 'www.roberon.com', 8, True, red, 'right')
    text(left, 285, 'Bengaluru, Karnataka, India  |  info@roberon.com', 7, color=muted)
    text(left, 291, 'System-generated payslip. No signature required.', 6.5, color=muted)
    text(right, 291, '01 / 01', 7, color=muted, align='right')
    c.showPage()
    c.save()
    return output_path
