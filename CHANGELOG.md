# Changelog

All notable changes to this SDK are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org).

## [0.1.0] - 2026-10-02

First public release. Document OCR (`/v1/documents`) only.

- `FastOCR` and `AsyncFastOCR` clients: `create`, `upload`, `start`, `get`,
  `list`, `delete`, `get_output_url`, `get_text`, `download_searchable_pdf`,
  `wait_for_completion`, `process`, and `extract_text`.
- Files are streamed from disk with an explicit `Content-Length`.
- Retries (default 2) for connection errors, 429 and 502/503/504, with backoff,
  jitter and `Retry-After`. One idempotency key per `process()` call, reused on
  every retry.
- Errors carry `status_code`, `code`, `type` and `request_id`, and are parsed
  from both the API's error envelope and API Gateway's `{"message": ...}`. HTTP
  401 and 403 raise `AuthenticationError`.
- `DocumentFailedError` for jobs that end in `failed`, with `code`, `retryable`
  and `document_id`.
- `extract_text()` raises `PartialResultError` instead of returning truncated text;
  pass `allow_partial=True` to accept it.
- API Gateway error bodies (`{"message"}` and the newer `error` object) raise the same
  exceptions, with `code`, `type` and `request_id` when present.
- `process()` with a caller-supplied `idempotency_key` is safe to repeat: it finds the same
  document and waits for it instead of failing with 409 `document_not_startable`.
- A blank or whitespace-only API key is treated as missing, and whitespace around a key is
  trimmed (a trailing newline used to surface as a connection error).
- Unknown response fields, statuses and error codes are tolerated.
- Distribution `fastocr-sdk`, import package `fastocr_sdk`.
