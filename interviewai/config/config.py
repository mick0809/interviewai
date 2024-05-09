import json
import logging
import os
from pathlib import Path

import boto3
import yaml  # TODO: Refactor to toml (low priority)
from botocore.exceptions import ClientError
from pydantic import BaseSettings

from interviewai.env import get_env

# set this logging at the very top because get_config need its
LOGGING_LEVEL = logging.INFO
logging.basicConfig(level=LOGGING_LEVEL)


CONFIG_FILE = "config.yaml"

PROD_KEYS = [
    "PINECONE_API_KEY",
    "OPENAI_API_KEY",
    "DEEPGRAM_API_KEY",
    "DEEPGRAM_API_KEY_LIST",
    # "CLERK_PEM_PUBLIC_KEY",
    "GS_BUCKET",
    "GCP_PROJECT",
    "FIREBASE_CERTS",
    "CLERK_API_KEY",
    "STRIPE_API_KEY",
    # "STRIPE_WEBHOOK_SECRET",
    # "CLERK_WEBHOOK_SECRET",
    # "LOOPS_API_KEY",
]

DEV_SECRET_NAME = "preprod/config"
PROD_SECRET_NAME = "prod/config"

# aws Secret manager key management, you pull the key from aws Secret manager
class SMConfig:
    def __init__(self, dev=True, region_name="us-east-1") -> None:
        self.session = boto3.Session()
        self.sm_client = self.session.client(service_name='secretsmanager',
                                             region_name=region_name)
        self.config_data = {}
        if dev:
            self.config_data = self.fetch_secrets(DEV_SECRET_NAME)
        else:
            self.config_data = self.fetch_secrets(PROD_SECRET_NAME)

    def fetch_secrets(self, secret_name):
        try:
            get_secret_value_response = self.sm_client.get_secret_value(
                SecretId=secret_name
            )
            secrets = get_secret_value_response['SecretString']
            return json.loads(secrets)
        except ClientError as e:
            # For a list of exceptions thrown, see
            # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
            raise e


# check if the environment is production or development(deployed to dev server),local
if get_env() == "dev":
    logging.info("Dev Environment Detected. Fetching Config from Secret manager.")
    sm_config = SMConfig(dev=True)
elif get_env() == "prod":
    logging.info("Production Environment Detected. Fetching Config from Secret manager.")
    sm_config = SMConfig(dev=False)


# get key from aws
def get_config(key: str, default: str = None) -> str:
    logging.info(f"Using {get_env()} Config: {key}")
    return sm_config.config_data.get(key, default)

