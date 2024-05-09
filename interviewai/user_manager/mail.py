from interviewai.config.config import get_config
import requests
import json
import hashlib
import logging
from interviewai.firebase import get_fs_client
from google.cloud.firestore_v1.base_query import FieldFilter
from enum import Enum

LOOPS_API_KEY = get_config("LOOPS_API_KEY")

# Loops email manager
class LoopsUserGroup(Enum):
    NEW_SIGN_UP = "New Sign Up"
    PAID = "Stripe Subscription Creation"
    RENEW_FAILED = "Stripe Subscription Renewal Failed"
    CANCELLED = "Stripe Subscription Cancelled"


class LoopsEventName(Enum):
    Non_LOGIN_7_DAYS = "7daysNoneLogin"
    Non_LOGIN_30_DAYS = "30daysNoneLogin"
    MOBILE_LOGIN = "MobileLogin"
    FIRST_TIME_COPILOT = "FirstTimeCopilot"
    LOW_CREDIT = "LowCreditRemind"
    CONV_REMINDER_14_DAYS = "14daysConvRemind"
    CONV_REMINDER_30_DAYS = "30daysConvRemind"
    REFERRAL_CONGRATS = "ReferralCongrats"


class LoopsManager:

    def __init__(self):
        self.api_url = "https://app.loops.so/api/v1/contacts"
        self.api_url_events = "https://app.loops.so/api/v1/events/send"
        self.headers = {
            "Authorization": f"Bearer {LOOPS_API_KEY}",
            "Content-Type": "application/json"
        }
        self.db = get_fs_client()

    def create_contact(self, email, first_name, last_name, subscribed, user_id, user_group) -> dict:
        """Create a new contact in Loops and put user into a group"""
        # check if the contact exists in firestore
        try:
            fs_loops_email = self.search_loops_fs(email, user_id)
            if fs_loops_email is None:
                data = {
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "subscribed": subscribed,
                    "userGroup": user_group,
                    "userId": user_id
                }
                response = requests.post(f"{self.api_url}/create", headers=self.headers, json=data)
                self.store_fs_email(email, user_group, user_id)
                response = response.text
                json_response = json.loads(response)
                if "message" in json_response and json_response["message"] == "Email or userId is already on list.":
                    response = self.update_contact(email, first_name, last_name, subscribed, user_id, user_group)   
            else:
                response = {'warning': 'Contact already exists in firestore. Skipping...'}
        except Exception as e:
            response = {'error': str(e)}
        return response

    def update_contact(self, email, first_name, last_name, subscribed, user_id, user_group) -> dict:
        """Update an existing contact in Loops"""
        data = {
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "subscribed": subscribed,
            "userGroup": user_group,
            "userId": user_id
        }
        response = requests.put(f"{self.api_url}/update", headers=self.headers, json=data)
        return response.text

    def delete_contact(self, email, user_id) -> dict:
        """Delete a contact in Loops"""
        data = {
            "email": email,
            "userId": user_id
        }
        response = requests.post(f"{self.api_url}/delete", headers=self.headers, json=data)
        return response.text

    def find_contact(self, email) -> list:
        """Find a contact in Loops"""
        response = requests.get(f"{self.api_url}/find?email={email}", headers=self.headers)
        response_list = json.loads(response.text)
        return response_list

    def send_event(self, user_id, event_name) -> dict:
        """Send an event to trigger an email in Loops"""
        data = {
            "userId": user_id,
            "eventName": event_name,
        }

        response = requests.post(f"{self.api_url_events}", headers=self.headers, json=data)
        return response.text

    def search_loops_fs(self, email: str, user_id: str):
        """Search Firestore for the member id_hash and return it."""
        # TODO: Implement Firestore search logic here
        loops_ref = self.db.collection(f'users/{user_id}/loops')
        loops_doc = loops_ref.where(filter=FieldFilter('email', '==', email)).get()
        if len(loops_doc) != 0:
            for doc in loops_doc:
                if doc.exists:
                    return doc.to_dict()['email']
                else:
                    return None
        else:
            return None

    def get_loops_email_fs(self, user_id: str):
        """Search email in firestore loops document"""
        loops_ref = self.db.collection(f'users/{user_id}/loops')
        loops_docs = loops_ref.get()
        if len(loops_docs) != 0:
            for doc in loops_docs:
                if doc.exists:
                    return doc.to_dict()['email']
        return None

    def store_fs_email(self, email, user_group, user_id):
        """Store the id_hash, email_address and tags in firestore"""
        loops_ref = self.db.collection(f'users/{user_id}/loops')
        loops_doc = loops_ref.where(filter=FieldFilter('email', '==', email)).get()
        if len(loops_doc) != 0:
            for doc in loops_doc:
                if doc.exists:
                    doc.reference.update({'email': email, 'user_group': user_group})
                else:
                    loops_ref.add({'email': email, 'user_group': user_group})
        else:
            loops_ref.add({'email': email, 'user_group': user_group})

    def update_usergroup_clerk(self, clerk_user, user_id, user_group):
        """Send payment related email to user group"""
        first_name = clerk_user["first_name"]
        last_name = clerk_user["last_name"]
        email = clerk_user["email_addresses"][0]["email_address"]
        response = self.update_contact(email, first_name, last_name, True, user_id, user_group)
        logging.info(f"Updated user {user_id} to {user_group}. Loops Status response: {response}")

    def first_time_copilot_event(self, user_id):
        """Send first time copilot event to user if it is the first time they are using copilot"""
        payment_status = self.db.document(f"payment/{user_id}").get().to_dict().get("payment_status", "free")
        if payment_status == "free":
            response = self.send_event(user_id, LoopsEventName.FIRST_TIME_COPILOT.value)
            logging.info(
                f"Send email <<{LoopsEventName.FIRST_TIME_COPILOT.value}>> event to user {user_id} about first time copilot. Status: {response}")
