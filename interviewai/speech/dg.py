import asyncio
import datetime
import queue
import numpy as np
import traceback
import json
import threading

from interviewai import LoggerMixed
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions
)
from typing import Callable, Dict
from interviewai.transcriber import Role, Transcript
from interviewai.tools.util import get_interview_room
from flask_socketio import SocketIO
from interviewai.user_manager.user_preference import UserSettings
from interviewai.speech.load_balancer import DeepgramLoadBalancer

DgLoadBalancer = DeepgramLoadBalancer()


class SentenceSplitter:
    def __init__(self, logger: LoggerMixed, sio: SocketIO):
        self.logger = logger
        self.sio = sio
        self.interviewer_temp_sentence = ""
        self.interviewee_temp_sentence = ""
        self.transcribe_queue = queue.Queue()

    def add_transcript(self, msg, role):
        transcript = msg["channel"]["alternatives"][0]["transcript"]
        if role == Role.INTERVIEWER:
            self.interviewer_temp_sentence += " " + transcript
        else:
            self.interviewee_temp_sentence += transcript

    def reset_temp_sentences(self, role):
        if role == Role.INTERVIEWER:
            self.interviewer_temp_sentence = ""
        else:
            self.interviewee_temp_sentence = ""

    def emit_result(self, role, output):
        streaming_transcript = json.loads(
            Transcript(
                role=role,
                transcript=f"""{output} """,
                timestamp=datetime.datetime.now(),
            ).json()
        )
        if role == Role.INTERVIEWER:
            streaming_socket = "streaming_interviewer"
        else:
            streaming_socket = "streaming_interviewee"
        self.sio.emit(
            streaming_socket,
            streaming_transcript,
            room=get_interview_room(self.logger.user_id),
        )

    def put_final_transcript_into_queue(self, role):
        """
        Put the final transcript into the transcribe queue and it will trigger the AI response EG, transcriber.py self.respond_interviewer_changed_event.set() 
        """
        timestamp_now = datetime.datetime.now()
        self.transcribe_queue.put(
            Transcript(
                role=role,
                transcript=f"""{(self.interviewer_temp_sentence
                                 if role == Role.INTERVIEWER
                                 else self.interviewee_temp_sentence)}\n""",
                timestamp=timestamp_now,
            )
        )
        self.logger.debug(
            f"Added new transcript to queue: {self.transcribe_queue.qsize()}"
        )

    def process_final_sentence(self, msg):
        if msg["type"] == "Results" and "channel" in msg and msg["channel"]["alternatives"][0]["transcript"] != "":
            role = detect_role(msg)
            if msg["speech_final"] == False:
                """
                Since is_final is only available when the interim result is available. When dg detect most accurate partial sentence,
                it will trigger the 'is_final' to be True. Then we emit this partial sentence to the client.
                """
                if msg["is_final"] == True:
                    self.emit_result(role, msg["channel"]["alternatives"][0]["transcript"])
                    self.add_transcript(msg, role)
            else:
                # sentence finished
                self.add_transcript(msg, role)
                self.put_final_transcript_into_queue(role)
                self.reset_temp_sentences(role)

        elif msg["type"] == "UtteranceEnd":
            """
            EG.{"type":"UtteranceEnd", "channel": [0,2], "last_word_end": 3.1}
            1. Triggered if you receive an UtteranceEnd message with no preceding speech_final=true message and send the 
            last-received transcript for further processing.
            2. This is independent of the Results type and will detect the end of the speech of specific channel and speaker
            without interrupted by the noise.
            https://developers.deepgram.com/docs/understanding-end-of-speech-detection
            """
            role = detect_role(msg)
            if role == Role.INTERVIEWER and self.interviewer_temp_sentence:
                self.logger.debug("triggered interviewer utterance end")
                self.put_final_transcript_into_queue(role)
                self.reset_temp_sentences(role)
            elif role == Role.INTERVIEWEE and self.interviewee_temp_sentence:
                self.logger.debug("triggered interviewee utterance end")
                self.put_final_transcript_into_queue(role)
                self.reset_temp_sentences(role)


class UltraInterimSentenceSplitter(SentenceSplitter):
    def __init__(self, logger: LoggerMixed, sio: SocketIO):
        super().__init__(logger, sio)
        self.interviewer_interim_temp_sentence = ""
        self.interviewee_interim_temp_sentence = ""

    def overlap(self, str1, str2, direction):
        overlap = ""
        min_len = min(len(str1), len(str2))
        if direction == "back":
            for i in range(1, min_len + 1):
                if str1[-i:] == str2[:i]:
                    overlap = str2[:i]
        elif direction == "front":
            for i in range(1, min_len + 1):
                if str1[:i] == str2[:i]:
                    overlap = str2[-i:]

        return overlap

    def process_interim_transcript(self, role, transcript):
        start_idx = len(self.interviewer_interim_temp_sentence)
        if start_idx >= len(transcript):
            """ 
            1. hi how are you doing my homie (prev)
            2. my homie? I'am (cur)
            Results:
            I'am
            Overlap: my homie
            """
            back_overlap = self.overlap(self.interviewer_interim_temp_sentence.lower(), transcript.lower(), "back")
            if len(back_overlap) > 0:
                start_idx = len(back_overlap)
                output = transcript[start_idx:].strip()
                self.interviewer_interim_temp_sentence = transcript
            else:
                """ 
                1. hi how are you doing (prev)
                2. hi how are (cur)
                Results:
                emit nothing 
                Overlap: hi how are
                """
                front_overlap = self.overlap(transcript.lower(), self.interviewer_interim_temp_sentence.lower(),
                                             "front")
                if len(front_overlap) > 0:
                    output = ""
                else:
                    """
                    this is the sitaution where prev sentence and current sentence are not overlapped at all
                    """
                    start_idx = 0
                    output = transcript[start_idx:].strip()
                    self.interviewer_interim_temp_sentence = transcript
        else:
            output = transcript[start_idx:].strip()
            self.interviewer_interim_temp_sentence = transcript
        self.emit_result(role, output)

    def reset_temp_sentences(self, role):
        if role == Role.INTERVIEWER:
            self.interviewer_temp_sentence = ""
            self.interviewer_interim_temp_sentence = ""
        else:
            self.interviewee_temp_sentence = ""
            self.interviewee_interim_temp_sentence = ""

    def process_final_sentence(self, msg):
        if msg["type"] == "Results" and "channel" in msg and msg["channel"]["alternatives"][0]["transcript"] != "":
            role = detect_role(msg)
            transcript = msg["channel"]["alternatives"][0]["transcript"]
            if msg["speech_final"] == False:
                self.process_interim_transcript(role, transcript)
                if msg['is_final'] == True:
                    self.add_transcript(msg, role)
            else:
                # sentence finished
                self.add_transcript(msg, role)
                self.put_final_transcript_into_queue(role)
                self.reset_temp_sentences(role)
        elif msg["type"] == "UtteranceEnd":
            role = detect_role(msg)
            if role == Role.INTERVIEWER and self.interviewer_temp_sentence:
                self.logger.debug("triggered utterance end")
                self.put_final_transcript_into_queue(role)
                self.reset_temp_sentences(role)
            elif role == Role.INTERVIEWEE and self.interviewee_temp_sentence:
                self.logger.debug("triggered utterance end")
                self.put_final_transcript_into_queue(role)
                self.reset_temp_sentences(role)


def merge_audio(data_mic, data_speaker):
    audio_mic = np.frombuffer(data_mic, dtype=np.int16)
    audio_speaker = np.frombuffer(data_speaker, dtype=np.int16)

    # Check if the two audio streams have the same length. If not, pad the shorter one with zeros
    if len(audio_mic) < len(audio_speaker):
        audio_mic = np.pad(audio_mic, (0, len(audio_speaker) - len(audio_mic)))
    elif len(audio_speaker) < len(audio_mic):
        audio_speaker = np.pad(audio_speaker, (0, len(audio_mic) - len(audio_speaker)))
    """
    # Stack the two audio streams horizontally to create a single 2-channel audio stream
    merged_audio = np.add(audio_mic, audio_speaker)

    # Convert the merged audio back into raw audio data
    merged_data = merged_audio.tobytes()
    """
    merged_audio = np.stack((audio_mic, audio_speaker), axis=-1)

    # Convert the merged audio back into raw audio data
    merged_data = merged_audio.astype(np.int16).tobytes()

    return merged_data


def detect_role(msg):
    if msg["type"] == "Results":
        audio_index = msg["channel_index"][0]
        channel_quantity = msg["channel_index"][1]
    elif msg["type"] == "UtteranceEnd":
        audio_index = msg["channel"][0]
        channel_quantity = msg["channel"][1]
    if audio_index == 0 and channel_quantity == 2:
        return Role.INTERVIEWER
    else:
        return Role.INTERVIEWEE


def detect_output_speaker(msg, threshold=0.5):
    speakers = {}  # speaker_id -> score
    for word in msg["channel"]["alternatives"][0]["words"]:
        speaker_id = word["speaker"]  # speaker_id
        if speaker_id not in speakers:
            speakers[speaker_id] = 0
        speakers[speaker_id] += 1
    # if speaker percentage is higher than threshold, return speaker_id
    for speaker_id, score in speakers.items():
        if score / len(msg["channel"]["alternatives"][0]["words"]) > threshold:
            return speaker_id

class DGTranscriber:
    def __init__(
            self,
            logger: LoggerMixed,
            sio: SocketIO,
            user_settings: UserSettings,
            is_dual_channel: bool = False,
            on_close_callback: Callable = None,
            user_id="",
            is_mock: bool = False,
    ) -> None:
        self.dg_key = DgLoadBalancer.get_next_key()
        self.dg_client = None
        self.logger = logger
        self.user_id = user_id
        self.sio = sio
        self.is_dual_channel = is_dual_channel
        self.on_close_callback = on_close_callback
        self.audio_queue_mic: asyncio.Queue
        self.audio_queue_speaker: asyncio.Queue
        self.dual_channel_queue: asyncio.Queue
        self.user_settings = user_settings
        self.sentence_splitter = UltraInterimSentenceSplitter(self.logger, self.sio)
        self.deepgram_socket = None
        self.terminated = False  # set this to True to terminate ASR. Other wise its gonna run forever. Cost $$$!!
        self.running = False
        self.options: LiveOptions = {
            # "encoding":"linear16",
            # "sample_rate":16000,
            "smart_format": True,
            "interim_results": True,
            "diarize": True,
            "channels": 2 if not is_mock else 1,
            "multichannel": True if not is_mock else False,
            "endpointing": self.user_settings.dg_endpoint,
            "model": self.user_settings.dg_model,
            "language": self.user_settings.dg_language,
            "utterance_end_ms": self.user_settings.utterance_end_ms,
        }
        self.lock = threading.Lock()

    def set_terminated(self, terminated=True):
        with self.lock:
            self.terminated = terminated

    async def shutdown(self):
        if self.deepgram_socket:
            await self.deepgram_socket.finish()
            self.logger.info("Deepgram Client shutdown complete")
            self.running = False
            self.logger.info("Deepgram not running and no audio to receive")

    async def on_dg_close(self, *args, **kwargs):
        self.logger.debug(f"Deepgram Client Closed: {args}. {kwargs}")
        self.running = False
        if self.on_close_callback:
            self.set_terminated(self.on_close_callback(self.user_id))
            if self.is_dual_channel:
                self.dual_channel_queue.put_nowait(None)
            self.audio_queue_mic.put_nowait(None)

    async def on_dg_open(self, *args, **kwargs):
        self.logger.debug(f"Deepgram connection established: {args}. {kwargs}")
        self.running = True
        self.sio.emit(
            "dg_ready",
            True,
            room=get_interview_room(self.logger.user_id),
        )

    async def on_dg_utterance_end(self, *args, **kwargs):
        self.logger.info(f"UTTERANCE END")
        self.logger.info(f"\n\n{kwargs}\n\n")
        data = vars(kwargs["utterance_end"])
        self.sentence_splitter.process_final_sentence(data)

    async def process_audio(self):
        """
        Using deepgram client
        """
        await self.initiate_deepgram()
        while True:
            with self.lock:  # Acquire the lock
                if self.terminated:
                    break
            if self.running:
                if self.is_dual_channel:
                    data = await self.dual_channel_queue.get()
                else:
                    data_mic = await self.audio_queue_mic.get()
                    # data_speaker = await self.audio_queue_speaker.get()
                    # data = merge_audio(data_mic, data_speaker)
                    # TODO, merge for dekstop audio
                    data = data_mic
                if data is None:
                    continue
                await self.deepgram_socket.send(data)
            else:
                """
                If DG is not in running state, we won't send any data to DG
                """
                await asyncio.sleep(1)
        await self.shutdown()
        self.logger.info("Deepgram Client Data Sender Terminated")

    async def get_transcript(self, *args, **kwargs):
        """
        Act as retrieving logic
        {'result': LiveResultResponse(type=......)}
        """
        data = vars(kwargs['result'])
        self.sentence_splitter.process_final_sentence(data)
        if data["type"] == "Results" and "channel" in data and data["channel"]["alternatives"][0]["transcript"] != "":
            if data["speech_final"] == True or data["is_final"] == True:
                channel_index = data["channel_index"][0]
                transcript = data["channel"]["alternatives"][0]["transcript"]
                self.logger.debug(f"[{channel_index}][Speaker:{detect_output_speaker(data)}]{transcript}")

    async def initiate_deepgram(self):
        """
        This would handle two cases.
        * Deepgram socket is closed with timeout
        * Deepgram socket has any error raised

        Keep running until recieve terminate signal
        """

        try:
            await self.new_dg()
            await self.deepgram_socket.start(self.options)
            self.logger.debug("Deepgram Client Started")
        except Exception as e:
            self.logger.error(f"Deepgram error with exception: {e} \n {traceback.format_exc()}")
            self.logger.info("Deepgram Client Terminated")
            self.running = False

    async def new_dg(self):
        config = DeepgramClientOptions(options={"keepalive": "true"})
        self.dg_client = DeepgramClient(api_key=self.dg_key, config=config)
        self.deepgram_socket = self.dg_client.listen.asynclive.v("1")

        self.logger.debug("Created new Deepgram Socket instance...")

        self.deepgram_socket.on(LiveTranscriptionEvents.Open, self.on_dg_open)
        self.deepgram_socket.on(LiveTranscriptionEvents.Transcript, self.get_transcript)
        self.deepgram_socket.on(LiveTranscriptionEvents.UtteranceEnd, self.on_dg_utterance_end)
        self.deepgram_socket.on(LiveTranscriptionEvents.Close, self.on_dg_close)
        self.deepgram_socket.on(
            LiveTranscriptionEvents.Error,
            lambda e: self.logger.error(f"Deepgram error: {e}"))
        self.deepgram_socket.on(
            LiveTranscriptionEvents.Unhandled,
            lambda e: self.logger.error(f"Deepgram unhandled: {e}"))

        self.logger.debug("Registered Deepgram Event Handler...")

    def run_dg(self, init: bool):
        """
        Main entry point.
        We will create async io loop context and run process2 with async queue created in async io loop context
        """
        if init:
            self.logger.info("Init new queue...")
            self.audio_queue_mic = asyncio.Queue()
            self.audio_queue_speaker = asyncio.Queue()
            self.dual_channel_queue = asyncio.Queue()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.process_audio())
        self.loop.close()
        self.logger.info("Deepgram Async loop closed")
