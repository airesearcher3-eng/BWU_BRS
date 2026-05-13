"""Excel output writer for the final BRS statement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

from engine.normaliser import format_brs_date


AMOUNT_FORMAT = "#,##0.00"
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


def generate_brs_excel(
    output_path: str | Path,
    *,
    as_on_date,
    bank_book_balance,
    bank_statement_balance,
    sections: dict[str, list[dict[str, Any]]],
    totals: dict[str, Any],
    bank_account: dict | None = None,
) -> str:
    """Write the BRS in the Brainware University layout used in the live workbook."""

    # Resolve bank details: use provided account or fall back to defaults
    acct = bank_account or {}
    institution = "BRAINWARE UNIVERSITY"
    bank_label = acct.get("label") or "ICICI New Savings Ltd.,Barasat Branch"
    account_no = acct.get("account_no") or "082401002764"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = f"BRS {as_on_date.strftime('%b').upper()}'{as_on_date.strftime('%y')}"

    worksheet.column_dimensions["A"].width = 14
    worksheet.column_dimensions["B"].width = 90
    worksheet.column_dimensions["C"].width = 16
    worksheet.column_dimensions["D"].width = 16
    worksheet.column_dimensions["E"].width = 20
    worksheet.column_dimensions["F"].width = 16
    worksheet.column_dimensions["G"].width = 18

    title_font = Font(name="Calibri", size=12, bold=True)
    normal_font = Font(name="Calibri", size=11)
    header_font = Font(name="Calibri", size=11, bold=True)

    worksheet["A1"] = institution
    worksheet["A1"].font = title_font
    worksheet["A2"] = f"Bank Reconcillation Statement as on {format_brs_date(as_on_date)}"
    worksheet["A2"].font = normal_font
    worksheet["A3"] = bank_label
    worksheet["A4"] = f"Account No.{account_no}"

    worksheet["A5"] = f"Balance as per Bank Book as on {as_on_date.strftime('%d.%m.%Y')}"
    worksheet["G5"] = bank_book_balance
    worksheet["G5"].number_format = AMOUNT_FORMAT

    row = 6
    row = _write_section(
        worksheet,
        row,
        "Add: Cheque issued but not debited by Bank:",
        sections["add_cheque_issued"],
        subtotal_column="F",
        fonts=(header_font, normal_font),
    )
    row = _write_section(
        worksheet,
        row,
        "Add: Amount credited by Bank but not entered in Bank Book:",
        sections["add_bank_credit"],
        subtotal_column="F",
        fonts=(header_font, normal_font),
    )
    row = _write_section(
        worksheet,
        row,
        "Less: Cheque deposited but not credited by Bank:",
        sections["less_cheque_deposit"],
        subtotal_column="F",
        fonts=(header_font, normal_font),
    )
    row = _write_section(
        worksheet,
        row,
        "Less: Amount debited by Bank but not entered in Bank Book:",
        sections["less_bank_debit"],
        subtotal_column="F",
        fonts=(header_font, normal_font),
    )

    worksheet[f"A{row}"] = f"Reconcile Balance as per Bank Book as on {format_brs_date(as_on_date)}"
    worksheet[f"G{row}"] = totals["reconciled_balance"]
    worksheet[f"G{row}"].number_format = AMOUNT_FORMAT
    row += 2

    worksheet[f"A{row}"] = f"Balance as per Bank Statement as on {format_brs_date(as_on_date)}"
    worksheet[f"G{row}"] = bank_statement_balance
    worksheet[f"G{row}"].number_format = AMOUNT_FORMAT
    row += 1

    worksheet[f"G{row}"] = totals["difference"]
    worksheet[f"G{row}"].number_format = AMOUNT_FORMAT

    for cell in ("A1", "A2", "A3", "A4"):
        worksheet[cell].alignment = Alignment(horizontal="left")
    for row_cells in worksheet.iter_rows():
        for cell in row_cells:
            if cell.column in (4, 6, 7):
                cell.alignment = Alignment(horizontal="right")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    workbook.close()
    return str(output)


def _write_section(
    worksheet,
    row: int,
    title: str,
    items: list[dict[str, Any]],
    *,
    subtotal_column: str,
    fonts,
) -> int:
    """Write one BRS section and return the next available row number."""

    header_font, normal_font = fonts
    worksheet[f"A{row}"] = title
    worksheet[f"A{row}"].font = header_font
    row += 1

    worksheet[f"A{row}"] = "Date"
    worksheet[f"B{row}"] = "Remarks"
    worksheet[f"C{row}"] = "Chq. No."
    worksheet[f"D{row}"] = "Amount"
    worksheet[f"E{row}"] = "Cleared/Cancelled on"
    for column in ("A", "B", "C", "D", "E"):
        worksheet[f"{column}{row}"].font = header_font
        worksheet[f"{column}{row}"].border = THIN_BORDER
    row += 1

    total = 0
    for item in items:
        worksheet[f"A{row}"] = format_brs_date(item["date"])
        worksheet[f"B{row}"] = item["remarks"]
        worksheet[f"C{row}"] = item.get("cheque_no")
        worksheet[f"D{row}"] = item["amount"]
        worksheet[f"D{row}"].number_format = AMOUNT_FORMAT
        worksheet[f"E{row}"] = format_brs_date(item.get("cleared_on"))
        total += item["amount"]

        for column in ("A", "B", "C", "D", "E"):
            worksheet[f"{column}{row}"].font = normal_font
            worksheet[f"{column}{row}"].border = THIN_BORDER
        row += 1

    worksheet[f"{subtotal_column}{row}"] = total
    worksheet[f"{subtotal_column}{row}"].number_format = AMOUNT_FORMAT
    row += 1
    return row
