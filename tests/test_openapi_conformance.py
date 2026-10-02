"""Checks the SDK against the OpenAPI spec, the single source of truth for the API.

The spec is frontend/openapi/fastocr-v1.json in the monorepo and openapi/fastocr-v1.json
in the exported public repo. Without it, every test here is skipped.
"""
import dataclasses
import json
import re
import typing
from pathlib import Path

import pytest
import respx

from fastocr_sdk import (
    AuthenticationError,
    BadRequestError,
    DocumentConflictError,
    DocumentCreateResponse,
    DocumentJob,
    DocumentListResponse,
    DocumentOutputs,
    DocumentOutputUrl,
    FastOCR,
    JobError,
    NotFoundError,
    RateLimitError,
    ServerError,
)

from conftest import call


def find_spec_path():
    here = Path(__file__).resolve()
    candidates = [here.parent.parent / "openapi" / "fastocr-v1.json"]
    candidates += [parent / "frontend" / "openapi" / "fastocr-v1.json" for parent in here.parents]
    return next((path for path in candidates if path.is_file()), None)


SPEC_PATH = find_spec_path()
SPEC = json.loads(SPEC_PATH.read_text()) if SPEC_PATH else {"paths": {}, "components": {"schemas": {}}}

pytestmark = pytest.mark.skipif(
    SPEC_PATH is None,
    reason="OpenAPI spec not found (openapi/fastocr-v1.json or frontend/openapi/fastocr-v1.json)",
)

CALLS = {
    "createDocument": lambda docs: docs.create(
        "scan.pdf", 1024, idempotency_key="key-1", external_id="order-1", sha256="n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg="
    ),
    "listDocuments": lambda docs: docs.list(limit=5, status="completed", cursor="cur", updated_since="2026-10-01"),
    "getDocument": lambda docs: docs.get("doc-1"),
    "deleteDocument": lambda docs: docs.delete("doc-1"),
    "startDocument": lambda docs: docs.start("doc-1"),
    "getDocumentOutput": lambda docs: docs.get_output_url("doc-1", "pdf"),
}

SUCCESS_TYPES = {
    "createDocument": DocumentCreateResponse,
    "listDocuments": DocumentListResponse,
    "getDocument": DocumentJob,
    "deleteDocument": dict,
    "startDocument": dict,
    "getDocumentOutput": DocumentOutputUrl,
}

ERROR_CLASSES = {
    400: BadRequestError,
    401: AuthenticationError,
    403: AuthenticationError,
    404: NotFoundError,
    409: DocumentConflictError,
    429: RateLimitError,
    500: ServerError,
}

MODEL_SCHEMAS = [
    (DocumentCreateResponse, "CreateResponse"),
    (DocumentJob, "DocumentStatusResponse"),
    (DocumentOutputUrl, "OutputResponse"),
    (DocumentListResponse, "DocumentListResponse"),
    (JobError, "JobError"),
]

OPERATIONS = [
    (method.upper(), path, operation)
    for path, methods in SPEC["paths"].items()
    for method, operation in methods.items()
]


STRUCTURAL_KEYWORDS = {
    "$ref", "type", "properties", "required", "items", "enum", "nullable", "minimum", "maximum",
    "minLength", "format", "additionalProperties", "anyOf", "oneOf",
}
ANNOTATION_KEYWORDS = {"description", "example", "examples", "default", "title"}
SUPPORTED_TYPES = {"string", "integer", "boolean", "object", "array"}
SUPPORTED_FORMATS = {"uri", "date-time", "int64"}


def check_schema(schema, where="schema"):
    """Fail loudly on anything the validator and sample generator would otherwise skip."""
    unknown = set(schema) - STRUCTURAL_KEYWORDS - ANNOTATION_KEYWORDS
    assert not unknown, f"{where}: unsupported schema keyword(s) {sorted(unknown)}"
    if "type" in schema:
        assert schema["type"] in SUPPORTED_TYPES, f"{where}: unsupported type {schema['type']!r}"
    if "format" in schema:
        assert schema["format"] in SUPPORTED_FORMATS, f"{where}: unsupported format {schema['format']!r}"
    if "additionalProperties" in schema:
        assert isinstance(schema["additionalProperties"], bool), f"{where}: unsupported additionalProperties (only booleans are understood)"
    for name, prop in schema.get("properties", {}).items():
        check_schema(prop, f"{where}.{name}")
    if "items" in schema:
        check_schema(schema["items"], f"{where}[]")
    for keyword in ("anyOf", "oneOf"):
        for index, branch in enumerate(schema.get(keyword, [])):
            check_schema(branch, f"{where}.{keyword}[{index}]")


def resolve(schema):
    while "$ref" in schema:
        schema = SPEC["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    check_schema(schema)
    return schema


def validate(schema, value, where="body"):
    schema = resolve(schema)
    for keyword, expected_matches in (("anyOf", lambda n: n >= 1), ("oneOf", lambda n: n == 1)):
        if keyword in schema:
            matching = sum(not validate(branch, value, where) for branch in schema[keyword])
            return [] if expected_matches(matching) else [f"{where}: matches {matching} {keyword} branches"]
    if value is None:
        return [] if schema.get("nullable") else [f"{where}: null is not allowed"]
    kind = schema.get("type")
    errors = []
    if kind == "string" and not isinstance(value, str):
        errors.append(f"{where}: expected string")
    elif kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        errors.append(f"{where}: expected integer")
    elif kind == "boolean" and not isinstance(value, bool):
        errors.append(f"{where}: expected boolean")
    elif kind == "array":
        errors += validate_array(schema, value, where)
    elif kind == "object":
        errors += validate_object(schema, value, where)
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in enum")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: above maximum")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{where}: too short")
    return errors


def validate_array(schema, value, where):
    if not isinstance(value, list):
        return [f"{where}: expected array"]
    return [e for i, item in enumerate(value) for e in validate(schema["items"], item, f"{where}[{i}]")]


def validate_object(schema, value, where):
    if not isinstance(value, dict):
        return [f"{where}: expected object"]
    properties = schema.get("properties", {})
    errors = [f"{where}.{name}: required but missing" for name in schema.get("required", []) if name not in value]
    for name, item in value.items():
        if name in properties:
            errors += validate(properties[name], item, f"{where}.{name}")
        elif schema.get("additionalProperties") is not True:
            errors.append(f"{where}.{name}: not in the spec")
    return errors


def branch_name(branch):
    return branch["$ref"].rsplit("/", 1)[1] if "$ref" in branch else None


def samples(schema, branch=None):
    """Every shape a payload of this schema can take, as (value, anyOf/oneOf branch name) pairs.

    Unions yield every branch. Objects yield a full payload, plus variants that swap in
    each alternative of one property, plus variants that omit each optional property.
    """
    schema_in = schema
    schema = resolve(schema)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            return [pair for b in schema[keyword] for pair in samples(b, branch_name(b))]
    return [(value, branch or branch_name(schema_in)) for value in plain_samples(schema)]


def plain_samples(schema):
    if "enum" in schema:
        return [schema["enum"][0]]
    kind = schema.get("type")
    if kind == "object":
        return object_samples(schema)
    if kind == "array":
        return [[value] for value, _ in samples(schema["items"])]
    if kind == "integer":
        return [max(schema.get("minimum", 1), 1)]
    if kind == "boolean":
        return [True]
    formats = {"uri": "https://example.test/file", "date-time": "2026-10-01T12:00:00+00:00"}
    return [formats.get(schema.get("format"), "text")]


def object_samples(schema):
    properties = schema.get("properties", {})
    options = {name: [value for value, _ in samples(prop)] for name, prop in properties.items()}
    base = {name: values[0] for name, values in options.items()}
    variants = [base]
    for name, values in options.items():
        variants += [{**base, name: alternative} for alternative in values[1:]]
        if name not in schema.get("required", []):
            variants.append({key: value for key, value in base.items() if key != name})
    return variants


def sample(schema):
    return samples(schema)[0][0]


def schema_of(response_spec):
    content = response_spec.get("content", {}).get("application/json")
    return content["schema"] if content else None


def path_regex(path):
    return re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


def operation_id(operation):
    return operation["operationId"]


def test_the_spec_was_loaded():
    assert OPERATIONS, f"no operations found in {SPEC_PATH}"


@pytest.mark.parametrize("method, path, operation", OPERATIONS, ids=[o[2]["operationId"] for o in OPERATIONS])
class TestOperation:
    async def test_has_an_sdk_method_that_calls_the_right_route(self, mock, harness, method, path, operation):
        assert operation_id(operation) in CALLS, f"{operation_id(operation)} has no SDK method"
        route = mock.route().respond(200, json={})

        await call(CALLS[operation_id(operation)](harness.client(max_retries=0).documents))

        request = route.calls.last.request
        assert request.method == method
        assert path_regex(path).match(request.url.path)

    async def test_builds_a_request_that_matches_the_spec(self, mock, harness, method, path, operation):
        route = mock.route().respond(200, json={})
        await call(CALLS[operation_id(operation)](harness.client(max_retries=0).documents))
        request = route.calls.last.request

        parameters = operation.get("parameters", [])
        for place, source in (("header", request.headers), ("query", request.url.params)):
            declared = {p["name"]: p for p in parameters if p["in"] == place}
            for name, parameter in declared.items():
                if parameter.get("required"):
                    assert name in source, f"required {place} parameter {name} not sent"
                if name in source:
                    assert not validate(parameter["schema"], coerce(parameter["schema"], source[name]), name)
            if place == "query":
                assert set(source.keys()) == set(declared), "query parameters differ from the spec"

        body_spec = operation.get("requestBody")
        if body_spec:
            schema = body_spec["content"]["application/json"]["schema"]
            assert validate(schema, json.loads(request.content)) == []
        else:
            assert not request.content

    @pytest.mark.parametrize("status", ["2xx", "4xx", "5xx"])
    async def test_documented_responses_map_to_the_intended_outcome(
        self, mock, harness, method, path, operation, status
    ):
        responses = {code: spec for code, spec in operation["responses"].items() if code[0] == status[0]}
        for code, response_spec in responses.items():
            schema = schema_of(response_spec)
            variants = samples(schema) if schema else [({}, None)]
            for body, branch in variants:
                headers = documented_headers(response_spec, branch)
                mock.reset()
                mock.route().respond(int(code), json=body, headers=headers)
                docs = harness.client(max_retries=0).documents
                await check_outcome(operation, status, int(code), body, headers, docs)


async def check_outcome(operation, status, code, body, headers, docs):
    call_sdk = CALLS[operation_id(operation)]
    where = f"{operation_id(operation)} {code} {body}"
    if status == "2xx":
        result = await call(call_sdk(docs))
        assert isinstance(result, SUCCESS_TYPES[operation_id(operation)]), where
        return

    assert code in ERROR_CLASSES, f"{code} on {operation_id(operation)} has no intended exception"
    with pytest.raises(ERROR_CLASSES[code]) as caught:
        await call(call_sdk(docs))
    error = caught.value
    assert type(error) is ERROR_CLASSES[code], where
    assert error.status_code == code
    detail = body.get("error") if isinstance(body.get("error"), dict) else {}
    assert error.code == detail.get("code"), where
    assert error.type == detail.get("type"), where
    assert error.request_id == detail.get("request_id"), where
    assert error.message == (detail.get("message") or body.get("message") or f"HTTP {code}"), where
    if code == 429:
        assert error.retry_after == (float(headers["Retry-After"]) if "Retry-After" in headers else None), where


def documented_headers(response_spec, branch):
    """Handler responses carry the documented headers; a gateway-generated body carries none."""
    if branch and branch.startswith("Gateway"):
        return {}
    return {name: str(sample(header["schema"])) for name, header in response_spec.get("headers", {}).items()}


def test_start_429_has_a_handler_branch_with_retry_after_and_a_gateway_branch_without():
    spec = SPEC["paths"]["/v1/documents/{documentId}/start"]["post"]["responses"]["429"]
    names = [branch_name(b) for b in schema_of(spec)["anyOf"]]
    assert names == ["ErrorResponse", "GatewayError"]
    assert "Retry-After" in spec["headers"]
    client = FastOCR(api_key="k", base_url="https://api.test", max_retries=0)
    handler = {"error": {"type": "rate_limit", "code": "concurrency_limit", "message": "busy", "request_id": "r"}}
    gateway_old = {"message": "Too Many Requests"}
    gateway_new = {"message": "Too Many Requests", "error": {"type": "gateway_error", "code": "throttled", "message": "Too Many Requests", "request_id": "g"}}
    outcomes = []
    with respx.mock(assert_all_called=False) as router:
        for body, headers in ((handler, {"Retry-After": "30"}), (gateway_old, {}), (gateway_new, {})):
            router.reset()
            router.post("https://api.test/v1/documents/d/start").respond(429, json=body, headers=headers)
            with pytest.raises(RateLimitError) as caught:
                client.documents.start("d")
            outcomes.append((type(caught.value), caught.value.retry_after, caught.value.code))
    assert outcomes == [
        (RateLimitError, 30.0, "concurrency_limit"),
        (RateLimitError, None, None),
        (RateLimitError, None, "throttled"),
    ]


def test_every_schema_in_the_spec_uses_only_supported_keywords():
    for name, schema in SPEC["components"]["schemas"].items():
        check_schema(schema, name)
    for path, methods in SPEC["paths"].items():
        for method, operation in methods.items():
            where = f"{method.upper()} {path}"
            for parameter in operation.get("parameters", []):
                check_schema(parameter["schema"], f"{where} parameter {parameter['name']}")
            body = operation.get("requestBody")
            if body:
                check_schema(body["content"]["application/json"]["schema"], f"{where} request")
            for code, response in operation["responses"].items():
                if schema_of(response):
                    check_schema(schema_of(response), f"{where} {code}")
                for name, header in response.get("headers", {}).items():
                    check_schema(header["schema"], f"{where} {code} header {name}")


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "pattern": "^a"},
        {"allOf": [{"type": "string"}]},
        {"type": "number"},
        {"type": "string", "format": "email"},
        {"type": "object", "properties": {"a": {"type": "array", "minItems": 1, "items": {"type": "string"}}}},
        {"type": "object", "additionalProperties": {"type": "string"}},
    ],
)
def test_the_helpers_refuse_schema_keywords_they_do_not_understand(schema):
    with pytest.raises(AssertionError, match="unsupported"):
        validate(schema, "x")
    with pytest.raises(AssertionError, match="unsupported"):
        samples(schema)


def test_unions_are_exercised_branch_by_branch():
    union = {"anyOf": [
        {"$ref": "#/components/schemas/ErrorResponse"},
        {"$ref": "#/components/schemas/GatewayError"},
    ]}
    branches = {branch for _, branch in samples(union)}
    bodies = [value for value, _ in samples(union)]

    assert branches == {"ErrorResponse", "GatewayError"}
    assert any("error" in b and "message" not in b for b in bodies)
    assert any(set(b) == {"message"} for b in bodies), "the old gateway shape (no error object) is exercised"
    assert any({"message", "error"} <= set(b) for b in bodies), "the new gateway shape is exercised"
    assert all(not validate(union, b) for b in bodies)
    assert validate(union, {"unrelated": 1})


def coerce(schema, text):
    return int(text) if resolve(schema).get("type") == "integer" else text


def python_type_matches(annotation, schema):
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        inner = [a for a in typing.get_args(annotation) if a is not type(None)][0]
        return python_type_matches(inner, schema)
    schema = resolve(schema)
    if origin in (list, typing.List):
        return schema.get("type") == "array" and python_type_matches(typing.get_args(annotation)[0], schema["items"])
    if dataclasses.is_dataclass(annotation):
        return schema.get("type") == "object"
    return {str: "string", int: "integer", bool: "boolean"}.get(annotation) == schema.get("type")


def model_fields(model):
    hints = typing.get_type_hints(model)
    return {f.name: hints[f.name] for f in dataclasses.fields(model) if f.name != "raw"}


@pytest.mark.parametrize("model, schema_name", MODEL_SCHEMAS, ids=[m[1] for m in MODEL_SCHEMAS])
class TestModel:
    def test_every_field_the_sdk_reads_exists_in_the_schema_with_a_compatible_type(self, model, schema_name):
        properties = resolve({"$ref": f"#/components/schemas/{schema_name}"})["properties"]
        for name, annotation in model_fields(model).items():
            assert name in properties, f"{model.__name__}.{name} is not in {schema_name}"
            assert python_type_matches(annotation, properties[name]), f"{model.__name__}.{name} type differs"

    def test_optional_fields_are_optional_or_nullable_in_the_schema(self, model, schema_name):
        schema = resolve({"$ref": f"#/components/schemas/{schema_name}"})
        for name, annotation in model_fields(model).items():
            if typing.get_origin(annotation) is typing.Union:
                prop = resolve(schema["properties"][name])
                assert name not in schema.get("required", []) or prop.get("nullable"), name

    def test_required_schema_fields_are_modelled(self, model, schema_name):
        schema = resolve({"$ref": f"#/components/schemas/{schema_name}"})
        assert set(schema.get("required", [])) <= set(model_fields(model)), schema_name

    def test_a_schema_shaped_payload_is_read_back_field_for_field(self, model, schema_name):
        payload = sample({"$ref": f"#/components/schemas/{schema_name}"})
        parsed = model.from_dict(payload)
        for name in model_fields(model):
            if name in payload and not isinstance(payload[name], (dict, list)):
                assert getattr(parsed, name) == payload[name], f"{model.__name__}.{name}"


def test_outputs_flags_follow_the_spec_property_names():
    properties = resolve({"$ref": "#/components/schemas/DocumentOutputs"})["properties"]
    flags = {name.removesuffix("_available") for name in model_fields(DocumentOutputs)}
    assert flags == set(properties)
    availability = resolve(properties["text"])["properties"]
    assert availability["available"]["type"] == "boolean"
    parsed = DocumentOutputs.from_dict(sample({"$ref": "#/components/schemas/DocumentOutputs"}))
    assert parsed.text_available and parsed.pdf_available


def test_job_nested_objects_are_read_from_a_schema_shaped_payload():
    payload = sample({"$ref": "#/components/schemas/DocumentStatusResponse"})
    job = DocumentJob.from_dict(payload)
    assert job.outputs.text_available and job.outputs.pdf_available
    assert (job.error.code, job.error.retryable) == (payload["error"]["code"], payload["error"]["retryable"])
    page = DocumentListResponse.from_dict(sample({"$ref": "#/components/schemas/DocumentListResponse"}))
    assert len(page.documents) == 1 and page.documents[0].id
