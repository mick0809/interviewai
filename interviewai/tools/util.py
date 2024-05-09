import hashlib
import mimetypes
import tiktoken
import queue
import pickle
import time
import threading
from interviewai.config.config import get_config
from functools import wraps
import os
from langchain_community.llms.openai import AzureOpenAI
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from tiktoken.model import encoding_for_model

tokenizer = tiktoken.encoding_for_model("text-davinci-003")
t = tiktoken.get_encoding("cl100k_base")


# create the length function
def tiktoken_len(text):
    tokens = t.encode(text, disallowed_special=())
    return len(tokens)


def count_token(input):
    return len(tokenizer.encode(input))


def url_to_uuid(url):
    # convert URL to unique ID
    m = hashlib.md5()
    m.update(url.encode("utf-8"))
    uid = m.hexdigest()[:12]
    return uid


def get_interview_room(user_id: str):
    return f"interview_session_{user_id}"


#### for token count and trim

MODEL_TIMEOUT_IN_SECONDS = {
    "gpt-3.5-turbo-16k": 20,
    "gpt-4": 60,
    "default": 60,
}
MODEL_TOKEN = {
    "gpt-3.5-turbo-1106": 16385,
    "gpt-3.5-turbo": 4096,
    "gpt-3.5-turbo-16k": 16385,
    "gpt-3.5-turbo-instruct": 4096,
    "gpt-3.5-turbo-0613": 4096,
    "gpt-3.5-turbo-16k-0613": 16385,
    "gpt-3.5-turbo-0301": 4096,
    "text-davinci-003": 4096,
    "text-davinci-002": 4096,
    "code-davinci-002": 8001,
    "text-curie-001": 2049,
    "text-babbage-001": 2049,
    "text-ada-001": 2049,
    "davinci": 2049,
    "curie": 2049,
    "babbage": 2049,
    "ada": 2049,
    "text-moderation-latest": 32768,
    "text-moderation-stable": 32768,
    "babbage-002": 16384,
    "davinci-002": 16384,
    "gpt-4-1106-preview": 128000,
    "gpt-4-vision-preview": 128000,
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-0613": 8192,
    "gpt-4-32k-0613": 32768,
    "gpt-4-0314": 8192,
    "gpt-4-32k-0314": 32768,
    "default": 8192
}


def count_token(input, model):
    if model in MODEL_TOKEN:
        return len(encoding_for_model(model).encode(input))
    else:
        return len(encoding_for_model("cl100k_base").encode(input))


def trim_message_if_exceeded(query, model):
    if model in MODEL_TOKEN:
        token_limit = MODEL_TOKEN[model] - 100

    else:
        token_limit = MODEL_TOKEN["default"] - 100
    query_count = count_token(query, model)
    cut_off = len(query) // 2
    if query_count > token_limit:
        query_after = query
        while query_count > token_limit:
            # trim from the top
            query_after = query_after[cut_off:]
            query_count = count_token(query_after, model)
            cut_off = len(query_after) // 2
        # track
    else:
        query_after = query
    return query_after
