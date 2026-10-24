<div align="center">

<a href="https://fastocr.org">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/fastocr-org/fastocr-python/main/docs/images/logo-dark.png">
    <img src="https://raw.githubusercontent.com/fastocr-org/fastocr-python/main/docs/images/logo-light.png" alt="FastOCR" width="200">
  </picture>
</a>

<h3>Python client for the FastOCR Document OCR API. Send a scanned PDF or an image, get back its text or a searchable PDF.</h3>

<p>
<a href="https://github.com/fastocr-org/fastocr-python/actions/workflows/ci.yml"><img src="https://github.com/fastocr-org/fastocr-python/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<a href="https://pypi.org/project/fastocr-sdk/"><img src="https://img.shields.io/pypi/v/fastocr-sdk.svg?label=pypi" alt="PyPI"></a>
<a href="https://pypi.org/project/fastocr-sdk/"><img src="https://img.shields.io/pypi/pyversions/fastocr-sdk.svg" alt="Python"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
</p>

<p>
<a href="https://fastocr.org">Website</a> ·
<a href="https://fastocr.org/docs">Docs</a> ·
<a href="https://fastocr.org/developers/keys">Get an API key</a>
</p>

<img src="https://raw.githubusercontent.com/fastocr-org/fastocr-python/main/docs/images/readme-hero.gif" alt="FastOCR reads a scanned page in English, Arabic and Hindi and returns its text" width="800">

</div>

## Install

```bash
pip install fastocr-sdk
export FASTOCR_API_KEY="your_api_key"
```

Python 3.9 or newer. The distribution is `fastocr-sdk` and the import is `fastocr_sdk`. Do not install the unrelated `fastocr` package from PyPI.

## Quickstart

```python
from fastocr_sdk import FastOCR

with FastOCR() as client:
    print(client.extract_text("scanned_contract.pdf"))
```

`extract_text` uploads the file, runs OCR, waits for it to finish, and returns the text. Images work the same way; pass `filename` when you send bytes.

## What you get back

| You get | How |
|---|---|
| The text of the document | `client.extract_text(file)` |
| A searchable PDF | `client.documents.download_searchable_pdf(job.id, "out.pdf")` |
| Status and page counts | `job.status`, `job.pages_billed`, `job.pages_total` |

## Examples

Runnable scripts are in [`examples/`](examples/), with RAG, archiving and cURL recipes in [`examples/cookbooks/`](examples/cookbooks/). [Full reference →](https://github.com/fastocr-org/fastocr-python/blob/main/docs/reference.md)

### Searchable PDF

```python
with FastOCR() as client:
    job = client.documents.process("scanned_archive.pdf")
    client.documents.download_searchable_pdf(job.id, "searchable.pdf")
```

### Async and batches

```python
import asyncio
from fastocr_sdk import AsyncFastOCR

async def main():
    async with AsyncFastOCR() as client:
        print(await client.extract_text("invoice.pdf"))

asyncio.run(main())
```

Accounts process 2 documents at once by default. Cap concurrency with a semaphore, as in [`examples/async_batch.py`](examples/async_batch.py).

## Errors

Every error is a `FastOCRError` with `status_code`, `code`, `request_id` and `message`. Branch on `code`, never on `message`.

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

If the account runs out of pages partway, `extract_text` raises `PartialResultError` instead of returning truncated text; pass `allow_partial=True` to accept it. Transient failures are retried and a document is never created twice.

<details>
<summary>Exception classes</summary>

| Exception | When |
|---|---|
| `AuthenticationError` | HTTP 401 or 403 |
| `BadRequestError` | HTTP 400 |
| `NotFoundError` | HTTP 404 |
| `DocumentConflictError` | HTTP 409 |
| `RateLimitError` | HTTP 429, after retries |
| `ServerError`, `APIConnectionError` | HTTP 5xx or no response, after retries |
| `DocumentFailedError` | The document reached status `failed` |
| `PartialResultError` | The document was only partly processed |

</details>

Full reference: [retries, idempotency, out-of-pages recovery, configuration](https://github.com/fastocr-org/fastocr-python/blob/main/docs/reference.md).

## RAG integrations

Use [`langchain-fastocr`](https://pypi.org/project/langchain-fastocr/) for LangChain or [`llama-index-readers-fastocr`](https://pypi.org/project/llama-index-readers-fastocr/) for LlamaIndex. Both are built on this SDK.

## Languages and files

- 100+ languages, detected automatically. No language setting is needed.
- PDF files up to 1 GiB, and PNG, JPG, JPEG, GIF, WebP and BMP images up to 20 MiB. An image counts as one page.
- Sync (`FastOCR`) and async (`AsyncFastOCR`) clients. One runtime dependency: `httpx`.

## Pages and pricing

Any signed-in FastOCR account can create an API key and start right away. Create one in the [developer console](https://fastocr.org/developers/keys); you accept the [API terms](https://fastocr.org/api-terms) with your first key. Each account gets 50 free document pages per month. When you need more, buy a prepaid API page pack by card in the [developer console](https://fastocr.org/developers/keys#buy-api-pages).

## Support and license

Questions: support@fastocr.org. Bugs and requests: [GitHub issues](https://github.com/fastocr-org/fastocr-python/issues). Full reference: [docs/reference.md](https://github.com/fastocr-org/fastocr-python/blob/main/docs/reference.md). MIT license; see [LICENSE](LICENSE).
