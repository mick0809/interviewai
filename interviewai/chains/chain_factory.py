from typing import Union, List
from interviewai.user_manager.user_preference import UserSettings
from langchain.callbacks.base import BaseCallbackHandler
from langchain.llms.base import BaseLLM
from interviewai.firebase import (
    get_active_interview,
    get_goal_dict,
)
from interviewai.chains.context import *
from interviewai.prompt.prompt import (
    CONSISE_PROMPT_001,
)
from interviewai.chains.interview_callback import InterviewCallback
from interviewai.tools.cost_calculator import GlobalCostCalculator
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from interviewai.chains.base_chain import InterviewChain
from langchain_openai import ChatOpenAI
from interviewai.tools.data_structure import InterviewType


def default_callbacks(socketio, logger: LoggerMixed, stream_topic="chat_token"):
    callbacks = [InterviewCallback(socketio, logger, stream_topic)]
    return callbacks


def cost_callback(logger: LoggerMixed, model: str):
    def callback(prompt_tokens: int, result_tokens: int):
        cost_info = GlobalCostCalculator.cost_per_run(
            model,
            token_count_dict={
                "input_count": prompt_tokens,
                "output_count": result_tokens,
            },
        )
        GlobalCostCalculator.update_session_cost(
            user_id=logger.user_id,
            session_id=logger.interview_session_id,
            ai_cost=cost_info["ai_cost"],
            token_info=cost_info["token_count_dict"],
        )
        current_cost = GlobalCostCalculator.get_session_info(
            logger.user_id, logger.interview_session_id
        )
        logger.info(f"Current cost: {current_cost}")

    return callback


class ChainFactory:
    """
    A chain factory to create different chains with shared common modules
    """

    def __init__(self, socketio, logger: LoggerMixed, **kwargs) -> None:
        self.llm: BaseLLM = None
        # socketio used for emitting events
        self.socketio = socketio
        # logger contains some critical information
        self.logger = logger
        self.kwargs = kwargs
        self.ta: TranscribeAssembler = self.kwargs["transcribe_assembler"]
        self.user_settings: UserSettings = self.kwargs["user_settings"]

    def default_contexts(self) -> List[Context]:
        contexts = [
            MaterialsContext.new(self.logger.user_id, self.llm),
            MemoryContext(self.ta, memory_mode=MemoryMode.SUMMARIZATION),
        ]
        # Only fetch active interview once
        active_interview = get_active_interview(self.logger.user_id)
        goal_id = active_interview.get("goal_id")
        if goal_id:
            goal_data = get_goal_dict(goal_id, self.logger.user_id)
            contexts.append(GoalContext(goal_data, self.logger.user_id))
        # this is used for last minute details
        last_minute_details = active_interview.get("last_minute_details")
        if last_minute_details:
            contexts.append(LastMinuteContext(self.logger.user_id, last_minute_details))
        # this is used for answer structure like STAR and SOAR
        answer_structure = active_interview.get("answer_structure")
        if answer_structure:
            contexts.append(AnswerStructureContext(self.logger.user_id, answer_structure))
        return contexts

    def default_llm(
            self,
            model: str = None,
            stream_topic: str = None,
            callbacks: Union[bool, List[BaseCallbackHandler]] = False,
    ):
        """
        create a default LLM
        if callbacks = False, callbacks will not be overwritten
        """
        model_callbacks = (
            default_callbacks(self.socketio, self.logger, stream_topic)
            if callbacks == False
            else callbacks
        )
        self.llm = ChatOpenAI(
            streaming=True,
            model=model,
            openai_api_key=get_config("OPENAI_API_KEY"),
            callbacks=model_callbacks,
            verbose=False,
        )
        return self.llm

    def build(
            self,
            model: str,
            stream_topic: str,
            prompt=CONSISE_PROMPT_001,
            callbacks: Union[bool, List[BaseCallbackHandler]] = False,
    ) -> InterviewChain:
        llm = self.default_llm(model, stream_topic, callbacks)
        contexts = self.default_contexts()
        # model used for cost calculation
        ic = InterviewChain(
            contexts=contexts,
            prompt=prompt,
            llm=llm,
            cost_callback=cost_callback(self.logger, model),
            logger=self.logger,
            user_settings=self.user_settings,
        )
        return ic
