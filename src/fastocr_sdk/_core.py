"""Logic shared by the sync and async clients. No I/O happens here."""
import asyncio
import os
import random
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, BinaryIO, Dict, FrozenSet, Iterator, Optional, Tuple, Union
from urllib.parse import quote

import httpx

from ._version import __version__
from .errors import (
    AuthenticationError,
    BadRequestError,
    DocumentConflictError,
    DocumentFailedError,
    DownloadError,
    FastOCRError,
    NotFoundError,
    PartialResultError,
    RateLimitError,
    ServerError,
    UploadError,
)
from .models import DocumentJob, JobError

DEFAULT_BASE_URL = "https://api.fastocr.org"
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_DELAY = 60.0
BACKOFF_BASE = 0.5
THROTTLE_BACKOFF_BASE = 1.0
BACKOFF_CAP = 8.0
CHUNK_SIZE = 1024 * 1024
TRANSFER_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

API_RETRY_STATUSES: FrozenSet[int] = frozenset({429, 502, 503, 504})
TRANSFER_RETRY_STATUSES: FrozenSet[int] = frozenset({500, 502, 503, 504})

FileInput = Union[str, Path, bytes, BinaryIO]


def resolve_api_key(api_key: Optional[str]) -> str:
    key = (api_key or os.environ.get("FASTOCR_API_KEY") or "").strip()
    if not key:
        raise AuthenticationError("API key must be provided or set via FASTOCR_API_KEY environment variable")
    return key


def resolve_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.environ.get("FASTOCR_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def default_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"fastocr-python/{__version__}",
    }


def new_idempotency_key() -> str:
    return str(uuid.uuid4())


def check_pdf_filename(filename: str) -> None:
    if not filename.lower().endswith(".pdf"):
        raise ValueError("FastOCR Document API accepts PDF files only (.pdf)")


def check_output_format(file_format: str) -> None:
    if file_format not in ("text", "pdf"):
        raise ValueError("file_format must be either 'text' or 'pdf'")


def create_payload(
    filename: str, size_bytes: int, external_id: Optional[str], sha256: Optional[str]
) -> Dict[str, Any]:
    check_pdf_filename(filename)
    payload: Dict[str, Any] = {"filename": filename, "size_bytes": size_bytes}
    if external_id is not None:
        payload["external_id"] = external_id
    if sha256 is not None:
        payload["sha256"] = sha256
    return payload


def list_params(
    limit: Optional[int], status: Optional[str], cursor: Optional[str], updated_since: Optional[str]
) -> Dict[str, Any]:
    values = {"limit": limit, "status": status, "cursor": cursor, "updated_since": updated_since}
    return {name: value for name, value in values.items() if value is not None}


def document_path(document_id: str, suffix: str = "") -> str:
    return f"/v1/documents/{quote(document_id, safe='')}{suffix}"


def parse_json(response: httpx.Response) -> Dict[str, Any]:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise ServerError(
            "FastOCR returned a response that is not valid JSON",
            status_code=response.status_code,
        ) from exc


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None


def error_from_response(response: httpx.Response) -> FastOCRError:
    """Build the right exception from a non-2xx API response.

    Handlers send {"error": {type, code, message, request_id}}. API Gateway sends
    {"message": ...} (an `error` object may be added next to it).
    """
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = None
    details = body if isinstance(body, dict) else {}
    nested = details.get("error")
    fields = nested if isinstance(nested, dict) else {}
    message = (
        fields.get("message")
        or details.get("message")
        or (nested if isinstance(nested, str) else None)
        or (response.text[:200].strip() if body is None else "")
        or f"HTTP {status}"
    )
    options: Dict[str, Any] = {
        "status_code": status,
        "code": fields.get("code") or details.get("code"),
        "type": fields.get("type"),
        "request_id": fields.get("request_id") or response.headers.get("x-amzn-requestid"),
        "details": details,
    }
    message = str(message)
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        return RateLimitError(message, retry_after=retry_after, **options)
    if status in (401, 403):
        return AuthenticationError(message, **options)
    error_class = {400: BadRequestError, 404: NotFoundError, 409: DocumentConflictError}.get(status)
    if error_class is None:
        error_class = ServerError if status >= 500 else FastOCRError
    return error_class(message, **options)


def transfer_error(error_class: type, action: str, response: httpx.Response) -> FastOCRError:
    code = re.search(r"<Code>([^<]+)</Code>", response.text[:2000])
    detail = f" ({code.group(1)})" if code else ""
    return error_class(
        f"Failed to {action}: status {response.status_code}{detail}",
        status_code=response.status_code,
        code=code.group(1) if code else None,
    )


def upload_error_from_response(response: httpx.Response) -> FastOCRError:
    return transfer_error(UploadError, "upload the document to the presigned URL", response)


def download_error_from_response(response: httpx.Response) -> FastOCRError:
    return transfer_error(DownloadError, "download the result from the presigned URL", response)


def retry_delay(
    error: FastOCRError, attempt: int, max_retries: int, statuses: FrozenSet[int]
) -> Optional[float]:
    """Seconds to wait before retrying, or None if this error must be raised."""
    if attempt >= max_retries:
        return None
    if error.status_code is not None and error.status_code not in statuses:
        return None
    retry_after = getattr(error, "retry_after", None)
    if retry_after is not None:
        return retry_after if retry_after <= MAX_RETRY_DELAY else None
    base = THROTTLE_BACKOFF_BASE if error.status_code == 429 else BACKOFF_BASE
    ceiling = min(BACKOFF_CAP, base * 2 ** attempt)
    return ceiling / 2 + random.uniform(0, ceiling / 2)


def upload_headers(size: int, sha256: Optional[str]) -> Dict[str, str]:
    # S3 rejects chunked uploads, so Content-Length is always explicit.
    headers = {"Content-Type": "application/pdf", "Content-Length": str(size)}
    if sha256 is not None:
        headers["x-amz-checksum-sha256"] = sha256
    return headers


def failed_error(job: DocumentJob) -> DocumentFailedError:
    error = job.error or JobError(code="processing_failed", message="Document processing failed", retryable=True)
    return DocumentFailedError(
        error.message or "Document processing failed",
        document_id=job.id,
        code=error.code,
        retryable=error.retryable,
        type=error.type or None,
        details=job.raw.get("error") if isinstance(job.raw.get("error"), dict) else None,
    )


def partial_error(job: DocumentJob) -> PartialResultError:
    done = job.pages_billed if job.pages_billed is not None else job.pages_processed
    scope = "only partly processed"
    if done is not None:
        scope = f"only {done}" + (f" of {job.pages_total}" if job.pages_total is not None else "") + " pages were processed"
    return PartialResultError(
        f"Document {job.id} is partial: {scope} because the account ran out of pages. "
        "Use documents.get_text() for the partial text, or pass allow_partial=True.",
        document_id=job.id,
        pages_processed=job.pages_processed,
        pages_billed=job.pages_billed,
        pages_total=job.pages_total,
    )


class UploadSource:
    """A file to upload: its size, and a fresh body for every attempt.

    Paths and seekable streams are read in chunks, never loaded whole. A stream
    that cannot seek has no known size, so it is read into memory.
    """

    def __init__(self, file: FileInput):
        self._data: Optional[bytes] = None
        self._path: Optional[Path] = None
        self._stream: Optional[BinaryIO] = None
        self._start = 0
        if isinstance(file, (str, Path)):
            self._path = Path(file)
            self.size = self._path.stat().st_size
        elif isinstance(file, (bytes, bytearray)):
            self._data = bytes(file)
            self.size = len(self._data)
        elif hasattr(file, "read"):
            self._init_stream(file)
        else:
            raise TypeError("Expected file path, bytes, or file-like object")

    def _init_stream(self, stream: BinaryIO) -> None:
        if getattr(stream, "seekable", lambda: False)():
            self._stream = stream
            self._start = stream.tell()
            self.size = stream.seek(0, os.SEEK_END) - self._start
            stream.seek(self._start)
        else:
            self._data = stream.read()
            self.size = len(self._data)

    def _open(self) -> Tuple[BinaryIO, bool]:
        if self._path is not None:
            return open(self._path, "rb"), True
        assert self._stream is not None
        self._stream.seek(self._start)
        return self._stream, False

    def sync_body(self) -> Union[bytes, Iterator[bytes]]:
        if self._data is not None:
            return self._data
        return self._read_sync()

    def async_body(self) -> Union[bytes, AsyncIterator[bytes]]:
        if self._data is not None:
            return self._data
        return self._read_async()

    def _read_sync(self) -> Iterator[bytes]:
        source, owned = self._open()
        try:
            remaining = self.size
            while remaining > 0:
                chunk = source.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk
        finally:
            if owned:
                source.close()

    async def _read_async(self) -> AsyncIterator[bytes]:
        source, owned = self._open()
        try:
            remaining = self.size
            while remaining > 0:
                chunk = await asyncio.to_thread(source.read, min(CHUNK_SIZE, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk
        finally:
            if owned:
                source.close()


def filename_for(file: FileInput, filename: Optional[str]) -> str:
    if filename:
        return filename
    if isinstance(file, (str, Path)):
        return Path(file).name
    return "document.pdf"
