from contextvars import ContextVar
from functools import wraps
import logging
import threading
from typing import List, Optional
from abc import ABC, abstractmethod
from enum import Enum
import uuid
import concurrent.futures

from interviewai.config.config import get_config
from langchain_core.language_models import BaseLanguageModel
from langchain_core.callbacks import CallbackManager
from langchain.chains.combine_documents.base import BaseCombineDocumentsChain
from langchain.chains.combine_documents.stuff import StuffDocumentsChain
from langchain.chains.llm import LLMChain
from langchain.chains.question_answering.stuff_prompt import PROMPT_SELECTOR
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain.prompts import PromptTemplate
from interviewai.firebase import FirebaseAnswerStructure
from interviewai import LoggerMixed  # get_tracer
from interviewai.db.index import InterviewDB, InterviewNamespace, index
from interviewai.transcriber import TranscribeAssembler

# tracer = get_tracer()
CONTEXT_LATENCY_BUDGET = 0.5  # 0.5s

logger = LoggerMixed(__name__)


class TimedCache:
    def __init__(self, timeout=CONTEXT_LATENCY_BUDGET):
        self.timeout = timeout
        self.cache = {}  # check thread safe

    def get_key(self, args):
        try:
            ctx = args[0]
            return ctx.user_id
        except:
            return uuid.uuid4().hex

    def __call__(self, func):
        def wrapper(*args, **kwargs):
            # Start a daemon thread to run the function and update the cache
            thread = threading.Thread(target=self._run_func, args=(func, args, kwargs))
            thread.daemon = True
            thread.start()
            # if cache is None, run the function synchronously
            if self.get_key(args) not in self.cache:
                thread.join()
            else:
                thread.join(self.timeout)
            logging.debug(
                f"thread {thread.is_alive()}. Returning cached result: {self.cache[self.get_key(args)][:100]}..."
            )
            return self.cache[self.get_key(args)]

        return wrapper

    def _run_func(self, func, args, kwargs):
        result = func(*args, **kwargs)
        self.cache[self.get_key(args)] = result
        logging.debug(f"function finished with result: {self.cache[self.get_key(args)][:100]}...")


class TimedCacheBak:
    def __init__(self, span_name, timeout=CONTEXT_LATENCY_BUDGET):
        self.cached_result = None
        self.timeout = timeout
        # self.span_name = span_name
        # self.span_var = ContextVar("span")

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # span = tracer.start_span(self.span_name)
            # self.span_var.set(span)
            if self.cached_result is None:
                # If there's no cached result, run the function synchronously
                self.cached_result = func(*args, **kwargs)
                # span.end()
                return self.cached_result

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(func, *args, **kwargs)
                try:
                    # Try to get the result within the timeout
                    result = future.result(timeout=self.timeout)
                    self.cached_result = result  # Update the cache
                    return result
                except concurrent.futures.TimeoutError:
                    # If the function call didn't complete in time, return the cached result
                    print(f"Timeout budget {CONTEXT_LATENCY_BUDGET}s exceed")
                    # print(f"returning cached result: {self.cached_result}")
                    print(self.cached_result)
                    return self.cached_result
                # finally:
                #     # Continue the expensive fetching in background
                #     if future.running():
                #         print("Task is still running, adding callback")
                #         future.add_done_callback(self.update_cache)

        return wrapper

    def update_cache(self, future):
        self.cached_result = future.result()  # Update the cache you can print the result here
        # span = self.span_var.get(None)
        # if span is not None:
        # span.end()  # End the span here when the cache is updated


class Context(ABC):
    def __init__(self, name, description) -> None:
        self.name: str = name
        self.description: str = description
        self.user_id: str

    @abstractmethod
    def context(self, query) -> str:
        """
        Context content.
        cached version of context,  would have latency budget.
        """
        raise NotImplementedError

    def prompt(self, query) -> str:
        return f"""
        Context {self.name} -- {self.description}:
        {self.context(query)}

        End of Context {self.name}.
        """


class MemoryMode(Enum):
    CONVERSATION_BUFFER = "conversation_buffer"  # keep k most recent conversations
    SUMMARIZATION = "summarization"
    RETRIEVAL = "retrieval"  # retrieve from a vector database
    ALLOW_BlANK = "allow_blank"  # allow blank memory


class GoalContext(Context):
    """
    Provide job goal context
    goal_id: job goal firebase id
    """

    def __init__(self, goal_data: dict, user_id=str) -> None:
        self.goal_data = goal_data
        self.user_id = user_id
        self.name = "GoalContext"
        self.description = "Information about this interview"
        self.extracted_data = self.extract_goal()

    @TimedCache()
    def context(self, query) -> str:
        goal_context = f"Company: {self.extracted_data['company']}\n\nPosition: {self.extracted_data['position']}\n\nJob Description: {self.extracted_data['job_description']}\n\nCompany Detail: {self.extracted_data['company_detail']}\n"
        return goal_context

    def extract_goal(self) -> dict:
        selected_fields = {
            "company",
            "company_detail",
            "job_description",
            "position",
        }
        goal_data = {
            field: value
            for field, value in self.goal_data.items()
            if field in selected_fields
        }
        return goal_data


class MaterialsContext(Context):
    """
    Personal Materials context
    """

    def __init__(
            self,
            user_id: str,
            vectorstore: InterviewDB,
            combine_documents_chain: BaseCombineDocumentsChain,
            callback: CallbackManager,
    ) -> None:
        self.callback = callback
        self.user_id = user_id
        self.vectorstore = vectorstore
        self.combine_documents_chain = combine_documents_chain
        self.name = "MaterialsContext"
        self.description = (
            "Some personal materials about me. Use this to impersonate me."
        )

    @TimedCache()
    def context(self, query) -> str:
        res = self.vectorstore.similarity_search_with_score(
            query, filter={"uid": self.user_id}
        )
        docs = [r[0] for r in res]
        context = self.combine_documents_chain._get_inputs(docs)["context"]
        # return self.combine_documents_chain.run(
        #     input_documents=docs, question=query, callbacks=self.callback
        # )
        return context

    @staticmethod
    def new(
            user_id: str,
            llm: BaseLanguageModel,
            prompt: Optional[PromptTemplate] = None,
            namespace=InterviewNamespace.MATERIALS.value,
    ):
        logger.info(
            f"Creating MaterialsContext for user {user_id} with namespace {namespace}"
        )
        embeddings = OpenAIEmbeddings()
        pc = InterviewDB(
            index=index,
            embedding=embeddings,
            namespace=namespace,
            text_key="text",
        )
        _prompt = prompt or PROMPT_SELECTOR.get_prompt(llm)
        llm_chain = LLMChain(llm=llm, prompt=_prompt)
        document_prompt = PromptTemplate(
            input_variables=["page_content"], template="Context:\n{page_content}"
        )
        combine_documents_chain = StuffDocumentsChain(
            llm_chain=llm_chain,
            document_variable_name="context",
            document_prompt=document_prompt,
        )
        return MaterialsContext(
            user_id, pc, combine_documents_chain, llm.callback_manager
        )


class MemoryContext(Context):
    memory_convesation_k = 10  # keep k most recent conversations, only used when memory_mode is conversation_buffer

    def __init__(
            self,
            transcribe_assembler: TranscribeAssembler,
            memory_mode=MemoryMode.CONVERSATION_BUFFER,
    ) -> None:
        self.user_id = transcribe_assembler.logger.user_id
        self.transcribe_assembler = transcribe_assembler
        self.memory_mode = memory_mode
        self.name = "MemoryContext"
        self.description = """ This context is the memory of the conversation.
            """

    @TimedCache()
    def context(self, query) -> str:
        if self.memory_mode == MemoryMode.CONVERSATION_BUFFER:
            output, request_id = self.transcribe_assembler.get_transcript_block()
            return output
        elif self.memory_mode == MemoryMode.SUMMARIZATION:
            # TODO ceaser: implement summarization.
            memory_context = self.transcribe_assembler.get_summary()
            return memory_context

        elif self.memory_mode == MemoryMode.RETRIEVAL:
            # TODO haonan: implement retrieval from vector store.
            pass


class KnowledgeContext(Context):
    """
    Personal Materials context
    """

    def __init__(
            self,
            filter_type: str,
            knowledge_type: str,
            vectorstore: InterviewDB,
            combine_documents_chain: BaseCombineDocumentsChain,
            callback: CallbackManager,
    ) -> None:
        self.filter_type = filter_type
        self.knowledge_type = knowledge_type
        self.callback = callback
        self.vectorstore = vectorstore
        self.combine_documents_chain = combine_documents_chain
        self.name = "KnowledgeContext"
        self.description = (
            "Useful knowledge related to the question. Use this in the situation where you need additional information to answer the technical question."
        )

    @TimedCache()
    def context(self, query) -> str:
        res = self.vectorstore.similarity_search_with_score(
            query, filter={self.filter_type: self.knowledge_type}
        )
        docs = [r[0] for r in res]
        context = self.combine_documents_chain._get_inputs(docs)["context"]
        return context

    @staticmethod
    def new(
            filter_type: str,
            knowledge_type: str,
            llm: BaseLanguageModel,
            prompt: Optional[PromptTemplate] = None,
            namespace=InterviewNamespace.KNOWLEDGE.value,
    ):
        logger.info(
            f"Creating Knowledge context with namespace {namespace}"
        )
        embeddings = OpenAIEmbeddings()
        pc = InterviewDB(
            index=index,
            embedding=embeddings,
            namespace=namespace,
            text_key="text",
        )
        _prompt = prompt or PROMPT_SELECTOR.get_prompt(llm)
        llm_chain = LLMChain(llm=llm, prompt=_prompt)
        document_prompt = PromptTemplate(
            input_variables=["page_content"], template="Context:\n{page_content}"
        )
        combine_documents_chain = StuffDocumentsChain(
            llm_chain=llm_chain,
            document_variable_name="context",
            document_prompt=document_prompt,
        )
        return KnowledgeContext(
            filter_type, knowledge_type, pc, combine_documents_chain, llm.callback_manager
        )


class AnswerStructureContext(Context):
    def __init__(self, user_id: str, mode: FirebaseAnswerStructure) -> None:
        self.user_id = user_id
        self.mode = mode
        self.name = "AnswerStructureContext"
        self.description = "Use the following method for behavioral interview questions only when it fits the scenario you're discussing, ensuring your response is both structured and impactful"

    def context(self, query) -> str:
        if self.mode == FirebaseAnswerStructure.STAR:
            return ('STAR Method:\n'
                    'Situation: Provide the context of the task or challenge.\n'
                    'Task: Describe the task or challenge.\n'
                    'Action: Detail the actions you took to address it.\n'
                    'Result: Highlight the outcomes and what you learned')
        if self.mode == FirebaseAnswerStructure.SOAR:
            return ("SOAR Method:\n"
                    "\n"
                    "Situation: Set the scene or context.\n"
                    "Objective: State the goal you aimed to achieve.\n"
                    "Action: Explain the steps you took towards the goal.\n"
                    "Result: Summarize the achievements and impact of your actions.")
        return "N/A"


class LastMinuteContext(Context):
    def __init__(self, user_id: str, last_minute_details: str) -> None:
        self.user_id = user_id
        self.name = "LastMinuteContext"
        self.description = "Last minute details for this interview."
        self.last_minute_details = last_minute_details

    def context(self, query) -> str:
        return self.last_minute_details
