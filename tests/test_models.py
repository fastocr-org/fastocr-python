import pytest

from fastocr_sdk import Balance, DocumentJob, DocumentListResponse, JobError
from fastocr_sdk.models import DocumentOutputs


def test_job_defaults_when_fields_are_missing():
    job = DocumentJob.from_dict({})

    assert (job.id, job.status) == ("", "")
    assert job.error is None and job.pages_billed is None
    assert not job.outputs.text_available


@pytest.mark.parametrize("outputs", [None, [], "x", {"text": None}, {"text": "yes", "pdf": 3}])
def test_malformed_outputs_do_not_crash(outputs):
    assert DocumentOutputs.from_dict(outputs) == DocumentOutputs()


def test_error_without_a_code_counts_as_processing_failed():
    assert JobError.from_dict({"message": "x"}).code == "processing_failed"
    assert JobError.from_dict("not a dict") is None


def test_unknown_fields_are_kept_in_raw_only():
    job = DocumentJob.from_dict({"id": "d", "status": "completed", "shiny": 1})

    assert job.raw["shiny"] == 1
    assert not hasattr(job, "shiny")


def test_list_tolerates_null_documents():
    page = DocumentListResponse.from_dict({"documents": None})

    assert page.documents == [] and page.next_cursor is None


def test_balance_reads_every_field_and_keeps_the_rest_in_raw():
    payload = {
        "object": "balance",
        "pages_remaining": 2480,
        "free_pages_per_month": 50,
        "free_pages_remaining": 30,
        "free_pages_reset_at": "2026-11-01T00:00:00+00:00",
        "contract_pages_per_month": None,
        "contract_pages_remaining": None,
        "statement_pages_remaining": 7,
        "shiny": 1,
    }

    balance = Balance.from_dict(payload)

    assert (balance.pages_remaining, balance.free_pages_remaining, balance.statement_pages_remaining) == (2480, 30, 7)
    assert balance.free_pages_reset_at == "2026-11-01T00:00:00+00:00"
    assert balance.contract_pages_per_month is None and balance.contract_pages_remaining is None
    assert balance.raw["shiny"] == 1
