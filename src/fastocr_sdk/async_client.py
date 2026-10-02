"""Asynchronous FastOCR client. Behaves exactly like the sync client."""
import asyncio
import os
import time
from pathlib import Path
from typing import Any, BinaryIO, Awaitable, Callable, Dict, FrozenSet, Optional, TypeVar, Union

import httpx

from . import _core
from .errors import (
    APIConnectionError,
    DownloadError,
    FastOCRError,
    PollingTimeoutError,
    UploadError,
)
from .models import (
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
    ) -> None:
        """PUT the file to the presigned URL. Paths and seekable streams are streamed from disk.

        `sha256` (base64 of the raw 32-byte digest) is only needed if you passed it to `create`;
        S3 checks it on upload.
        """
        await self._upload_source(upload_url, _core.UploadSource(file), sha256)

    async def _upload_source(self, upload_url: str, source: _core.UploadSource, sha256: Optional[str]) -> None:
        headers = _core.upload_headers(source.size, sha256)

        async def attempt() -> None:
            try:
                async with httpx.AsyncClient(timeout=_core.TRANSFER_TIMEOUT) as uploader:
                    res = await uploader.put(upload_url, content=source.async_body(), headers=headers)
            except httpx.RequestError as exc:
                raise UploadError(f"Network error while uploading the document: {exc}") from exc
            if not res.is_success:
                raise _core.upload_error_from_response(res)

        await self._client._retry(attempt, _core.TRANSFER_RETRY_STATUSES)

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
                async with httpx.AsyncClient(timeout=_core.TRANSFER_TIMEOUT) as downloader:
                    res = await downloader.get(url)
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
            await self._download_to_path(url, Path(destination))
        else:
            await self._stream_pdf(url, destination)

    async def _stream_pdf(self, url: str, sink: BinaryIO) -> None:
        try:
            async with httpx.AsyncClient(timeout=_core.TRANSFER_TIMEOUT) as downloader:
                async with downloader.stream("GET", url) as response:
                    if not response.is_success:
                        await response.aread()
                        raise _core.download_error_from_response(response)
                    async for chunk in response.aiter_bytes():
                        sink.write(chunk)
        except httpx.RequestError as exc:
            raise DownloadError(f"Network error while downloading the PDF: {exc}") from exc

    async def _download_to_path(self, url: str, destination: Path) -> None:
        partial = destination.with_name(destination.name + ".part")

        async def attempt() -> None:
            with open(partial, "wb") as sink:
                await self._stream_pdf(url, sink)

        try:
            await self._client._retry(attempt, _core.TRANSFER_RETRY_STATUSES)
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)

    async def wait_for_completion(
        self,
        document_id: str,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> DocumentJob:
        """Poll until the document is `completed` or `partial`.

        Raises DocumentFailedError if it fails and PollingTimeoutError on timeout.
        An unrecognized status counts as still processing.
        """
        deadline = time.monotonic() + timeout
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
            await self._client._sleep(poll_interval)

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
        await self._upload_source(created.upload_url, source, sha256)
        await self.start(created.id)
        return await self.wait_for_completion(created.id, timeout=timeout, poll_interval=poll_interval)


class AsyncFastOCR:
    """Asynchronous FastOCR API client.

    `max_retries` bounds retries of connection errors, 429 (honoring Retry-After)
    and 502/503/504. Other errors are raised immediately.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = _core.DEFAULT_MAX_RETRIES,
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
        )
        self.documents = AsyncDocumentsResource(self)

    async def extract_text(
        self,
        file: Union[str, Path, bytes, BinaryIO],
        timeout: float = 300.0,
        allow_partial: bool = False,
    ) -> str:
        """Process a PDF and return its text.

        Raises PartialResultError if the account ran out of pages partway through, so
        truncated text is never returned silently. Pass `allow_partial=True` to get the
        text of the processed pages instead.
        """
        job = await self.documents.process(file, timeout=timeout)
        if job.is_partial and not allow_partial:
            raise _core.partial_error(job)
        return await self.documents.get_text(job.id)

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
        async def attempt() -> Dict[str, Any]:
            try:
                res = await self._http.request(method, path, **kwargs)
            except httpx.RequestError as exc:
                raise APIConnectionError(f"Network request to FastOCR API failed: {exc}") from exc
            if res.is_success:
                return _core.parse_json(res)
            raise _core.error_from_response(res)

        return await self._retry(attempt, _core.API_RETRY_STATUSES)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncFastOCR":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
