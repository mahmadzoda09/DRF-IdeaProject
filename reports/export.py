"""Выгрузка отчёта (см. builder.py) в Excel, Word и PDF."""

import io
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings

GREEN = "00694D"
LIGHT = "E5FFF7"

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _text(value, type_, language):
    """Значение ячейки строкой — для Word и PDF."""
    if value in ("", None):
        return ""
    if type_ == "money":
        s = f"{Decimal(value):,.2f}"
        return s if language == "en" else s.replace(",", " ").replace(".", ",")
    if type_ == "pct":
        s = f"{Decimal(value):.1f}".rstrip("0").rstrip(".")
        return f"{s if language == 'en' else s.replace('.', ',')} %"
    if type_ == "datetime":
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    if type_ == "int" and isinstance(value, int):
        return f"{value:,}".replace(",", " ")
    if type_ == "int" and isinstance(value, float):  # доли упаковки (блистеры)
        s = f"{value:,.2f}".rstrip("0").rstrip(".")
        return s if language == "en" else s.replace(",", " ").replace(".", ",")
    return str(value)


def _numeric(type_):
    return type_ in ("int", "money", "pct")


# ---------------------------------------------------------------- Excel


def to_xlsx(report, language):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor=GREEN)
    total_fill = PatternFill("solid", fgColor=LIGHT)
    thin = Border(bottom=Side(style="thin", color="BCCAC1"))
    formats = {"money": "#,##0.00", "int": "#,##0", "pct": '0.0" %"', "datetime": "dd.mm.yyyy hh:mm"}

    def number_format(value, type_):
        if type_ == "int" and isinstance(value, float):  # доли упаковки (блистеры)
            return "#,##0.00"
        return formats.get(type_, "General")

    def cell_value(value, type_):
        if value in ("", None):
            return None
        if type_ == "money" or type_ == "pct":
            return float(value)
        if type_ == "datetime":
            return datetime.fromisoformat(value).replace(tzinfo=None)
        return value

    def autosize(ws, skip_rows=0):
        for col in ws.columns:
            cells = col[skip_rows:]  # длинный заголовок отчёта не растягивает первую колонку
            width = max((len(str(c.value)) if c.value is not None else 0) for c in cells) if cells else 0
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(width + 3, 10), 48)

    def write_summary(ws, section):
        for label, value, type_ in section["rows"]:
            ws.append([label, cell_value(value, type_)])
            ws.cell(ws.max_row, 2).number_format = number_format(value, type_)
            ws.cell(ws.max_row, 1).border = ws.cell(ws.max_row, 2).border = thin

    def write_table(ws, section):
        columns = section["columns"]
        ws.append([c["label"] for c in columns])
        head = ws.max_row
        for cell in ws[head]:
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for row in section["rows"]:
            ws.append([cell_value(v, c["type"]) for v, c in zip(row, columns)])
        if section.get("total") and section["rows"]:
            ws.append([cell_value(v, c["type"]) for v, c in zip(section["total"], columns)])
            for cell in ws[ws.max_row]:
                cell.font, cell.fill = Font(bold=True), total_fill
        for i, c in enumerate(columns, start=1):
            for (cell,) in ws.iter_rows(min_row=head + 1, min_col=i, max_col=i):
                cell.number_format = number_format(cell.value, c["type"])
        ws.freeze_panes = f"A{head + 1}"
        if section["rows"]:
            ws.auto_filter.ref = f"A{head}:{get_column_letter(len(columns))}{ws.max_row}"

    def write(ws, section):
        (write_summary if section["type"] == "summary" else write_table)(ws, section)

    def sheet_title(text):
        return re.sub(r"[\\/*?:\[\]]", " ", text)[:31]  # символы, запрещённые в названии листа Excel

    # первый лист: заголовок, шапка и первая секция (итоги или единственная таблица), остальные — на своих листах
    wb = Workbook()
    ws = wb.active
    first, *rest = report["sections"]
    ws.title = sheet_title(first["title"])
    ws.append([report["title"]])
    ws["A1"].font = Font(bold=True, size=14, color=GREEN)
    for label, value in report["meta"]:
        ws.append([label, value])
    ws.append([])
    header_rows = ws.max_row
    write(ws, first)
    autosize(ws, header_rows)

    for section in rest:
        ws = wb.create_sheet(sheet_title(section["title"]))
        write(ws, section)
        autosize(ws)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------------------------------------------------------- Word


def to_docx(report, language):
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    green = RGBColor.from_string(GREEN)

    def shade(cell, color):
        tc_pr = cell._tc.get_or_add_tcPr()
        fill = OxmlElement("w:shd")
        fill.set(qn("w:val"), "clear")
        fill.set(qn("w:fill"), color)
        tc_pr.append(fill)

    def write(cell, text, bold=False, right=False, color=None):
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT if right else WD_ALIGN_PARAGRAPH.LEFT
        run = paragraph.add_run(text)
        run.bold = bold
        run.font.size = Pt(9)
        if color:
            run.font.color.rgb = color

    doc = Document()
    page = doc.sections[0]
    page.orientation = WD_ORIENT.LANDSCAPE
    page.page_width, page.page_height = page.page_height, page.page_width
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    style.font.size = Pt(10)

    title = doc.add_heading(report["title"], level=0)
    for run in title.runs:
        run.font.color.rgb = green
    for label, value in report["meta"]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.add_run(f"{label}: ").bold = True
        p.add_run(str(value))

    for section in report["sections"]:
        heading = doc.add_heading(section["title"], level=2)
        for run in heading.runs:
            run.font.color.rgb = green
        if section["type"] == "summary":
            table = doc.add_table(rows=0, cols=2)
            table.style = "Table Grid"
            for label, value, type_ in section["rows"]:
                cells = table.add_row().cells
                write(cells[0], label)
                write(cells[1], _text(value, type_, language), bold=True, right=True)
            continue
        if not section["rows"]:
            doc.add_paragraph("—")
            continue
        columns = section["columns"]
        table = doc.add_table(rows=1, cols=len(columns))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for cell, c in zip(table.rows[0].cells, columns):
            shade(cell, GREEN)
            write(cell, c["label"], bold=True, right=_numeric(c["type"]), color=RGBColor(255, 255, 255))
        rows = section["rows"] + ([section["total"]] if section.get("total") else [])
        for index, row in enumerate(rows):
            is_total = section.get("total") and index == len(rows) - 1
            cells = table.add_row().cells
            for cell, value, c in zip(cells, row, columns):
                if is_total:
                    shade(cell, LIGHT)
                write(cell, _text(value, c["type"], language), bold=bool(is_total), right=_numeric(c["type"]))

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# ---------------------------------------------------------------- PDF

# Шрифт с кириллицей и таджикскими буквами (ӯ ҳ қ ғ ҷ ӣ). Можно задать свой в settings.REPORT_FONTS.
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


class FontNotFound(Exception):
    pass


def _register_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "Report" in pdfmetrics.getRegisteredFontNames():
        return
    for regular, bold in getattr(settings, "REPORT_FONTS", None) or FONT_CANDIDATES:
        if Path(regular).exists():
            pdfmetrics.registerFont(TTFont("Report", regular))
            pdfmetrics.registerFont(TTFont("Report-Bold", bold if Path(bold).exists() else regular))
            # чтобы <b> в Paragraph брал жирное начертание
            pdfmetrics.registerFontFamily("Report", normal="Report", bold="Report-Bold", italic="Report", boldItalic="Report-Bold")
            return
    raise FontNotFound("Не найден шрифт с кириллицей для PDF. Укажите пути в settings.REPORT_FONTS.")


def to_pdf(report, language):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_fonts()
    green = colors.HexColor(f"#{GREEN}")
    light = colors.HexColor(f"#{LIGHT}")
    line = colors.HexColor("#BCCAC1")
    title_style = ParagraphStyle("t", fontName="Report-Bold", fontSize=18, leading=22, textColor=green, spaceAfter=6)
    meta_style = ParagraphStyle("m", fontName="Report", fontSize=9, leading=13)
    h_style = ParagraphStyle("h", fontName="Report-Bold", fontSize=12, leading=16, textColor=green, spaceBefore=12, spaceAfter=6)
    cell_style = ParagraphStyle("c", fontName="Report", fontSize=8, leading=10)

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    page = landscape(A4)
    width = page[0] - 30 * mm
    story = [Paragraph(esc(report["title"]), title_style)]
    for label, value in report["meta"]:
        story.append(Paragraph(f"<b>{esc(label)}:</b> {esc(value)}", meta_style))

    for section in report["sections"]:
        story.append(Paragraph(esc(section["title"]), h_style))
        if section["type"] == "summary":
            data = [[Paragraph(esc(label), cell_style), _text(value, type_, language)] for label, value, type_ in section["rows"]]
            table = Table(data, colWidths=[90 * mm, 50 * mm], hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("FONT", (0, 0), (-1, -1), "Report", 9),
                        ("FONT", (1, 0), (1, -1), "Report-Bold", 9),
                        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.5, line),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]
                )
            )
            story.append(table)
            continue
        if not section["rows"]:
            story.append(Paragraph("—", meta_style))
            continue
        columns = section["columns"]
        # текстовые колонки шире числовых
        weights = [3 if c["type"] == "text" else 1.6 if c["type"] == "datetime" else 1.3 for c in columns]
        col_widths = [width * w / sum(weights) for w in weights]
        head_style = ParagraphStyle("hc", parent=cell_style, textColor=colors.white)
        head_right = ParagraphStyle("hcr", parent=head_style, alignment=2)  # над числами — по правому краю
        data = [[Paragraph(f"<b>{esc(c['label'])}</b>", head_right if _numeric(c["type"]) else head_style) for c in columns]]
        rows = section["rows"] + ([section["total"]] if section.get("total") else [])
        for row in rows:
            data.append(
                [
                    _text(v, c["type"], language) if _numeric(c["type"]) else Paragraph(esc(_text(v, c["type"], language)), cell_style)
                    for v, c in zip(row, columns)
                ]
            )
        style = [
            ("FONT", (0, 0), (-1, -1), "Report", 8),
            ("BACKGROUND", (0, 0), (-1, 0), green),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, line),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4FBF8")]),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for i, c in enumerate(columns):
            if _numeric(c["type"]):
                style.append(("ALIGN", (i, 0), (i, -1), "RIGHT"))
        if section.get("total"):
            style += [("BACKGROUND", (0, -1), (-1, -1), light), ("FONT", (0, -1), (-1, -1), "Report-Bold", 8)]
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle(style))
        story.append(table)
    story.append(_signature_block(report["signature"], width))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Report", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(page[0] - 15 * mm, 8 * mm, f"{report['title']} · {doc.page}")
        canvas.restoreState()

    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=page, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=report["title"],
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def _signature_block(sign, width):
    """
    Подпись директора в конце PDF (не разрывается между страницами):
        Данные отчёта проверены. Подтверждаю.
        Директор аптеки   ____________________   Махмадов А.
                               подпись              Ф. И. О.
        Дата «____» ______________ 20____ г.                    М. П.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    text = ParagraphStyle("s", fontName="Report", fontSize=10, leading=14)
    bold = ParagraphStyle("sb", parent=text, fontName="Report-Bold")
    hint = ParagraphStyle("sh", parent=text, fontSize=7, leading=9, textColor=colors.grey, alignment=1)
    line = colors.HexColor("#333333")

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # колонки: должность | линия подписи | промежуток | линия Ф. И. О.
    name = Paragraph(esc(sign["name"]), ParagraphStyle("sn", parent=text, alignment=1))
    table = Table(
        [
            [Paragraph(esc(sign["role"]), bold), "", "", name],
            ["", Paragraph(esc(sign["signature"]), hint), "", Paragraph(esc(sign["full_name"]), hint)],
        ],
        colWidths=[45 * mm, 60 * mm, 10 * mm, 70 * mm],
        rowHeights=[12 * mm, 5 * mm],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, 0), "BOTTOM"),
                ("LINEBELOW", (1, 0), (1, 0), 0.7, line),
                ("LINEBELOW", (3, 0), (3, 0), 0.7, line),
            ]
        )
    )
    date_row = Table(
        [
            [
                # дата — конец периода отчёта; за всё время — пустой бланк для записи от руки
                Paragraph(f"{esc(sign['date'])}: <b>{esc(sign['date_value'])}</b>" if sign["date_value"]
                          else f"{esc(sign['date'])}: «____» ____________________ 20____", text),
                Paragraph(esc(sign["stamp"]), bold),
            ]
        ],
        colWidths=[width - 40 * mm, 40 * mm],
        hAlign="LEFT",
    )
    date_row.setStyle(TableStyle([("ALIGN", (1, 0), (1, 0), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    return KeepTogether(
        [Spacer(1, 12 * mm), Paragraph(esc(sign["confirm"]), text), Spacer(1, 2 * mm), table, Spacer(1, 8 * mm), date_row]
    )


EXPORTERS = {"xlsx": to_xlsx, "docx": to_docx, "pdf": to_pdf}
