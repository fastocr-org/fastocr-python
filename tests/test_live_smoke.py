"""Opt-in smoke test against a real FastOCR deployment. Skipped unless both variables are set.

    FASTOCR_LIVE_API_KEY=... FASTOCR_LIVE_BASE_URL=https://api.fastocr.org pytest tests/test_live_smoke.py

It processes one single-page PDF, so it uses one page of the key's allowance.
"""
import os

import pytest

from fastocr_sdk import AuthenticationError, NotFoundError

from conftest import call

API_KEY = os.environ.get("FASTOCR_LIVE_API_KEY")
BASE_URL = os.environ.get("FASTOCR_LIVE_BASE_URL")

pytestmark = pytest.mark.skipif(
    not (API_KEY and BASE_URL),
    reason="set FASTOCR_LIVE_API_KEY and FASTOCR_LIVE_BASE_URL to run",
)

PHRASE = "FastOCR SDK smoke test"


def minimal_pdf(text):
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return out


@pytest.fixture
def live(harness):
    return harness.client(api_key=API_KEY, base_url=BASE_URL, timeout=90.0)


async def test_process_download_and_delete(live, tmp_path):
    pdf = tmp_path / "smoke.pdf"
    pdf.write_bytes(minimal_pdf(PHRASE))

    job = await call(live.documents.process(pdf, timeout=240, poll_interval=3))
    text = await call(live.documents.get_text(job.id))
    searchable = tmp_path / "searchable.pdf"
    await call(live.documents.download_searchable_pdf(job.id, searchable))
    listed = await call(live.documents.list(limit=5))
    await call(live.documents.delete(job.id))

    assert job.is_completed and job.pages_billed == 1
    assert PHRASE.lower() in text.lower()
    assert searchable.read_bytes().startswith(b"%PDF")
    assert job.id in [d.id for d in listed.documents]


async def test_unknown_document_is_not_found(live):
    with pytest.raises(NotFoundError) as caught:
        await call(live.documents.get("00000000-0000-0000-0000-000000000000"))
    assert caught.value.code == "document_not_found"


async def test_a_wrong_key_is_an_authentication_error(harness):
    bad = harness.client(api_key="fok_live_" + "0" * 32, base_url=BASE_URL, max_retries=0)
    with pytest.raises(AuthenticationError) as caught:
        await call(bad.documents.list())
    assert caught.value.status_code == 403
