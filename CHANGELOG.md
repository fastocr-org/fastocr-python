# Changelog

All notable changes to this SDK are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org).

## [0.2.1] - 2026-10-25

- Documentation only: a redesigned README with a quickstart first, links to the LangChain
  and LlamaIndex loaders, and the full reference moved to `docs/reference.md`. No code changes.

## [0.2.0] - 2026-10-20

- Images: `create`, `process` and `extract_text` accept PNG, JPG, JPEG, GIF, WebP and BMP
  files as well as PDFs. Each upload is sent with the Content-Type of the filename's
  extension, which the presigned URL is signed for. `upload` takes `content_type`
  (default: a path's extension, else `application/pdf`) and `extract_text` takes
  `filename` for bytes and streams. Other extensions still raise `ValueError` before
  any request.
- `wait_for_completion` and `process` back off while polling: `poll_interval` (default
  2 s) is the first wait, and each later wait is 1.5 times longer, up to 10 s.

## [0.1.1] - 2026-10-14

- Uploads and result downloads now go through one long-lived transfer client instead of
  a new client per attempt. `FastOCR` and `AsyncFastOCR` accept `transport`, `verify`,
  `trust_env` and `transfer_timeout`, which apply to the API connection and to the
  transfers alike. The transfer client is closed by `close()` (awaited on the async
  client) and by the context managers. Retry behavior is unchanged.
- `documents.create` raises `ValueError` when `sha256` is not the base64 of the raw
  32-byte digest (for example a hex digest), instead of waiting for the API's
  `400 invalid_sha256`.
- README: corrected the retry description (uploads and downloads retry 500 and never
  429), documented that a replayed `create` gets a fresh `upload_url`, and added a CI
  badge.

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
- `process()` with a caller-supplied `idempotency_key` is safe to repeat: it finds the same
  document and waits for it instead of failing with 409 `document_not_startable`.
- A blank or whitespace-only API key is treated as missing, and whitespace around a key is
  trimmed (a trailing newline used to surface as a connection error).
- Unknown response fields, statuses and error codes are tolerated.
- A 2xx response whose body is not a JSON object raises `ServerError`.
- Every SDK error can be pickled, so worker processes (multiprocessing, Celery) can pass
  them back.
- Distribution `fastocr-sdk`, import package `fastocr_sdk`.
