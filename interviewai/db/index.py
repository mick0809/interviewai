import uuid
import datetime
import logging
from enum import Enum
from typing import Any, Iterable, List, Optional
# File loders
from langchain.document_loaders.base import BaseLoader
from langchain_community.document_loaders.gcs_file import GCSFileLoader
from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_community.document_loaders.word_document import Docx2txtLoader
from langchain_community.document_loaders.text import TextLoader

from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter, TextSplitter
# Vectorstore
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores.pinecone import Pinecone
from pinecone import Pinecone as Pinecone_client
from langchain.vectorstores.base import VectorStore

from enum import Enum
from tqdm import tqdm
from interviewai.config.config import get_config
from interviewai.tools.data_structure import ModelType

# initialize pinecone
pinecone_init = Pinecone_client(api_key=get_config("PINECONE_API_KEY"))


class InterviewNamespace(Enum):
    MATERIALS = "materials"  # user owned materials
    KNOWLEDGE = "knowledge"  # shared knowledge base
    ROLE = "role"  # role specific knowledge base for job related info


DEFAULT_INDEX = "lockedinai"
index = pinecone_init.Index(DEFAULT_INDEX)


class UserIndexHelper:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.metadata = {}

    def gen_docs(
            self, loader: BaseLoader, text_splitter: TextSplitter
    ) -> List[Document]:
        self.metadata = {
            "loader_type": type(loader).__name__,
            "splitter": type(text_splitter).__name__,
            "uid": self.user_id,
            "date_uploaded": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        docs = loader.load_and_split(text_splitter)
        return docs

    def index(self, index: VectorStore, docs: List[Document]) -> List[str]:
        texts = [doc.page_content for doc in docs]
        metadatas = [{**self.metadata, **doc.metadata} for doc in docs]
        return index.add_texts(texts=texts, metadatas=metadatas)

    def get_metadata_filter(self) -> dict:
        return {"filter": {"uid": self.user_id}}

    @staticmethod
    def index_loader(
            user_id: str,
            loader: BaseLoader,
            namespace: InterviewNamespace,
    ) -> List[str]:
        # Our default pinecone dimension is 1536 so don't use the large yet
        embeddings = OpenAIEmbeddings(model=ModelType.OPENAI_EMBEDDING_003_SMALL.value)
        pc = InterviewDB(
            index=index,
            embedding=embeddings,
            namespace=namespace.value,
            text_key="text",
        )
        chunk_size = 256
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=20,
            length_function=len,
            add_start_index=True,
        )
        helper = UserIndexHelper(user_id)
        docs = helper.gen_docs(loader, text_splitter)
        return helper.index(pc, docs)

    @staticmethod
    def index_pdf(user_id: str, pdf: str, namespace: InterviewNamespace) -> List[str]:
        pdf_loader = PyPDFLoader(file_path=pdf)
        return UserIndexHelper.index_loader(user_id, pdf_loader, namespace)

    @staticmethod
    def index_docx(user_id: str, docx: str, namespace: InterviewNamespace) -> List[str]:
        docx_loader = Docx2txtLoader(file_path=docx)
        return UserIndexHelper.index_loader(user_id, docx_loader, namespace)

    @staticmethod
    def index_txt(user_id: str, txt: str, namespace: InterviewNamespace) -> List[str]:
        txt_loader = TextLoader(file_path=txt, encoding="utf-8")
        return UserIndexHelper.index_loader(user_id, txt_loader, namespace)

    @staticmethod
    def index_gcs(
            user_id: str, project_name: str, path: str, namespace: InterviewNamespace
    ) -> List[str]:
        # split the path into bucket and blob
        bucket, blob = path.split("/", 1)
        logging.info(f"bucket: {bucket}, blob: {blob}")
        gcs_loader = GCSFileLoader(project_name=project_name, bucket=bucket, blob=blob)
        return UserIndexHelper.index_loader(user_id, gcs_loader, namespace)


class InterviewDB(Pinecone):
    def add_texts(
            self,
            texts: Iterable[str],
            metadatas: Optional[List[dict]] = None,
            ids: Optional[List[str]] = None,
            namespace: Optional[str] = None,
            batch_size: int = 32,
            **kwargs: Any,
    ) -> List[str]:
        """Run more texts through the embeddings and add to the vectorstore.

        Args:
            texts: Iterable of strings to add to the vectorstore.
            metadatas: Optional list of metadatas associated with the texts.
            ids: Optional list of ids to associate with the texts.
            namespace: Optional pinecone namespace to add the texts to.

        Returns:
            List of ids from adding the texts into the vectorstore.

        """
        if namespace is None:
            namespace = self._namespace
        # Embed and create the documents
        docs = []
        ids = ids or [str(uuid.uuid4()) for _ in texts]
        for i, text in enumerate(tqdm(texts)):
            embedding = self._embed_query(text)
            metadata = metadatas[i] if metadatas else {}
            metadata[self._text_key] = text
            docs.append((ids[i], embedding, metadata))
        # upsert to Pinecone
        self._index.upsert(vectors=docs, namespace=namespace, batch_size=batch_size)
        return ids
