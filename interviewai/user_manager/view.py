from functools import wraps
from flask import render_template, jsonify, request, Blueprint
from flask_cors import cross_origin

from interviewai.user_manager.clerkapi import *
from interviewai.user_manager.credits_manager import CreditsManager
from interviewai.user_manager.payment import has_subscription, active_subscription_price_id
import traceback

app = Blueprint('view', __name__)


def clerk_jwt_required(f):
    """
    Decorator to enforce JWT-based authentication for protected views.

    Parameters:
    - f: The view function to be decorated.

    Logic Summary:
    1. Check if the HTTP method is allowed for JWT verification.
    2. If JSON data is present in the request, try to extract and verify 'auth' token.
    3. If token is valid, pass its payload to the decorated function.
    4. If 'Authorization' header or '__session' cookie is absent, return a 401 response.
    5. If token is present, decode it using HS256 algorithm; if '__session' token, decode using RS256.
    6. If decoding is successful, pass the decoded payload to the decorated function.
    7. If decoding fails, return a 400 response with the error message.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if the HTTP method is allowed for JWT verification
        if request.method not in ['GET', 'DELETE', 'HEAD', 'OPTIONS', 'TRACE']:
            requestjson = request.get_json()
            if requestjson is not None:
                token = requestjson.get('auth')
                if token is not None:
                    try:
                        payload = verify_jwt(token)  # Verify the JWT token
                    except jwt.ExpiredSignatureError:
                        logging.debug(f"{f.__name__} token expired")
                        return jsonify({'message': 'Token expired'}), 400
                    except jwt.InvalidTokenError:
                        logging.debug(f"{f.__name__} invalid token")
                        return jsonify({'message': 'Invalid token'}), 400
                    kwargs['auth'] = payload  # Pass the payload to the decorated function
                    return f(*args, **kwargs)

        # Check for the presence of session token and Authorization token
        sess_token = request.cookies.get('__session')
        token = request.headers.get('Authorization')
        if sess_token is None and token is None:
            logging.debug(f"{f.__name__} missing token")
            return jsonify({'message': 'not signed in'}), 401

        try:
            decoded = ''
            if token:
                decoded = verify_jwt(token)  # Verify the JWT token
            else:
                decoded = verify_jwt(sess_token)
        except Exception as error:
            logging.error(f"{f.__name__} Given sess_token: {sess_token}, token: {token}. {error}")
            return jsonify({'message': str(error)}), 400

        kwargs['auth'] = decoded  # Pass the decoded payload to the decorated function
        return f(*args, **kwargs)

    return decorated_function


@app.route('/track_user', methods=['POST'])
@clerk_jwt_required
def track_user_fn(auth):
    """
    Get cookie's information and track user's information for analytics purpose.
    * IP
    * User Agent
    * ttp (Tiktok pixel id)
    * ttclid (Tiktok ad click id)
    """
    requestjson = request.get_json()

    user_id = auth['sub']  # Extract user_id from the authenticated payload
    # Need to add track the IP back
    user_agent = request.headers.get('User-Agent')
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    last_referer_url = requestjson.get('last_referer_url')
    logging.debug(
        f"User {user_id} track user info: user_agent: {user_agent}, ip: {ip}, last_referer_url: {last_referer_url}")
    try:
        # TODO: Implement tracking logic later
        cm = CreditsManager(user_id)
        cm.update_payment_status(has_subscription(user_id))
        cm.update_subscription_details(active_subscription_price_id(user_id))
    except Exception as e:
        logging.info(f"Failed to update payment status for user {user_id} error: {traceback.format_exc()}")
        return jsonify({'message': f"Failed to update payment status for user {user_id} error: {e}"}), 500
    return jsonify({'message': 'success'}), 200
