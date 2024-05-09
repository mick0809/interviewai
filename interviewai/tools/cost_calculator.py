from typing import Any, Dict, List
from google.api_core.datetime_helpers import DatetimeWithNanoseconds
from interviewai.firebase import get_fs_client
import datetime

MODEL_COST_INPUT = {
    "gpt-4": 0.03,
    "gpt-4-turbo": 0.01,
    "gpt-4-0613": 0.03,
    "gpt-4-32k": 0.06,
    "gpt-4-32k-0613": 0.06,
    "gpt-3.5-turbo": 0.0015,
    "gpt-3.5-turbo-0613": 0.0015,
    "gpt-3.5-turbo-16k": 0.003,
    "gpt-3.5-turbo-16k-0613": 0.003,
}

MODEL_COST_OUTPUT = {
    "gpt-4": 0.06,
    "gpt-4-turbo": 0.03,
    "gpt-4-0613": 0.06,
    "gpt-4-32k": 0.12,
    "gpt-4-32k-0613": 0.12,
    "gpt-3.5-turbo": 0.002,
    "gpt-3.5-turbo-0613": 0.002,
    "gpt-3.5-turbo-16k": 0.004,
    "gpt-3.5-turbo-16k-0613": 0.004,
}

DEEPGRAM_COST = 0.0059


class CostCalculator:
    def __init__(self) -> None:
        self.session_costs = {}  # Dictionary to store cost for each session
        self.db = get_fs_client()
        self.timestamp = {}

    def default_cost_dict(self, key):
        self.session_costs[key] = {
            "total_cost": {
                "total_cost": 0,
                "ai_total_cost": 0,
                "input_token_count": 0,
                "output_token_count": 0,
                "dg_total_cost": 0
            },
            "chain_types": []
        }

    def cost_per_run(self, model, token_count_dict: dict) -> dict:
        input = token_count_dict["input_count"]
        output = token_count_dict["output_count"]
        ai_cost = MODEL_COST_INPUT[model] * (input / 1000) + MODEL_COST_OUTPUT[model] * (output / 1000)
        cost_info = {
            "ai_cost": ai_cost,
            "token_count_dict": token_count_dict
        }
        return cost_info

    def update_session_cost(self, user_id: str, session_id: str, ai_cost: float = None, token_info: dict = None):
        key = (user_id, session_id)
        if key not in self.session_costs:
            self.default_cost_dict(key)
        if ai_cost:
            ai_cost = round(ai_cost, 4)
            self.session_costs[key]["total_cost"]["ai_total_cost"] += ai_cost
            self.session_costs[key]["total_cost"]["total_cost"] += ai_cost
            self.session_costs[key]["total_cost"]["input_token_count"] += token_info["input_count"]
            self.session_costs[key]["total_cost"]["output_token_count"] += token_info["output_count"]
        if key in self.timestamp:
            dg_cost = self.dg_cost_calculator(user_id, session_id)
            dg_cost = round(dg_cost, 4)
            self.session_costs[key]["total_cost"]["dg_total_cost"] += dg_cost
            self.session_costs[key]["total_cost"]["total_cost"] += dg_cost

    def update_chain_type(self, user_id: str, session_id: str, chain_type: str):
        key = (user_id, session_id)
        if key not in self.session_costs:
            self.default_cost_dict(key)
        if chain_type not in self.session_costs[key]["chain_types"]:
            self.session_costs[key]["chain_types"].append(chain_type)

    def get_session_info(self, user_id: str, session_id: str) -> dict:
        key = (user_id, session_id)
        session_info = self.session_costs.get(key, None)
        return session_info

    def dg_cost_calculator(self, user_id: str, session_id: str) -> float:
        '''Calculate the cost of deepgram by current time and activated time'''
        key = (user_id, session_id)
        previous_time = self.timestamp[key]["previous_time"]
        current_timestamp = DatetimeWithNanoseconds.now(tz=datetime.timezone.utc)
        duration = current_timestamp - previous_time
        duration_seconds = duration.total_seconds()
        duration_minutes = duration_seconds / 60
        dg_total_cost = DEEPGRAM_COST * duration_minutes
        # set the activated time to current time
        self.timestamp[key]["previous_time"] = current_timestamp
        return dg_total_cost

    def store_timestamp(self, user_id: str, session_id: str):
        '''Store the activated time of deepgram'''
        key = (user_id, session_id)
        if key not in self.timestamp:
            self.timestamp[key] = {"previous_time": DatetimeWithNanoseconds.now(tz=datetime.timezone.utc),
                                   "total_time": 0}

    def add_cost_firestore(self, user_id: str, session_id: str):
        session_info = self.get_session_info(user_id, session_id)
        doc_ref = self.db.collection("users").document(user_id).collection("sessions").document(session_id)
        doc_ref.update(
            {
                "cost": session_info["total_cost"] if session_info else 0,
            }
        )
        self.clear_session_cost(user_id, session_id)

    def clear_session_cost(self, user_id: str, session_id: str):
        key = (user_id, session_id)
        self.session_costs.pop(key, None)


GlobalCostCalculator = CostCalculator()
