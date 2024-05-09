"""
The autopilot websocket server via socket.io

Method: think
Input: 
* session_id
* It would takes in bytes data from interviewer and interviewee.
Output:
* It would generate a AI response token by token
* Finally it would have the final response.

Method: History
Input: session_id
"""
import datetime
import logging
import traceback
import uuid

import socketio
import stripe
import svix
from engineio.payload import Payload
from flask import Flask, request, abort, jsonify
from flask_socketio import ConnectionRefusedError, SocketIO

from interviewai import LoggerMixed
from interviewai.ai import InterviewSession
from interviewai.auth import verify_jwt
from interviewai.chains.base_chain import InterviewChain
from interviewai.chains.chain_manager import CHAIN_MAP
from interviewai.config.config import get_config
from interviewai.db.index_material import index_user_material, delete_material_index
from interviewai.firebase import get_user_payment
from interviewai.session import InterviewSessionManager
from interviewai.transcriber import Role, Transcript
from interviewai.user_manager.clerkapi import get_user_by_id
from interviewai.user_manager.credits_manager import CreditsManager, InterviewType
from interviewai.user_manager.mail import LoopsManager, LoopsUserGroup, LoopsEventName
from interviewai.user_manager.payment import get_subscriptions, new_checkout_session, get_customer_userid, \
    price_id_from_checkout_complete, get_all_subscriptions
from interviewai.user_manager.view import clerk_jwt_required
from flask_cors import CORS
from interviewai.user_manager.view import app as view_app

# TODO: best design (medium)
# Old: deepgram <-(audio) server <-(audio) client
# Old: deepgram (text)-> server (text)-> client
# New:                 client (audio)-> deepgram
# New: server <-(text) client <-(text) deepgram
logger = LoggerMixed(__name__)
PORT = 5001
app = Flask(__name__)
app.config["SECRET_KEY"] = "secret!"
# https://github.com/miguelgrinberg/python-engineio/issues/142
Payload.max_decode_packets = 50

socketio = SocketIO(
    app,
    async_mode="threading",
    ping_timeout=120,
    cors_allowed_origins="*",
    # logger=True,
    # engineio_logger=True,
)

im = InterviewSessionManager.new(socketio)
loops = LoopsManager()
stripe_webhook_secret = get_config("STRIPE_WEBHOOK_SECRET")
clerk_webhook_secret = get_config("CLERK_WEBHOOK_SECRET")


@app.route("/")
def index():
    return "hello world!"


@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200


# TODO: Add clerk webhook to send the email
@app.route('/clerk_webhook', methods=['POST'])
def handle_clerk_webhook():
    headers = {
        'svix-id': request.headers.get('svix-id'),
        'svix-timestamp': request.headers.get('svix-timestamp'),
        'svix-signature': request.headers.get('svix-signature')
    }
    event = None
    payload = request.get_data()
    webhook = svix.webhooks.Webhook(clerk_webhook_secret)

    try:
        event = webhook.verify(payload, headers)
    except svix.webhooks.WebhookVerificationError as e:
        logging.error(f"Failed to verify webhook: {e}")
        logging.error(traceback.format_exc())
        abort(400)
    # event_data = json.loads(payload)
    logging.info('Event type {}'.format(event['type']))
    # Process the event data
    if event['type'] == 'user.created':
        # Extract relevant information from the event data
        email = event['data']['email_addresses'][0]['email_address']
        first_name = event['data']['first_name']
        last_name = event['data']['last_name']
        user_id = event['data']['id']
        # Create a new user in the payment reference in firestore
        cm = CreditsManager(user_id)
        # Loop Email Logic
        response = loops.create_contact(email, first_name, last_name, True, user_id, LoopsUserGroup.NEW_SIGN_UP.value)
        logging.info(f"User {user_id} created, send email response: {response}")
    return jsonify(success=True), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    event = None
    payload = request.data
    sig_header = request.headers["STRIPE_SIGNATURE"]

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, stripe_webhook_secret)
    except ValueError as e:
        # Invalid payload
        logging.error(f"Invalid payload {payload}, {e}")
        logging.error(traceback.format_exc())
        abort(400)
    except stripe.SignatureVerificationError as e:
        # Invalid signature
        logging.error(f"Invalid signature {payload}, {e}")
        logging.error(traceback.format_exc())
        abort(400)

    logging.info("Event type {}".format(event["type"]))

    # Handle the event
    if event["type"] == "payment_intent.succeeded":
        payment_intent = event["data"]["object"]
        payment_description = payment_intent["description"]
        logging.info(f"event type: {event['type']}, customer_id: {payment_intent['customer']}")
        if not payment_intent["customer"]:
            logging.info(f"Customer ID is None for payment intent {payment_intent['id']}")
            return jsonify(success=True)
        try:
            user_id = get_customer_userid(payment_intent["customer"])
            clerk_user = get_user_by_id(user_id)
            cm = CreditsManager(user_id)
            if payment_description == "Subscription creation":
                # send email if payment creation successful
                loops.update_usergroup_clerk(clerk_user, user_id, LoopsUserGroup.PAID.value)
            elif payment_description == "Subscription update":
                cm.update_payment_status(True)
                logging.info(f"Updated user {user_id} payment status to paid")
        except Exception as e:
            logging.error(f"Failed to update contact for user {user_id} because {e}")

    if event["type"] == "payment_intent.payment_failed":
        payment_intent = event["data"]["object"]
        payment_description = payment_intent["description"]
        logging.info(f"event type: {event['type']}, customer_id: {payment_intent['customer']}")
        if not payment_intent["customer"]:
            logging.info(f"Customer ID is None for payment intent {payment_intent['id']}")
            return jsonify(success=True)
        try:
            if payment_description == "Subscription update":
                # send email if renew failed
                user_id = get_customer_userid(payment_intent["customer"])
                clerk_user = get_user_by_id(user_id)
                loops.update_usergroup_clerk(clerk_user, user_id, LoopsUserGroup.RENEW_FAILED.value)
        except Exception as e:
            logging.error(f"Failed to update contact or send event for user {user_id} becasue {e}")

    if event["type"] == "customer.subscription.updated":
        sub_data = event["data"]["object"]
        sub_status = sub_data["status"]
        cancel_at = sub_data["cancel_at"]
        logging.info(f"event type: {event['type']}, customer_id: {sub_data['customer']}")
        if not sub_data["customer"]:
            logging.info(f"Customer ID is None for payment intent {sub_data['id']}")
            return jsonify(success=True)
        try:
            if sub_status == "active" and cancel_at is not None:
                # send email if subscription cancelled
                user_id = get_customer_userid(sub_data["customer"])
                clerk_user = get_user_by_id(user_id)
                response = loops.update_usergroup_clerk(clerk_user, user_id, LoopsUserGroup.CANCELLED.value)
                logging.info(f"User {user_id} cancelled subscription, send email response: {response}")
        except Exception as e:
            logging.error(f"Failed to update contact or send event for user {user_id} becasue {e}")

    if event["type"] == "invoice.paid":
        # main logic for topup credit after payment is successful
        # https://stripe.com/docs/api/invoices/object
        logging.info(event)
        invoice = event["data"]["object"]
        # check if invoice is paid and amount paid is greater than 0 because if the amount_paid is 0 it might be a downgrade
        if invoice["status"] == "paid" and invoice.get("amount_paid", 0) > 0:
            user_id = get_customer_userid(invoice["customer"])
            cm = CreditsManager(user_id)
            items = invoice["lines"]["data"]
            for item in items:
                price_id = item["price"]["id"]
                print(f"Price ID: {price_id}")
                cm.topup_credit_payment(
                    price_id,
                    customer_id=invoice["customer"],
                    invoice_id=invoice["id"],
                    hosted_invoice_url=invoice["hosted_invoice_url"],
                )
                cm.update_payment_status(True)
                logging.info(f"Updated user {user_id} payment status to paid")
                subscriptions = get_all_subscriptions(invoice["customer"])
                if len(subscriptions.data) <= 1:
                    cm.referral_topup_credit(user_id)
                else:
                    logging.info(f"User {user_id} already has past subscription, no referral topup credit")
        else:
            logging.info(
                f"invoice {invoice['id']} status is not paid: {invoice['status']}"
            )

    if event["type"] == "customer.subscription.updated":
        # TODO: if user updated from monthly to yearly. or vice versa
        logging.info(event)

    if event["type"] == "checkout.session.completed":
        # https://stripe.com/docs/api/checkout/sessions/object#checkout_session_object-client_reference_id
        # Main Checkout Logic for new subscription
        session = event['data']['object']
        logging.info(f"Checking event data object: {event}")
        logging.info(f"Checking session: {session}")
        user_id = session['client_reference_id']
        cm = CreditsManager(user_id)
        customer_id = session['customer']
        if customer_id is None:
            logging.info(f"Customer ID is None for user {user_id}, might be one time purchase!")
            price_id = price_id_from_checkout_complete(session['id'])
            logging.info(f"checkout.session.completed: {price_id} to be topup for user {user_id}")
            cm.topup_credit_payment(price_id, type="one_time_payment", session_id=session['id'],
                                    webhook="checkout.session.completed")
            # TODO: track conversion
            return jsonify(success=True)
        logging.info(f"Checking customer id format: {customer_id}")
        # update customer metadata
        customer = stripe.Customer.modify(
            customer_id,
            metadata={
                "user_id": user_id,
            },
        )
        ref = get_user_payment(user_id)
        ref.update({"stripe_customer_id": customer.id})
        logging.info(
            f"Updated stripe customer {customer.id} with user_id: {user_id} in stripe payment"
        )
        logging.debug(event)

    return jsonify(success=True)


@app.route("/payment/subscriptions", methods=["POST", "GET"])
@clerk_jwt_required
def payment_subscriptions(auth):
    user_id = auth["sub"]  # Extract user_id from the authenticated payload
    logging.info(f"User {user_id} is requesting payment subscriptions...")
    # if method is post, create a new subscription
    if request.method == "POST":
        # get price_id from request body
        if not request.is_json:
            raise Exception("Invalid request body!")
        if not request.get_json().get("price_id"):
            raise Exception("Missing price_id in request body!")
        price_id = request.get_json().get("price_id")
        logger.info(
            f"Creating new checkout subscription for user {user_id} with price {price_id}",
            user_id=user_id,
        )
        session = new_checkout_session(user_id, price_id)
        logger.info(f"Created new checkout session {session}", user_id=user_id)
        return session
    elif request.method == "GET":
        return get_subscriptions(user_id)
    else:
        raise Exception(f"Invalid method {request.method}!")


@app.route('/index_material', methods=['POST'])
@clerk_jwt_required
def index_material(auth):
    """
    Index the user materials
    """
    try:
        data = request.get_json()
        print(data)
        user_id = data['user_id']
        filename = data['filename']
        file_id = data['file_id']

        index_user_material(user_id, filename, file_id)
        return jsonify(success=True, message="Successfully trained material on AI")
    except Exception as e:
        logging.error(f"Failed to index user material: {e}")
        return jsonify(success=False, message=f"Failed to train material on AI: Contact support on Discord")


@app.route('/delete_index', methods=['POST'])
@clerk_jwt_required
def delete_index(auth):
    """
    Delete the user material index
    """
    try:
        data = request.get_json()
        user_id = data['user_id']
        file_id = data['file_id']
        delete_material_index(user_id, file_id)
        return jsonify(success=True, message=f"Successfully deleted user material user id: {user_id}")
    except Exception as e:
        logging.error(f"Failed to delete user material user id: {e}")
        return jsonify(success=False, message=f"Failed to delete train: Contact support on Discord")


@app.route("/chain_types")
def chain_types():
    return list(CHAIN_MAP.keys())


@app.route("/end_session", methods=["GET"])
@clerk_jwt_required
def end_session_restapi(auth):
    user_id = auth["sub"]
    if user_id not in im.interview_sessions:
        return jsonify({"message": f"No session associated with user {user_id}"})
    try:
        im.end_session(user_id)
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"message": f"Failed to end session for user {user_id}! {e}"})
    return jsonify({"message": f"Session associated with user {user_id} terminated"})


@socketio.event
def connect(auth):
    try:
        connection = verify_jwt(auth["session"])  # verify clerk jwt token
        logger.debug(connection, user_id=connection["sub"])
        client_id = request.sid
        logger.info(
            f"socket client id: {client_id}, user id: {connection['sub']}",
            user_id=connection["sub"],
        )
        im.add_new_connection(connection, client_id)
        im.socketio.emit("chain_types", list(CHAIN_MAP.keys()))
    except Exception as error:
        logger.info(f"Not authorized client id:{request.sid}! {error}")
        logger.error(traceback.format_exc())
        raise ConnectionRefusedError(f"Connection Refused! {error}")


@socketio.event
def disconnect():
    try:
        if request.sid in im.client_to_user:
            user = im.remove_client(request.sid)
            logger.info(
                f"Client disconnected {request.sid}, user id: {user}", user_id=user
            )
    except Exception:
        logger.error(
            f"Failed to disconnect client {request.sid}! \n {traceback.format_exc()}"
        )


@socketio.event
def chat(message):
    """
    Schema:
    {
        "role": "interviewer",
        "transcript": "hello world",
        "reset": True or False,
    }
    """
    interview_session = im.get_interview_session_by_client(request.sid)
    interview_session.chat(message)
    # tracking
    if request.sid in im.client_to_user:
        user = im.client_to_user[request.sid]


@socketio.event
def chat_bytes(message):
    """
    Schema:
    {
        "channels": [
            {
                "role": "interviewer",
                "bytes": 0x1234,
            },
            {
                "role": "interviewee",
                "bytes": 0x1234,
            },
        ]
    }
    """
    interview_session = im.get_interview_session_by_client(request.sid)
    interview_session.chat_bytes(message)


@socketio.event
def chat_dual_channel(message):
    try:
        if not im.has_interview_session(request.sid):
            # if user has no interview session, return silently to save resources
            # dont spam log because it would floot gcp cloud log and overflow RAM.
            return
        interview_session = im.get_interview_session_by_client_with_timeout(request.sid)
        # TODO: if interview_session.ready:
        interview_session.chat_bytes_dual_channel(message)
    except Exception as error:
        logger.debug(f"chat_dual_channel error: {error}, {traceback.format_exc()}")


@socketio.event
def end_session(message):
    """
    End a user's interview session
    """
    client_id = request.sid
    im.end_session_by_client(client_id)


@socketio.event
def paused(message):
    """
    Message boolean:
    True -- pause
    False -- resume
    Pause or resume a user's interview session to prevent AI from responding
    We still track user's audio's transcript and chat history
    """
    client_id = request.sid
    logger.info(f"Paused: {message} client_id: {client_id}")
    im.pause_resume_by_client(client_id, message)
    # tracking
    if client_id in im.client_to_user:
        user = im.client_to_user[client_id]


@socketio.event
def update_chain(message):
    """
    Change the chain type
    e.g. message:
    {'chain_type': {'id': 'chain_001'}}
    """
    print(message)
    chain_type = message["chain_type"]["id"]
    if chain_type not in CHAIN_MAP:
        logger.error(
            f"Invalid chain type! {message}. Supported types: {list(CHAIN_MAP.keys())}"
        )
    interview_session = im.get_interview_session_by_client(request.sid)
    # TODO: let user to pick their preferred coach too
    interview_session.responder.update_chain(chain_type)


@socketio.event
def next_question():
    """
    Send next question to the interviewer, incase user press next question before dg complete
    transcript. The temp sentence contains all the transcript before sending it to AI. Then after
    we send the message to AI, we reset the temp sentence.
    """
    interview_session: InterviewSession = im.get_interview_session_by_client(request.sid)
    if interview_session.dg is None:
        logger.debug(f"DG is not ready for user {interview_session.user_id}")
        return
    if interview_session.transcriber:
        transcript = Transcript(
            role=Role.INTERVIEWEE,
            transcript=interview_session.dg.sentence_splitter.interviewee_temp_sentence,
            timestamp=datetime.datetime.now(),
            request_id=uuid.uuid4().hex,
        )
        interview_session.transcriber.transcript_data[Role.INTERVIEWEE].insert(0, transcript)
        interview_session.dg.sentence_splitter.reset_temp_sentences(Role.INTERVIEWEE)
    interview_session.transcriber.respond_interviewee_changed_event.set()


@socketio.event
def solve(message):
    """
    Create coding solution based on the current interview session's context
    {
        context: {
            "image": "base64 encoded image",
            "selected_text": "selected text",
        }
    }
    """
    interview_session = im.get_interview_session_by_client(request.sid)
    chain: InterviewChain = interview_session.responder.chain
    try:
        interview_session.cm.deduct_credit(InterviewType.ONE_TIME_IMAGE)
        interview_session.credit_consumption += interview_session.cm.cost_map[InterviewType.ONE_TIME_IMAGE]
        # Check if the credit deductor start event is set
        if interview_session.start_event.is_set() == False:
            interview_session.start_event.set()
        if "context" in message:
            if "image" in message["context"]:
                base64_image = message["context"]["image"]
                response = chain.predict_image(base64_image, interview_session.sio)
                interview_session.transcriber.save_conversation(Role.IMAGE_CONTEXT, response)
                interview_session.transcriber.chat_history_queue.put(
                    Transcript(
                        role=Role.AI,
                        transcript=response,
                        timestamp=datetime.datetime.now(),
                        request_id=uuid.uuid4().hex,
                    )
                )
    except:
        logger.error(f"Failed to solve! {traceback.format_exc()}")

def configure_logging():
    # Set specific logging levels for third-party libraries
    logging.getLogger('httpx').setLevel(logging.WARNING)  # httpx
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('googlecloudprofiler').setLevel(logging.WARNING)

def create_app():
    app.register_blueprint(view_app)
    CORS(app, resources={r"/*": {"origins": "*"}})
    configure_logging()
    return app

if __name__ == "__main__":
    create_app()
    socketio.run(app, debug=False, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True
    )

"""
python interviewai/server.py
"""
