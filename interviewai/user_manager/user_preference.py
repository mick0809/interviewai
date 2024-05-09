from interviewai.firebase import get_user_preference, get_user_responder_config, get_user_coach_config
import logging

DEEPGRAM_LANGUAGES = {
    "Chinese": {"code": "zh", "tier": "base"},
    "China": {"code": "zh-CN", "tier": "base"},
    "Czech": {"code": "cs", "tier": "nova-2"},
    "Taiwan": {"code": "zh-TW", "tier": "base"},
    "Danish": {"code": "da", "tier": "nova-2"},
    "Dutch": {"code": "nl", "tier": "nova-2"},
    "English (Global)": {"code": "en", "tier": "nova-2"},
    "Australia": {"code": "en-AU", "tier": "nova-2"},
    "United Kingdom": {"code": "en-GB", "tier": "nova-2"},
    "India": {"code": "en-IN", "tier": "nova-2"},
    "New Zealand": {"code": "en-NZ", "tier": "nova-2"},
    "United States": {"code": "en-US", "tier": "nova-2"},
    "Flemish": {"code": "nl-BE", "tier": "nova-2"},
    "French": {"code": "fr", "tier": "nova-2"},
    "Canada": {"code": "fr-CA", "tier": "nova-2"},
    "German": {"code": "de", "tier": "nova-2"},
    "Greek": {"code": "el", "tier": "nova-2"},
    "Hindi": {"code": "hi", "tier": "nova-2"},
    "Roman Script": {"code": "hi-Latn", "tier": "nova-2"},
    "Indonesian": {"code": "id", "tier": "nova-2"},
    "Italian": {"code": "it", "tier": "nova-2"},
    "Japanese": {"code": "ja", "tier": "nova-2"},
    "Korean": {"code": "ko", "tier": "nova-2"},
    "Norwegian": {"code": "no", "tier": "nova-2"},
    "Polish": {"code": "pl", "tier": "nova-2"},
    "Portuguese": {"code": "pt", "tier": "nova-2"},
    "Brazil": {"code": "pt-BR", "tier": "nova-2"},
    "Russian": {"code": "ru", "tier": "nova-2"},
    "Spanish": {"code": "es", "tier": "nova-2"},
    "Latin America": {"code": "es-419", "tier": "nova-2"},
    "Swedish": {"code": "sv", "tier": "nova-2"},
    "Tamil": {"code": "ta", "tier": "enhanced"},
    "Turkish": {"code": "tr", "tier": "nova-2"},
    "Ukrainian": {"code": "uk", "tier": "nova-2"},
}

ENGLISH_ACCENT = ["English (Global)", "Australia", "United Kingdom", "India", "New Zealand", "United States"]
CHINESE_ACCENT = ["Chinese", "China", "Taiwan"]
FRENCH_ACCENT = ["French", "Canada"]
ASIAN_LANGUAGES = ["Chinese", "China", "Taiwan", "Japanese", "Korean"]


### language setting ###
class UserSettings:
    def __init__(self, user_id):
        self.user_id = user_id
        self.preferences = self.fetch_user_preferences()

    def fetch_user_preferences(self):
        """
        Fetch all user preferences at once.
        """
        try:
            # Replace `get_user_preference` with the actual function you use to fetch preferences
            user_preferences = get_user_preference(self.user_id)
            logging.info(f"{self.user_id} preferences fetched successfully.")
            if user_preferences is None:
                return {}
            return user_preferences
        except Exception as e:
            logging.error(f"{self.user_id} failed to fetch preferences: {e}")
            return {}

    def get_language(self):
        """
        Get user preferred language from the preferences.
        """
        return self.preferences.get("language", "United States")

    def dg_language_checker(self):
        language = self.get_language()
        if language is None:
            return "United States"
        elif language not in DEEPGRAM_LANGUAGES:
            return "United States"
        return language

    @property
    def gpt_output_language(self):
        language = self.get_language()
        if language is None or language in ENGLISH_ACCENT:
            return "English"
        elif language in CHINESE_ACCENT:
            return "Chinese"
        elif language in FRENCH_ACCENT:
            return "French"
        elif language not in DEEPGRAM_LANGUAGES:
            return "English"
        return language

    @property
    def dg_model(self):
        """
        Fall back default:
        nova-2
        """
        language = self.dg_language_checker()
        try:
            return DEEPGRAM_LANGUAGES[language]["tier"]
        except:
            logging.error(f"Failed to get model for {self.user_id} with language {language}")
            return "nova-2"

    @property
    def dg_language(self):
        """
        Get user preferred language from the preferences.
        Fall back default:
        en-US
        """
        language = self.dg_language_checker()
        try:
            return DEEPGRAM_LANGUAGES[language]["code"]
        except Exception as e:
            logging.error(f"{self.user_id} failed to get language {language}")
            return "en-US"

    @property
    def dg_endpoint(self):
        """
        Get user preferred endpoint from the preferences.
        Fall back default:
        "default": 800
        """
        dg_delay = self.preferences.get("dgDelay", "default")
        if dg_delay == "low":
            return 800
        elif dg_delay == "default":
            return 1000
        elif dg_delay == "high":
            return 1200
        else:
            return 1000

    @property
    def utterance_end_ms(self):
        """
        Get user preferred endpoint from the preferences.
        Fall back default:
        2000
        """
        return self.dg_endpoint + 1000

    @property
    def system_responder_chain(self):
        """
        system responder reply length
        Has: "concise", "default", "lengthy"
        """
        return self.preferences.get("level", "default")

    @property
    def user_responder_chain(self):
        """
        Get user defined chain from the preferences.
        """
        chain_id = self.preferences.get("responder_chain_id", None)
        if chain_id is None:
            return None
        else:
            return "user_responder_chain"

    @property
    def user_coach_chain(self):
        """
        Get user defined chain from the preferences.
        Has: "coach_chain" and "chain_mock"
        """
        chain_id = self.preferences.get("coach_chain_id", None)
        if chain_id is None:
            return None
        else:
            return "user_coach_chain"

    @property
    def user_responder_config(self):
        """
        Get user defined chain configuration.
        """
        chain_id = self.preferences.get("responder_chain_id", None)
        if chain_id is None:
            return None
        else:
            return get_user_responder_config(self.user_id, chain_id)

    @property
    def user_coach_config(self):
        """
        Get user defined chain configuration.
        """
        chain_id = self.preferences.get("coach_chain_id", None)
        if chain_id is None:
            return None
        else:
            return get_user_coach_config(self.user_id, chain_id)


if __name__ == "__main__":
    user_id = "user_2SH6wFxunlugxLVnQZalKoFhGEs"
    user_settings = UserSettings(user_id)
    print(user_settings.dg_model)
    print(user_settings.dg_language)
    print(user_settings.dg_endpoint)
    print(user_settings.dg_interim)
    print(user_settings.gpt_output_language)
    print(user_settings.preferences)
