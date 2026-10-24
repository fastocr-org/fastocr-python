# FastOCR cookbooks

Recipes for using the [FastOCR Document OCR API](https://fastocr.org/docs/documents)
with the [FastOCR Python SDK](https://pypi.org/project/fastocr-sdk/). The API reads
PDFs and returns plain text or a searchable PDF.

| Recipe | What it does | Needs |
| --- | --- | --- |
| [01. LangChain scanned PDF RAG](./01_langchain_scanned_pdf_rag) | Load a scan with `langchain-fastocr`, chunk it, embed it in Chroma, and ask questions. | `langchain-fastocr`, Chroma, OpenAI |
| [02. LlamaIndex reader](./02_llamaindex_multilingual_reader) | Load a scan with `llama-index-readers-fastocr` into a `VectorStoreIndex`. | `llama-index-readers-fastocr` |
| [03. Searchable PDF archive](./03_searchable_pdf_compliance_archive) | Convert a folder of scanned PDFs into searchable PDFs. | the SDK only |
| [04. cURL recipe](./04_local_curl_recipes) | Extract text with `curl`, no SDK. | Bash, curl |

## Setup

```bash
pip install fastocr-sdk
export FASTOCR_API_KEY="your_api_key"
```

See the SDK README for how to get an API key. Recipes 01 and 02 install
[`langchain-fastocr`](https://pypi.org/project/langchain-fastocr/) and
[`llama-index-readers-fastocr`](https://pypi.org/project/llama-index-readers-fastocr/)
instead of the SDK. They use third-party libraries that change often, so pin the
versions you use.

## Running out of pages

If the account runs out of pages, `extract_text` raises `PartialResultError`
instead of returning half a document. `documents.process` returns the job, and
`job.is_partial` tells you. See the SDK README for what to do next.
