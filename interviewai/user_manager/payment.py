import stripe
from interviewai.config.config import get_config
from interviewai.firebase import get_user_payment
from interviewai.user_manager.clerkapi import get_user_by_id
from interviewai.env import get_env
import logging
from tenacity import retry, stop_after_attempt, stop_after_delay, wait_fixed

stripe.api_key = get_config("STRIPE_API_KEY")


def price_id_from_checkout_complete(session_id):
    """
    Returns price_id from checkout session_id.
    """
    # Retrieve the line items in the session
    line_items = stripe.checkout.Session.list_line_items(session_id)
    price_ids = []
    # Loop through the line items and print the price IDs
    for item in line_items['data']:
        price_ids.append(item['price']['id'])
    assert len(price_ids) == 1, f"Expecting only one price_id, got {price_ids}"
    return price_ids[0]


@retry(
    reraise=True,
    wait=wait_fixed(1),
    stop=(stop_after_delay(10) | stop_after_attempt(5)),
)
def get_customer_userid(customer_id):
    return stripe.Customer.retrieve(customer_id).metadata["user_id"]


def get_or_create_customer(user_id):
    """
    Associate stripe's customer with user_id using metadata.
    """
    users = stripe.Customer.search(
        query=f"metadata['user_id']:'{user_id}'",
    )
    try:
        clerk_user = get_user_by_id(user_id)
    except Exception as e:
        logging.error(f"Failed to get clerk user {user_id}. {e}")
        clerk_user = None
    if "errors" in clerk_user:
        raise Exception(f"Failed to get clerk user {user_id}, {clerk_user}")
    if len(users.data) == 0 and clerk_user:
        logging.info(f"Found existed clerk_user, creating stripe customer for user_id: {user_id}")
        args = {}
        if (
                "email_addresses" in clerk_user
                and len(clerk_user["email_addresses"]) > 0
        ):
            args["email"] = clerk_user["email_addresses"][0]["email_address"]
        if "first_name" in clerk_user:
            args["name"] = clerk_user["first_name"]
        if "phone_numbers" in clerk_user and len(clerk_user["phone_numbers"]) > 0:
            args["phone"] = clerk_user["phone_numbers"][0]["phone_number"]

        customer = stripe.Customer.create(
            **args,
            metadata={
                "user_id": user_id,
            },
        )
        logging.debug(f"Created stripe customer {customer}")
        ref = get_user_payment(user_id)
        ref.update({"stripe_customer_id": customer.id})
        logging.info(
            f"Assoicated stripe customer {customer.id} with user_id: {user_id} in firebase payment"
        )
        return customer
    else:
        customer = users.data[0]
        return customer


def create_new_subscription(user_id, plan_id):
    customer = get_or_create_customer(user_id)
    subscription = stripe.Subscription.create(
        customer=customer.id,
        items=[
            {
                "price": plan_id,
            },
        ],
    )
    return subscription


def new_checkout_session(user_id, price_id, mode="subscription"):
    customer = get_or_create_customer(user_id)
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price": price_id,
            "quantity": 1
        }],
        mode=mode,
        success_url="https://app.lockedinai.com/app/payment",
        cancel_url="https://app.lockedinai.com/app/payment",
        customer=customer.id
    )
    return session


def get_subscriptions(user_id):
    customer = get_or_create_customer(user_id)
    subscriptions = stripe.Subscription.list(customer=customer.id)
    return subscriptions


def get_all_subscriptions(customer_id):
    subscriptions = stripe.Subscription.list(customer=customer_id)
    return subscriptions


def get_active_subscription(user_id):
    """
    Returns list of subscriptions.
    Example:
    {
        "data": [
            {}
        ]
        "has_more": false,
        "object": "list",
        "url": "/v1/subscriptions"
    }
    """
    try:
        ref = get_user_payment(user_id).get()
        stripe_customer_id = ref.get("stripe_customer_id")
        if stripe_customer_id is None:
            logging.info(f"User {user_id} does not have stripe_customer_id")
            return None
        subscriptions = stripe.Subscription.list(customer=stripe_customer_id, status='active')
        return subscriptions
    except Exception as e:
        logging.debug(f"Failed to get active subscription for user_id: {user_id}. {e}")
        return None


def has_subscription(user_id):
    subscriptions = get_active_subscription(user_id)
    if subscriptions is None or len(subscriptions.data) == 0:
        return False
    else:
        return True


def get_subscriptions_status_map(user_id):
    subscriptions = get_subscriptions(user_id)
    if subscriptions.data is None or len(subscriptions.data) == 0:
        return {}
    else:
        return {s["plan"]["id"]: s["status"] for s in subscriptions.data}


def active_subscription_price_id(user_id):
    """
    Returns price_id of active subscription.
    """
    subscriptions = get_active_subscription(user_id)
    if subscriptions is None or len(subscriptions.data) == 0:
        return None
    else:
        try:
            return subscriptions.data[0]["items"]["data"][0]["plan"]["id"]
        except KeyError:
            return None


if __name__ == "__main__":
    # print(create_new_subscription("user_2Ruhyjedi4SLbxUz3uNeFU2PmfP", "price_1NYN2JFlzx2utzjAPdtlAIRe"))
    # print(get_subscriptions("user_2Ruhyjedi4SLbxUz3uNeFU2PmfP"))
    # print(get_active_subscription("user_2WsbV17cg3emGZDTIDuVqVuswhv"))
    # print(has_subscription("user_2VNQhksoVhQUVawDaSFyGFnXn4k"))
    print(price_id_from_checkout_complete("cs_test_a1y5YXJcRVsCaTclBfsVRARASHj2ehFkKdp1I8rmcBNigKVHtnX5WQqx7S"))

"""
python interviewai/payment.py
"""
