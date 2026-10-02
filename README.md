# FastOCR Python SDK

[![PyPI](https://img.shields.io/pypi/v/fastocr-sdk.svg)](https://pypi.org/project/fastocr-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/fastocr-sdk.svg)](https://pypi.org/project/fastocr-sdk/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Python client for the [FastOCR Document OCR API](https://fastocr.org/docs/documents).
Send a scanned PDF, get back its text or a searchable PDF.

- Document OCR only: PDF input, up to 1 GiB per file.
- Sync (`FastOCR`) and async (`AsyncFastOCR`) clients with identical behavior.
- Streams large files from disk, retries transient failures, and never creates a document twice.
- One runtime dependency: `httpx`. Python 3.9+.

## Install

```bash
pip install fastocr-sdk
```

The distribution is `fastocr-sdk` and the import is `fastocr_sdk`. Do not
install the unrelated `fastocr` package from PyPI; it is a different project.

```python
from fastocr_sdk import FastOCR
```

## Get access

API access is currently granted per account on request. Email
[support@fastocr.org](mailto:support@fastocr.org?subject=FastOCR%20API%20Access%20Request)
from your FastOCR account email. Once approved, create a key at
<https://fastocr.org/developers/keys>. Approved accounts get 10 free pages per
month and buy more pages by card on the same page.

```bash
export FASTOCR_API_KEY="your_api_key"
```

## Quickstart

```python
from fastocr_sdk import FastOCR

with FastOCR() as client:
    text = client.extract_text("scanned_contract.pdf")
    print(text)
```

`extract_text` uploads the file, starts OCR, waits for it to finish, and
returns the text. To keep the job and the searchable PDF:

```python
with FastOCR() as client:
    job = client.documents.process("scanned_archive.pdf")
    client.documents.download_searchable_pdf(job.id, "searchable.pdf")
    print(job.id, job.status, job.pages_billed)
```

`job.status` is `completed`, or `partial` if the account ran out of pages
partway (`job.is_partial`); see [Out of pages](#out-of-pages).

## Async

```python
import asyncio
from fastocr_sdk import AsyncFastOCR

async def main():
    async with AsyncFastOCR() as client:
        text = await client.extract_text("invoice.pdf")
        print(text)

asyncio.run(main())
```

Accounts can have 2 documents processing at once by default (10 with a prepaid
page allowance). When fanning out, cap concurrency with a semaphore, as in
[`examples/async_batch.py`](examples/async_batch.py).

## Step by step

`process()` runs these five steps; each is also a method on `client.documents`:

```python
created = client.documents.create("scan.pdf", size_bytes)    # POST /v1/documents
client.documents.upload(created.upload_url, "scan.pdf")      # PUT to the presigned URL
client.documents.start(created.id)                           # POST /v1/documents/{id}/start
job = client.documents.wait_for_completion(created.id)       # GET until a final status
text = client.documents.get_text(created.id)                 # GET /output?format=text
```

Also available: `get`, `list`, `delete`, and `get_output_url`.

## Large files

Files given as a path (or a seekable binary stream) are streamed from disk in
1 MiB chunks, so a 1 GiB PDF does not need 1 GiB of memory. The upload is sent
with a real `Content-Length`, because S3 rejects chunked uploads. A stream that
cannot seek has no known size, so it is read into memory first.

## Errors

Every error is a `FastOCRError` with `status_code`, `code`, `type`,
`request_id` and `message`. Branch on `code`, never on `message`.

| Exception | When |
|---|---|
| `AuthenticationError` | HTTP 401 or 403: missing, unknown, or malformed API key |
| `BadRequestError` | HTTP 400: invalid request |
| `NotFoundError` | HTTP 404: no such document |
| `DocumentConflictError` | HTTP 409: wrong state, or an idempotency key reused with a different body |
| `RateLimitError` | HTTP 429, after retries; `retry_after` holds the seconds, if sent |
| `ServerError` | HTTP 5xx, after retries |
| `APIConnectionError` | No response (DNS, TLS, reset, timeout), after retries |
| `UploadError`, `DownloadError` | The presigned upload or download failed |
| `PollingTimeoutError` | The document did not finish in `timeout` seconds |
| `DocumentFailedError` | The document reached status `failed` |
| `PartialResultError` | `extract_text()` finished a document that was only partly processed (out of pages) |

```python
from fastocr_sdk import DocumentFailedError, FastOCR, FastOCRError

with FastOCR() as client:
    try:
        text = client.extract_text("scan.pdf")
    except DocumentFailedError as error:
        print(error.code, error.retryable, error.document_id)
    except FastOCRError as error:
        print(error.status_code, error.code, error.request_id)
```

Requests rejected by API Gateway before they reach the API (bad or missing key,
throttling) raise the same classes. Their body is either `{"message": ...}` or,
on newer deployments, also carries an `error` object with `type`, `code` (for
example `unauthorized`, `access_denied`, `throttled`) and `request_id`. A
throttled request has no `Retry-After`, so the SDK backs off 0.5 to 1 second,
then 1 to 2 seconds, between its retries.

A failed document is not an HTTP error: the API answers 200 with
`status: "failed"`, and the SDK raises `DocumentFailedError` with the failure's
`code` and `retryable` flag. Treat an unknown code as `processing_failed`.

### Out of pages

Running out of pages ends in one of two ways, and they are handled differently.

**The document fails with `buy_pages`.** Nothing was processed. `process()` and
`extract_text()` raise `DocumentFailedError` with `error.out_of_pages == True`.
After pages are added to the account, start the same document again:

```python
try:
    client.documents.process("scan.pdf")
except DocumentFailedError as error:
    if error.out_of_pages:
        ...  # pages added to the account
        client.documents.start(error.document_id)
        job = client.documents.wait_for_completion(error.document_id)
```

**The document finishes as `partial`.** The pages that fit were processed (the
first `pages_billed` pages) and the rest were not. `process()` returns the job,
and `job.is_partial` is true. `extract_text()` never returns truncated text
silently: it raises `PartialResultError`, which carries `document_id`,
`pages_processed`, `pages_billed` and `pages_total`.

```python
from fastocr_sdk import PartialResultError

try:
    text = client.extract_text("scan.pdf")
except PartialResultError as error:
    print(f"{error.pages_billed} of {error.pages_total} pages were processed")
    partial_text = client.documents.get_text(error.document_id)

text = client.extract_text("scan.pdf", allow_partial=True)  # accept truncated text
```

A partial document cannot be started again (the API answers `409
document_not_startable`). After pages are added, submit the file again as a new
document, which is billed as a new document.

## Retries and idempotency

`FastOCR(max_retries=2)` retries connection errors, HTTP 429 and 502/503/504
with exponential backoff and jitter. It waits for `Retry-After` when the server
sends one (up to 60 seconds; longer waits are raised as `RateLimitError`). It
does not retry other errors, including 400, 401, 403, 404, 409 and 500. Set
`max_retries=0` to turn retries off. Uploads and downloads of results retry the
same way.

Creating a document is safe to retry because every `create` call sends an
`Idempotency-Key`, and the same key is reused across all retries of that call.
`process()` uses one key for the whole call. Pass your own to make your own
retries safe too:

```python
job = client.documents.process("scan.pdf", idempotency_key="invoice-2026-10-001")
```

Calling `process()` again with the same key is safe: it finds the same document and,
if it was already started, just waits for it instead of uploading again.

Reusing a key with a different filename, size, `sha256` or `external_id`
raises `DocumentConflictError` (`idempotency_key_conflict`).

## Examples

Runnable scripts are in [`examples/`](examples/), and
[`examples/cookbooks/`](examples/cookbooks/) has LangChain, LlamaIndex, batch
archiving and cURL recipes.

## Configuration

```python
FastOCR(
    api_key=None,       # defaults to $FASTOCR_API_KEY
    base_url=None,      # defaults to $FASTOCR_BASE_URL, then https://api.fastocr.org
    timeout=30.0,       # seconds per API request
    max_retries=2,
)
```

## Versioning and compatibility

The API only adds things: new response fields, statuses, and error codes can
appear without a new version. The SDK ignores unknown fields (they stay in each
model's `raw` dict) and treats an unknown `status` as still processing and an
unknown error `code` as an ordinary failure. Write your own code the same way:
branch on the codes you know and have a default.

The SDK follows [semantic versioning](https://semver.org). Releases before 1.0
may change the SDK's own interface in a minor version, and each change is
listed in the [changelog](CHANGELOG.md). The HTTP API itself is not versioned
by the SDK.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The suite includes an end-to-end test against a local server and a conformance test
that checks the SDK against `openapi/fastocr-v1.json`. A live smoke test
is skipped unless `FASTOCR_LIVE_API_KEY` and `FASTOCR_LIVE_BASE_URL` are set; it
processes one page.

## License

MIT. See [LICENSE](LICENSE).
