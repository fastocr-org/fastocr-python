"""LlamaIndex Custom Reader for FastOCR API.

Requires:
    pip install fastocr-sdk llama-index-core
"""
from typing import List, Optional, Dict, Any
from pathlib import Path
from llama_index.core.readers.base import BaseReader
from llama_index.core.schema import Document
from fastocr_sdk import FastOCR


class FastOCRReader(BaseReader):
    """LlamaIndex reader that extracts text from scanned PDFs with FastOCR."""

    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        self.client = FastOCR(api_key=api_key)

    def load_data(
        self,
        file_path: Path,
        extra_info: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """Load data from the scanned file and return a list of LlamaIndex Document objects."""
        path_obj = Path(file_path)
        raw_text = self.client.extract_text(path_obj)

        metadata = {
            "file_name": path_obj.name,
            "file_path": str(path_obj.resolve()),
            "source": "FastOCR",
            "format": "raw_text",
        }
        if extra_info:
            metadata.update(extra_info)

        return [Document(text=raw_text, metadata=metadata)]


if __name__ == "__main__":
    reader = FastOCRReader()
    print("FastOCRReader initialized successfully. Use with: docs = reader.load_data('arabic_contract.pdf')")
