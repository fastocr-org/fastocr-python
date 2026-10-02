"""Print the text of a scanned PDF.

    FASTOCR_API_KEY=... python examples/extract_text.py scan.pdf
"""
import sys

from fastocr_sdk import FastOCR

with FastOCR() as client:
    print(client.extract_text(sys.argv[1]))
