"""Asynchronous FastOCR client. Behaves exactly like the sync client."""
import asyncio
import json
import os
import ssl
import time
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    BinaryIO,
    Callable,
    Dict,
    FrozenSet,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

import httpx

from . import _core
from .errors import (
    APIConnectionError,
    DocumentConflictError,
    DownloadError,
    FastOCRError,
    PollingTimeoutError,
    RateLimitError,
    UploadError,
)
from .models import (
    Balance,
    BankStatementCreateResponse,
    BankStatementJob,
    BankStatementListResponse,
    BankStatementOutputUrl,
    DocumentCreateResponse,
    DocumentJob,
    DocumentListResponse,
    DocumentOutputUrl,
)

T = TypeVar("T")


class AsyncDocumentsResource:
    """Document OCR operations (`/v1/documents`)."""

    def __init__(self, client: "AsyncFastOCR"):
        self._client = client

    async def create(
        self,
        filename: str,
        size_bytes: int,
        idempotency_key: Optional[str] = None,
        external_id: Optional[str] = None,
        sha256: Optional[str] = None,
    ) -> DocumentCreateResponse:
        """Create a document and get a presigned upload URL.

        The same `idempotency_key` is sent on every retry of this call, so a retry
        can never create a second document.
        """
        payload = _core.create_payload(filename, size_bytes, external_id, sha256)
        headers = {"Idempotency-Key": idempotency_key or _core.new_idempotency_key()}
        resp = await self._client._request("POST", "/v1/documents", json=payload, headers=headers)
        return DocumentCreateResponse.from_dict(resp)

    async def upload(
        self,
        upload_url: str,
        file: Union[str, Path, bytes, BinaryIO],
        sha256: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> None:
        """PUT the file to the presigned URL. Paths and seekable streams are streamed from disk.

        `sha256` (base64 of the raw 32-byte digest) is only needed if you passed it to `create`;
        S3 checks it on upload. `content_type` must match the filename given to `create`
        (for example `image/png` for `photo.png`); it defaults to the type of a path's
        extension, else `application/pdf`.
        """
        source = _core.UploadSource(file)
        await self._client._upload_source(upload_url, source, sha256, _core.upload_content_type(file, content_type))

    async def start(self, document_id: str) -> Dict[str, Any]:
        """Start OCR on an uploaded document, or run a `buy_pages` failure again after adding pages."""
        return await self._client._request("POST", _core.document_path(document_id, "/start"))

    async def get(self, document_id: str) -> DocumentJob:
        resp = await self._client._request("GET", _core.document_path(document_id))
        return DocumentJob.from_dict(resp)

    async def list(
        self,
        limit: Optional[int] = None,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> DocumentListResponse:
        params = _core.list_params(limit, status, cursor, updated_since)
        resp = await self._client._request("GET", "/v1/documents", params=params)
        return DocumentListResponse.from_dict(resp)

    async def delete(self, document_id: str) -> Dict[str, Any]:
        """Delete a document and its files. Cleanup is asynchronous."""
        return await self._client._request("DELETE", _core.document_path(document_id))

    async def get_output_url(self, document_id: str, file_format: str = "text") -> DocumentOutputUrl:
        """Get a presigned download URL (valid one hour) for `text` or `pdf`."""
        _core.check_output_format(file_format)
        resp = await self._client._request(
            "GET", _core.document_path(document_id, "/output"), params={"format": file_format}
        )
        return DocumentOutputUrl.from_dict(resp)

    async def get_text(self, document_id: str) -> str:
        """Download the extracted text."""
        url = (await self.get_output_url(document_id, "text")).url

        async def attempt() -> bytes:
            try:
                res = await self._client._transfer.get(url)
            except httpx.RequestError as exc:
                raise DownloadError(f"Network error while downloading the text: {exc}") from exc
            if not res.is_success:
                raise _core.download_error_from_response(res)
            return res.content

        return (await self._client._retry(attempt, _core.TRANSFER_RETRY_STATUSES)).decode("utf-8")

    async def download_searchable_pdf(self, document_id: str, destination: Union[str, Path, BinaryIO]) -> None:
        """Download the searchable PDF to a path or a writable binary stream.

        A path is written atomically and retried on transient failures. A stream is
        tried once, because a partial write cannot be undone.
        """
        url = (await self.get_output_url(document_id, "pdf")).url
        if isinstance(destination, (str, Path)):
            await self._client._download_to_path(url, Path(destination), "PDF")
        else:
            await self._client._stream_download(url, destination, "PDF")

    async def wait_for_completion(
        self,
        document_id: str,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> DocumentJob:
        """Poll until the document is `completed` or `partial`.

        Waits `poll_interval` seconds after the first check, then 1.5 times longer
        each time, up to 10 seconds (a longer `poll_interval` stays as given).
        Raises DocumentFailedError if it fails and PollingTimeoutError on timeout.
        An unrecognized status counts as still processing.
        """
        deadline = time.monotonic() + timeout
        interval = poll_interval
        while True:
            job = await self.get(document_id)
            if job.is_completed:
                return job
            if job.is_failed:
                raise _core.failed_error(job)
            if time.monotonic() >= deadline:
                raise PollingTimeoutError(
                    f"Document {document_id} did not finish processing within {timeout} seconds",
                    document_id=document_id,
                    last_status=job.status,
                )
            await self._client._sleep(interval)
            interval = _core.next_poll_interval(interval, poll_interval)

    async def process(
        self,
        file: Union[str, Path, bytes, BinaryIO],
        filename: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        external_id: Optional[str] = None,
        sha256: Optional[str] = None,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> DocumentJob:
        """Create, upload, start, and wait. Returns the finished job.

        One idempotency key covers the whole call. Pass your own to make a repeated
        `process()` for the same file safe: it finds the same document and, if that
        document was already uploaded or started, just waits for it.
        """
        source = _core.UploadSource(file)
        name = _core.filename_for(file, filename)
        key = idempotency_key or _core.new_idempotency_key()
        created = await self.create(name, source.size, idempotency_key=key, external_id=external_id, sha256=sha256)
        if idempotency_key and (await self.get(created.id)).status != "awaiting_upload":
            return await self.wait_for_completion(created.id, timeout=timeout, poll_interval=poll_interval)
        await self._client._upload_source(created.upload_url, source, sha256, _core.content_type_for(name))
        await self.start(created.id)
        return await self.wait_for_completion(created.id, timeout=timeout, poll_interval=poll_interval)


class AsyncBankStatementsResource:
    """Bank statement extraction (`/v1/bank-statements`), beta."""

    def __init__(self, client: "AsyncFastOCR"):
        self._client = client

    async def create(
        self,
        filename: str,
        size_bytes: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        external_id: Optional[str] = None,
    ) -> BankStatementCreateResponse:
        """Create a statement job and get a presigned upload URL (PDF, PNG, JPG, JPEG or WebP).

        `size_bytes` is optional; when given, the server checks that the upload has exactly that size.

        The same `idempotency_key` is sent on every retry of this call, so a retry
        can never create a second job.
        """
        payload = _core.statement_create_payload(filename, size_bytes, external_id)
        headers = {"Idempotency-Key": idempotency_key or _core.new_idempotency_key()}
        resp = await self._client._request("POST", "/v1/bank-statements", json=payload, headers=headers)
        return BankStatementCreateResponse.from_dict(resp)

    async def upload(
        self,
        upload_url: str,
        file: Union[str, Path, bytes, BinaryIO],
        content_type: Optional[str] = None,
    ) -> None:
        """PUT the file to the presigned URL. `content_type` must match the filename given to `create`."""
        source = _core.UploadSource(file)
        content_type = content_type or _core.upload_content_type(file, None)
        await self._client._upload_source(upload_url, source, None, content_type)

    async def start(self, statement_id: str) -> Dict[str, Any]:
        """Start a statement the server has not started yet, or run a `buy_pages` failure again after adding pages."""
        return await self._client._request("POST", _core.statement_path(statement_id, "/start"))

    async def get(self, statement_id: str) -> BankStatementJob:
        """Get a statement. A completed one carries fresh one-hour download links in `outputs`."""
        resp = await self._client._request("GET", _core.statement_path(statement_id))
        return BankStatementJob.from_dict(resp)

    async def list(
        self,
        limit: Optional[int] = None,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> BankStatementListResponse:
        params = _core.list_params(limit, status, cursor, updated_since)
        resp = await self._client._request("GET", "/v1/bank-statements", params=params)
        return BankStatementListResponse.from_dict(resp)

    async def delete(self, statement_id: str) -> Dict[str, Any]:
        """Delete a statement and its files. Cleanup is asynchronous."""
        return await self._client._request("DELETE", _core.statement_path(statement_id))

    async def get_output_url(
        self, statement_id: str, file_format: str = "json", account: Optional[int] = None
    ) -> BankStatementOutputUrl:
        """Get a presigned download URL (valid one hour) for one format.

        Formats: json, csv, xlsx, qbo, quickbooks_csv, qfx, ofx. `account` (1 is the first)
        picks the account of a qbo, quickbooks_csv, qfx or ofx statement that has several.
        """
        params = _core.statement_output_params(file_format, account)
        resp = await self._client._request("GET", _core.statement_path(statement_id, "/output"), params=params)
        return BankStatementOutputUrl.from_dict(resp)

    async def download(self, statement_id: str, file_format: str = "json", account: Optional[int] = None) -> bytes:
        """Download one format of a completed statement."""
        url = (await self.get_output_url(statement_id, file_format, account)).url
        return await self._client._download_bytes(url, "statement")

    async def save(
        self,
        statement_id: str,
        path: Union[str, Path],
        file_format: str = "json",
        account: Optional[int] = None,
    ) -> Path:
        """Download one format to `path`, written atomically and retried on transient failures."""
        url = (await self.get_output_url(statement_id, file_format, account)).url
        destination = Path(path)
        await self._client._download_to_path(url, destination, "statement")
        return destination

    async def json(self, statement_id: str) -> Dict[str, Any]:
        """The statement's structured data (schema 3 when the server has it)."""
        url = (await self.get_output_url(statement_id, "json")).url
        data = json.loads((await self._client._download_bytes(url, "statement")).decode("utf-8"))
        if not isinstance(data, dict):
            raise DownloadError("The statement JSON is not an object")
        return data

    async def wait_for_completion(
        self,
        statement_id: str,
        timeout: float = 600.0,
        poll_interval: float = 2.0,
    ) -> BankStatementJob:
        """Poll until the statement is `completed`.

        Waits `poll_interval` seconds after the first check, then 1.5 times longer each
        time, up to 10 seconds, and never less than a `Retry-After` the server sends.
        `awaiting_upload`, `queued`, `processing` and unknown statuses count as still
        working. Raises StatementFailedError if it fails and PollingTimeoutError on timeout.
        """
        return await self._wait(statement_id, timeout, poll_interval, start_if_idle=False)

    async def _wait(self, statement_id: str, timeout: float, poll_interval: float, start_if_idle: bool) -> BankStatementJob:
        deadline = time.monotonic() + timeout
        interval = poll_interval
        while True:
            body, retry_after = await self._client._request_full("GET", _core.statement_path(statement_id))
            job = BankStatementJob.from_dict(body)
            if job.is_completed:
                return job
            if job.is_failed:
                raise _core.statement_failed_error(job)
            if start_if_idle and job.status == "awaiting_upload":
                try:
                    await self.start(statement_id)
                except (RateLimitError, DocumentConflictError) as busy:
                    retry_after = getattr(busy, "retry_after", None) or retry_after
            if time.monotonic() >= deadline:
                raise PollingTimeoutError(
                    f"Bank statement {statement_id} did not finish processing within {timeout} seconds",
                    document_id=statement_id,
                    last_status=job.status,
                )
            await self._client._sleep(max(interval, retry_after or 0.0))
            interval = _core.next_poll_interval(interval, poll_interval)

    async def process(
        self,
        file: Union[str, Path, bytes, BinaryIO],
        filename: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        external_id: Optional[str] = None,
        timeout: float = 600.0,
        poll_interval: float = 2.0,
    ) -> BankStatementJob:
        """Create, upload, and wait. Returns the completed job, with download links in `outputs`.

        The server starts the statement once it sees the upload. If a server does not,
        this calls `start` itself. Pass your own `idempotency_key` to make a repeated call
        for the same file safe: it finds the same job and, if that job was already
        uploaded, just waits for it.
        """
        source = _core.UploadSource(file)
        name = _core.filename_for(file, filename, "statement.pdf")
        content_type = _core.statement_content_type_for(name)
        key = idempotency_key or _core.new_idempotency_key()
        created = await self.create(name, source.size, idempotency_key=key, external_id=external_id)
        if idempotency_key and (await self.get(created.id)).status != "awaiting_upload":
            return await self._wait(created.id, timeout, poll_interval, start_if_idle=False)
        await self._client._upload_source(created.upload_url, source, None, content_type)
        return await self._wait(created.id, timeout, poll_interval, start_if_idle=True)


class AsyncFastOCR:
    """Asynchronous FastOCR API client.

    `max_retries` bounds retries of connection errors, 429 (honoring Retry-After)
    and 502/503/504. Other errors are raised immediately.

    `transport`, `verify` and `trust_env` configure both the API connection and the
    file transfers (uploads and result downloads); `transfer_timeout` overrides the
    transfer timeout (default: 10 s to connect, 120 s otherwise).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = _core.DEFAULT_MAX_RETRIES,
        *,
        transfer_timeout: Optional[float] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        verify: Union[bool, str, ssl.SSLContext] = True,
        trust_env: bool = True,
    ):
        self.api_key = _core.resolve_api_key(api_key)
        self.base_url = _core.resolve_base_url(base_url)
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=_core.default_headers(self.api_key),
            transport=transport,
            verify=verify,
            trust_env=trust_env,
        )
        self._transfer = httpx.AsyncClient(
            timeout=_core.TRANSFER_TIMEOUT if transfer_timeout is None else transfer_timeout,
            transport=transport,
            verify=verify,
            trust_env=trust_env,
        )
        self.documents = AsyncDocumentsResource(self)
        self.bank_statements = AsyncBankStatementsResource(self)

    async def get_balance(self) -> Balance:
        """Get the pages the account can still use (`GET /v1/balance`)."""
        return Balance.from_dict(await self._request("GET", "/v1/balance"))

    async def extract_text(
        self,
        file: Union[str, Path, bytes, BinaryIO],
        timeout: float = 300.0,
        allow_partial: bool = False,
        filename: Optional[str] = None,
    ) -> str:
        """Process a PDF or an image (PNG, JPG, GIF, WebP, BMP) and return its text.

        The file type comes from `filename`, else a path's name; bytes and streams
        without a filename are sent as a PDF.

        Raises PartialResultError if the account ran out of pages partway through, so
        truncated text is never returned silently. Pass `allow_partial=True` to get the
        text of the processed pages instead.
        """
        job = await self.documents.process(file, filename=filename, timeout=timeout)
        if job.is_partial and not allow_partial:
            raise _core.partial_error(job)
        return await self.documents.get_text(job.id)

    async def extract_statement(
        self,
        path: Union[str, Path, bytes, BinaryIO],
        *,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
        filename: Optional[str] = None,
        external_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> BankStatementJob:
        """Upload a bank statement (PDF, PNG, JPG, JPEG or WebP) and wait for the result.

        Returns the completed `BankStatementJob`; `job.outputs["xlsx"].url` is a one-hour
        download link, and `bank_statements.save(job.id, "out.xlsx", "xlsx")` saves a file.
        Raises StatementFailedError if extraction fails and PollingTimeoutError after
        `timeout` seconds. The statement keeps processing: call `bank_statements.get(id)`.
        Bytes and streams need a `filename` to set the file type; without one they are sent as a PDF.
        """
        return await self.bank_statements.process(
            path,
            filename=filename,
            idempotency_key=idempotency_key,
            external_id=external_id,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def _upload_source(
        self, upload_url: str, source: _core.UploadSource, sha256: Optional[str], content_type: str
    ) -> None:
        headers = _core.upload_headers(source.size, sha256, content_type)

        async def attempt() -> None:
            try:
                res = await self._transfer.put(upload_url, content=source.async_body(), headers=headers)
            except httpx.RequestError as exc:
                raise UploadError(f"Network error while uploading the document: {exc}") from exc
            if not res.is_success:
                raise _core.upload_error_from_response(res)

        await self._retry(attempt, _core.TRANSFER_RETRY_STATUSES)

    async def _stream_download(self, url: str, sink: BinaryIO, what: str) -> None:
        try:
            async with self._transfer.stream("GET", url) as response:
                if not response.is_success:
                    await response.aread()
                    raise _core.download_error_from_response(response)
                async for chunk in response.aiter_bytes():
                    sink.write(chunk)
        except httpx.RequestError as exc:
            raise DownloadError(f"Network error while downloading the {what}: {exc}") from exc

    async def _download_to_path(self, url: str, destination: Path, what: str) -> None:
        partial = destination.with_name(destination.name + ".part")

        async def attempt() -> None:
            with open(partial, "wb") as sink:
                await self._stream_download(url, sink, what)

        try:
            await self._retry(attempt, _core.TRANSFER_RETRY_STATUSES)
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)

    async def _download_bytes(self, url: str, what: str) -> bytes:
        async def attempt() -> bytes:
            try:
                res = await self._transfer.get(url)
            except httpx.RequestError as exc:
                raise DownloadError(f"Network error while downloading the {what}: {exc}") from exc
            if not res.is_success:
                raise _core.download_error_from_response(res)
            return res.content

        return await self._retry(attempt, _core.TRANSFER_RETRY_STATUSES)

    async def _retry(self, attempt: Callable[[], Awaitable[T]], statuses: FrozenSet[int]) -> T:
        retries = 0
        while True:
            try:
                return await attempt()
            except FastOCRError as error:
                delay = _core.retry_delay(error, retries, self.max_retries, statuses)
                if delay is None:
                    raise
                retries += 1
                await self._sleep(delay)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        return (await self._request_full(method, path, **kwargs))[0]

    async def _request_full(self, method: str, path: str, **kwargs: Any) -> Tuple[Dict[str, Any], Optional[float]]:
        """The JSON body and the Retry-After header (seconds) of a successful response."""

        async def attempt() -> Tuple[Dict[str, Any], Optional[float]]:
            try:
                res = await self._http.request(method, path, **kwargs)
            except httpx.RequestError as exc:
                raise APIConnectionError(f"Network request to FastOCR API failed: {exc}") from exc
            if res.is_success:
                return _core.parse_json(res), _core.parse_retry_after(res.headers.get("Retry-After"))
            raise _core.error_from_response(res)

        return await self._retry(attempt, _core.API_RETRY_STATUSES)

    async def close(self) -> None:
        try:
            await self._http.aclose()
        finally:
            await self._transfer.aclose()

    async def __aenter__(self) -> "AsyncFastOCR":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
