"""Handle the errors you are likely to meet.

    FASTOCR_API_KEY=... python examples/handle_errors.py scan.pdf
"""
import sys

from fastocr_sdk import (
    AuthenticationError,
    DocumentFailedError,
    FastOCR,
    FastOCRError,
    RateLimitError,
)

path = sys.argv[1]

with FastOCR() as client:
    try:
        print(client.extract_text(path))
    except AuthenticationError:
        sys.exit("The API key was rejected. Check FASTOCR_API_KEY.")
    except RateLimitError as error:
        sys.exit(f"Still rate limited after retries. Try again in {error.retry_after or 30} seconds.")
    except DocumentFailedError as error:
        if error.out_of_pages:
            sys.exit(
                f"Out of pages. After pages are added, run client.documents.start({error.document_id!r}) "
                "to process the same document again."
            )
        sys.exit(f"Failed with code {error.code} (retryable: {error.retryable}): {error.message}")
    except FastOCRError as error:
        sys.exit(f"{error} (request id: {error.request_id})")
