import queue
import threading
import time
import datetime
from typing import Dict, List

from flask_socketio import SocketIO, join_room, rooms
import traceback

from interviewai import LoggerMixed
from interviewai.ai import InterviewSession
from interviewai.tools.util import get_interview_room
from interviewai.firebase import get_active_interview_id, get_fs_client, archive_session, get_active_interview
from interviewai.speech.dg import DGTranscriber
from interviewai.tools.cost_calculator import GlobalCostCalculator
from interviewai.user_manager.mail import LoopsManager
from interviewai.tools.data_structure import InterviewType
from interviewai.user_manager.user_preference import UserSettings

logger = LoggerMixed(__name__)

loops = LoopsManager()


class InterviewSessionManager:
    """
    This would be a singleton class and it would help manage multiple interview sessions.
    For a given authenticated user, there can be one and only one interview sessions at the same time in concurrent.
    User could have multiple socket connection, but they will eventually share the same InterviewSession
    However, user could have multiple interview sessions sequantially (finish one, start another one).
    Each interview session will have its own interview_session_id.
    """
    socketio: SocketIO

    def __init__(self, socketio):
        self.socketio = socketio
        self.interview_sessions: Dict[
            str, InterviewSession
        ] = {}  # user_id -> InterviewSession
        self.client_to_user: Dict[str, str] = {}  # socket_id -> user_id
        self.new_connection_event = threading.Event()
        self.connection_queue = queue.Queue()  # session connection

    @property
    def user_to_clients(self) -> Dict[str, List[str]]:
        result = {}
        for client, user in self.client_to_user.items():
            if user not in result:
                result[user] = []
            result[user].append(client)
        return result

    def _put_connection(self, connection):
        self.connection_queue.put(connection)
        self.new_connection_event.set()

    # ensure duplicate connections are not created
    def ensure_user_firebase_consistency(self, user_id: str):
        """
        When a session connect, we would detect if the active session id from firebase
        is consistent with the session have in server memory.
        """
        active_session_id = get_active_interview_id(user_id)
        if user_id in self.interview_sessions:
            interview_session = self.interview_sessions[user_id]
            if interview_session.interview_session_id != active_session_id:
                logger.error(
                    f"active session id from firebase is different from the one in server memory. "
                    f"firebase: {active_session_id}, server memory: {interview_session.interview_session_id}",
                    user_id=user_id,
                    interview_session_id=interview_session.interview_session_id,
                )
                try:
                    interview_session = self.get_interview_session(user_id)
                    interview_session.ready = False
                    # stop the interview session, do some clean up with datastructures and threads
                    # do it asyncly
                    t = threading.Thread(
                        target=interview_session.stop, args=(), daemon=True
                    )
                    t.start()
                    logger.info("interview session stopping asyncly", user_id=user_id,
                                interview_session_id=interview_session.interview_session_id)
                except Exception as e:
                    logger.error(
                        f"failed to end session: {e}, traceback: {traceback.format_exc()}",
                        user_id=user_id,
                        interview_session_id=interview_session.interview_session_id,
                    )
                del self.interview_sessions[user_id]
                logger.info(f"interview session force deleted", user_id=user_id)
                logger.info(
                    f"interview session inconsistency resolved", user_id=user_id
                )
                return
        logger.info(
            f"interview session consistency check passed",
            user_id=user_id,
            interview_session_id=active_session_id,
        )

    def add_new_connection(self, connection, client_id):
        try:
            join_room(get_interview_room(connection["sub"]), client_id)
        except Exception as e:
            logger.error(
                f"failed to join room {get_interview_room(connection['sub'])}: {e}",
                user_id=connection["sub"],
            )
        try:
            if connection["sub"] in self.interview_sessions:
                # if user has an interview session on server
                logger.debug(f"interview session exists on server for user {connection['sub']}",
                             user_id=connection["sub"],
                             interview_session_id=self.interview_sessions[connection["sub"]].interview_session_id)
                self.ensure_user_firebase_consistency(connection["sub"])
        except Exception as e:
            logger.error(
                f"failed to ensure user firebase consistency: {traceback.format_exc()}",
                user_id=connection["sub"],
            )
        if connection["sub"] in self.interview_sessions:
            # user already has an interview session
            interview_session = self.interview_sessions[connection["sub"]]
            interview_session.ready = False
            logger.info(
                f"User {connection['sub']} already has an interview session",
                user_id=connection["sub"],
                interview_session_id=interview_session.interview_session_id,
            )
            logger.info(
                f"client {client_id} is going to share the same interview session",
                user_id=connection["sub"],
                interview_session_id=interview_session.interview_session_id,
            )
            logger.info(
                f"interview session {interview_session.interview_session_id} is ready: {interview_session.ready}"
            )
            # a user already has an interview session, check if DG is ready
            if interview_session.dg is not None and interview_session.dg.terminated:
                logger.info(f"DG is terminated, respawn the session")
                interview_session.dg.set_terminated(False)
                interview_session.keep_asr_alive()
            interview_session.load_fs_data()
            interview_session.ready = True
        else:
            self._put_connection(connection)
        # track client id to user id mapping
        self.client_to_user[client_id] = connection["sub"]
        client_rooms = rooms(sid=client_id)
        logger.info(
            f"rooms for client_id {client_id}: {client_rooms}",
            user_id=connection["sub"],
        )
        logger.debug(
            f"client_to_user map:\n {self.client_to_user}",
            user_id=connection["sub"],
        )

    def new_connection(self, user_id: str):
        logger.info(f"create a new connection for {user_id}", user_id=user_id)
        interview_session = self.create_interview_session(user_id)
        logger.info(f"interview session created in memory", user_id=user_id)
        self.run_interview_session(interview_session)
        logger.info(
            f"interview session {interview_session.interview_session_id} running",
            user_id=user_id,
            interview_session_id=interview_session.interview_session_id,
        )

    def _process_new_connection(self, user_id):
        # Retrieve the auth from the queue
        if user_id in self.interview_sessions:
            logger.info(f"process_new_connection failed. interview session exists for {user_id}", user_id=user_id,
                        interview_session_id=self.interview_sessions[user_id].interview_session_id)
        logger.info(f"processing connection for {user_id}", user_id=user_id)
        try:
            new_connection_async = True
            logger.info(f"processing new connection for {user_id}", user_id=user_id)
            if new_connection_async:
                logger.info("new connection async", user_id=user_id)
                t = threading.Thread(
                    target=self.new_connection, args=(user_id,), daemon=True
                )
                t.start()
            else:
                self.new_connection(user_id)
        except Exception as e:

            logger.error(f"failed to process new connection: {e} \n {traceback.format_exc()}", user_id=user_id)

    def process_new_connection(self):
        # FIFO would cause user wait if connection_queue is long
        # TODO: use a priority queue to prioritize the connection
        while True:
            start_time = datetime.datetime.now()
            # Block until the event is set
            self.new_connection_event.wait()
            after_wait_time = datetime.datetime.now()
            # Reset the event
            self.new_connection_event.clear()
            clear_time = datetime.datetime.now()
            connection = self.connection_queue.get()
            user_id = connection["sub"]
            self._process_new_connection(user_id)

    @staticmethod
    def new(sio):
        im = InterviewSessionManager(sio)
        # new connection handling thread
        t = threading.Thread(target=im.process_new_connection, args=())
        t.daemon = True
        t.start()
        # long running connection handling thread
        t_clean = threading.Thread(target=im.clean_long_running_connection, args=())
        t_clean.daemon = True
        t_clean.start()
        return im
    
    def clean_long_running_connection(self):
        logger.debug(f"cleaning long running connection thread started")
        while True:
            try:
                to_end: Dict[str, InterviewSession] = {}
                for user_id, interview_session in self.interview_sessions.items():
                    if interview_session.long_running:
                        to_end[user_id] = interview_session
                to_end_id_map = {user_id: interview_session.interview_session_id for user_id, interview_session in to_end.items()}
                logger.info(f"long running connection: {to_end_id_map}")
                # another for loop to avoid RuntimeError: dictionary changed size during iteration
                
                for user_id, interview_session in to_end.items():
                    logger.info(f"cleaning long running connection for {user_id}", user_id=user_id, interview_session_id=interview_session.interview_session_id)
                    interview_session.sio.emit("force_termination", room=get_interview_room(user_id))
                    time.sleep(30)
                    # if somehow frontend didn't shutdown session properly check if the user is still in long running connection
                    if user_id in self.interview_sessions:
                        self.end_session(user_id)
            except Exception as e:
                logger.debug(f"failed to clean long running connection: {e} \n {traceback.format_exc()}")
            time.sleep(60)  # run every 1 min

    def create_interview_session(self, user_id: str) -> InterviewSession:
        if user_id in self.interview_sessions:
            raise Exception(f"User {user_id} already has an interview session")

        # we will always assume frontend would create a new interview session document with id
        interview_session_id = get_active_interview_id(user_id)
        self.interview_sessions[user_id] = InterviewSession(
            user_id, interview_session_id, self.socketio, UserSettings(user_id)
        )

        try:
            loops.first_time_copilot_event(user_id)
        except Exception as e:
            logger.error(f"failed to send first time copilot event: {e}")
        return self.interview_sessions[user_id]

    def run_interview_session(self, interview_session: InterviewSession):
        logger.info(
            "Creating a new interview session in session manager...",
            user_id=interview_session.user_id,
        )

        interview_session.ready = False
        dg = DGTranscriber(
            logger=interview_session.logger,
            is_dual_channel=interview_session.interview_type != InterviewType.MOCK,
            sio=interview_session.sio,
            on_close_callback=self.respawn_session,
            user_id=interview_session.user_id,
            is_mock=interview_session.interview_type == InterviewType.MOCK,
            user_settings=interview_session.user_settings,
        )
        # TODO: set_dg need to run first, later we need to figure out why
        interview_session.load_fs_data()
        interview_session.set_dg(dg)
        interview_session.keep_asr_alive()  # only start to run ASR when audio is received
        interview_session.run()
        interview_session.ready = True

    def get_interview_session(self, user_id: str) -> InterviewSession:
        if user_id not in self.interview_sessions:
            raise Exception(f"User {user_id} does not have an interview session")
        return self.interview_sessions[user_id]

    def check_any_clients(self, user_id: str) -> bool:
        return (
            len(self.user_to_clients[user_id]) > 0
            if user_id in self.user_to_clients
            else False
        )

    def remove_client(self, client_id: str) -> str:
        """
        return user
        """
        if client_id in self.client_to_user:
            user = self.client_to_user[client_id]
            del self.client_to_user[client_id]
            if not self.check_any_clients(user) and user in self.interview_sessions:
                if user not in self.interview_sessions:
                    logger.debug(f"no interview session for user {user} when removing client {client_id}", user_id=user)
                    return user
                logger.info("user has no more clients, shutting down DG")
                session = self.interview_sessions[user] if user in self.interview_sessions else None
                if session is None:
                    return user
                if session.dg:
                    session.dg.running = False
                    session.dg.set_terminated(True)
                    # This is a hacky way to stop the credit_ductor to keep killing the credit
                    session.start_event.clear()
            return user

    def end_session_by_client(self, client_id: str):
        user_id = self.client_to_user[client_id]
        self.end_session(user_id)

    def end_session(self, user_id: str):
        """
        though a user could still connected (client -> user map is unchanged),
        we shall
        1. stop the interview session by given user id
        2. remove the interview session from the interview session map
        """
        interview_session = self.get_interview_session(user_id)
        interview_session.ready = False
        # archive from firestore
        archive_session(user_id, interview_session.interview_session_id)
        # stop the interview session, do some clean up with datastructures and threads
        interview_session.stop()
        self.finish_interview_session(user_id)
        logger.info(
            f"interview session ended for user {user_id}",
            user_id=user_id,
            interview_session_id=interview_session.interview_session_id,
        )
        # update deepgram cost
        GlobalCostCalculator.update_session_cost(
            user_id, interview_session.interview_session_id
        )
        session_info = GlobalCostCalculator.get_session_info(
            user_id, interview_session.interview_session_id
        )

        logger.info(
            f"User:{user_id} cost in session {interview_session.interview_session_id}: {session_info}"
        )
        GlobalCostCalculator.add_cost_firestore(
            user_id, interview_session.interview_session_id
        )

    def respawn_session(self, user_id: str):
        """
        Return False if we want to respawn the session
        Return True if we don't want to respawn the session
        """
        # self.end_session(user_id)
        # dont need to kill the whole session anymore. too expensive
        if user_id not in self.user_to_clients:
            logger.info(
                f"no active client for this user: {user_id}, no need to respawn DG...",
                user_id=user_id,
            )
            return True
        return False

    def get_interview_session_by_client(self, client_id: str) -> InterviewSession:
        if client_id not in self.client_to_user:
            raise Exception(f"Client {client_id} does not have an interview session")
        user_id = self.client_to_user[client_id]
        return self.get_interview_session(user_id)

    def get_interview_session_by_client_with_timeout(
            self, client_id: str, timeout: int = 3
    ) -> InterviewSession:
        # TODO: this is a hacky way to wait for the interview session to be ready
        # use get_interview_session_by_client and retry until timeout
        start_time = time.time()
        while True:
            try:
                return self.get_interview_session_by_client(client_id)
            except Exception as e:
                if time.time() - start_time > timeout:
                    raise e
                time.sleep(0.1)

    def has_interview_session(self, client_id: str) -> bool:
        if client_id not in self.client_to_user:
            return False
        user_id = self.client_to_user[client_id]
        if user_id not in self.interview_sessions:
            return False
        return True

    def pause_resume_by_client(self, client_id: str, paused: bool):
        try:
            interview_session = self.get_interview_session_by_client(client_id)
            interview_session.paused = paused
        except Exception as e:
            logger.info(f"failed to pause/resume: {e}")

    def finish_interview_session(self, user_id: str):
        if user_id not in self.interview_sessions:
            logger.info(f"User {user_id} does not have an interview session", user_id=user_id)
            return
        # persist the interview session data to Firestore.
        # TODO
        interview_session_id = self.interview_sessions[user_id].interview_session_id
        del self.interview_sessions[user_id]
        logger.info(
            f"{user_id}'s interview session deleted and finished",
            user_id=user_id,
            interview_session_id=interview_session_id,
        )
