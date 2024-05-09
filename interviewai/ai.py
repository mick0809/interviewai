import datetime
import json
import queue
import threading
import time
import uuid

from dateutil import tz
from flask_socketio import SocketIO
from google.api_core.datetime_helpers import DatetimeWithNanoseconds
from interviewai.user_manager.user_preference import UserSettings
from langchain.schema import ChatMessage

from interviewai import LoggerMixed
from interviewai.chains.chain_manager import ChainManager
from interviewai.firebase import (
    get_fs_client,
    insert_chat_history,
    get_user_preference,
)
from interviewai.speech.dg import DGTranscriber
from interviewai.tools.cost_calculator import GlobalCostCalculator
from interviewai.tools.data_structure import ChatHistoryQueue
from interviewai.tools.util import get_interview_room
from interviewai.transcriber import (
    Role,
    InterviewType,
    ResponderType,
    TranscribeAssembler,
    Transcript,
)
from interviewai.user_manager.credits_manager import CreditsManager, UserPaymentStatus


class InterviewSession:
    """
    Usage:
    # text chat
    interview_session = InterviewSession(user_id, interview_session_id)
    interview_session.run()
    interview_session.chat(message)

    # audio bytes chat
    interview_session = InterviewSession(user_id, interview_session_id)
    dg = DGTranscriber()
    interview_session.run()
    interview_session.set_dg(dg)
    interview_session.chat_bytes(message)
    """

    sio: SocketIO

    def __init__(
            self, user_id: str, interview_session_id: str, sio: SocketIO, user_settings: UserSettings, debug=False
    ) -> None:
        self.logger = LoggerMixed(
            __name__,
            user_id=user_id,
            interview_session_id=interview_session_id,
        )
        self.sio = sio
        self.user_id = user_id
        self.interview_session_id = interview_session_id
        self.user_settings = user_settings  # user settings

        # First step initialize the Deepgram
        self.dg: DGTranscriber = None
        self.transcribe_queue = queue.Queue()  # Deepgram text will be queued in transcribe_queue and further processed by the chat_history_queue
        self.chat_history_queue = ChatHistoryQueue(user_id,
                                                   interview_session_id)  # queue on all conversation (interviewer, interviewee, AI) chat history

        # Trancsriber assembler: Get the deepgram text from the transcribe queue and put it in the chat history queue then trigger the transcriber changed event
        self.transcriber = (
            TranscribeAssembler(chat_history_queue=self.chat_history_queue, user_settings=self.user_settings,
                                logger=self.logger))
        # Responder 
        if self.interview_type not in [InterviewType.MOCK, InterviewType.COACH]:
            self.responder = GPTResponder(self, responder_type=ResponderType.RESPOND_INTERVIEWER)
        # if mock interview, no need for coach responder
        if self.interview_type not in [InterviewType.GENERAL, InterviewType.CODING]:
            self.coach_responder = CoachResponder(self, responder_type=ResponderType.RESPOND_INTERVIEWEE)
        # billing and credit manager
        self.cm = CreditsManager(user_id)
        self.credit_consumption = 0
        # Thread events
        self.stop_event = threading.Event()
        self.start_event = threading.Event()
        # Conditionally to stop the credit deductor thread
        self.condition = threading.Condition()
        # Threads
        self.transcribe_thread: threading.Thread
        if self.interview_type not in [InterviewType.MOCK, InterviewType.COACH]:
            self.responder_thread: threading.Thread
        if self.interview_type not in [InterviewType.GENERAL, InterviewType.CODING]:
            self.coach_responder_thread: threading.Thread
        self.history_generator: threading.Thread
        self.audio_transcriber_thread: threading.Thread
        self.credit_deductor_thread: threading.Thread
        self._ready = False
        # type: google.api_core.datetime_helpers.DatetimeWithNanoseconds
        self.activated_timestamp: DatetimeWithNanoseconds = None

    @property
    def interview_type(self) -> InterviewType:
        fs = get_fs_client()
        ref = (
            fs.collection("users")
            .document(self.user_id)
            .collection("sessions")
            .document(self.interview_session_id)
        )
        doc_snapshot = ref.get()
        if doc_snapshot.exists and "type" in doc_snapshot.to_dict():
            return InterviewType[doc_snapshot.get("type").upper()]
        return InterviewType.GENERAL

    @property
    def programming_language(self) -> str:
        fs = get_fs_client()
        ref = (
            fs.collection("users")
            .document(self.user_id)
            .collection("sessions")
            .document(self.interview_session_id)
        )
        doc_snapshot = ref.get()
        if doc_snapshot.exists and "programming_language" in doc_snapshot.to_dict():
            obj = doc_snapshot.get("programming_language")
            if "lan" in obj:
                return obj["lan"]
        return "Python"

    @property
    def long_running(self) -> bool:
        # if interview session is running over 2 hours.
        if self.activated_timestamp == None:
            return False
        now = datetime.datetime.now(tz.tzlocal())
        memeber_session_limit = 60 * 60 * 2  # 2 hours
        non_member_session_limit = 60 * 30  # 30 minutes
        #testing = 60  # 1 minutes
        if self.cm.payment_status == UserPaymentStatus.PAID:
            long_running = (now - self.activated_timestamp).total_seconds() > memeber_session_limit
        elif self.cm.payment_status == UserPaymentStatus.FREE:
            long_running = (now - self.activated_timestamp).total_seconds() > non_member_session_limit
        else:
            long_running = False

        if long_running:
            self.logger.debug(
                f"Long running interview session detected. Activated timestamp: {self.activated_timestamp}. Now: {now}",
                user_id=self.user_id,
                interview_session_id=self.interview_session_id,
            )
        return long_running

    @property
    def ready(self):
        return self._ready

    @ready.setter
    def ready(self, value):
        if self._ready != value:
            self._ready = value
            self.logger.info(f"ready: {value}")
            self.sio.emit("ready", value, room=get_interview_room(self.user_id))

    @property
    def paused(self):
        return self.transcriber.paused

    @paused.setter
    def paused(self, value):
        """
        This would prevent AI from responding to user's chat.
        it would prevent setting respond_interviewer_changed_event or respond_interviewee_changed_event
        """
        if self.transcriber.paused != value:
            self.transcriber.paused = value
            self.logger.info(f"paused: {value}")

    def load_fs_data(self):
        t = threading.Thread(target=self._load_fs_data)
        t.daemon = True
        t.start()

    def _load_fs_data(self):
        """
        Load data from firestore the current interview session.
        Recover persisted data including:
        * chat history
        """
        fs = get_fs_client()
        ref = (
            fs.collection("users")
            .document(self.user_id)
            .collection("sessions")
            .document(self.interview_session_id)
            .get()
        )
        self.activated_timestamp = ref.get("activated_timestamp")
        GlobalCostCalculator.store_timestamp(self.user_id, self.interview_session_id)
        data = ref.get("requests")
        transcripts = []
        for transcript in data:
            json_object = json.loads(transcript)

            transcripts.append(
                json.loads(
                    Transcript(
                        role=Role[json_object["role"].upper()],
                        transcript=json_object["transcript"],
                        timestamp=json_object["timestamp"],
                        request_id=json_object["request_id"],
                    ).json()
                )
            )

            if self.transcriber.memory:
                role = Role[json_object["role"].upper()]
                if role != Role.AI and role != Role.AI_COACH:
                    self.transcriber.memory.chat_memory.add_message(
                        ChatMessage(role=role.value, content=json_object["transcript"])
                    )
        self.sio.emit(
            "chat_persisted",
            transcripts,
            room=get_interview_room(self.user_id),
        )
        # memory buffer related logic
        if self.transcriber.memory:
            curr_buffer_length = (
                self.transcriber.memory.llm.get_num_tokens_from_messages(self.transcriber.memory.chat_memory.messages))
            self.logger.info(f"conversation buffer token before pruning: {curr_buffer_length}")

            if curr_buffer_length > self.transcriber.token_limit:
                self.logger.info(
                    f"Exceeding token limit {self.transcriber.token_limit}, throwing away old messages before send to llm")
                discarded = 0
                while curr_buffer_length > self.transcriber.token_limit:
                    self.transcriber.memory.chat_memory.pop(0)
                    curr_buffer_length = (
                        self.transcriber.memory.llm.get_num_tokens_from_messages(
                            self.transcriber.memory.chat_memory.messages
                        )
                    )
                    discarded += 1
                self.logger.info(f"Discarded {discarded} messages")
            self.transcriber.memory.prune()
            self.logger.info(f"Memory pruned: {self.transcriber.memory.moving_summary_buffer}")

    def set_dg(self, dg: DGTranscriber):
        self.dg = dg
        self.transcribe_queue = dg.sentence_splitter.transcribe_queue

    def keep_asr_alive(self, init_queue=True):
        if self.dg.running == False:
            if self.dg != None:
                self.audio_transcriber_thread = threading.Thread(
                    target=self.dg.run_dg, args=(init_queue,)
                )
                self.audio_transcriber_thread.daemon = True
                self.audio_transcriber_thread.start()
            else:
                raise Exception("DGTranscriber not set")

    def run(self):
        # start all threads
        self.transcribe_thread = threading.Thread(
            target=self.transcriber.process_streamed_transcripts,
            args=(
                self.transcribe_queue,
                self.stop_event,
            ),
        )
        self.transcribe_thread.daemon = True
        self.transcribe_thread.start()
        # Notice the comma after self.stop_event in each instance, making it a single-element tuple:
        if self.interview_type not in [InterviewType.MOCK, InterviewType.COACH]:
            self.responder_thread = threading.Thread(target=self.responder.respond_to_transcriber,
                                                     args=(self.stop_event,))
            self.responder_thread.daemon = True
            self.responder_thread.start()
        if self.interview_type not in [InterviewType.GENERAL, InterviewType.CODING]:
            self.coach_responder_thread = threading.Thread(target=self.coach_responder.respond_to_transcriber,
                                                           args=(self.stop_event,))
            self.coach_responder_thread.daemon = True
            self.coach_responder_thread.start()
        self.history_generator = threading.Thread(target=self.chat_history_generator, args=(self.stop_event,))
        self.history_generator.daemon = True
        self.history_generator.start()

        self.credit_deductor_thread = threading.Thread(target=self.credit_deductor,
                                                       args=(self.start_event, self.stop_event, self.condition))
        self.credit_deductor_thread.daemon = True
        self.credit_deductor_thread.start()

    def chat(self, message):
        self.transcribe_queue.put(
            Transcript(
                role=Role(message["role"]),
                transcript=message["transcript"],
                timestamp=datetime.datetime.now(),
            )
        )
        if "reset" in message:
            if message["reset"] == True:
                self.dg.sentence_splitter.reset_temp_sentences(Role(message["role"]))

    def chat_bytes(self, message):
        for channel in message["channels"]:
            role = Role(channel["role"])
            input_data = channel["bytes"]
            if role == Role.INTERVIEWEE:
                self.dg.audio_queue_mic.put_nowait(input_data)
            elif role == Role.INTERVIEWER:
                self.dg.audio_queue_speaker.put_nowait(input_data)

    def chat_bytes_dual_channel(self, message):
        # if it is in InterviewType.MOCK
        if self.dg and self.dg.running and self.interview_type != InterviewType.MOCK:
            self.dg.dual_channel_queue.put_nowait(message["bytes"])
        else:
            self.logger.debug(
                f"DGTranscriber not set or not running. DG: {self.dg}. Running State: {self.dg.running if self.dg else False}"
            )

    def chat_history_generator(self, stop_event: threading.Event):
        """
        Chat response generator thread, will emit AI, interviewer and interviewee.
        """
        while not stop_event.is_set():
            empty_count = 0
            try:
                chat_history: Transcript = self.chat_history_queue.get_nowait()
                self.sio.emit(
                    "chat_history",
                    json.loads(chat_history.json()),
                    room=get_interview_room(self.user_id),
                )
                insert_chat_history(
                    self.user_id, self.interview_session_id, chat_history
                )
            except queue.Empty:
                empty_count += 1  # Increment the count when the queue is empty
                sleep_time = min(
                    1.0, empty_count * 0.01
                )  # Increase the sleep time up to a maximum of 1 second
                time.sleep(sleep_time)
                continue
        self.logger.info("chat_history_generator thread ended...")

    # Main credit deductor function
    def credit_deductor(self, start_event: threading.Event, stop_event: threading.Event,
                        condition: threading.Condition):
        """
        Main Deductor thread, will deduct credit if notificed by the condition. 
        condition.wait will wait for 60 secs and constantly check if notified.
        """
        self.logger.info("Credit deductor thread started...")
        while not stop_event.is_set():
            if start_event.is_set():
                with condition:
                    notified = condition.wait(timeout=60)  # Waits up to 60 seconds for a notification
                if notified:
                    break
                self.logger.info("deducting credit...")
                # if credit is low, emit a signal to the frontend and force an early stop
                # Frontend will end the session in 5 seconds and it still didn't stop the backend will end it
                # Don't use self.stop() here, it will casue problem because frontend will keep sending chat audio bytes
                if not self.cm.deduct_credit(self.interview_type):
                    self.sio.emit("force_termination", room=get_interview_room(self.user_id))
                    time.sleep(60)
                    self.stop_event.set()
                else:
                    self.credit_consumption += self.cm.cost_map[self.interview_type]
        self.logger.info("Credit deductor thread ended...")

    def stop_credit_deductor(self):
        """
        stop the credit deductor thread
        """
        with self.condition:
            self.condition.notify()

    def stop(self):
        self.logger.debug(
            f"Stopping all threads... Current self ref: {self}",
            user_id=self.user_id,
            interview_session_id=self.interview_session_id,
        )
        # Track total credit consumption remeber to put negative sign
        self.cm.track_transaction(amount=-self.credit_consumption, transaction_type=self.interview_type.value)
        # stop all threads
        self.stop_event.set()
        # TODO stop logic is wrong, rewrite
        if self.dg:
            self.dg.set_terminated(True)
        self.transcribe_thread.join()
        self.credit_deductor_thread.join()
        if self.interview_type not in [InterviewType.MOCK, InterviewType.COACH]:
            self.responder_thread.join()
        if self.interview_type not in [InterviewType.GENERAL, InterviewType.CODING]:
            self.coach_responder_thread.join()
        self.history_generator.join()
        # no need to join DG thread. async io would do CPU damage.
        self.logger.debug(
            f"All threads stopped. Current self ref: {self}",
            user_id=self.user_id,
            interview_session_id=self.interview_session_id,
        )


class GPTResponder:
    def __init__(self, interview_session: InterviewSession, responder_type: ResponderType):
        self.logger = interview_session.logger
        self.sio = interview_session.sio
        self.transcriber = interview_session.transcriber
        self.response_interval = 2  # pause in between each transcript + LLM api call
        self.cm = ChainManager(self.sio, self.logger)
        self.credit_manager = CreditsManager(self.logger.user_id)
        self.interview_session = interview_session
        # TODO: Caesar refactor this to use the user_settings
        self.system_responder_chain = self.interview_session.user_settings.system_responder_chain
        self.user_defined_chain = self.interview_session.user_settings.user_responder_chain
        self.user_coach_chain = self.interview_session.user_settings.user_coach_chain

        if responder_type == ResponderType.RESPOND_INTERVIEWEE:
            if self.interview_session.interview_type == InterviewType.MOCK:
                self.chain_type = "chain_mock"
            elif self.user_coach_chain:
                self.chain_type = self.user_coach_chain
            else:
                self.chain_type = "default_coach"
        elif responder_type == ResponderType.RESPOND_INTERVIEWER:
            if self.user_defined_chain:
                self.chain_type = self.user_defined_chain
            else:
                self.chain_type = self.system_responder_chain
        self.update_chain(self.chain_type)

    def update_chain(self, chain_type: str):
        self.chain_type = chain_type
        self.chain = self.cm.new_chain(chain_type, transcribe_assembler=self.transcriber,
                                       user_settings=self.interview_session.user_settings)

        self.logger.info(f"Updating AI Responder chain to {chain_type}")
        GlobalCostCalculator.update_chain_type(
            self.logger.user_id, self.logger.interview_session_id, chain_type
        )

    def prune(self):
        """
        prune in non blocking way
        """

        def _prune():
            curr_buffer_length = (
                self.transcriber.memory.llm.get_num_tokens_from_messages(
                    self.transcriber.memory.chat_memory.messages
                )
            )
            if curr_buffer_length > self.transcriber.memory.max_token_limit:
                self.logger.debug(
                    f"conversation buffer token before pruning: {curr_buffer_length}, max_token_limit: {self.transcriber.memory.max_token_limit}"
                )
            # this call utilize `chain.predict` which might be unstable and slow
            self.transcriber.memory.prune()
            if (
                    len(self.transcriber.memory.moving_summary_buffer)
                    > self.transcriber.memory.max_token_limit
            ):
                self.transcriber.memory.clear()
                self.logger.debug(
                    f"force clear memory buffer due to exceeding max_token_limit, limit: {self.transcriber.memory.max_token_limit}"
                )

        # run it in non-blocking way
        t = threading.Thread(target=_prune)
        t.daemon = True
        t.start()

    def respond_to_transcriber(self, stop_event: threading.Event):
        while not stop_event.is_set():
            if self.transcriber.respond_interviewer_changed_event.is_set():
                # if the start event is not set, set it once
                if not self.interview_session.start_event.is_set():
                    self.interview_session.start_event.set()
                self.sio.emit(
                    "busy_status", True, room=get_interview_room(self.logger.user_id)
                )
                # transcripte updated, so we should generate new transribed block conversation context.
                start_time = time.time()
                self.transcriber.respond_interviewer_changed_event.clear()
                question, request_id = self.cm.gen_question(
                    self.chain_type, transcribe_assembler=self.transcriber
                )
                self.logger.info(
                    f"[Question Input]\n {question}",
                    user_id=self.logger.user_id,
                    interview_session_id=self.logger.interview_session_id,
                )
                # call LLM for inference
                # response = generate_response_from_transcript(transcript_string)
                query = f"Current question from {Role.INTERVIEWER.value}: {question}"
                response = self.chain.run(query)

                self.transcriber.save_conversation(Role.AI, response)
                self.prune()

                # self.logger.debug(f"[AI Response]\n {response}", user_id=self.logger.user_id, interview_session_id=self.logger.interview_session_id)
                # AI Response
                self.transcriber.chat_history_queue.put(
                    Transcript(
                        role=Role.AI,
                        transcript=response,
                        timestamp=datetime.datetime.now(),
                        request_id=request_id,
                    )
                )
                end_time = time.time()  # Measure end time
                execution_time = (
                        end_time - start_time
                )  # Calculate the time it took to execute the function
                self.sio.emit(
                    "busy_status", False, room=get_interview_room(self.logger.user_id)
                )

                if response != "":
                    self.response = response

                remaining_time = self.response_interval - execution_time
                if remaining_time > 0:
                    time.sleep(remaining_time)
            else:
                time.sleep(0.1)
        # check if the start event is set, if so trigger the stop_credit_deductor and set the notification condition
        if self.interview_session.start_event.is_set():
            self.interview_session.stop_credit_deductor()
        self.logger.info("GPTResponder thread ended...")


class CoachResponder(GPTResponder):
    def __init__(self, interview_session: InterviewSession, responder_type: ResponderType):
        super().__init__(interview_session, responder_type)

    def respond_to_transcriber(self, stop_event: threading.Event):
        while not stop_event.is_set():
            if self.transcriber.respond_interviewee_changed_event.is_set():
                # if the start event is not set, set it once
                if not self.interview_session.start_event.is_set():
                    self.interview_session.start_event.set()
                self.sio.emit(
                    "coach_busy_status",
                    True,
                    room=get_interview_room(self.logger.user_id),
                )
                start_time = time.time()
                self.transcriber.respond_interviewee_changed_event.clear()
                question, request_id = self.cm.gen_question(
                    self.chain_type, transcribe_assembler=self.transcriber
                )
                self.logger.info(f"[Question Input MockInterview]\n {question}")
                query = f"Current response from {Role.INTERVIEWEE.value}: {question}"

                response = self.chain.run(query)
                self.logger.debug(f"[AI Response MockInterview]\n {response}")
                transcript = Transcript(
                    role=Role.AI_COACH,
                    transcript=response,
                    timestamp=datetime.datetime.now(),
                    request_id=request_id,
                )
                self.transcriber.chat_history_queue.put(transcript)
                end_time = time.time()  # Measure end time
                execution_time = end_time - start_time
                self.sio.emit(
                    "coach_busy_status",
                    False,
                    room=get_interview_room(self.logger.user_id),
                )

                remaining_time = self.response_interval - execution_time
                if remaining_time > 0:
                    time.sleep(remaining_time)
            else:
                time.sleep(0.1)
        # check if the start event is set, if so trigger the stop_credit_deductor and set the notification condition
        if self.interview_session.start_event.is_set():
            self.interview_session.stop_credit_deductor()
        self.logger.info("AICoachResponder thread ended...")


if __name__ == "__main__":
    GPTResponder.run()
