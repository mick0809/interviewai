import json

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from google.cloud.firestore_v1.base_query import FieldFilter
from interviewai.config.config import get_config
from enum import Enum

certs = json.loads(get_config("FIREBASE_CERTS"))

cred = credentials.Certificate(certs)
firebase_admin.initialize_app(cred, {
    'storageBucket': 'lockedinai-6fb81.appspot.com'
})


class FirebaseAnswerStructure(Enum):
    DEFAULT = "default"
    STAR = "STAR"
    SOAR = "SOAR"


def get_fs_client():
    firestore_client = firestore.client()
    return firestore_client


def get_user_info_dict(user_id) -> dict:
    fs = get_fs_client()
    ref = fs.collection("users").document(user_id)
    return ref.get().to_dict()


def get_user_info_ref(user_id) -> dict:
    fs = get_fs_client()
    ref = fs.collection("users").document(user_id)
    return ref


def get_active_interview(user_id) -> DocumentSnapshot:
    fs = get_fs_client()
    query = (
        fs.collection("users")
        .document(user_id)
        .collection("sessions")
        .where(filter=FieldFilter("active", "==", True))
    )
    data = list(query.stream())
    if len(data) != 1:
        raise Exception(f"{len(data)} active sessions detected")
    active_session = data[0]
    return active_session


def get_active_interview_id(user_id):
    active_session = get_active_interview(user_id)
    interview_session_id = active_session.id
    return interview_session_id


def get_goal_dict(goal_id, user_id):
    fs = get_fs_client()
    ref = fs.collection("users").document(user_id).collection("goals").document(goal_id)
    return ref.get().to_dict()


def insert_chat_history(user_id, interview_session_id, chat_history):
    fs = get_fs_client()
    ref = (
        fs.collection("users")
        .document(user_id)
        .collection("sessions")
        .document(interview_session_id)
    )
    ref.update({"requests": firestore.ArrayUnion([chat_history.json()])})


def create_interview_session(user_id, interview_session_id):
    fs = get_fs_client()
    ref = (
        fs.collection("users")
        .document(user_id)
        .collection("sessions")
        .document(interview_session_id)
    )
    ref.set({"active": True, "requests": [], "created_by_backend": True})


def archive_session(user_id, interview_session_id):
    fs = get_fs_client()
    ref = (
        fs.collection("users")
        .document(user_id)
        .collection("sessions")
        .document(interview_session_id)
    )
    ref.update({"archived": True, "active": False})


# Prefrences
def get_user_preference(user_id):
    fs = get_fs_client()
    ref = (
        fs.collection("users")
        .document(user_id)
        .collection("settings")
        .document("preferences")
    )
    return ref.get().to_dict()


def get_user_responder_config(user_id, document_id):
    fs = get_fs_client()
    path = f"users/{user_id}/settings/preferences/copilot_preferences/{document_id}"
    ref = (
        fs.collection(path)
    )
    return ref.get().to_dict()


def get_user_coach_config(user_id, document_id):
    fs = get_fs_client()
    path = f"users/{user_id}/settings/preferences/coach_preferences/{document_id}"
    ref = (
        fs.collection(path)
    )
    return ref.get().to_dict()


def get_user_payment(user_id):
    fs = get_fs_client()
    ref = fs.collection("payment").document(user_id)
    return ref
