import logging
import os
from sys import platform
from interviewai.config.config import get_config  # TODO: move stuff from config to __init__.py
from interviewai.env import get_env

try:
    os.environ["OPENAI_API_KEY"] = get_config("OPENAI_API_KEY")
except:
    logging.error("OPENAI_API_KEY not set")
logging.info(f"Current running ENVIRONMENT: {get_env()}")

# custom logger also save user id, interview session id
class LoggerMixed:
    """
    A logger that logs to both Google Cloud Logging and default Python logging.
    """

    def __init__(
            self,
            logger_name,
            user_id: str = None,
            interview_session_id: str = None,
    ):
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.DEBUG)
        self.user_id = user_id
        self.interview_session_id = interview_session_id

    def info(self, message, **kwargs):
        self.logger.info(msg = message, extra={**kwargs})

    def debug(self, message, **kwargs):
        self.logger.debug(msg = message, extra={**kwargs})

    def error(self, message, **kwargs):
        self.logger.error(msg = message, extra={**kwargs})
####################
# OLD CODE


# import logging
# import os
# from sys import platform

# import google.cloud.logging
# import googlecloudprofiler

# from interviewai.config.config import get_config  # TODO: move stuff from config to __init__.py
# from interviewai.env import is_prod, get_env

# # from opentelemetry import trace
# # from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
# # from opentelemetry.sdk.trace import TracerProvider
# # from opentelemetry.sdk.trace.export import BatchSpanProcessor
# try:
#     os.environ["OPENAI_API_KEY"] = get_config("OPENAI_API_KEY")
# except:
#     print("OPENAI_API_KEY not set")
# print(f"Current running ENVIRONMENT: {get_env()}")
# # if is_prod():
# #    # get GCP service account from SSM
# #    gcp_service_account_json = get_config("GOOGLE_APPLICATION_CREDENTIALS")
# #    with open("/tmp/gcp_service_account.json", "w") as f:
# #        f.write(gcp_service_account_json)
# #    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/gcp_service_account.json"
# #    print("set GOOGLE_APPLICATION_CREDENTIALS to /tmp/gcp_service_account.json")


# # TODO: Refactor to mo
# LOGGING_LEVEL = logging.INFO
# # setup google cloud logging
# GCLOUD_LOGGING = False
# if "DISABLE_GCLOUD_LOGGING" not in os.environ:
#     try:
#         client = google.cloud.logging.Client()
#         client.setup_logging(log_level=LOGGING_LEVEL)
#         if is_prod():
#             GCLOUD_LOGGING = True
#             logging.info("Using Google Cloud Logging.")
#         else:
#             logging.info("Not in production. Not using Google Cloud Logging.")
#     except Exception as e:
#         logging.error(
#             "Fall back to use default logging only. Could not set up Google Cloud Logging: %s",
#             e,
#         )
# else:
#     print("DISABLE_GCLOUD_LOGGING is set. Not using Google Cloud Logging.")

# # Profiler initialization. It starts a daemon thread which continuously
# # collects and uploads profiles. Best done as early as possible.
# try:
#     # check os if it is linux
#     if platform == "linux" or platform == "linux2":
#         googlecloudprofiler.start(
#             service="lockedinai-profiler",
#             service_version="0.0.1",
#             # verbose is the logging level. 0-error, 1-warning, 2-info,
#             # 3-debug. It defaults to 0 (error) if not set.
#             verbose=3,
#             # project_id must be set if not running on GCP.
#             project_id="lockedinai",
#         )
#     else:
#         logging.warn(f"Failed to start cloud profiler. Not running on linux")
# except Exception as exc:
#     logging.warn(f"Failed to start cloud profiler {exc}")  # Handle errors here


# # try:
# #     tracer_provider = TracerProvider()
# #     cloud_trace_exporter = CloudTraceSpanExporter()
# #     tracer_provider.add_span_processor(
# #         # BatchSpanProcessor buffers spans and sends them in batches in a
# #         # background thread. The default parameters are sensible, but can be
# #         # tweaked to optimize your performance
# #         BatchSpanProcessor(cloud_trace_exporter)
# #     )
# #     trace.set_tracer_provider(tracer_provider)

# # except Exception as e:
# #     logging.error(f"Failed to set up OpenTelemetry: {e}")


# # def get_tracer():
# #    return trace.get_tracer("lockedinai")

# class LoggerMixed:
#     """
#     A logger that logs to both Google Cloud Logging and default Python logging.
#     """

#     def __init__(
#             self,
#             logger_name,
#             user_id: str = None,
#             interview_session_id: str = None,
#             socketio=None
#     ):
#         if GCLOUD_LOGGING:
#             self.logger = client.logger(logger_name)
#         else:
#             self.logger = logging.getLogger(logger_name)
#         if type(self.logger) is logging.Logger:
#             self.logger.setLevel(LOGGING_LEVEL)
#         self.user_id = user_id
#         self.interview_session_id = interview_session_id
#         self.socketio = socketio

#     def _log(self, message, level, **kwargs):
#         user_id = kwargs["user_id"] if "user_id" in kwargs else self.user_id
#         if "interview_session_id" in kwargs:
#             interview_session_id = kwargs["interview_session_id"]
#         else:
#             interview_session_id = self.interview_session_id
#         if "user_id" in kwargs:
#             del kwargs["user_id"]
#         if "interview_session_id" in kwargs:
#             del kwargs["interview_session_id"]

#         if not GCLOUD_LOGGING:
#             self.logger._log(level, message, None, **kwargs)
#         else:
#             self.logger.log_struct(
#                 {
#                     "user_id": user_id,
#                     "interview_session_id": interview_session_id,
#                     "log": message,
#                 },
#                 severity=logging._levelToName[level],
#                 **kwargs,
#             )

#     def info(self, message, **kwargs):
#         self._log(message, logging.INFO, **kwargs)

#     def debug(self, message, **kwargs):
#         self._log(message, logging.DEBUG, **kwargs)

#     def error(self, message, **kwargs):
#         self._log(message, logging.ERROR, **kwargs)