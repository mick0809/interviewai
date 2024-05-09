import datetime
import queue
import threading
import time
import string
from enum import Enum
from heapq import merge
from typing import Dict, List, Optional

from pydantic import BaseModel
import uuid
from interviewai import LoggerMixed
from langchain.memory import ConversationSummaryBufferMemory
from langchain_openai import ChatOpenAI
from interviewai.prompt.prompt import SUMMARY_PROMPT_001
from langchain.schema import ChatMessage, get_buffer_string
from interviewai.config.config import get_config
from interviewai.user_manager.user_preference import UserSettings, ASIAN_LANGUAGES
from interviewai.tools.data_structure import *


class TranscribeAssembler:

    def __init__(
            self,
            chat_history_queue: ChatHistoryQueue,
            user_settings: UserSettings,
            logger: LoggerMixed,
            max_phrases=10,
    ):
        self.max_phrases = max_phrases
        self.logger = logger
        self.language = user_settings.gpt_output_language
        self.transcript_data: Dict[Role, List[Transcript]] = {
            Role.INTERVIEWEE: [],
            Role.INTERVIEWER: [],
        }
        # this would let other threads know that the transcript has changed
        self.respond_interviewer_changed_event = threading.Event()
        self.respond_interviewee_changed_event = threading.Event()
        self.chat_history_queue = chat_history_queue
        self.paused = False
        self.saved_memory = ""
        self.token_limit = 16000
        self.memory = ConversationSummaryBufferMemory(
            llm=ChatOpenAI(model_name=ModelType.OPENAI_GPT_35_TURBO.value),
            max_token_limit=self.token_limit,
            prompt=SUMMARY_PROMPT_001,
        )

    def save_conversation(self, role, transcript):
        Chat_message = ChatMessage(role=role.value, content=transcript)
        self.memory.chat_memory.add_message(Chat_message)

    def get_summary(self):
        summary_buffer = self.memory.moving_summary_buffer
        messages = [message for message in self.memory.chat_memory.messages if
                    len(message.content) > 0 and message.role != "ai"]
        cur_history = get_buffer_string(
            messages=messages,
        )
        summary = f"""
        --- Conversation Summary: Long term memory summary ---
        {summary_buffer}
        --- End of Conversation Summary ---
        """
        total_summary = f"""
        {summary if len(summary_buffer) > 0 else ""}
        --- Conversation Buffer: Short term memory verbose recent transcribed conversations ---
        {cur_history}
        --- End of Conversation Buffer ---
        """
        return total_summary

    def check_transcript_len(self, transcript):
        clean_sentence = ''.join([char for char in transcript if char not in string.punctuation])
        if self.language not in ASIAN_LANGUAGES:
            transcript_len = len(clean_sentence.split())
        else:
            transcript_len = len(clean_sentence)

        if transcript_len > 2:
            return True
        else:
            return False

    def process_streamed_transcripts(
            self, transcript_queue: queue.Queue, stop_event: threading.Event
    ):
        """
        We will have a transribing service to constantly add transcripts to the queue.
        """
        empty_count = 0  # Keep track of how often the queue is empty
        while not stop_event.is_set():
            try:
                transcript: Transcript = transcript_queue.get_nowait()
                transcript_id = uuid.uuid4().hex
                transcript.request_id = transcript_id
                if transcript.role == Role.INTERVIEWEE:
                    self.transcript_data[Role.INTERVIEWEE].insert(0, transcript)
                    self.save_conversation(transcript.role, transcript.transcript)
                if transcript.role == Role.INTERVIEWER:
                    self.transcript_data[Role.INTERVIEWER].insert(0, transcript)
                    self.save_conversation(transcript.role, transcript.transcript)
                # Put the transcript into the chat history queue so frontend can overwrite the streaming tokens
                self.chat_history_queue.put(transcript)
                if (transcript.role == Role.INTERVIEWER and not self.paused and self.check_transcript_len(
                        transcript.transcript)):  # Only trigger response per interviewer's transcript.
                    self.respond_interviewer_changed_event.set()  # trigger event to let other threads know that the transcript has changed.

                elif (transcript.role == Role.INTERVIEWEE and not self.paused and self.check_transcript_len(
                        transcript.transcript)):  # Only trigger response per interviewee's transcript.
                    self.respond_interviewee_changed_event.set()  # trigger event to let other threads know that the transcript has changed.   
                else:
                    self.logger.debug(
                        f"changed_event ignored, reason: transcript role: {transcript.role}, paused: {self.paused}, transcript len: {self.check_transcript_len(transcript.transcript)}. Transcript: {transcript.transcript}"
                    )
            except queue.Empty:
                empty_count += 1  # Increment the count when the queue is empty
                sleep_time = min(
                    1.0, empty_count * 0.01
                )  # Increase the sleep time up to a maximum of 1 second
                time.sleep(sleep_time)
        self.logger.info("TranscribeAssembler thread ended...")

    def get_last(self) -> Transcript:
        """
        Get last transcript from the either interviewer or interviewee.
        AI not included.
        """
        try:
            combined_transcript = list(
                merge(
                    self.transcript_data[Role.INTERVIEWEE],
                    self.transcript_data[Role.INTERVIEWER],
                    key=lambda x: x.timestamp,
                    reverse=True,
                )
            )
            return combined_transcript[0]
        except Exception as e:
            self.logger.error(f"Error get_last: {e}")
            return Transcript(
                role=Role.INTERVIEWER,
                transcript="Welcome to Mock. Are you ready to ACE the interview?",
                timestamp=datetime.datetime.now(),
                request_id=uuid.uuid4().hex
            )

    def is_empty_transcript_data(self) -> bool:
        return len(self.transcript_data[Role.INTERVIEWEE]) == 0 and len(self.transcript_data[Role.INTERVIEWER]) == 0

    def get_transcript_block(self):
        """
        We will use this to get the transcript block to display to the user via UI.
        Also would be useful for feeding input for LLM.
        - Merges the transcripts for "You" and "Speaker" into a combined transcript, sorted by timestamp in reverse chronological order
        - Limits the combined transcript to self.max_phrases phrases
        - Joins the phrases into a single string and returns it

        Returns:
        transcript block and request_id
        """
        combined_transcript = list(
            merge(
                self.transcript_data[Role.INTERVIEWEE],
                self.transcript_data[Role.INTERVIEWER],
                key=lambda x: x.timestamp,
                reverse=True,
            )
        )
        combined_transcript = combined_transcript[: self.max_phrases]
        output = "".join(
            [
                f"[{t.timestamp}][{t.role}]:{t.transcript}"
                for t in reversed(combined_transcript)
            ]
        )
        return output, combined_transcript[0].request_id

    def clear_transcript_data(self):
        self.transcript_data[Role.INTERVIEWEE].clear()
        self.transcript_data[Role.INTERVIEWER].clear()

    def load_queue(self):
        # for testing purpose
        # put all items in queue into self.transcript_data
        while not self.chat_history_queue.empty():
            transcript: Transcript = self.chat_history_queue.get()
            if transcript.role == Role.INTERVIEWEE:
                self.transcript_data[Role.INTERVIEWEE].insert(0, transcript)
            if transcript.role == Role.INTERVIEWER:
                self.transcript_data[Role.INTERVIEWER].insert(0, transcript)
