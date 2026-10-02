"""Every route, with the response shapes the API handlers produce."""
import io
import json

import httpx
import pytest
import respx

import fastocr_sdk
from fastocr_sdk import (
    AsyncFastOCR,
    AuthenticationError,
    DocumentFailedError,
    FastOCR,
    PartialResultError,
    PollingTimeoutError,
)

from conftest import BASE_URL, call

UPLOAD_URL = "https://s3.test/bucket/uploads/original.pdf?X-Amz-Signature=sig"
CREATED = {
    "id": "doc_1",
    "status": "awaiting_upload",
    "upload_url": UPLOAD_URL,
    "expires_at": "2026-10-01T12:00:00+00:00",
}
FAILED_BODY = {
    "id": "doc_1",
    "status": "failed",
    "external_id": None,
    "error": {
        "type": "insufficient_credits",
        "code": "buy_pages",
        "message": "This account is out of document pages.",
        "retryable": False,
    },
}


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("FASTOCR_API_KEY", raising=False)
    for cls in (FastOCR, AsyncFastOCR):
        with pytest.raises(AuthenticationError, match="API key must be provided"):
            cls()


def test_api_key_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("FASTOCR_API_KEY", "fok_env")
    assert FastOCR().api_key == "fok_env"
    assert AsyncFastOCR().api_key == "fok_env"


async def test_requests_carry_auth_and_versioned_user_agent(mock, client):
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "processing"})

    await call(client.documents.get("doc_1"))

    headers = route.calls.last.request.headers
    assert headers["Authorization"] == "Bearer fok_test"
    assert headers["User-Agent"] == f"fastocr-python/{fastocr_sdk.__version__}"


async def test_create_sends_the_documented_body_and_returns_a_model(mock, client):
    route = mock.post(f"{BASE_URL}/v1/documents").respond(201, json={**CREATED, "new_field": 1})

    created = await call(client.documents.create(
        "scan.pdf", 1024, idempotency_key="key-1", external_id="order-9", sha256="abc="
    ))

    request = route.calls.last.request
    assert json.loads(request.content) == {
        "filename": "scan.pdf", "size_bytes": 1024, "external_id": "order-9", "sha256": "abc=",
    }
    assert request.headers["Idempotency-Key"] == "key-1"
    assert (created.id, created.status, created.upload_url) == ("doc_1", "awaiting_upload", UPLOAD_URL)
    assert created.raw["new_field"] == 1


async def test_create_omits_optional_fields_and_generates_a_key(mock, client):
    route = mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)

    await call(client.documents.create("scan.pdf", 10))

    request = route.calls.last.request
    assert json.loads(request.content) == {"filename": "scan.pdf", "size_bytes": 10}
    assert len(request.headers["Idempotency-Key"]) >= 32


async def test_create_rejects_non_pdf_before_any_request(mock, client):
    with pytest.raises(ValueError, match="PDF files only"):
        await call(client.documents.create("scan.png", 10))
    assert mock.calls.call_count == 0


async def test_start_returns_the_status_body(mock, client):
    route = mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})

    assert await call(client.documents.start("doc_1")) == {"status": "processing"}
    assert route.called


async def test_get_parses_a_completed_job_and_ignores_unknown_fields(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={
        "id": "doc_1", "status": "completed", "external_id": "o-1",
        "pages_billed": 3, "pages_total": 3, "pages_processed": 3, "is_truncated": False,
        "outputs": {"text": {"available": True}, "pdf": {"available": True, "size": 9}},
        "brand_new_field": {"a": 1},
    })

    job = await call(client.documents.get("doc_1"))

    assert job.is_completed and not job.is_partial and not job.is_failed
    assert (job.pages_billed, job.pages_total, job.pages_processed) == (3, 3, 3)
    assert job.outputs.text_available and job.outputs.pdf_available
    assert job.external_id == "o-1"


async def test_get_parses_a_partial_job(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={
        "id": "doc_1", "status": "partial", "is_truncated": True, "pages_billed": 5, "pages_total": 9,
    })

    job = await call(client.documents.get("doc_1"))

    assert job.is_completed and job.is_partial


async def test_get_parses_the_failure_object(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json=FAILED_BODY)

    job = await call(client.documents.get("doc_1"))

    assert job.is_failed
    assert (job.error.code, job.error.type, job.error.retryable) == ("buy_pages", "insufficient_credits", False)


async def test_unknown_status_does_not_crash(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "quarantined"})

    job = await call(client.documents.get("doc_1"))

    assert job.status == "quarantined"
    assert not (job.is_completed or job.is_failed)


async def test_document_id_is_url_encoded(mock, client):
    route = mock.get(url__regex=r".*").respond(200, json={"id": "x", "status": "processing"})

    await call(client.documents.get("a/b"))

    assert route.calls.last.request.url.raw_path == b"/v1/documents/a%2Fb"


async def test_list_sends_only_given_params_and_returns_the_cursor(mock, client):
    route = mock.get(f"{BASE_URL}/v1/documents").respond(200, json={
        "documents": [{"id": "d1", "status": "completed"}, {"id": "d2", "status": "awaiting_upload"}],
        "next_cursor": "cur-2",
    })

    page = await call(client.documents.list(limit=2, status="completed", updated_since="2026-10-01T00:00:00Z"))

    params = dict(route.calls.last.request.url.params)
    assert params == {"limit": "2", "status": "completed", "updated_since": "2026-10-01T00:00:00Z"}
    assert [d.id for d in page.documents] == ["d1", "d2"]
    assert page.next_cursor == "cur-2"


async def test_list_without_filters_sends_no_query(mock, client):
    route = mock.get(f"{BASE_URL}/v1/documents").respond(200, json={"documents": [], "next_cursor": None})

    page = await call(client.documents.list())

    assert dict(route.calls.last.request.url.params) == {}
    assert page.documents == [] and page.next_cursor is None


async def test_delete_returns_the_deleting_status(mock, client):
    route = mock.delete(f"{BASE_URL}/v1/documents/doc_1").respond(202, json={"status": "deleting"})

    assert await call(client.documents.delete("doc_1")) == {"status": "deleting"}
    assert route.called


async def test_get_output_url_for_each_format(mock, client):
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1/output").respond(200, json={
        "format": "pdf", "url": "https://s3.test/out.pdf", "expires_at": "2026-10-01T13:00:00+00:00",
        "pages_billed": 2, "pages_total": 2,
    })

    out = await call(client.documents.get_output_url("doc_1", "pdf"))

    assert route.calls.last.request.url.params["format"] == "pdf"
    assert (out.url, out.pages_billed, out.pages_total) == ("https://s3.test/out.pdf", 2, 2)


async def test_get_output_url_rejects_unknown_format_locally(mock, client):
    with pytest.raises(ValueError, match="'text' or 'pdf'"):
        await call(client.documents.get_output_url("doc_1", "docx"))
    assert mock.calls.call_count == 0


async def test_get_text_decodes_utf8_without_a_charset(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1/output").respond(200, json={
        "format": "text", "url": "https://s3.test/ocr-text.txt", "expires_at": "x",
    })
    route = mock.get("https://s3.test/ocr-text.txt").respond(
        200, content="café 世界".encode(), headers={"Content-Type": "text/plain"}
    )

    text = await call(client.documents.get_text("doc_1"))

    assert text == "café 世界"
    assert "Authorization" not in route.calls.last.request.headers


async def test_download_pdf_to_a_stream(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1/output").respond(200, json={
        "format": "pdf", "url": "https://s3.test/searchable.pdf", "expires_at": "x",
    })
    mock.get("https://s3.test/searchable.pdf").respond(200, content=b"%PDF-result")
    sink = io.BytesIO()

    await call(client.documents.download_searchable_pdf("doc_1", sink))

    assert sink.getvalue() == b"%PDF-result"


async def test_failed_pdf_download_leaves_no_file_behind(mock, harness, tmp_path):
    client = harness.client(max_retries=0)
    mock.get(f"{BASE_URL}/v1/documents/doc_1/output").respond(200, json={
        "format": "pdf", "url": "https://s3.test/searchable.pdf", "expires_at": "x",
    })
    mock.get("https://s3.test/searchable.pdf").respond(403, content=b"<Error><Code>AccessDenied</Code></Error>")
    destination = tmp_path / "out.pdf"

    with pytest.raises(fastocr_sdk.DownloadError) as caught:
        await call(client.documents.download_searchable_pdf("doc_1", destination))

    assert caught.value.status_code == 403 and caught.value.code == "AccessDenied"
    assert list(tmp_path.iterdir()) == []


async def test_wait_polls_until_completed_and_ignores_unknown_statuses(mock, harness):
    client = harness.client()
    mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=[
        httpx.Response(200, json={"id": "doc_1", "status": "awaiting_upload"}),
        httpx.Response(200, json={"id": "doc_1", "status": "processing"}),
        httpx.Response(200, json={"id": "doc_1", "status": "some_future_status"}),
        httpx.Response(200, json={"id": "doc_1", "status": "completed", "pages_billed": 1}),
    ])

    job = await call(client.documents.wait_for_completion("doc_1", poll_interval=0.5))

    assert job.status == "completed"
    assert harness.sleeps == [0.5, 0.5, 0.5]


async def test_wait_returns_partial_without_raising(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "partial"})

    assert (await call(client.documents.wait_for_completion("doc_1"))).is_partial


async def test_wait_raises_a_failed_error_with_code_retryable_and_document_id(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json=FAILED_BODY)

    with pytest.raises(DocumentFailedError) as caught:
        await call(client.documents.wait_for_completion("doc_1"))

    error = caught.value
    assert (error.document_id, error.code, error.retryable) == ("doc_1", "buy_pages", False)
    assert error.type == "insufficient_credits"
    assert error.out_of_pages
    assert "out of document pages" in error.message


async def test_a_retryable_failure_and_an_unlisted_code_are_both_carried_through(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={
        "id": "doc_1", "status": "failed",
        "error": {"type": "new_type", "code": "brand_new_code", "message": "m", "retryable": True},
    })

    with pytest.raises(DocumentFailedError) as caught:
        await call(client.documents.wait_for_completion("doc_1"))

    assert (caught.value.code, caught.value.retryable, caught.value.out_of_pages) == ("brand_new_code", True, False)


async def test_a_failed_job_without_an_error_object_still_raises(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "failed"})

    with pytest.raises(DocumentFailedError) as caught:
        await call(client.documents.wait_for_completion("doc_1"))

    assert caught.value.code == "processing_failed" and caught.value.retryable


async def test_wait_times_out_with_the_last_status(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "processing"})

    with pytest.raises(PollingTimeoutError) as caught:
        await call(client.documents.wait_for_completion("doc_1", timeout=0))

    assert (caught.value.document_id, caught.value.last_status) == ("doc_1", "processing")


async def test_buy_pages_job_can_be_started_again_with_the_same_id(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=[
        httpx.Response(200, json=FAILED_BODY),
        httpx.Response(200, json={"id": "doc_1", "status": "completed"}),
    ])
    start = mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})

    with pytest.raises(DocumentFailedError) as caught:
        await call(client.documents.wait_for_completion("doc_1"))
    await call(client.documents.start(caught.value.document_id))
    job = await call(client.documents.wait_for_completion("doc_1", poll_interval=0))

    assert start.called and job.is_completed


async def test_process_runs_the_five_steps_in_order_with_one_idempotency_key(mock, client, tmp_path):
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.4 body")
    order = []
    create = mock.post(f"{BASE_URL}/v1/documents").mock(side_effect=lambda r: (order.append("create"), httpx.Response(201, json=CREATED))[1])
    upload = mock.put(url__startswith="https://s3.test/bucket/").mock(side_effect=lambda r: (order.append("upload"), httpx.Response(200))[1])
    start = mock.post(f"{BASE_URL}/v1/documents/doc_1/start").mock(side_effect=lambda r: (order.append("start"), httpx.Response(202, json={"status": "processing"}))[1])
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "completed"})

    job = await call(client.documents.process(pdf, external_id="inv-1", poll_interval=0))

    assert job.is_completed
    assert order == ["create", "upload", "start"]
    body = json.loads(create.calls.last.request.content)
    assert body == {"filename": "invoice.pdf", "size_bytes": 13, "external_id": "inv-1"}
    put = upload.calls.last.request
    assert put.content == b"%PDF-1.4 body"
    assert put.headers["Content-Length"] == "13"
    assert put.headers["Content-Type"] == "application/pdf"
    assert "Authorization" not in put.headers
    assert start.called


async def test_process_accepts_bytes_and_streams_and_uses_the_given_filename(mock, client):
    create = mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)
    mock.put(url__startswith="https://s3.test/bucket/").respond(200)
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "completed"})

    await call(client.documents.process(b"%PDF-bytes", poll_interval=0))
    await call(client.documents.process(io.BytesIO(b"%PDF-stream"), filename="mine.pdf", poll_interval=0))

    bodies = [json.loads(c.request.content) for c in create.calls]
    assert bodies == [
        {"filename": "document.pdf", "size_bytes": 10},
        {"filename": "mine.pdf", "size_bytes": 11},
    ]


async def test_process_uses_the_callers_idempotency_key(mock, client):
    create = mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)
    mock.put(url__startswith="https://s3.test/bucket/").respond(200)
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "completed"})

    await call(client.documents.process(b"%PDF-x", idempotency_key="mine-1", poll_interval=0))

    assert create.calls.last.request.headers["Idempotency-Key"] == "mine-1"


async def test_process_rejects_non_pdf_and_bad_input_before_any_request(mock, client):
    with pytest.raises(ValueError, match="PDF files only"):
        await call(client.documents.process(b"x", filename="scan.png"))
    with pytest.raises(TypeError):
        await call(client.documents.process(12345))
    assert mock.calls.call_count == 0


async def test_extract_text_returns_the_text(mock, client):
    mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)
    mock.put(url__startswith="https://s3.test/bucket/").respond(200)
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "completed"})
    mock.get(f"{BASE_URL}/v1/documents/doc_1/output").respond(200, json={"format": "text", "url": "https://s3.test/t.txt", "expires_at": "x"})
    mock.get("https://s3.test/t.txt").respond(200, content=b"page one")

    assert await call(client.extract_text(b"%PDF-1")) == "page one"


def test_base_url_comes_from_the_environment_or_the_argument(monkeypatch):
    monkeypatch.setenv("FASTOCR_BASE_URL", "https://staging.test/")
    assert FastOCR(api_key="k").base_url == "https://staging.test"
    assert AsyncFastOCR(api_key="k", base_url="https://other.test").base_url == "https://other.test"
    monkeypatch.delenv("FASTOCR_BASE_URL")
    assert FastOCR(api_key="k").base_url == "https://api.fastocr.org"


def mock_partial_flow(mock, job_fields):
    mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)
    mock.put(url__startswith="https://s3.test/bucket/").respond(200)
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", **job_fields})
    mock.get(f"{BASE_URL}/v1/documents/doc_1/output").respond(
        200, json={"format": "text", "url": "https://s3.test/t.txt", "expires_at": "x"}
    )
    return mock.get("https://s3.test/t.txt").respond(200, content=b"first pages only")


PARTIAL_JOB = {
    "status": "partial", "is_truncated": True,
    "pages_billed": 5, "pages_processed": 5, "pages_total": 9,
}


@pytest.mark.parametrize(
    "job_fields",
    [PARTIAL_JOB, {**PARTIAL_JOB, "status": "completed"}],
    ids=["status-partial", "completed-but-truncated"],
)
async def test_extract_text_refuses_to_return_truncated_text(mock, client, job_fields):
    download = mock_partial_flow(mock, job_fields)

    with pytest.raises(PartialResultError) as caught:
        await call(client.extract_text(b"%PDF-1"))

    error = caught.value
    assert error.document_id == "doc_1"
    assert (error.pages_processed, error.pages_billed, error.pages_total) == (5, 5, 9)
    assert "5 of 9" in str(error)
    assert not download.called


async def test_the_partial_text_is_still_available_through_get_text(mock, client):
    mock_partial_flow(mock, PARTIAL_JOB)

    with pytest.raises(PartialResultError) as caught:
        await call(client.extract_text(b"%PDF-1"))

    assert await call(client.documents.get_text(caught.value.document_id)) == "first pages only"


async def test_allow_partial_returns_the_partial_text(mock, client):
    mock_partial_flow(mock, PARTIAL_JOB)

    assert await call(client.extract_text(b"%PDF-1", allow_partial=True)) == "first pages only"


async def test_partial_error_copes_with_missing_page_counts(mock, client):
    mock_partial_flow(mock, {"status": "partial"})

    with pytest.raises(PartialResultError) as caught:
        await call(client.extract_text(b"%PDF-1"))

    assert caught.value.pages_total is None
    assert "None" not in str(caught.value)


async def test_process_still_returns_a_partial_job(mock, client):
    mock_partial_flow(mock, PARTIAL_JOB)

    job = await call(client.documents.process(b"%PDF-1", poll_interval=0))

    assert job.is_partial and job.is_completed
    assert (job.pages_billed, job.pages_total) == (5, 9)


async def test_a_complete_document_is_not_flagged_partial(mock, client):
    mock_partial_flow(mock, {"status": "completed", "is_truncated": False, "pages_billed": 9, "pages_total": 9})

    assert await call(client.extract_text(b"%PDF-1")) == "first pages only"


def mock_replayed_create(mock, status):
    mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)
    put = mock.put(url__startswith="https://s3.test/bucket/").respond(200)
    start = mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})
    mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=[
        httpx.Response(200, json={"id": "doc_1", "status": status}),
        httpx.Response(200, json={"id": "doc_1", "status": "completed"}),
    ])
    return put, start


@pytest.mark.parametrize("status", ["processing", "completed", "partial"])
async def test_process_with_a_repeated_key_does_not_upload_or_start_an_already_started_document(mock, client, status):
    put, start = mock_replayed_create(mock, status)

    job = await call(client.documents.process(b"%PDF-1", idempotency_key="k", poll_interval=0))

    assert job.id == "doc_1" and job.is_completed
    assert not put.called and not start.called


async def test_process_with_a_repeated_key_finishes_a_document_that_never_got_uploaded(mock, client):
    put, start = mock_replayed_create(mock, "awaiting_upload")

    job = await call(client.documents.process(b"%PDF-1", idempotency_key="k", poll_interval=0))

    assert job.is_completed and put.called and start.called


async def test_process_with_a_repeated_key_raises_when_the_document_had_failed(mock, client):
    mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json=FAILED_BODY)

    with pytest.raises(DocumentFailedError) as caught:
        await call(client.documents.process(b"%PDF-1", idempotency_key="k", poll_interval=0))

    assert caught.value.document_id == "doc_1" and caught.value.out_of_pages


async def test_process_without_a_key_does_not_look_the_document_up_first(mock, client):
    mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)
    mock.put(url__startswith="https://s3.test/bucket/").respond(200)
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})
    get = mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "completed"})

    await call(client.documents.process(b"%PDF-1", poll_interval=0))

    assert get.call_count == 1


@pytest.mark.parametrize("blank", ["   ", "\n", " \t "])
def test_a_blank_api_key_is_a_missing_key(monkeypatch, blank):
    monkeypatch.delenv("FASTOCR_API_KEY", raising=False)
    for cls in (FastOCR, AsyncFastOCR):
        with pytest.raises(AuthenticationError, match="API key must be provided"):
            cls(api_key=blank)


def test_whitespace_around_a_key_is_trimmed(monkeypatch):
    assert FastOCR(api_key="fok_x\n").api_key == "fok_x"
    monkeypatch.setenv("FASTOCR_API_KEY", " fok_env \n")
    assert AsyncFastOCR().api_key == "fok_env"
