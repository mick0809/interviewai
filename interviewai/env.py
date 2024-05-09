import os


def get_env():
    try:
        if os.environ["ENVIRONMENT"] not in ["prod", "dev"]:
            return "dev"
        else:
            return os.environ["ENVIRONMENT"]
    except Exception as e:
        print(e)
        return "dev"
