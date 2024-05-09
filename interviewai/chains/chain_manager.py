from langchain.chains.llm import LLMChain
from langchain.chains.base import Chain
from langchain.prompts.prompt import PromptTemplate
from typing import Tuple
from interviewai.chains.chain_assembler import (
    chain_lengthy,
    chain_concise,
    chain_default,
    chain_mock_interview,
    followup_chain_001,
    coach_default_chain,
    user_responder_chain,
    user_coach_chain,
)
from interviewai.transcriber import TranscribeAssembler
from interviewai.env import get_env
from interviewai.tools.data_structure import ModelType
import logging

if get_env() == "prod":
    logging.info("Production Environment Detected. Using Production Chains.")
    CHAIN_MAP = {
        # Prod user facing
        "concise": chain_concise,
        "default": chain_default,
        "lengthy": chain_lengthy,
        "chain_mock": chain_mock_interview,
        "default_coach": coach_default_chain,
        "user_responder_chain": user_responder_chain,
        "user_coach_chain": user_coach_chain,
    }
else:
    CHAIN_MAP = {
        # Prod user facing
        "chain_mock": chain_mock_interview,
        "concise": chain_concise,
        "default": chain_default,
        "lengthy": chain_lengthy,
        "default_coach": coach_default_chain,
        # Dev purpose
        "followup_chain_001": followup_chain_001,
        "user_responder_chain": user_responder_chain,
        "user_coach_chain": user_coach_chain,
    }


class ChainManager:
    def __init__(self, socketio, logger) -> None:
        self.logger = logger
        self.socketio = socketio
        self.model = ModelType.OPENAI_GPT_35_TURBO.value
        self.supported_types = list(CHAIN_MAP.keys()) + ["dynamic_prompt"]

    def gen_question(self, chain_type: str, **kwargs) -> Tuple[str, str]:
        """
        Returns both question and request_id
        """
        ta: TranscribeAssembler = kwargs["transcribe_assembler"]
        if chain_type in CHAIN_MAP:
            return ta.get_last().transcript, ta.get_last().request_id
        raise Exception(f"{chain_type} chain type not supported.")

    def new_chain(self, chain_type: str, **kwargs) -> Chain:
        if chain_type not in self.supported_types:
            raise Exception(
                f"{chain_type} chain type not supported. Supported types: {self.supported_types}"
            )
        return CHAIN_MAP[chain_type](self.socketio, self.model, self.logger, **kwargs)


def update_prompt(llm: LLMChain, prompt: str) -> None:
    new_prompt = PromptTemplate(input_variables=["transcript"], template=prompt)
    llm.prompt = new_prompt
