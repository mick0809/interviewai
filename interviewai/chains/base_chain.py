import base64
from interviewai.chains.context import MemoryContext
from interviewai.config.config import get_config
from langchain_core.language_models.base import BaseLanguageModel
from interviewai import LoggerMixed
from interviewai.user_manager.user_preference import UserSettings
from typing import List
import asyncio
from interviewai.chains.context import Context
from interviewai.prompt.prompt import (
    DEFAULT_PROMPT,
)
from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, stop_after_delay, before_log, after_log
from openai import OpenAI
import logging
import concurrent.futures
import base64
from PIL import Image
import io
from interviewai.tools.data_structure import InterviewType, ModelType
from interviewai.chains.interview_callback import InterviewCallback
from langchain_core.messages import HumanMessage

LLM_PREDICT_LATENCY_BUDGET = 60 * 2  # 2 minutes

logger = LoggerMixed(__name__)
logger_default = logging.getLogger(__name__)
OPENAI_API_KEY = get_config("OPENAI_API_KEY")

class InterviewChain:
    """
    Interview Chain would have incorpoerate multiple contexts for doing QA
    Retrieval QA Chain with Long & Short term memory
    """

    memory: MemoryContext = None

    def __init__(
            self,
            contexts: List[Context] = [],
            llm: BaseLanguageModel = None,
            prompt=DEFAULT_PROMPT,
            cost_callback: callable = None,
            logger: LoggerMixed = None,
            user_settings: UserSettings = None,
    ):
        self.llm = llm
        self.contexts = contexts  # we support connecting to multiple contexts
        self.instruction_prompt = prompt
        self.cost_callback = cost_callback
        self.language = user_settings.gpt_output_language
        self.logger = logger

        for context in self.contexts:
            if isinstance(context, MemoryContext):
                self.memory = context
        if self.memory is not None:
            logging.info(f"Memory Context is set to {self.memory.name}")

    def context_prompt(self, query) -> str:
        # Create a ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Use list comprehension to create a list of futures
            futures = [executor.submit(context.prompt, query) for context in self.contexts]
        prompts = [future.result() for future in concurrent.futures.as_completed(futures)]
        return "\n".join(prompts)

    def prompt(self, query) -> str:
        """
        Main prompt to combine all of the contexts together with user question.
        """
        return f"""
        -- START CONTEXT --
        You are given the following contexts that would help you to contextualize your question:
        {self.context_prompt(query)}
        -- END CONTEXT --
        """

    @retry(
        reraise=True,
        stop=(stop_after_delay(10) | stop_after_attempt(3)),
        before=before_log(logger_default, logging.INFO),
        after=after_log(logger_default, logging.INFO),
    )
    def run(self, query) -> str:
        prompted_query = f"""
        {self.prompt(query)}
        {query}
        {self.instruction_prompt}
        Your language output should be in: {self.language}
        """
        prompt_tokens = self.llm.get_num_tokens(prompted_query)

        result = self.safe_predict(prompted_query)
        # Returned resule is langchain_core.messages.ai.AIMessage
        extracted_result = result.content
        result_tokens = self.llm.get_num_tokens(extracted_result)
        if self.cost_callback:
            self.cost_callback(prompt_tokens, result_tokens)

        return extracted_result

    def safe_predict(self, query) -> str:
        """
        Predict with LLM with a timeout budget. 
        Sometimes downstream services would hang and we want to prevent that.
        No caching is needed. If the timeout budget is exceeded simply return ""
        Use this to replace self.llm.predict
        TODO: later we could also modify this to make our AI engine async and support multiple QAs at same time.
        """
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # TODO: sometimes it would hang here. Need to figure out why.
            try:
                # Try to get the result within the timeout
                future = executor.submit(self.llm.invoke, query)
                result = future.result(timeout=LLM_PREDICT_LATENCY_BUDGET)
                return result
            except concurrent.futures.TimeoutError:
                # If the function call didn't complete in time, return the cached result
                logging.error(
                    f"Timeout budget {LLM_PREDICT_LATENCY_BUDGET}s exceed for `self.llm.predict`, early stopping for user {self.logger.user_id}")
                return "Waiting AI timed out. Please try again later."

    def predict_image(self, image_base64, socketio) -> str:
        if not self.validate_and_prepare_image(image_base64):
            return "Invalid image. Please ensure it is a supported format and less than 20MB."
        prompt = "You're a professional senior software engineers for over 15 years. You're given an image of a code snippet. Solve the problem and explain your solution."
        model = ChatOpenAI(
            streaming=True,
            model=ModelType.OPENAI_GPT_4_TURBO.value,
            openai_api_key=OPENAI_API_KEY,
            callbacks=[InterviewCallback(socketio, self.logger, 'chat_token')],
            verbose=False,
            )    
        result = model.invoke(
                                [
                                    HumanMessage(
                                                    content=[
                                                                {"type":"text", "text":prompt}, 
                                                                {"type":"image_url", "image_url":{"url": f"{image_base64}"}}
                                                            ]
                                                )
                                ]
                             )
        extracted_result = result.content
        return extracted_result

    def validate_and_prepare_image(self, image_base64, max_size_mb=20):
        try:
            # Check if the base64 string is formatted properly
            if not image_base64.startswith("data:image/"):
                self.logger.error("Invalid image data")

            # Split the header from the base64 content
            header, base64_data = image_base64.split(',', 1)
            image_format = header.split(';')[0].split('/')[1]

            # Ensure the format is one of the accepted types
            if image_format.lower() not in ['jpeg', 'jpg', 'png', 'gif', 'webp']:
                self.logger.error("Unsupported image format")

            # Decode the base64 string
            image_data = base64.b64decode(base64_data)

            # Check file size
            if len(image_data) > max_size_mb * 1024 * 1024:
                self.logger.error(f"Image exceeds the maximum size of {max_size_mb}MB")

            return True  # The image is valid and within size limits
        except Exception as e:
            self.logger.error(f"Image validation failed: {e}")
            return False
