"""LlamaIndex reader for scanned PDFs and images, with FastOCR.

Requires:
    pip install llama-index-readers-fastocr llama-index-core
"""
from llama_index.core import VectorStoreIndex
from llama_index.readers.fastocr import FastOCRReader

if __name__ == "__main__":
    documents = FastOCRReader().load_data("arabic_contract.pdf")
    index = VectorStoreIndex.from_documents(documents)
    print(index.as_query_engine().query("What is this document about?"))
