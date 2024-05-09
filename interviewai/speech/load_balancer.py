import asyncio
from enum import Enum
import json
import logging
from interviewai.config.config import get_config
from deepgram import DeepgramClient
from google.cloud.firestore_v1.base_query import FieldFilter
from interviewai.firebase import get_fs_client

DEEPGRAM_API_KEY = get_config("DEEPGRAM_API_KEY")
DEEPGRAM_API_KEY_LIST = json.loads(get_config("DEEPGRAM_API_KEY_LIST"))['DEEPGRAM_API_KEY_LIST']

DEEPGRAM_BALANCE_WARNING = 20
DEEPGRAM_BALANCE_CRITICAL = 10

class LBMode(Enum):
    ROUND_ROBIN = 1
    BALANCE_SORTED = 2


class DeepgramLoadBalancer:
    def __init__(self, mode=LBMode.ROUND_ROBIN):
        self.db = get_fs_client()
        self.current_index = 0
        self.mode = mode
        self.key_list = DEEPGRAM_API_KEY_LIST   # List of API keys

    def get_next_key(self):
        """
        Fetches the next configuration with a sufficient balance.
        """
        if self.key_list is None:
            return DEEPGRAM_API_KEY
        else:
            if self.mode == LBMode.ROUND_ROBIN:
                best_config = self.select_key_round_robin(self.key_list)
            elif self.mode == LBMode.BALANCE_SORTED:
                sorted_configs = asyncio.run(self.check_balances(self.key_list))
                best_config = self.select_key_balance_sorted(sorted_configs)
            current_key = best_config["key"]
            current_balance = best_config["balance"]
            current_project_id = best_config["project_id"]
            logging.info(f"Current balance: {current_balance}")
            self.firestore_add_key(current_key, current_project_id, current_balance)
            return current_key

    def select_key_round_robin(self, key_list):
        """
        round robin
        """
        start_index = self.current_index
        while True:
            current_key = key_list[self.current_index % len(key_list)]
            # fallback logic to prevent infinite loop
            if self.current_index - start_index >= len(key_list):
                return current_key
            current_key_balance = asyncio.run(self.get_balance_for_key(current_key['project_id'], current_key['key']))["balance"]
            if current_key_balance> self.balance_threshold_critical:
                self.current_index += 1
                return {"key": current_key['key'], "project_id": current_key['project_id'], "balance": current_key_balance}
            else:
                self.current_index += 1
                logging.warning(f"API key {current_key['key']} has insufficient balance. Trying next key.")

    def select_key_balance_sorted(self, configs):
        """
        select the key with the highest balance
        """
        for config in configs:
            if config["balance"] > self.balance_threshold_critical:
                return config
        logging.error("No valid API keys with sufficient balance.")
        return configs[0]

    async def get_balance_for_key(self, project_id, key):
        """
        Checks the balance for a single project ID and key.
        """
        dg_client = DeepgramClient(api_key=key) # Initialize your Deepgram client with the current key
        response = dg_client.manage.v("1").get_balances(project_id=project_id)
        balance = float(response["balances"][0]["amount"])
        return {"key": key, "project_id": project_id, "balance": balance}

    async def check_balances(self, configs):
        """
        Fetches balances for all configurations and returns a sorted list.
        """
        tasks = [self.get_balance_for_key(config['project_id'], config['key']) for config in configs]
        results = await asyncio.gather(*tasks)
        # Sort the results based on balance, descending order
        sorted_results = sorted(results, key=lambda x: x['balance'], reverse=True)
        # return the highest balance and the key
        return sorted_results

    def firestore_add_key(self, key, project_id, balance):
        api_key_collection = self.db.collection("api_key")
        doc_keys = api_key_collection.where(filter=FieldFilter("api_key", "==", key)).get()
        if balance > self.balance_threshold_warning:
            status = "Healthy"
        elif balance <= self.balance_threshold_warning and balance > self.balance_threshold_critical:
            status = "Warning"
        elif balance <= self.balance_threshold_critical:
            status = "Critical"
        if len(doc_keys) != 0:
            for doc in doc_keys:
                if doc.exists:
                    doc.reference.update({"balance": balance, "type": "deepgram", "status": status})
                else:
                    api_key_collection.add(
                        {"api_key": key, "project_id": project_id, "balance": balance, "type": "deepgram",
                         "status": status})
        else:
            api_key_collection.add(
                {"api_key": key, "project_id": project_id, "balance": balance, "type": "deepgram", "status": status})

    @property
    def balance_threshold_warning(self):
        return DEEPGRAM_BALANCE_WARNING

    @property
    def balance_threshold_critical(self):
        return DEEPGRAM_BALANCE_CRITICAL