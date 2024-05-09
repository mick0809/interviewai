import queue
from enum import Enum
from pydantic import BaseModel
from typing import Dict, List, Optional
import datetime


class Role(Enum):
    INTERVIEWER = "interviewer"
    INTERVIEWEE = "interviewee"
    AI = "ai"
    AI_COACH = "ai_coach"
    IMAGE_CONTEXT = "image_context"


class InterviewType(Enum):
    # with AI Copilot ONLY
    GENERAL = "general"  # Only with AI Copilot
    CODING = "coding"  # Only with AI Copilot
    # with AI Copilot and AI Coach
    GENERAL_AND_COACH = "general_and_coach"  # AI Copilot and AI Coach
    CODING_AND_COACH = "coding_and_coach"  # AI Copilot and AI Coach
    # with AI Coach ONLY
    MOCK = "mock"  # Only with AI Coach for mock: no need for dual channel
    COACH = "coach"  # Only with AI Coach for live interview: need dual channel
    ONE_TIME_IMAGE = "one_time_image"  # Only with AI Copilot for image processing

class ResponderType(Enum):
    RESPOND_INTERVIEWER = "respond_interviewer"
    RESPOND_INTERVIEWEE = "respond_interviewee"


class ModelType(Enum):
    OPENAI_GPT_35_TURBO = "gpt-3.5-turbo"  # 16k Maximum output 4096 tokens
    OPENAI_GPT_4_TURBO = "gpt-4-turbo"  # 128k with vision,Maximum output 4096 tokens vision request can use JSON mode and fucntion calling
    AZURE_GPT_35_TURBO = "gpt-35-turbo"  # 16k Maximum output 4096 tokens
    AZURE_GPT_4_TURBO = "gpt-4-turbo"  # 128k with vision,Maximum output 4096 tokens vision request can use JSON mode and fucntion calling
    OPENAI_EMBEDDING_003_SMALL = "text-embedding-3-small"  # Output Dimension 3072
    OPENAI_EMBEDDING_003_LARGE = "text-embedding-3-large"  # Output Dimension 1536
    OPENAI_EMBEDDING_002 = "text-embedding-3-small"  # Output Dimension 1536


class Transcript(BaseModel):
    role: Role
    transcript: str  # dont forget to add new line.
    timestamp: datetime.datetime
    request_id: Optional[
        str
    ]  # this is for AI response to human's question one on one correspondence.


class ChatHistoryQueue(queue.Queue):
    """
    Special queue for storing chat history (Transcript)
    """

    def __init__(self, user_id, interview_session_id, maxsize: int = 0) -> None:
        self.user_id = user_id
        self.interview_session_id = interview_session_id
        super().__init__(maxsize)

    def put(self, item: Transcript, block: bool = True, timeout: int = None) -> None:
        super().put(item, block, timeout)

    def get(self, block: bool = True, timeout: int = None) -> Transcript:
        item: Transcript = super().get(block, timeout)
        return item
