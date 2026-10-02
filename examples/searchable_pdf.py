"""Turn a scanned PDF into a searchable PDF.

    FASTOCR_API_KEY=... python examples/searchable_pdf.py scan.pdf searchable.pdf
"""
import sys

from fastocr_sdk import FastOCR

source, destination = sys.argv[1], sys.argv[2]

with FastOCR() as client:
    job = client.documents.process(source)
    client.documents.download_searchable_pdf(job.id, destination)

print(f"Saved {destination} ({job.pages_billed} pages billed)")
