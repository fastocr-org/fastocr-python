"""Both error shapes (handler and API Gateway) and every status-to-exception mapping."""
import httpx
import pytest
import respx

from fastocr_sdk import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    DocumentConflictError,
    FastOCRError,
    NotFoundError,
    RateLimitError,
    ServerError,
)

from conftest import BASE_URL, call


def handler_error(code, message, error_type="invalid_request", request_id="req-1"):
    return {"error": {"type": error_type, "code": code, "message": message, "request_id": request_id}}


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(harness):
    return harness.client(max_retries=0)


@pytest.mark.parametrize(
    "status, body, error_class, code, error_type",
    [
        (400, handler_error("missing_idempotency_key", "Idempotency-Key header is required"), BadRequestError, "missing_idempotency_key", "invalid_request"),
        (404, handler_error("document_not_found", "Document not found", "not_found"), NotFoundError, "document_not_found", "not_found"),
        (409, handler_error("document_not_startable", "cannot be started", "conflict"), DocumentConflictError, "document_not_startable", "conflict"),
        (409, handler_error("idempotency_key_conflict", "already used", "conflict"), DocumentConflictError, "idempotency_key_conflict", "conflict"),
        (429, handler_error("concurrency_limit", "Too many API documents are processing", "rate_limit"), RateLimitError, "concurrency_limit", "rate_limit"),
        (500, handler_error("InternalError", "Internal server error", "error"), ServerError, "InternalError", "error"),
        (418, handler_error("teapot", "short and stout"), FastOCRError, "teapot", "invalid_request"),
    ],
)
async def test_handler_errors_keep_their_nested_fields(mock, client, status, body, error_class, code, error_type):
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(status, json=body)

    with pytest.raises(error_class) as caught:
        await call(client.documents.start("doc_1"))

    error = caught.value
    assert type(error) is error_class
    assert error.status_code == status
    assert error.code == code
    assert error.type == error_type
    assert error.request_id == "req-1"
    assert error.message == body["error"]["message"]
    assert isinstance(error.message, str)
    assert error.details == body
    assert code in str(error) and "req-1" in str(error)


async def test_concurrency_limit_exposes_retry_after(mock, client):
    mock.post(f"{BASE_URL}/v1/documents/doc_1/start").respond(
        429, json=handler_error("concurrency_limit", "busy", "rate_limit"), headers={"Retry-After": "30"}
    )

    with pytest.raises(RateLimitError) as caught:
        await call(client.documents.start("doc_1"))

    assert caught.value.retry_after == 30.0


@pytest.mark.parametrize(
    "status, body, error_class",
    [
        (401, {"message": "Unauthorized"}, AuthenticationError),
        (403, {"message": "User is not authorized to access this resource with an explicit deny"}, AuthenticationError),
        (403, {"message": "Forbidden", "error": {"type": "authentication_error", "code": "forbidden", "message": "Forbidden", "request_id": "gw-1"}}, AuthenticationError),
        (429, {"message": "Too Many Requests"}, RateLimitError),
        (429, {"message": "Limit Exceeded"}, RateLimitError),
    ],
)
async def test_gateway_shapes_map_to_the_same_exceptions(mock, client, status, body, error_class):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(status, json=body)

    with pytest.raises(error_class) as caught:
        await call(client.documents.get("doc_1"))

    error = caught.value
    assert error.status_code == status
    assert error.message == body["message"]
    assert error.code == body.get("error", {}).get("code")
    assert error.request_id == body.get("error", {}).get("request_id")


async def test_gateway_throttle_without_retry_after_has_no_retry_after(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(429, json={"message": "Too Many Requests"})

    with pytest.raises(RateLimitError) as caught:
        await call(client.documents.get("doc_1"))

    assert caught.value.retry_after is None


async def test_request_id_falls_back_to_the_gateway_header(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(
        403, json={"message": "Forbidden"}, headers={"x-amzn-RequestId": "gw-77"}
    )

    with pytest.raises(AuthenticationError) as caught:
        await call(client.documents.get("doc_1"))

    assert caught.value.request_id == "gw-77"


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(502, text="<html>Bad Gateway</html>"), "<html>Bad Gateway</html>"),
        (httpx.Response(503, content=b""), "HTTP 503"),
        (httpx.Response(500, json=["unexpected", "list"]), "HTTP 500"),
        (httpx.Response(500, json={"error": "old style string"}), "old style string"),
        (httpx.Response(500, json={"error": {"code": "x"}}), "HTTP 500"),
    ],
)
async def test_odd_error_bodies_never_crash(mock, client, response, message):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(return_value=response)

    with pytest.raises(ServerError) as caught:
        await call(client.documents.get("doc_1"))

    assert caught.value.message == message


async def test_unknown_error_codes_are_passed_through(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(
        400, json=handler_error("a_code_from_the_future", "new", "new_type")
    )

    with pytest.raises(BadRequestError) as caught:
        await call(client.documents.get("doc_1"))

    assert (caught.value.code, caught.value.type) == ("a_code_from_the_future", "new_type")


async def test_a_2xx_with_a_non_json_body_raises_a_server_error(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(200, text="not json")

    with pytest.raises(ServerError, match="not valid JSON"):
        await call(client.documents.get("doc_1"))


async def test_connection_failures_raise_api_connection_error(mock, client):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(APIConnectionError, match="refused") as caught:
        await call(client.documents.get("doc_1"))

    assert caught.value.status_code is None
    assert isinstance(caught.value, FastOCRError)


def test_every_error_is_a_fastocr_error():
    for error_class in (
        APIConnectionError, AuthenticationError, BadRequestError, DocumentConflictError,
        NotFoundError, RateLimitError, ServerError,
    ):
        assert issubclass(error_class, FastOCRError)


def gateway_body(code, status_message, request_id="gw-req-1"):
    return {
        "message": status_message,
        "error": {"type": "gateway_error", "code": code, "message": status_message, "request_id": request_id},
    }


GATEWAY_CASES = [
    (401, "unauthorized", "Unauthorized", AuthenticationError),
    (403, "access_denied", "User is not authorized to access this resource", AuthenticationError),
    (403, "missing_authentication_token", "Missing Authentication Token", AuthenticationError),
    (429, "throttled", "Too Many Requests", RateLimitError),
    (502, "gateway_error", "Bad gateway", ServerError),
    (504, "gateway_error", "Endpoint request timed out", ServerError),
]


@pytest.mark.parametrize("status, code, message, error_class", GATEWAY_CASES)
async def test_new_gateway_shape_populates_code_type_and_request_id(mock, client, status, code, message, error_class):
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(status, json=gateway_body(code, message))

    with pytest.raises(error_class) as caught:
        await call(client.documents.get("doc_1"))

    error = caught.value
    assert type(error) is error_class
    assert (error.status_code, error.code, error.type) == (status, code, "gateway_error")
    assert error.request_id == "gw-req-1"
    assert error.message == message


@pytest.mark.parametrize("status, code, message, error_class", GATEWAY_CASES)
async def test_old_and_new_gateway_shapes_raise_the_same_class(mock, client, status, code, message, error_class):
    route = mock.get(f"{BASE_URL}/v1/documents/doc_1")
    route.side_effect = [
        httpx.Response(status, json={"message": message}),
        httpx.Response(status, json=gateway_body(code, message)),
    ]

    classes = []
    for _ in range(2):
        with pytest.raises(error_class) as caught:
            await call(client.documents.get("doc_1"))
        classes.append(type(caught.value))

    assert classes == [error_class, error_class]


async def test_a_nested_message_wins_but_the_top_level_message_is_the_fallback(mock, client):
    body = gateway_body("access_denied", "Forbidden")
    body["message"] = "unchanged gateway text"
    mock.get(f"{BASE_URL}/v1/documents/doc_1").respond(403, json=body)

    with pytest.raises(AuthenticationError) as caught:
        await call(client.documents.get("doc_1"))
    assert caught.value.message == "Forbidden"

    del body["error"]["message"]
    mock.get(f"{BASE_URL}/v1/documents/doc_2").respond(403, json=body)
    with pytest.raises(AuthenticationError) as caught:
        await call(client.documents.get("doc_2"))
    assert caught.value.message == "unchanged gateway text"
