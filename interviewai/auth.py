import jwt
from interviewai.config.config import get_config
from datetime import timedelta


CLERK_PEM_PUBLIC_KEY = get_config("CLERK_PEM_PUBLIC_KEY")


def verify_jwt(session_token):
    """
    https://clerk.com/docs/request-authentication/jwt-templates
    return format:
    {
        "azp": "http://localhost:3000",
        "exp": 1688364584,
        "iat": 1688364524,
        "iss": "https://correct-coral-66.clerk.accounts.dev",
        "nbf": 1688364514,
        "sid": "sess_2S39iL2X9Ha5VK1I4qBlqlgb2oA",
        "sub": "user_2Ruhyjedi4SLbxUz3uNeFU2PmfP",
    }
    """
    if session_token is None:
        raise Exception({"error": "not signed in"})

    decoded = jwt.decode(session_token, CLERK_PEM_PUBLIC_KEY, leeway=timedelta(60), algorithms=["RS256"])
    return decoded


def get_userid(session_token):
    decoded = verify_jwt(session_token)
    return decoded["sub"]


if __name__ == "__main__":
    verify_jwt(
        "eyJhbGciOiJSUzI1NiIsImtpZCI6Imluc18yUmFhUzFNN1FZU3pXMGpmOXY3OXlTek1mRFEiLCJ0eXAiOiJKV1QifQ.eyJhenAiOiJodHRwOi8vbG9jYWxob3N0OjMwMDAiLCJleHAiOjE2ODgzNjE4NTQsImlhdCI6MTY4ODM2MTc5NCwiaXNzIjoiaHR0cHM6Ly9jb3JyZWN0LWNvcmFsLTY2LmNsZXJrLmFjY291bnRzLmRldiIsIm5iZiI6MTY4ODM2MTc4NCwic2lkIjoic2Vzc18yUnVpOU9SQmZqRlZsNm9sYVNJRURBVjRHaWEiLCJzdWIiOiJ1c2VyXzJSdWh5amVkaTRTTGJ4VXozdU5lRlUyUG1mUCJ9.mju9gIq_BlGW-Mpm0rXLICFb6qmgFYumkOvUnMqpImY-3yFjZ9hJcxrumUaW2-B7w-KugwqAwVlxKxqJnop5crsSeYlqBzYbdGH07LLBOp2cvkxKjQZDcmdpAIepGYOs4PXnQ8EQ2JGh2PTNpQW53Nqf-0dmohMDaypgmVGXsLHy7f24SJ_YdXmCO3JyNGqCzCH1KJNkYRP1fF_G_7hp-MxHEE6r0FEhBimI3ekT9-vRImkbZJS4BF6_5C5NdBhsxQzup7mxV0u1s7o4eLcJMZcSPZqTXpogNIxLQHOw565G0sgw844NmbqtP_XHP4doF4sC_clb8jUZzUIxCi4ubg"
    )
