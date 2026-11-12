"""
Printable receipt PDF (POS-015, POS-016, SRS 8.3).

Laid out for 80 mm thermal receipt paper; the page height grows with the
number of lines, so it prints as one continuous receipt. Built from the
same dict as the JSON receipt endpoint, so both always agree.
"""
import io

from reportlab.lib.pagesizes import mm
from reportlab.pdfgen import canvas

WIDTH = 80 * mm
MARGIN = 4 * mm
LINE = 4.2 * mm
FONT = "Helvetica"
BOLD = "Helvetica-Bold"


def _wrap(text: str, max_chars: int) -> list[str]:
    words, lines, current = str(text).split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _fmt(amount: str, decimals: int) -> str:
    from decimal import Decimal, ROUND_HALF_UP

    value = Decimal(str(amount)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return f"{value:,.{decimals}f}"


def build_receipt_pdf(receipt: dict, decimals: int) -> bytes:
    cur = receipt["currency_code"]
    rows: list[tuple] = []  # (kind, left, right)

    rows.append(("center_bold", receipt["business"], ""))
    rows.append(("center", receipt["branch"], ""))
    rows.append(("rule", "", ""))
    rows.append(("pair", "Receipt", receipt["transaction_number"]))
    rows.append(("pair", "Date", (receipt.get("datetime") or "")[:16].replace("T", " ")))
    rows.append(("pair", "Cashier", receipt["cashier"]))
    if receipt.get("status") and receipt["status"].lower() != "completed":
        rows.append(("pair_bold", "Status", receipt["status"].upper()))
    rows.append(("rule", "", ""))

    for item in receipt["items"]:
        for part in _wrap(item["name"], 34):
            rows.append(("pair", part, ""))
        rows.append((
            "pair",
            f"  {_fmt(item['quantity'], 2).rstrip('0').rstrip('.')} x {_fmt(item['unit_price'], decimals)}",
            _fmt(item["line_total"], decimals),
        ))
        if item.get("discount") and float(item["discount"]) > 0:
            rows.append(("pair", "  Discount", f"-{_fmt(item['discount'], decimals)}"))

    rows.append(("rule", "", ""))
    rows.append(("pair", "Subtotal", _fmt(receipt["subtotal"], decimals)))
    if float(receipt["discount_total"]) > 0:
        rows.append(("pair", "Discounts", f"-{_fmt(receipt['discount_total'], decimals)}"))
    rows.append(("pair_bold", f"TOTAL ({cur})", _fmt(receipt["total_amount"], decimals)))
    rows.append(("rule", "", ""))

    for p in receipt["payments"]:
        label = f"Refund ({p['method']})" if p["is_refund"] else p["method"]
        sign = "-" if p["is_refund"] else ""
        rows.append(("pair", label, f"{sign}{_fmt(p['amount'], decimals)} {cur}"))
        if p.get("reference") and not p["is_refund"]:
            rows.append(("pair", f"  Ref: {p['reference']}", ""))
    if receipt.get("note"):
        rows.append(("rule", "", ""))
        for part in _wrap(receipt["note"], 40):
            rows.append(("pair", part, ""))
    rows.append(("rule", "", ""))
    rows.append(("center", "Thank you!", ""))

    height = 2 * MARGIN + LINE * (len(rows) + 1)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(WIDTH, height))
    pdf.setTitle(f"Receipt {receipt['transaction_number']}")
    y = height - MARGIN - LINE * 0.8
    for kind, left, right in rows:
        if kind == "rule":
            pdf.setDash(1, 2)
            pdf.line(MARGIN, y + LINE * 0.35, WIDTH - MARGIN, y + LINE * 0.35)
            pdf.setDash()
        elif kind.startswith("center"):
            pdf.setFont(BOLD if kind == "center_bold" else FONT, 10 if kind == "center_bold" else 8)
            pdf.drawCentredString(WIDTH / 2, y, left)
        else:
            pdf.setFont(BOLD if kind == "pair_bold" else FONT, 8)
            pdf.drawString(MARGIN, y, left)
            if right:
                pdf.drawRightString(WIDTH - MARGIN, y, right)
        y -= LINE
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
