"""Batch convert scanned PDF files in a folder into Searchable PDFs with OCR text layers.

Requires:
    pip install fastocr-sdk
"""
import sys
from pathlib import Path
from fastocr_sdk import FastOCR, FastOCRError

def batch_convert_folder(input_dir: str, output_dir: str):
    with FastOCR() as client:
        convert_folder(client, input_dir, output_dir)


def convert_folder(client: FastOCR, input_dir: str, output_dir: str):
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pdf_files = list(input_path.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {input_dir}")
        return

    print(f"Found {len(pdf_files)} PDF documents to process...")

    for i, pdf_file in enumerate(pdf_files, 1):
        target_file = output_path / f"searchable_{pdf_file.name}"
        if target_file.exists():
            print(f"[{i}/{len(pdf_files)}] Skipping {pdf_file.name} (already archived)")
            continue

        print(f"[{i}/{len(pdf_files)}] Processing {pdf_file.name}...")
        try:
            job = client.documents.process(pdf_file)
            if job.is_partial:
                print(f"  -> Skipped {pdf_file.name}: only {job.pages_billed} of {job.pages_total} pages were processed (out of pages)")
                continue
            client.documents.download_searchable_pdf(job.id, target_file)
            print(f"  -> Saved searchable PDF: {target_file.name} (Pages billed: {job.pages_billed})")
        except FastOCRError as e:
            print(f"  -> Failed to process {pdf_file.name}: {e}")

if __name__ == "__main__":
    in_dir = sys.argv[1] if len(sys.argv) > 1 else "./scans"
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "./searchable_archive"
    batch_convert_folder(in_dir, out_dir)
