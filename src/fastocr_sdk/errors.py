"""Exceptions raised by the FastOCR SDK."""
from typing import Any, Dict, Optional


class FastOCRError(Exception):
    """Base class for every error the SDK raises."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        type: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.type = type
        self.request_id = request_id
        self.details = details or {}

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"(status: {self.status_code})")
        if self.code:
            parts.append(f"(code: {self.code})")
        if self.request_id:
            parts.append(f"(request_id: {self.request_id})")
        return " ".join(parts)


class APIConnectionError(FastOCRError):
    """The request never produced an HTTP response (DNS, TLS, reset, timeout)."""


class BadRequestError(FastOCRError):
    """HTTP 400: the request was rejected. Fix the input; do not retry as is."""


class AuthenticationError(FastOCRError):
    """HTTP 401 or 403: the API key is missing, unknown, or malformed."""


class NotFoundError(FastOCRError):
    """HTTP 404: the document does not exist or belongs to another account."""


class DocumentConflictError(FastOCRError):
    """HTTP 409: the document is in the wrong state, or an idempotency key was reused."""


class RateLimitError(FastOCRError):
    """HTTP 429: too many requests or too many documents processing."""

    def __init__(self, message: str, *, retry_after: Optional[float] = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(FastOCRError):
    """HTTP 5xx: FastOCR failed to handle the request."""


class UploadError(FastOCRError):
    """Uploading the file bytes to the presigned URL failed."""


class DownloadError(FastOCRError):
    """Downloading a result from its presigned URL failed."""


class PollingTimeoutError(FastOCRError):
    """The document did not reach a final status within the timeout."""

    def __init__(self, message: str, *, document_id: str, last_status: str):
        super().__init__(message)
        self.document_id = document_id
        self.last_status = last_status


class PartialResultError(FastOCRError):
    """The document finished, but the account ran out of pages partway through.

    Only the first `pages_billed` pages were processed. `documents.get_text(document_id)`
    returns the text of those pages; to process the rest, add pages to the account and
    submit the document again as a new document.
    """

    def __init__(
        self,
        message: str,
        *,
        document_id: str,
        pages_processed: Optional[int],
        pages_billed: Optional[int],
        pages_total: Optional[int],
    ):
        super().__init__(message, code="partial_result")
        self.document_id = document_id
        self.pages_processed = pages_processed
        self.pages_billed = pages_billed
        self.pages_total = pages_total


class DocumentFailedError(FastOCRError):
    """The document reached status `failed`.

    Branch on `code`. `code == "buy_pages"` means the account is out of pages:
    after pages are added, call `documents.start(document_id)` to run the same
    document again.
    """

    def __init__(
        self,
        message: str,
        *,
        document_id: str,
        code: Optional[str],
        retryable: bool,
        type: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, code=code, type=type, details=details)
        self.document_id = document_id
        self.retryable = retryable

    @property
    def out_of_pages(self) -> bool:
        return self.code == "buy_pages"
