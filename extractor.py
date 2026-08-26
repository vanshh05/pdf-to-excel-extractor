import os
import re
import pandas as pd
import pdfplumber
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from config import CONFIGS

try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


def extract_raw_lines_native(pdf_path):
    """Extracts text line-by-line using pdfplumber."""
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(layout=False)
            if text:
                lines.extend(text.splitlines())
    return lines


def extract_raw_lines_ocr(pdf_path, tesseract_cmd=None, poppler_path=None):
    """Fallback OCR extractor for scanned PDFs."""
    if not OCR_AVAILABLE:
        raise RuntimeError("pytesseract or pdf2image is not installed.")
    
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    images = convert_from_path(pdf_path, dpi=300, poppler_path=poppler_path)
    lines = []
    custom_config = r'--oem 3 --psm 6'
    
    for img in images:
        text = pytesseract.image_to_string(img, config=custom_config)
        if text:
            lines.extend(text.splitlines())
    return lines


def parse_regulatory_lines(lines, config_key="CRR_CCR"):
    """State machine to parse hierarchy into structured rows."""
    cfg = CONFIGS.get(config_key, CONFIGS["CRR_CCR"])
    sec_re = re.compile(cfg["section_regex"])
    sub_re = re.compile(cfg["sub_clause_regex"])
    date_re = re.compile(cfg["date_regex"])

    extracted_rows = []
    current_sec = None
    preamble = ""
    current_sub = None
    current_text_lines = []
    effective_date = ""

    def flush_clause():
        nonlocal current_text_lines, current_sub, current_sec, preamble, effective_date
        if current_sec and current_sub:
            full_rule_text = "\n".join(current_text_lines).strip()
            extracted_rows.append({
                "Regulation name": cfg["regulation_name"],
                "Regulatory reference": f"{current_sec} ({current_sub})",
                "Rule": full_rule_text,
                "Published Rule Date": "",
                "Effective Date": effective_date,
                "BAU Line C": ""
            })
            current_text_lines = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # 1. Effective Date check
        d_match = date_re.match(line)
        if d_match:
            effective_date = d_match.group(1)
            continue

        # 2. Main Section Header check (e.g. 3.1)
        s_match = sec_re.match(line)
        if s_match:
            flush_clause()
            current_sec = s_match.group(1)
            preamble = s_match.group(2).strip()
            current_sub = None
            continue

        # 3. Sub-clause check (e.g. (1), (2))
        sub_match = sub_re.match(line)
        if sub_match:
            flush_clause()
            current_sub = sub_match.group(1)
            sub_text = sub_match.group(2).strip()
            
            # Sub-clause (1) inherits preamble if present
            if current_sub == "1" and preamble:
                current_text_lines = [preamble, f"({current_sub}) {sub_text}".strip()]
            else:
                current_text_lines = [f"({current_sub}) {sub_text}".strip()]
            continue

        # 4. Continuation lines or sub-letters ((a), (b))
        if current_sub:
            current_text_lines.append(line)

    flush_clause()
    return pd.DataFrame(extracted_rows)


def style_excel(output_path):
    """Applies the exact styling and color scheme to match your template."""
    wb = openpyxl.load_workbook(output_path)
    ws = wb.active

    # Palette
    blue_header_fill = PatternFill(start_color="00AEEF", end_color="00AEEF", fill_type="solid")
    green_header_fill = PatternFill(start_color="6BA539", end_color="6BA539", fill_type="solid")
    white_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    regular_font = Font(name="Calibri", size=10, color="000000")
    
    thin_border = Border(
        left=Side(style='thin', color='BFBFBF'),
        right=Side(style='thin', color='BFBFBF'),
        top=Side(style='thin', color='BFBFBF'),
        bottom=Side(style='thin', color='BFBFBF')
    )

    # Style Header Row
    for col_idx, cell in enumerate(ws[1], start=1):
        cell.font = white_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.fill = green_header_fill if cell.value == "BAU Line C" else blue_header_fill
        cell.border = thin_border

    # Style Data Rows
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
        for cell in row:
            cell.font = regular_font
            cell.border = thin_border
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    # Set column widths
    col_widths = {'A': 18, 'B': 22, 'C': 70, 'D': 20, 'E': 18, 'F': 20}
    for col_letter, width in col_widths.items():
        ws.column_dimensions[col_letter].width = width

    ws.row_dimensions[1].height = 28
    wb.save(output_path)


def convert_pdf_to_excel(pdf_path, output_excel_path="output.xlsx", use_ocr=False, tesseract_cmd=None, poppler_path=None):
    print(f"Reading {pdf_path}...")
    
    if use_ocr:
        print("Extracting text via OCR...")
        lines = extract_raw_lines_ocr(pdf_path, tesseract_cmd, poppler_path)
    else:
        print("Extracting native PDF text...")
        lines = extract_raw_lines_native(pdf_path)
        if not lines and OCR_AVAILABLE:
            print("No digital text found. Automatically falling back to OCR...")
            lines = extract_raw_lines_ocr(pdf_path, tesseract_cmd, poppler_path)

    df = parse_regulatory_lines(lines)
    if df.empty:
        print("Warning: No matching regulatory rules were found. Check pattern rules in config.py.")
        return

    df.to_excel(output_excel_path, index=False)
    style_excel(output_excel_path)
    print(f"Success! Excel generated at: {output_excel_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Extract regulatory PDFs to structured Excel format.")
    parser.add_argument("pdf_path", help="Path to input PDF file")
    parser.add_argument("-o", "--output", default="Extracted_Rules.xlsx", help="Path to output Excel file")
    parser.add_argument("--ocr", action="store_true", help="Force OCR extraction")
    
    args = parser.parse_args()
    
    if os.path.exists(args.pdf_path):
        convert_pdf_to_excel(args.pdf_path, args.output, use_ocr=args.ocr)
    else:
        print(f"Error: File '{args.pdf_path}' not found.")
