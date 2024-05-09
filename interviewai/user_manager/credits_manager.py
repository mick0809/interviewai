from enum import Enum
from interviewai.firebase import get_user_payment, get_user_info_ref
import logging
import time
from google.api_core.datetime_helpers import DatetimeWithNanoseconds
import uuid
import datetime
from dateutil import tz
from interviewai.user_manager.mail import LoopsManager, LoopsEventName
from interviewai.tools.data_structure import InterviewType

loops = LoopsManager()


class UserPaymentStatus(Enum):
    FREE = "free"
    PAID = "paid"
    ADMIN = "admin"

NEW_USER_CREDITS = 10
LOW_CREDIT_THRESHOLD = 5

COST_MAP = {
    InterviewType.GENERAL: 1,
    InterviewType.CODING: 1,
    InterviewType.COACH: 1,
    InterviewType.MOCK: 0,
    InterviewType.GENERAL_AND_COACH: 2,
    InterviewType.CODING_AND_COACH: 2,
    InterviewType.ONE_TIME_IMAGE: 3,
}

PRICE_TO_CREDITS = {
    # production
    "price_1PC7xy2LNTJ5x39U7ByEtKCT": 2400,  # yearly plan
    "price_1PC7yN2LNTJ5x39UOFTZSQwi": 600,  # 3 months plan
    "price_1PC7tV2LNTJ5x39U1x7mehsm": 200,  # monthly plan
    "price_1P87GD2LNTJ5x39UHby2NgKt": 100,  # one time payment
    # development
    "price_1PAG7i2LNTJ5x39Ux0V7J2LR": 2400,  # yearly plan
    "price_1PAG5W2LNTJ5x39UGLxMkrIw": 600,  # 3 months plan
    "price_1PAG592LNTJ5x39US5lNfL8U": 200,  # monthly plan
    "price_1P87E12LNTJ5x39UVTs4uhU5": 100,  # one time payment
}


class CreditsManager:
    """
    This class manages the billing and credits of the user.
    Top up from -
        * Refer a new user
        * Directly paid from subscription plan
    Deduct from -
        * using any service
    data structure is stored in firestore.

    Frontend:
        * Show the number of credits (credits balance)
    """

    def __init__(self, user_id) -> None:
        self.user_id = user_id
        self.load_fs()
        self.cost_map = COST_MAP

    def load_fs(self):
        """
        Loads the data from firestore.
        id: user_id
        {
            "balance": 10,  // credit balance
            "payment_status" : 0 // payment enum
        }
        """
        ref = get_user_payment(self.user_id)
        # check if payment ref exists, if not create a new one with default role.
        if not ref.get().exists:
            logging.info(
                f"User {self.user_id} does not exist in payment collection. Creating new one..."
            )
            ref.set({"balance": NEW_USER_CREDITS, "payment_status": UserPaymentStatus.FREE.value})
        self.credit_ref = ref

    @property
    def payment_status(self) -> UserPaymentStatus:
        """
        Returns the payment status of the user.
        """
        return UserPaymentStatus(self.credit_ref.get().get("payment_status"))

    def update_payment_status(self, has_subscription: bool):
        """
        Updates the payment status of the user.
        """
        if self.payment_status == UserPaymentStatus.ADMIN:
            return
        if has_subscription:
            self.credit_ref.update({"payment_status": UserPaymentStatus.PAID.value})
            logging.debug(f"{self.user_id} payment status set to PAID")
        else:
            self.credit_ref.update({"payment_status": UserPaymentStatus.FREE.value})
            logging.debug(f"{self.user_id} payment status set to FREE")

    def update_subscription_details(self, plan_id: str):
        """
        Updates the subscription details of the user to identify if it is monthly, quarterly or yearly.
        """
        if plan_id in PRICE_TO_CREDITS:
            if PRICE_TO_CREDITS[plan_id] == 100:
                self.credit_ref.update({"subscription_plan": "monthly"})
            elif PRICE_TO_CREDITS[plan_id] == 300:
                self.credit_ref.update({"subscription_plan": "quarterly"})
            elif PRICE_TO_CREDITS[plan_id] == 1200:
                self.credit_ref.update({"subscription_plan": "yearly"})
        else:
            self.credit_ref.update({"subscription_plan": "Not available"})

    def referral_topup_credit(self, user_id: str) -> None:
        reward_credit = 50
        try:
            referee_ref = get_user_info_ref(user_id)
            referee_payment_ref = get_user_payment(user_id)
            if not referee_ref.get().exists:
                logging.info(f"User {user_id} does not exist.")
                return

            referee_dict = referee_ref.get().to_dict()
            referrer_id = referee_dict.get("referrer_id")
            referee_payment_dict = referee_payment_ref.get().to_dict()
            referee_referral = referee_payment_dict.get("referral")

            if referrer_id is None:
                logging.info(f"No referrer for user {user_id}.")
                return

            elif referrer_id == user_id:
                referee_ref.update({"referrer_id": None})
                logging.info(f"Referrer is same as referee for user {user_id}.")
                return

            if referee_referral is not None and referee_referral.get("credit_rewarded") == True:
                logging.info(f"Credit already rewarded for user {user_id}.")
                return

            referrer_payment_ref = get_user_payment(referrer_id)
            if not referrer_payment_ref.get().exists:
                logging.info(f"Referrer {referrer_id} does not exist.")
                return

            # update referrer's balance and total credit earned
            referrer_payment_dict = referrer_payment_ref.get().to_dict()
            referrer_referral = referrer_payment_dict.get("referral")
            if referrer_referral is None:
                referrer_referral = self.get_default_referral()
            referrer_referral["total_credit_earned"] += reward_credit
            referrer_payment_ref.update({
                "referral": referrer_referral,
                "balance": referrer_payment_dict.get("balance", 0) + reward_credit,
            })
            self.track_transaction(reward_credit, "referral", referee_id=user_id)
            logging.info(f"Topup 50 credits to referrer {referrer_id}.")

            # update referee's balance and total credit earned
            if referee_referral is None:
                referee_referral = self.get_default_referral()
            referee_referral["credit_rewarded"] = True
            referee_referral["total_credit_earned"] += reward_credit
            referee_payment_ref.update({
                "referral": referee_referral,
                "balance": referee_payment_dict.get("balance", 0) + reward_credit,
            })
            self.track_transaction(reward_credit, "referral", referrer_id=referrer_id)
            logging.info(f"Topup 50 credits to referee {user_id}.")
            try:
                # send congratulation email to referee
                response = loops.send_event(user_id, LoopsEventName.REFERRAL_CONGRATS.value)
                logging.info(f"Send Referal Congratulation email to referee {user_id}. Status: {response}")
                # send congratulation email to referrer
                response = loops.send_event(referrer_id, LoopsEventName.REFERRAL_CONGRATS.value)
                logging.info(f"Send Referal Congratulation email to referrer {referrer_id}. Status: {response}")
            except Exception as e:
                logging.error(f"Referral Congratulation email failed: {e}")
        except Exception as e:
            logging.error(f"Referral topup credit failed: {e}")

    def get_default_referral(self) -> dict:
        """
        Returns the default referral object.
        """
        return {
            "total_credit_earned": 0,
            "credit_rewarded": False,
        }

    def get_credits(self) -> int:
        """
        Returns the number of credits the user has.
        """
        return self.credit_ref.get().get("balance")

    def topup_credit(self, amount: int = 1) -> None:
        """
        Adds the given amount to the user's balance.
        """
        original_balance = self.get_credits()
        self.credit_ref.update({"balance": original_balance + amount})
        logging.info(
            f"{self.user_id} Topup credit: {amount}. Original balance: {original_balance}. New balance: {self.get_credits()}"
        )

    def track_transaction(
            self, amount: int, transaction_type: str, price_id: str = None, **kwargs
    ) -> None:
        """
        Track the transaction in firestore
        """
        transaction_id = str(uuid.uuid4())
        data = {
            "amount": amount,
            "type": transaction_type,
            "metadata": {**{"price_id": price_id}, **kwargs},
        }
        self.credit_ref.collection("transactions").document(transaction_id).set(data)

    def topup_credit_payment(self, price_id, **kwargs):
        """
        This function is called when the user has paid for a subscription plan.
        """
        if price_id in PRICE_TO_CREDITS:
            logging.info(f"User paid for price id: {price_id}, executing topup credit.")
            self.topup_credit(PRICE_TO_CREDITS[price_id])
            self.track_transaction(
                PRICE_TO_CREDITS[price_id],
                "subscription_payment",
                price_id=price_id,
                **kwargs,
            )
        else:
            logging.info(f"Unknown price id: {price_id}, topup credit failed.")

    def deduct_credit(self, service: InterviewType) -> bool:
        """
        Would be called at the beginning of a given service
        Deducts the credit from the user's balance.
        Returns true if the deduction is successful, false otherwise.
        """
        if service not in self.cost_map:
            logging.info(f"Service {service} not found in cost map.")
            return False
        if self.payment_status == UserPaymentStatus.ADMIN:
            # ADMIN user would get unlimited access
            logging.info("ADMIN user. No deduction.")
            return True
        cost = self.cost_map[service]
        current_balance = self.get_credits()
        if current_balance < cost:
            logging.info(f"{self.user_id} Insufficient credits")
            return False
        logging.info(
            f"{self.user_id} Deducting credit: {cost}. Original balance: {self.get_credits()}. New balance: {self.get_credits() - cost}"
        )
        updated_balance = current_balance - cost
        self.credit_ref.update({"balance": updated_balance})
        # TODO: Caroline or Ze to implement email service
        if current_balance <= LOW_CREDIT_THRESHOLD:
            try:
                response = loops.send_event(self.user_id, LoopsEventName.LOW_CREDIT.value)
                logging.info(f"Send Low Credit email to {self.user_id}. Status: {response}")
            except Exception as e:
                logging.error(f"Failed to send low credit email: {e}")
        return True

"""
python interviewai/credits_manager.py
"""
if __name__ == "__main__":
    # for testing purpose
    cm = CreditsManager("test_user")
    cm.topup_credit(10)

    deduct_credit = cm.deduct_credit("copilot")
    print(deduct_credit)
    deduct_credit = cm.deduct_credit("copilot")
    print(deduct_credit)
