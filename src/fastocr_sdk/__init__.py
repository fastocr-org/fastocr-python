"""FastOCR Python SDK for the Document OCR API."""

from ._version import __version__
from .async_client import AsyncFastOCR
from .client import FastOCR
from .errors import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    DocumentConflictError,
    DocumentFailedError,
    DownloadError,
    FastOCRError,
    NotFoundError,
    PartialResultError,
    PollingTimeoutError,
    RateLimitError,
    ServerError,
    UploadError,
)
from .models import (
    DocumentCreateResponse,
    DocumentJob,
    DocumentListResponse,
    DocumentOutputs,
    DocumentOutputUrl,
    JobError,
)

__all__ = [
    "__version__",
    "FastOCR",
    "AsyncFastOCR",
    "DocumentJob",
    "DocumentCreateResponse",
    "DocumentOutputs",
    "DocumentOutputUrl",
    "DocumentListResponse",
    "JobError",
    "FastOCRError",
    "APIConnectionError",
    "AuthenticationError",
    "BadRequestError",
    "DocumentConflictError",
    "DocumentFailedError",
    "DownloadError",
    "NotFoundError",
    "PartialResultError",
    "PollingTimeoutError",
    "RateLimitError",
    "ServerError",
    "UploadError",
]
