"""Bank statement routes: models, the three-call extract_statement flow, and downloads."""
import json

import httpx
import pytest
import respx

from conftest import BASE_URL, call
from fastocr_sdk import (
    DocumentFailedError,
    PollingTimeoutError,
    StatementFailedError,
)

S3 = "https://s3.test/bucket/"
URL = f"{BASE_URL}/v1/bank-statements"
CREATED = {
    "id": "stmt_1",
    "status": "awaiting_upload",
    "upload_url": f"{S3}uploads/original.pdf?X-Amz-Signature=sig",
    "expires_at": "2026-11-05T12:00:00+00:00",
}
COMPLETED = {
    "id": "stmt_1",
    "status": "completed",
    "external_id": "acct-9",
    "pages_billed": 3,
    "pages_total": 3,
    "has_transactions": True,
    "needs_review": False,
    "coverage": {"first": "2026-10-01"},
    "outputs": {
        "json": {"available": True, "url": "https://s3.test/out.json", "expires_at": "2026-11-05T13:00:00+00:00"},
        "xlsx": {"available": True, "url": "https://s3.test/out.xlsx", "expires_at": "2026-11-05T13:00:00+00:00"},
        "qbo": {"available": True, "accounts": [1, 2], "files": [
            {"account": 1, "url": "https://s3.test/1.qbo", "expires_at": "2026-11-05T13:00:00+00:00"},
            {"account": 2, "url": "https://s3.test/2.qbo", "expires_at": "2026-11-05T13:00:00+00:00"},
        ]},
    },
}


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "statement.pdf"
    path.write_bytes(b"%PDF-1.4 body")
    return path


async def test_create_sends_the_documented_body_and_rejects_unsupported_types(mock, client):
    route = mock.post(URL).respond(201, json=CREATED)

    created = await call(client.bank_statements.create("s.pdf", 10, idempotency_key="k", external_id="e"))

    request = route.calls.last.request
    assert json.loads(request.content) == {"filename": "s.pdf", "size_bytes": 10, "external_id": "e"}
    assert request.headers["Idempotency-Key"] == "k"
    assert (created.id, created.upload_url) == ("stmt_1", CREATED["upload_url"])
    with pytest.raises(ValueError, match="PDF, PNG, JPG, JPEG and WebP"):
        await call(client.bank_statements.create("s.gif", 10))
    assert route.call_count == 1


async def test_get_parses_outputs_for_every_shape_and_keeps_unknown_fields(mock, client):
    mock.get(f"{URL}/stmt_1").respond(200, json={**COMPLETED, "new_field": 1})

    job = await call(client.bank_statements.get("stmt_1"))

    assert job.is_completed and not job.is_failed
    assert (job.pages_billed, job.needs_review, job.has_transactions) == (3, False, True)
    assert job.outputs["xlsx"].url == "https://s3.test/out.xlsx"
    assert job.outputs["qbo"].accounts == [1, 2]
    assert [(f.account, f.url) for f in job.outputs["qbo"].files] == [(1, "https://s3.test/1.qbo"), (2, "https://s3.test/2.qbo")]
    assert job.raw["new_field"] == 1


async def test_get_reads_a_list_response_without_links(mock, client):
    mock.get(f"{URL}/stmt_1").respond(200, json={
        "id": "stmt_1", "status": "completed",
        "outputs": {"csv": {"available": True}, "qbo": {"available": True, "accounts": [1, 2]}},
    })

    job = await call(client.bank_statements.get("stmt_1"))

    assert job.outputs["csv"].available and job.outputs["csv"].url is None
    assert job.outputs["qbo"].accounts == [1, 2] and job.outputs["qbo"].files == []


async def test_list_returns_statements_and_the_cursor(mock, client):
    route = mock.get(URL).respond(200, json={"bank_statements": [COMPLETED], "next_cursor": "c2"})

    page = await call(client.bank_statements.list(limit=5, status="completed"))

    assert dict(route.calls.last.request.url.params) == {"limit": "5", "status": "completed"}
    assert (page.bank_statements[0].id, page.next_cursor) == ("stmt_1", "c2")


async def test_start_and_delete_use_their_routes(mock, client):
    start = mock.post(f"{URL}/stmt_1/start").respond(202, json={"status": "processing"})
    delete = mock.delete(f"{URL}/stmt_1").respond(202, json={"status": "deleting"})

    assert (await call(client.bank_statements.start("stmt_1")))["status"] == "processing"
    assert (await call(client.bank_statements.delete("stmt_1")))["status"] == "deleting"
    assert start.called and delete.called


async def test_output_url_sends_format_and_account_and_validates_locally(mock, client):
    route = mock.get(f"{URL}/stmt_1/output").respond(200, json={
        "format": "qbo", "schema": "3", "url": "https://s3.test/2.qbo", "expires_at": "x", "pages_billed": 3,
    })

    out = await call(client.bank_statements.get_output_url("stmt_1", "qbo", account=2))

    assert dict(route.calls.last.request.url.params) == {"format": "qbo", "account": "2"}
    assert (out.url, out.schema, out.pages_billed) == ("https://s3.test/2.qbo", "3", 3)
    for args in (("txt", None), ("xlsx", 1), ("qbo", 0), ("qbo", True)):
        with pytest.raises(ValueError):
            await call(client.bank_statements.get_output_url("stmt_1", *args))
    assert route.call_count == 1


async def test_download_save_and_json_fetch_the_linked_file(mock, client, tmp_path):
    mock.get(f"{URL}/stmt_1/output").mock(side_effect=lambda r: httpx.Response(200, json={
        "format": r.url.params["format"], "url": f"{S3}file.{r.url.params['format']}", "expires_at": "x",
    }))
    mock.get(f"{S3}file.xlsx").respond(200, content=b"workbook")
    mock.get(f"{S3}file.json").respond(200, content=json.dumps({"schema": "3", "accounts": []}).encode())

    assert await call(client.bank_statements.download("stmt_1", "xlsx")) == b"workbook"
    saved = await call(client.bank_statements.save("stmt_1", tmp_path / "out.xlsx", "xlsx"))
    assert saved.read_bytes() == b"workbook"
    assert (await call(client.bank_statements.json("stmt_1")))["schema"] == "3"
    assert [p.name for p in tmp_path.iterdir()] == ["out.xlsx"]


async def test_extract_statement_runs_create_upload_poll_without_start(mock, harness, pdf):
    client = harness.client()
    order = []
    create = mock.post(URL).mock(side_effect=lambda r: (order.append("create"), httpx.Response(201, json=CREATED))[1])
    upload = mock.put(url__startswith=S3).mock(side_effect=lambda r: (order.append("upload"), httpx.Response(200))[1])
    start = mock.post(f"{URL}/stmt_1/start").respond(202, json={})
    mock.get(f"{URL}/stmt_1").mock(side_effect=[
        httpx.Response(200, json={"id": "stmt_1", "status": "queued"}, headers={"Retry-After": "30"}),
        httpx.Response(200, json={"id": "stmt_1", "status": "processing"}),
        httpx.Response(200, json=COMPLETED),
    ])

    job = await call(client.extract_statement(pdf, poll_interval=2, external_id="acct-9"))

    assert job.outputs["xlsx"].url == "https://s3.test/out.xlsx"
    assert order == ["create", "upload"] and not start.called
    assert json.loads(create.calls.last.request.content) == {
        "filename": "statement.pdf", "size_bytes": 13, "external_id": "acct-9",
    }
    put = upload.calls.last.request
    assert (put.headers["Content-Type"], put.headers["Content-Length"]) == ("application/pdf", "13")
    assert harness.sleeps == [30, 3]


async def test_extract_statement_starts_the_job_itself_on_a_server_that_does_not(mock, harness, pdf):
    client = harness.client()
    mock.post(URL).respond(201, json=CREATED)
    mock.put(url__startswith=S3).respond(200)
    start = mock.post(f"{URL}/stmt_1/start").respond(202, json={"status": "processing"})
    mock.get(f"{URL}/stmt_1").mock(side_effect=[
        httpx.Response(200, json={"id": "stmt_1", "status": "awaiting_upload"}),
        httpx.Response(200, json=COMPLETED),
    ])

    job = await call(client.extract_statement(pdf))

    assert job.is_completed and start.call_count == 1


async def test_a_full_start_queue_keeps_polling(mock, harness, pdf):
    client = harness.client(max_retries=0)
    mock.post(URL).respond(201, json=CREATED)
    mock.put(url__startswith=S3).respond(200)
    mock.post(f"{URL}/stmt_1/start").respond(
        429, json={"error": {"type": "rate_limit", "code": "concurrency_limit", "message": "busy"}},
        headers={"Retry-After": "20"},
    )
    mock.get(f"{URL}/stmt_1").mock(side_effect=[
        httpx.Response(200, json={"id": "stmt_1", "status": "awaiting_upload"}),
        httpx.Response(200, json=COMPLETED),
    ])

    job = await call(client.extract_statement(pdf))

    assert job.is_completed and harness.sleeps == [20]


async def test_extract_statement_sends_images_with_their_content_type_and_bytes_need_a_filename(mock, client):
    mock.post(URL).respond(201, json=CREATED)
    upload = mock.put(url__startswith=S3).respond(200)
    mock.get(f"{URL}/stmt_1").respond(200, json=COMPLETED)

    await call(client.extract_statement(b"\x89PNG", filename="scan.png"))

    assert upload.calls.last.request.headers["Content-Type"] == "image/png"
    with pytest.raises(ValueError):
        await call(client.extract_statement(b"GIF", filename="scan.gif"))


async def test_a_repeated_key_for_an_uploaded_statement_only_waits(mock, client, pdf):
    mock.post(URL).respond(201, json=CREATED)
    upload = mock.put(url__startswith=S3).respond(200)
    mock.get(f"{URL}/stmt_1").respond(200, json=COMPLETED)

    job = await call(client.extract_statement(pdf, idempotency_key="k"))

    assert job.is_completed and not upload.called


@pytest.mark.parametrize("error, code, retryable", [
    ({"type": "insufficient_credits", "code": "buy_pages", "message": "Out of pages", "retryable": False}, "buy_pages", False),
    ({"type": "x", "code": "a_future_code", "message": "New", "retryable": False}, "a_future_code", False),
    (None, "processing_failed", True),
])
async def test_a_failed_statement_raises_the_typed_error(mock, client, pdf, error, code, retryable):
    mock.post(URL).respond(201, json=CREATED)
    mock.put(url__startswith=S3).respond(200)
    body = {"id": "stmt_1", "status": "failed", **({"error": error} if error else {})}
    mock.get(f"{URL}/stmt_1").respond(200, json=body)

    with pytest.raises(StatementFailedError) as caught:
        await call(client.extract_statement(pdf))

    assert isinstance(caught.value, DocumentFailedError)
    assert (caught.value.statement_id, caught.value.code, caught.value.retryable) == ("stmt_1", code, retryable)
    assert caught.value.out_of_pages == (code == "buy_pages")


async def test_extract_statement_times_out_with_the_last_status(mock, client, pdf):
    mock.post(URL).respond(201, json=CREATED)
    mock.put(url__startswith=S3).respond(200)
    mock.get(f"{URL}/stmt_1").respond(200, json={"id": "stmt_1", "status": "queued"})

    with pytest.raises(PollingTimeoutError) as caught:
        await call(client.extract_statement(pdf, timeout=0))

    assert caught.value.last_status == "queued"
