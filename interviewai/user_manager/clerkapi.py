from interviewai.config.config import get_config
import jwt
import requests
# from config import API_KEY
from interviewai.auth import verify_jwt
from interviewai.user_manager.credits_manager import CreditsManager
import logging

CLERK_API_KEY = get_config('CLERK_API_KEY')


def get_userid(session_token):
    decoded = verify_jwt(session_token)
    return decoded["sub"]


def get_user_by_token(session_token):
    """
    Retrieve user details based on the session token.

    Parameters:
    - session_token (str): The JWT session token.

    Returns:
    dict: User details retrieved from the user's decoded session token.
    """
    decoded = verify_jwt(session_token)  # Assuming verify_jwt function is defined elsewhere
    return get_user_by_id(decoded["sub"])


def clerk_api(endpoint, method='GET', params=None, data=None):
    """
    Make API requests to the Clerk API.

    Parameters:
    - endpoint (str): The API endpoint to request.
    - method (str, optional): The HTTP request method ('GET', 'POST', 'PATCH'). Default is 'GET'.
    - params (dict, optional): Query parameters for the request. Default is None.
    - data (dict, optional): JSON data for the request body. Default is None.

    Returns:
    dict: JSON response from the API.
    """
    headers = {
        'Authorization': f'Bearer {CLERK_API_KEY}',
        'Content-Type': 'application/json'
    }
    url = f'https://api.clerk.dev/v1{endpoint}'

    if method == 'GET':
        response = requests.get(url, headers=headers, params=params)
    elif method == 'POST':
        response = requests.post(url, headers=headers, json=data)
    elif method == 'PATCH':
        response = requests.patch(url, headers=headers, json=data)

    if response.status_code not in range(200, 300):
        # raise Exception({"error": response.text})
        logging.info(response.text)

    return response.json()


def get_user_by_id(user_id):
    """
    Retrieve user details by user ID.

    Parameters:
    - user_id (str): The ID of the user.

    Returns:
    dict: User details retrieved from the Clerk API.
    """
    endpoint = f'/users/{user_id}'
    user = clerk_api(endpoint)
    return user


def get_user_by_emails(emails):
    endpoint = '/users'
    users = clerk_api(endpoint, params={'email_address': emails})
    return users


def list_user(params):
    """
    List users using provided query parameters.

    Parameters:
    - params (dict): Query parameters for filtering and pagination.

    Returns:
    dict: List of users from the Clerk API response.
    """
    return clerk_api('/users', method='GET', params=params)
