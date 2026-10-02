"""LangChain Scanned PDF Ingestion and RAG Question-Answering Pipeline.

Requires:
    pip install fastocr-sdk langchain-core langchain-community langchain-text-splitters langchain-openai chromadb
"""
import os
from fastocr_sdk import FastOCR
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser


def ingest_scanned_pdf(file_path: str) -> Document:
    """Extract text from a scanned PDF with FastOCR and wrap it in a LangChain Document.

    extract_text raises PartialResultError if the account ran out of pages partway through,
    so a half-read document is never indexed by accident.
    """
    print(f"Uploading and extracting text from {file_path} via FastOCR...")
    with FastOCR() as client:
        raw_text = client.extract_text(file_path)
    print(f"Extraction complete ({len(raw_text)} characters extracted).")

    return Document(
        page_content=raw_text,
        metadata={
            "source": file_path,
            "extractor": "FastOCR",
            "format": "raw_text",
        },
    )


def build_rag_chain(document: Document):
    """Chunk raw text, embed into Chroma, and build question-answering chain."""
    # 1. Chunk document
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    splits = text_splitter.split_documents([document])
    print(f"Split document into {len(splits)} chunks.")

    # 2. Vector store & retriever
    vectorstore = Chroma.from_documents(splits, embedding=OpenAIEmbeddings())
    retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

    # 3. Prompt & LLM Chain
    template = """You are a helpful assistant analyzing a scanned document.
Use the following extracted raw text context to answer the question:

Context:
{context}

Question: {question}

Answer with specific details from the document:"""
    prompt = ChatPromptTemplate.from_template(template)
    llm = ChatOpenAI(model="gpt-4o", temperature=0)

    def format_docs(docs):
        return "\n\n".join(doc.page_content for doc in docs)

    rag_chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    return rag_chain


if __name__ == "__main__":
    sample_file = "sample_scan.pdf"
    if not os.path.exists(sample_file):
        print(f"Place a scanned PDF at '{sample_file}' to run this example.")
    else:
        doc = ingest_scanned_pdf(sample_file)
        chain = build_rag_chain(doc)
        response = chain.invoke("What is the summary of this document?")
        print("\nAnswer:\n", response)
