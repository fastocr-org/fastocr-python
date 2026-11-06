"""Extract a bank statement into an Excel workbook.

    FASTOCR_API_KEY=... python examples/extract_bank_statement.py statement.pdf statement.xlsx
"""
import sys

from fastocr_sdk import FastOCR

with FastOCR() as client:
    job = client.extract_statement(sys.argv[1])
    client.bank_statements.save(job.id, sys.argv[2], "xlsx")
    print(f"{job.pages_billed} page(s), needs review: {job.needs_review}")
