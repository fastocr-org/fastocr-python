# FastOCR cookbooks

Recipes for using the [FastOCR Document OCR API](https://fastocr.org/docs/documents)
with the [FastOCR Python SDK](https://pypi.org/project/fastocr-sdk/). The API reads
PDFs and returns plain text or a searchable PDF.

| Recipe | What it does | Needs |
| --- | --- | --- |
| [01. LangChain scanned PDF RAG](./01_langchain_scanned_pdf_rag) | Extract text, chunk it, embed it in Chroma, and ask questions. | LangChain, Chroma, OpenAI |
| [02. LlamaIndex reader](./02_llamaindex_multilingual_reader) | A `FastOCRReader` that loads scanned PDFs into a `VectorStoreIndex`. | LlamaIndex |
| [03. Searchable PDF archive](./03_searchable_pdf_compliance_archive) | Convert a folder of scanned PDFs into searchable PDFs. | the SDK only |
| [04. cURL recipe](./04_local_curl_recipes) | Extract text with `curl`, no SDK. | Bash, curl |

## Setup

```bash
pip install fastocr-sdk
export FASTOCR_API_KEY="your_api_key"
```

API access is granted per account on request; see the SDK README. The LangChain
and LlamaIndex recipes use third-party libraries that change often. They are
checked against the SDK's signatures, not run against those libraries, so pin the
versions you use.

## Running out of pages

If the account runs out of pages, `extract_text` raises `PartialResultError`
instead of returning half a document. `documents.process` returns the job, and
`job.is_partial` tells you. See the SDK README for what to do next.
