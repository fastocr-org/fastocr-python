"""Bounded retries: what is retried, what is not, and that nothing is created twice."""
import json

import httpx
import pytest
import respx

from fastocr_sdk import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    DocumentConflictError,
    NotFoundError,
    RateLimitError,
    ServerError,
    UploadError,
)

from conftest import BASE_URL, call

CREATED = {"id": "doc_1", "status": "awaiting_upload", "upload_url": "https://s3.test/up/doc_1?sig=1", "expires_at": "x"}
BUSY = {"error": {"type": "rate_limit", "code": "concurrency_limit", "message": "busy", "request_id": "r"}}


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


async def test_create_retries_a_429_honoring_retry_after_with_the_same_idempotency_key(mock, harness):
    client = harness.client()
    route = mock.post(f"{BASE_URL}/v1/documents").mock(side_effect=[
        httpx.Response(429, json=BUSY, headers={"Retry-After": "30"}),
        httpx.Response(201, json=CREATED),
    ])

    created = await call(client.documents.create("a.pdf", 10))

    assert created.id == "doc_1"
    assert route.call_count == 2
    assert harness.sleeps == [30.0]
    keys = {c.request.headers["Idempotency-Key"] for c in route.calls}
    assert len(keys) == 1


@pytest.mark.parametrize("status", [502, 503, 504])
async def test_gateway_errors_are_retried_with_growing_backoff(mock, harness, status):
    client = harness.client(max_retries=2)
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=[
        httpx.Response(status, json={"message": "Service Unavailable"}),
        httpx.Response(status, json={"message": "Service Unavailable"}),
        httpx.Response(200, json={"id": "doc_1", "status": "processing"}),
    ])

    job = await call(client.documents.get("doc_1"))

    assert job.id == "doc_1"
    assert route.call_count == 3
    first, second = harness.sleeps
    assert 0.25 <= first <= 0.5
    assert 0.5 <= second <= 1.0


async def test_retries_stop_at_the_limit_and_raise_the_last_error(mock, harness):
    client = harness.client(max_retries=2)
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(503, json={"message": "down"})

    with pytest.raises(ServerError) as caught:
        await call(client.documents.get("doc_1"))

    assert caught.value.status_code == 503
    assert route.call_count == 3
    assert len(harness.sleeps) == 2


async def test_default_is_two_retries(mock, harness):
    client = harness.client()
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(502, json={"message": "bad"})

    with pytest.raises(ServerError):
        await call(client.documents.get("doc_1"))

    assert client.max_retries == 2 and route.call_count == 3


async def test_zero_retries_means_one_attempt(mock, harness):
    client = harness.client(max_retries=0)
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(503, json={"message": "down"})

    with pytest.raises(ServerError):
        await call(client.documents.get("doc_1"))

    assert route.call_count == 1 and harness.sleeps == []


@pytest.mark.parametrize(
    "status, body, error_class",
    [
        (400, {"error": {"type": "invalid_request", "code": "invalid_size_bytes", "message": "m", "request_id": "r"}}, BadRequestError),
        (401, {"message": "Unauthorized"}, AuthenticationError),
        (403, {"message": "Forbidden"}, AuthenticationError),
        (404, {"error": {"type": "not_found", "code": "document_not_found", "message": "m", "request_id": "r"}}, NotFoundError),
        (409, {"error": {"type": "conflict", "code": "document_not_ready", "message": "m", "request_id": "r"}}, DocumentConflictError),
        (500, {"error": {"type": "error", "code": "InternalError", "message": "m", "request_id": "r"}}, ServerError),
    ],
)
async def test_other_errors_are_not_retried(mock, harness, status, body, error_class):
    client = harness.client(max_retries=3)
    route = mock.post(f"{BASE_URL}/v1/documents").respond(status, json=body)

    with pytest.raises(error_class):
        await call(client.documents.create("a.pdf", 10))

    assert route.call_count == 1 and harness.sleeps == []


async def test_connection_errors_are_retried_with_the_same_key(mock, harness):
    client = harness.client()
    route = mock.post(f"{BASE_URL}/v1/documents").mock(side_effect=[
        httpx.ConnectError("reset"),
        httpx.ReadTimeout("slow"),
        httpx.Response(201, json=CREATED),
    ])

    created = await call(client.documents.create("a.pdf", 10))

    assert created.id == "doc_1" and route.call_count == 3
    assert len({c.request.headers["Idempotency-Key"] for c in route.calls}) == 1
    assert len(harness.sleeps) == 2


async def test_connection_errors_exhaust_into_api_connection_error(mock, harness):
    client = harness.client()
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(APIConnectionError):
        await call(client.documents.get("doc_1"))

    assert route.call_count == 3


async def test_retry_after_longer_than_the_cap_is_raised_not_slept(mock, harness):
    client = harness.client()
    route = mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(
        429, json=BUSY, headers={"Retry-After": "3600"}
    )

    with pytest.raises(RateLimitError) as caught:
        await call(client.documents.start("doc_1"))

    assert caught.value.retry_after == 3600.0
    assert route.call_count == 1 and harness.sleeps == []


async def test_an_http_date_retry_after_falls_back_to_backoff(mock, harness):
    client = harness.client()
    mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=[
        httpx.Response(429, json={"message": "slow down"}, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        httpx.Response(200, json={"id": "doc_1", "status": "processing"}),
    ])

    await call(client.documents.get("doc_1"))

    assert 0.5 <= harness.sleeps[0] <= 1.0


async def test_start_retries_the_concurrency_limit(mock, harness):
    client = harness.client()
    route = mock.post(f"{BASE_URL}/v1/documents/doc_1/start").mock(side_effect=[
        httpx.Response(429, json=BUSY, headers={"Retry-After": "30"}),
        httpx.Response(202, json={"status": "processing"}),
    ])

    assert await call(client.documents.start("doc_1")) == {"status": "processing"}
    assert route.call_count == 2 and harness.sleeps == [30.0]


async def test_a_retried_process_reuses_one_idempotency_key_across_every_create_attempt(mock, harness):
    client = harness.client()
    create = mock.post(f"{BASE_URL}/v1/documents").mock(side_effect=[
        httpx.Response(503, json={"message": "down"}),
        httpx.ConnectError("reset"),
        httpx.Response(201, json=CREATED),
    ])
    mock.put(url__startswith="https://s3.test/up/").respond(200)
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "completed"})

    await call(client.documents.process(b"%PDF-1", poll_interval=0))

    keys = [c.request.headers["Idempotency-Key"] for c in create.calls]
    assert len(keys) == 3 and len(set(keys)) == 1
    assert len({c.request.content for c in create.calls}) == 1


async def test_two_separate_process_calls_use_different_keys(mock, harness):
    client = harness.client()
    create = mock.post(f"{BASE_URL}/v1/documents").respond(201, json=CREATED)
    mock.put(url__startswith="https://s3.test/up/").respond(200)
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(202, json={"status": "processing"})
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, json={"id": "doc_1", "status": "completed"})

    await call(client.documents.process(b"%PDF-1", poll_interval=0))
    await call(client.documents.process(b"%PDF-1", poll_interval=0))

    keys = [c.request.headers["Idempotency-Key"] for c in create.calls]
    assert len(set(keys)) == 2


async def test_upload_is_retried_and_resends_the_whole_file(mock, harness, tmp_path):
    client = harness.client()
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-" + b"x" * 5000)
    put = mock.put("https://s3.test/up/doc_1").mock(side_effect=[
        httpx.Response(503, content=b"<Error><Code>SlowDown</Code></Error>"),
        httpx.ConnectError("reset"),
        httpx.Response(200),
    ])

    await call(client.documents.upload("https://s3.test/up/doc_1", pdf))

    assert put.call_count == 3
    assert {c.request.content for c in put.calls} == {pdf.read_bytes()}
    assert {c.request.headers["Content-Length"] for c in put.calls} == {str(pdf.stat().st_size)}


async def test_upload_failures_that_will_not_heal_are_raised_with_the_s3_code(mock, harness):
    client = harness.client()
    put = mock.put("https://s3.test/up/doc_1").respond(
        403, content=b"<Error><Code>SignatureDoesNotMatch</Code><Message>no</Message></Error>"
    )

    with pytest.raises(UploadError) as caught:
        await call(client.documents.upload("https://s3.test/up/doc_1", b"%PDF-1"))

    assert put.call_count == 1
    assert (caught.value.status_code, caught.value.code) == (403, "SignatureDoesNotMatch")


async def test_upload_connection_failures_raise_upload_error(mock, harness):
    client = harness.client(max_retries=1)
    put = mock.put("https://s3.test/up/doc_1").mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(UploadError):
        await call(client.documents.upload("https://s3.test/up/doc_1", b"%PDF-1"))

    assert put.call_count == 2


async def test_pdf_download_to_a_path_is_retried(mock, harness, tmp_path):
    client = harness.client()
    mock.get(f"{BASE_URL}/v1/documents/doc_1/output").respond(200, json={"format": "pdf", "url": "https://s3.test/r.pdf", "expires_at": "x"})
    download = mock.get("https://s3.test/r.pdf").mock(side_effect=[
        httpx.Response(503, content=b"slow down"),
        httpx.Response(200, content=b"%PDF-ok"),
    ])
    destination = tmp_path / "r.pdf"

    await call(client.documents.download_searchable_pdf("doc_1", destination))

    assert download.call_count == 2 and destination.read_bytes() == b"%PDF-ok"
    assert [p.name for p in tmp_path.iterdir()] == ["r.pdf"]


async def test_text_download_is_retried(mock, harness):
    client = harness.client()
    mock.get(f"{BASE_URL}/v1/documents/doc_1/output").respond(200, json={"format": "text", "url": "https://s3.test/t.txt", "expires_at": "x"})
    download = mock.get("https://s3.test/t.txt").mock(side_effect=[
        httpx.ConnectError("reset"),
        httpx.Response(200, content=b"hello"),
    ])

    assert await call(client.documents.get_text("doc_1")) == "hello"
    assert download.call_count == 2


async def test_polling_survives_a_transient_gateway_error(mock, harness):
    client = harness.client()
    mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=[
        httpx.Response(504, json={"message": "Endpoint request timed out"}),
        httpx.Response(200, json={"id": "doc_1", "status": "completed"}),
    ])

    job = await call(client.documents.wait_for_completion("doc_1"))

    assert job.is_completed


async def test_the_request_body_is_identical_on_every_attempt(mock, harness):
    client = harness.client()
    route = mock.post(f"{BASE_URL}/v1/documents").mock(side_effect=[
        httpx.Response(502, json={"message": "bad"}),
        httpx.Response(201, json=CREATED),
    ])

    await call(client.documents.create("a.pdf", 10, external_id="e1", sha256="h="))

    bodies = [json.loads(c.request.content) for c in route.calls]
    assert bodies[0] == bodies[1] == {"filename": "a.pdf", "size_bytes": 10, "external_id": "e1", "sha256": "h="}


THROTTLED = {
    "message": "Too Many Requests",
    "error": {"type": "gateway_error", "code": "throttled", "message": "Too many requests", "request_id": "gw-1"},
}


@pytest.mark.parametrize("body", [{"message": "Too Many Requests"}, THROTTLED], ids=["old-shape", "new-shape"])
async def test_a_gateway_throttle_without_retry_after_backs_off_and_does_not_wait_30_seconds(mock, harness, body):
    client = harness.client(max_retries=2)
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=[
        httpx.Response(429, json=body),
        httpx.Response(429, json=body),
        httpx.Response(200, json={"id": "doc_1", "status": "processing"}),
    ])

    await call(client.documents.get("doc_1"))

    assert route.call_count == 3
    first, second = harness.sleeps
    assert 0.5 <= first <= 1.0
    assert 1.0 <= second <= 2.0


@pytest.mark.parametrize("body", [{"message": "Too Many Requests"}, THROTTLED], ids=["old-shape", "new-shape"])
async def test_a_persistent_gateway_throttle_ends_in_rate_limit_error_with_the_gateway_fields(mock, harness, body):
    client = harness.client(max_retries=2)
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(429, json=body)

    with pytest.raises(RateLimitError) as caught:
        await call(client.documents.get("doc_1"))

    assert route.call_count == 3
    assert caught.value.retry_after is None
    assert caught.value.code == body.get("error", {}).get("code")


NOT_STARTABLE = {"error": {"type": "conflict", "code": "document_not_startable", "message": "m", "request_id": "r"}}


async def test_a_retried_start_that_gets_409_still_raises(mock, harness):
    client = harness.client()
    route = mock.post(f"{BASE_URL}/v1/documents/doc_1/start").mock(side_effect=[
        httpx.Response(503, json={"message": "down"}),
        httpx.Response(409, json=NOT_STARTABLE),
    ])

    with pytest.raises(DocumentConflictError) as caught:
        await call(client.documents.start("doc_1"))

    assert caught.value.code == "document_not_startable"
    assert route.call_count == 2


async def test_a_retried_start_that_gets_202_succeeds(mock, harness):
    client = harness.client()
    route = mock.post(f"{BASE_URL}/v1/documents/doc_1/start").mock(side_effect=[
        httpx.ConnectError("reset"),
        httpx.Response(202, json={"status": "processing"}),
    ])

    assert await call(client.documents.start("doc_1")) == {"status": "processing"}
    assert route.call_count == 2
