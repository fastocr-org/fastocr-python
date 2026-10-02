"""The five-step flow against a real local HTTP server: no mocks in the transport."""
import hashlib
import io
import os
import tracemalloc

import pytest

from fastocr_sdk import DocumentFailedError
from fake_server import PAGES_TEXT, RESULT_PDF, FakeApi

from conftest import call

LARGE_FILE_BYTES = 50 * 1024 * 1024
MEMORY_BUDGET_BYTES = 10 * 1024 * 1024


@pytest.fixture
def api():
    with FakeApi() as server:
        yield server


@pytest.fixture
def make_client(harness, api):
    return lambda **options: harness.client(base_url=api.base_url, **options)


def write_random_file(path, size):
    digest = hashlib.sha256()
    with open(path, "wb") as f:
        for _ in range(size // (1024 * 1024)):
            chunk = os.urandom(1024 * 1024)
            digest.update(chunk)
            f.write(chunk)
    return digest.hexdigest()


async def test_full_flow_for_a_small_file(make_client, api, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 hello")
    client = make_client()

    job = await call(client.documents.process(pdf, poll_interval=0))
    text = await call(client.documents.get_text(job.id))
    destination = tmp_path / "out.pdf"
    await call(client.documents.download_searchable_pdf(job.id, destination))

    assert job.status == "completed"
    assert text == PAGES_TEXT
    assert destination.read_bytes() == RESULT_PDF
    assert not (tmp_path / "out.pdf.part").exists()
    assert api.created["doc-1"] == {"filename": "scan.pdf", "size_bytes": 14}


async def test_upload_has_content_length_and_is_not_chunked(make_client, api, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(os.urandom(3 * 1024 * 1024 + 17))
    client = make_client()

    await call(client.documents.process(pdf, poll_interval=0))

    upload = api.uploads[0]
    headers = {name.lower(): value for name, value in upload["headers"].items()}
    assert upload["chunked"] is False
    assert "transfer-encoding" not in headers
    assert headers["content-length"] == str(pdf.stat().st_size)
    assert headers["content-type"] == "application/pdf"
    assert "authorization" not in headers
    assert upload["sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()


async def test_stream_from_the_current_position_uploads_only_the_rest(make_client, api):
    stream = io.BytesIO(b"IGNORED" + b"%PDF-body")
    stream.seek(len(b"IGNORED"))
    client = make_client()

    await call(client.documents.process(stream, filename="x.pdf", poll_interval=0))

    assert api.created["doc-1"]["size_bytes"] == len(b"%PDF-body")
    assert api.uploads[0]["sha256"] == hashlib.sha256(b"%PDF-body").hexdigest()


async def test_unseekable_stream_is_buffered_with_the_right_length(make_client, api):
    class Pipe(io.RawIOBase):
        def __init__(self, data):
            self._buffer = io.BytesIO(data)

        def readable(self):
            return True

        def seekable(self):
            return False

        def read(self, size=-1):
            return self._buffer.read(size)

    client = make_client()

    await call(client.documents.process(Pipe(b"%PDF-pipe"), filename="x.pdf", poll_interval=0))

    assert api.created["doc-1"]["size_bytes"] == len(b"%PDF-pipe")
    assert api.uploads[0]["chunked"] is False
    assert api.uploads[0]["sha256"] == hashlib.sha256(b"%PDF-pipe").hexdigest()


async def test_sha256_is_sent_as_the_signed_checksum_header(make_client, api):
    client = make_client()

    await call(client.documents.process(b"%PDF-1", filename="x.pdf", sha256="abc=", poll_interval=0))

    headers = {name.lower(): value for name, value in api.uploads[0]["headers"].items()}
    assert headers["x-amz-checksum-sha256"] == "abc="
    assert api.created["doc-1"]["sha256"] == "abc="


async def test_failed_document_raises_with_code(harness, tmp_path):
    with FakeApi(final_status="failed") as api:
        client = harness.client(base_url=api.base_url)
        with pytest.raises(DocumentFailedError) as caught:
            await call(client.documents.process(b"%PDF-1", filename="x.pdf", poll_interval=0))
    assert caught.value.document_id == "doc-1"
    assert caught.value.code == "processing_failed"


async def test_large_file_streams_with_bounded_memory(make_client, api, tmp_path):
    pdf = tmp_path / "large.pdf"
    expected = write_random_file(pdf, LARGE_FILE_BYTES)
    client = make_client()

    tracemalloc.start()
    try:
        await call(client.documents.process(pdf, poll_interval=0))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    upload = api.uploads[0]
    assert upload["chunked"] is False
    assert upload["received"] == LARGE_FILE_BYTES
    assert upload["sha256"] == expected
    assert peak < MEMORY_BUDGET_BYTES, f"peak {peak / 1e6:.1f} MB while uploading 50 MB"


async def test_memory_measurement_would_catch_a_whole_file_read(make_client, api, tmp_path):
    pdf = tmp_path / "large.pdf"
    write_random_file(pdf, LARGE_FILE_BYTES)
    client = make_client()

    tracemalloc.start()
    try:
        await call(client.documents.process(pdf.read_bytes(), filename="large.pdf", poll_interval=0))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak > LARGE_FILE_BYTES
