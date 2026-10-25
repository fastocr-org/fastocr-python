"""Response models. Unknown fields are ignored; the full payload stays in `raw`."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass
class JobError:
    """Why a document failed. Branch on `code`; `message` is for humans."""
    code: str
    type: str = ""
    message: str = ""
    retryable: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> Optional["JobError"]:
        if not isinstance(data, dict):
            return None
        return cls(
            code=data.get("code") or "processing_failed",
            type=data.get("type") or "",
            message=data.get("message") or "",
            retryable=bool(data.get("retryable")),
        )


@dataclass
class Balance:
    """The pages an account can still use. Documents spend free pages, then contract pages, then Page Packs."""
    pages_remaining: int
    free_pages_per_month: int
    free_pages_remaining: int
    free_pages_reset_at: str
    statement_pages_remaining: int
    contract_pages_per_month: Optional[int] = None
    contract_pages_remaining: Optional[int] = None
    object: str = "balance"
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Balance":
        return cls(
            pages_remaining=data.get("pages_remaining", 0),
            free_pages_per_month=data.get("free_pages_per_month", 0),
            free_pages_remaining=data.get("free_pages_remaining", 0),
            free_pages_reset_at=data.get("free_pages_reset_at", ""),
            statement_pages_remaining=data.get("statement_pages_remaining", 0),
            contract_pages_per_month=data.get("contract_pages_per_month"),
            contract_pages_remaining=data.get("contract_pages_remaining"),
            object=data.get("object", "balance"),
            raw=data,
        )


@dataclass
class DocumentCreateResponse:
    id: str
    status: str
    upload_url: str
    expires_at: str
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentCreateResponse":
        return cls(
            id=data.get("id", ""),
            status=data.get("status", ""),
            upload_url=data.get("upload_url", ""),
            expires_at=data.get("expires_at", ""),
            raw=data,
        )


@dataclass
class DocumentOutputs:
    text_available: bool = False
    pdf_available: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "DocumentOutputs":
        data = _dict(data)
        return cls(
            text_available=bool(_dict(data.get("text")).get("available")),
            pdf_available=bool(_dict(data.get("pdf")).get("available")),
        )


@dataclass
class DocumentJob:
    """A document job. `status` is a plain string; new statuses may appear."""
    id: str
    status: str
    external_id: Optional[str] = None
    pages_billed: Optional[int] = None
    pages_total: Optional[int] = None
    pages_processed: Optional[int] = None
    is_truncated: bool = False
    outputs: DocumentOutputs = field(default_factory=DocumentOutputs)
    error: Optional[JobError] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_completed(self) -> bool:
        return self.status in ("completed", "partial")

    @property
    def is_partial(self) -> bool:
        """True if only some pages were processed because the account ran out of pages."""
        return self.status == "partial" or self.is_truncated

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentJob":
        return cls(
            id=data.get("id", ""),
            status=data.get("status", ""),
            external_id=data.get("external_id"),
            pages_billed=data.get("pages_billed"),
            pages_total=data.get("pages_total"),
            pages_processed=data.get("pages_processed"),
            is_truncated=bool(data.get("is_truncated")),
            outputs=DocumentOutputs.from_dict(data.get("outputs")),
            error=JobError.from_dict(data.get("error")),
            raw=data,
        )


@dataclass
class DocumentListResponse:
    documents: List[DocumentJob]
    next_cursor: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentListResponse":
        return cls(
            documents=[DocumentJob.from_dict(item) for item in data.get("documents") or []],
            next_cursor=data.get("next_cursor"),
            raw=data,
        )


@dataclass
class DocumentOutputUrl:
    """Presigned download URL, valid for one hour."""
    format: str
    url: str
    expires_at: str
    pages_billed: Optional[int] = None
    pages_total: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentOutputUrl":
        return cls(
            format=data.get("format", ""),
            url=data.get("url", ""),
            expires_at=data.get("expires_at", ""),
            pages_billed=data.get("pages_billed"),
            pages_total=data.get("pages_total"),
            raw=data,
        )
