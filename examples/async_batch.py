"""OCR every PDF in a folder, a few at a time, and write a .txt next to each.

    FASTOCR_API_KEY=... python examples/async_batch.py ./scans

New accounts may run 2 documents at once, so keep the limit at 2 unless yours is higher.
"""
import asyncio
import sys
from pathlib import Path

from fastocr_sdk import AsyncFastOCR, FastOCRError

CONCURRENCY = 2


async def convert(client: AsyncFastOCR, limit: asyncio.Semaphore, pdf: Path) -> None:
    async with limit:
        try:
            text = await client.extract_text(pdf)
        except FastOCRError as error:
            print(f"{pdf.name}: failed ({error})")
            return
        pdf.with_suffix(".txt").write_text(text, encoding="utf-8")
        print(f"{pdf.name}: done")


async def main(folder: Path) -> None:
    limit = asyncio.Semaphore(CONCURRENCY)
    async with AsyncFastOCR() as client:
        await asyncio.gather(*(convert(client, limit, pdf) for pdf in sorted(folder.glob("*.pdf"))))


asyncio.run(main(Path(sys.argv[1])))
