from interviewai import LoggerMixed
from interviewai.chains.context import *
from interviewai.prompt.prompt import (
    LENGTHY_PROMPT,
    DEFAULT_PROMPT,
    MOCK_INTERVIEW_PROMPT,
    FOLLOWUP_PROMPT_001,
    CONSISE_PROMPT_001,
    COACH_PROMPT_DEFAULT,
    USER_DEFINED_RESPONDER_PROMPT,
    USER_DEFINED_COACH_PROMPT,
)
from interviewai.chains.chain_factory import ChainFactory
from interviewai.tools.data_structure import ModelType
from interviewai.user_manager.user_preference import UserSettings

logger = LoggerMixed(__name__)


#########################
### Chain Definitions ###
#########################
def chain_mock_interview(socketio, model, logger: LoggerMixed, **kwargs):
    cf = ChainFactory(socketio, logger, **kwargs)
    # Streaming output in the frontend is the interviewer text box
    ic = cf.build(model=ModelType.OPENAI_GPT_4_TURBO.value, stream_topic="coach_token", prompt=MOCK_INTERVIEW_PROMPT)
    return ic


def chain_concise(socketio, model, logger: LoggerMixed, **kwargs):
    cf = ChainFactory(socketio, logger, **kwargs)
    ic = cf.build(model=model, stream_topic="chat_token", prompt=CONSISE_PROMPT_001)
    return ic


def chain_default(socketio, model, logger: LoggerMixed, **kwargs):
    cf = ChainFactory(socketio, logger, **kwargs)
    ic = cf.build(model=model, stream_topic="chat_token", prompt=DEFAULT_PROMPT)
    return ic


def chain_lengthy(socketio, model, logger: LoggerMixed, **kwargs):
    cf = ChainFactory(socketio, logger, **kwargs)
    ic = cf.build(model=model, stream_topic="chat_token", prompt=LENGTHY_PROMPT)
    return ic


def followup_chain_001(socketio, model, logger: LoggerMixed, **kwargs):
    cf = ChainFactory(socketio, logger, **kwargs)
    ic = cf.build(model=ModelType.OPENAI_GPT_4_TURBO.value, stream_topic="chat_token", prompt=FOLLOWUP_PROMPT_001)
    return ic


def coach_default_chain(socketio, model, logger: LoggerMixed, **kwargs):
    cf = ChainFactory(socketio, logger, **kwargs)
    ic = cf.build(model=model, stream_topic="coach_token", prompt=COACH_PROMPT_DEFAULT)
    return ic


def user_responder_chain(socketio, model, logger: LoggerMixed, **kwargs):
    cf = ChainFactory(socketio, logger, **kwargs)
    user_settings: UserSettings = kwargs["user_settings"]
    ic = cf.build(model=model, stream_topic="chat_token", prompt=USER_DEFINED_RESPONDER_PROMPT.format(
        industry=user_settings.user_responder_config.get("industry", "general"),
        instruction=user_settings.user_responder_config.get("instruction", "general"),
        tone=user_settings.user_responder_config.get("tone", "Friendly"),
        output_length=user_settings.user_responder_config.get("output_length", "general"),
        answer_format=user_settings.user_responder_config.get("answer_format", "general"),
    ))
    return ic


def user_coach_chain(socketio, model, logger: LoggerMixed, **kwargs):
    cf = ChainFactory(socketio, logger, **kwargs)
    user_settings: UserSettings = kwargs["user_settings"]
    ic = cf.build(model=model, stream_topic="coach_token", prompt=USER_DEFINED_COACH_PROMPT.format(
        industry=user_settings.user_coach_config.get("industry", "general"),
        topic=user_settings.user_coach_config.get("topic", "general"),
        topic_instruction=user_settings.user_coach_config.get("topic_instruction", "general"),
        additional_instruction=user_settings.user_coach_config.get("additional_instruction", ""),
        tone=user_settings.user_coach_config.get("tone", "Friendly"),
        output_length=user_settings.user_coach_config.get("output_length", "general"),
    ))
    return ic
