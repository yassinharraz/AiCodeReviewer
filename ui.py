import streamlit as st
import torch
from pathlib import Path

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from langchain_huggingface import HuggingFacePipeline
from pydantic import BaseModel, Field
from typing import List


class Issue(BaseModel):
    severity: str = Field(description="Severity level: Low, Medium, or High")
    line: int = Field(description="Line number where the issue occurs")
    issue: str = Field(description="Short title of the detected issue")
    explanation: str = Field(description="Detailed explanation of why this is a problem")
    suggested_fix: str = Field(description="Recommended way to fix the issue")


class CodeReviewReport(BaseModel):
    file: str = Field(description="Name of the analyzed Python file")
    issues: List[Issue] = Field(description="List of detected issues")


@st.cache_resource
def load_embedding_model():
    return HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-en-v1.5",
        encode_kwargs={"normalize_embeddings": True},
    )


@st.cache_resource
def load_vector_store():
    cheatsheets_path = str(Path(__file__).parent / "cheatsheets")
    loader = DirectoryLoader(cheatsheets_path, glob="**/*.md", loader_cls=TextLoader)
    documents = loader.load()

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=150)
    chunks = text_splitter.split_documents(documents)

    embedding_model = load_embedding_model()
    vector_store = FAISS.from_documents(documents=chunks, embedding=embedding_model)
    return vector_store


@st.cache_resource
def load_llm():
    model_name = "Qwen/Qwen2.5-7B-Instruct"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(
        model_name, torch_dtype=torch.float16, device_map="auto"
    )
    text_generation_pipeline = pipeline(
        task="text-generation",
        model=model,
        tokenizer=tokenizer,
        max_new_tokens=512,
        temperature=0.2,
        do_sample=False,
        return_full_text=False,
    )
    return HuggingFacePipeline(pipeline=text_generation_pipeline)


def add_line_numbers(code: str) -> str:
    return "\n".join(
        f"{i+1:4} | {line}" for i, line in enumerate(code.splitlines())
    )


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


def build_chain(vector_store, llm):
    retriever = vector_store.as_retriever(
        search_type="similarity", search_kwargs={"k": 3}
    )
    parser = PydanticOutputParser(pydantic_object=CodeReviewReport)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """
You are an expert Python code reviewer.

Your job is to review Python code using ONLY the provided documentation.

Review the code for:

- PEP8 violations
- Clean Code issues
- Security vulnerabilities
- Bugs
- Performance problems

For every issue you find:

- Explain why it is a problem.
- Suggest a fix.

If there are no issues, clearly state that no issues were found.

Return your response in a structured format.
""",
        ),
        (
            "human",
            """
Documentation:

{context}

----------------------------------------

Python Code:

{code}

----------------------------------------

Review this code.

Return your response using the following format:

{format_instructions}
""",
        ),
    ])

    chain = (
        {
            "context": retriever | format_docs,
            "code": RunnablePassthrough(),
            "format_instructions": RunnableLambda(
                lambda _: parser.get_format_instructions()
            ),
        }
        | prompt
        | llm
        | parser
    )
    return chain


def review_code(code: str, chain):
    numbered_code = add_line_numbers(code)
    return chain.invoke(numbered_code)


def main():
    st.set_page_config(page_title="AI Code Reviewer", page_icon="🔍")
    st.title("🔍 AI Code Reviewer")
    st.markdown("Upload Python files and get an AI-powered security & code quality review.")

    with st.sidebar:
        st.header("Pipeline Status")
        with st.spinner("Loading embedding model..."):
            load_embedding_model()
        st.success("Embedding model loaded")
        with st.spinner("Building vector store from cheatsheets..."):
            load_vector_store()
        st.success("Vector store ready")
        with st.spinner("Loading LLM (Qwen2.5-7B)..."):
            load_llm()
        st.success("LLM loaded")

    uploaded_files = st.file_uploader(
        "Upload Python files", type=["py"], accept_multiple_files=True
    )

    if uploaded_files and st.button("Review Code"):
        vector_store = load_vector_store()
        llm = load_llm()
        chain = build_chain(vector_store, llm)

        for uploaded_file in uploaded_files:
            code = uploaded_file.read().decode("utf-8")
            st.subheader(f"📄 {uploaded_file.name}")
            st.code(code, language="python")

            with st.spinner(f"Reviewing {uploaded_file.name}..."):
                try:
                    report = review_code(code, chain)
                    if not report.issues:
                        st.success("✅ No issues found!")
                    else:
                        for issue in report.issues:
                            color = {
                                "High": "🔴",
                                "Medium": "🟠",
                                "Low": "🟢",
                            }.get(issue.severity, "⚪")
                            with st.expander(
                                f"{color} [{issue.severity}] Line {issue.line}: {issue.issue}"
                            ):
                                st.markdown(
                                    f"**Explanation:** {issue.explanation}"
                                )
                                st.markdown(
                                    f"**Suggested Fix:** `{issue.suggested_fix}`"
                                )
                except Exception as e:
                    st.error(f"Review failed: {e}")


if __name__ == "__main__":
    main()
