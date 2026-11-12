"""
Report export to CSV and PDF (REP-016, REP-017, REP-018, CUR-025).

Works on the JSON shape every report returns: a `results` list of flat
dicts, plus optional extra lists of dicts (e.g. `approved_totals`), which
are exported as additional tables. Each export starts with a header block
naming the report, business, period and branch scope; currency codes are
already a column in every money row, so no amount is ever shown without
its currency.
"""
import csv
import io
from datetime import datetime

from django.http import HttpResponse
from django.utils import timezone

EXPORT_FORMATS = ("csv", "pdf")


def _label(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, dict)):
        return str(value)
    return str(value)


def _tables(data: dict) -> list[tuple[str, list[dict]]]:
    """(title, rows) for `results` and any other list-of-dicts in the payload."""
    tables = [("Results", data.get("results") or [])]
    for key, value in data.items():
        if key == "results":
            continue
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            tables.append((_label(key), value))
    return tables


def _columns(rows: list[dict]) -> list[str]:
    cols: list[str] = []
    for row in rows:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


def _header_lines(*, title: str, business_name: str, data: dict, scope_label: str) -> list[tuple[str, str]]:
    lines = [("Report", title), ("Business", business_name), ("Branches", scope_label)]
    period = data.get("period")
    if period:
        lines.append(("Period", f"{period.get('from')} to {period.get('to')}"))
    lines.append(("Generated", timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")))
    if data.get("note"):
        lines.append(("Note", data["note"]))
    return lines


def filename_for(slug: str, data: dict, fmt: str) -> str:
    period = data.get("period") or {}
    if period:
        stamp = f"{period.get('from')}_{period.get('to')}"
    else:
        stamp = datetime.now().strftime("%Y-%m-%d")
    return f"{slug}_{stamp}.{fmt}"


# ------------------------------------------------------------------------------
# CSV
# ------------------------------------------------------------------------------
def to_csv(*, title, slug, business_name, scope_label, data) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for key, value in _header_lines(title=title, business_name=business_name,
                                    data=data, scope_label=scope_label):
        writer.writerow([key, value])

    for table_title, rows in _tables(data):
        writer.writerow([])
        writer.writerow([table_title])
        cols = _columns(rows)
        if not cols:
            writer.writerow(["No data for this period."])
            continue
        writer.writerow([_label(c) for c in cols])
        for row in rows:
            writer.writerow([_cell(row.get(c)) for c in cols])

    # UTF-8 BOM so Excel opens non-ASCII names and currency symbols correctly.
    response = HttpResponse("﻿" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename_for(slug, data, "csv")}"'
    return response


# ------------------------------------------------------------------------------
# PDF
# ------------------------------------------------------------------------------
def to_pdf(*, title, slug, business_name, scope_label, data) -> HttpResponse:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    tables = _tables(data)
    widest = max((len(_columns(rows)) for _, rows in tables), default=0)
    pagesize = landscape(A4) if widest > 6 else A4

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize, title=title,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    cell_style = styles["BodyText"].clone("cell", fontSize=7.5, leading=9)
    num_style = cell_style.clone("num", alignment=2)  # right-aligned
    head_style = cell_style.clone("head", fontName="Helvetica-Bold", textColor=colors.white)
    decimals = _currency_decimals(tables)

    story = [Paragraph(title, styles["Title"])]
    for key, value in _header_lines(title=title, business_name=business_name,
                                    data=data, scope_label=scope_label)[1:]:
        story.append(Paragraph(f"<b>{key}:</b> {_escape(value)}", styles["Normal"]))

    for table_title, rows in tables:
        story += [Spacer(1, 6 * mm), Paragraph(table_title, styles["Heading3"])]
        cols = _columns(rows)
        if not cols:
            story.append(Paragraph("No data for this period.", styles["Normal"]))
            continue
        matrix = [[Paragraph(_escape(_label(c)), head_style) for c in cols]]
        for row in rows:
            line = []
            for c in cols:
                text, numeric = _pdf_cell(c, row, decimals)
                line.append(Paragraph(_escape(text), num_style if numeric else cell_style))
            matrix.append(line)
        table = Table(matrix, repeatRows=1, colWidths=[doc.width / len(cols)] * len(cols))
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3b2a20")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4efe9")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9bfb5")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)

    doc.build(story)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename_for(slug, data, "pdf")}"'
    return response


# Columns holding money: shown in the PDF with the row's currency precision
# and thousands separators. (CSV keeps raw values so spreadsheets can compute.)
MONEY_COLUMNS = {
    "total_sales", "refunds_total", "net_sales", "average_transaction_value",
    "unpaid_sales_total", "gross_sales", "refunds", "revenue", "cost_of_goods_sold",
    "gross_profit", "operating_expenses", "operating_result", "expenses", "total",
    "collected", "refunded", "net", "opening_balance", "expected_cash",
    "actual_cash", "difference",
}


def _currency_decimals(tables) -> dict[str, int]:
    from apps.settings.models import Currency

    codes = {
        row["currency_code"]
        for _, rows in tables for row in rows if row.get("currency_code")
    }
    return dict(Currency.objects.filter(code__in=codes).values_list("code", "decimal_places"))


def _pdf_cell(column: str, row: dict, decimals: dict[str, int]) -> tuple[str, bool]:
    """(text, is_numeric) for one PDF cell."""
    from decimal import Decimal, InvalidOperation

    value = row.get(column)
    if column in MONEY_COLUMNS and value not in (None, "") and row.get("currency_code"):
        try:
            places = decimals.get(row["currency_code"], 2)
            amount = Decimal(str(value)).quantize(Decimal(1).scaleb(-places))
            return f"{amount:,.{places}f}", True
        except (InvalidOperation, ValueError):
            pass
    text = _cell(value)
    try:
        Decimal(text)
        return text, bool(text)
    except (InvalidOperation, ValueError):
        return text, False


def _escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
