from pathlib import Path
from typing import Optional

_api_txt = "\nIf you dont set it, users will have to enter their own on registration."

TDLIB_PATH: Path
TDLIB_PATH__DOC = "Defaults to ${SLIDGE_HOME_DIR}/tdlib"
TDLIB_PATH__DYNAMIC_DEFAULT = True

TDLIB_KEY: str = "NOT_SECURE"
TDLIB_KEY__DOC = "Key used to encrypt tdlib persistent DB"

API_ID: Optional[int] = None
API_ID__DOC = "Telegram app api_id, obtained at https://my.telegram.org/apps" + _api_txt

API_HASH: Optional[str] = None
API_HASH__DOC = (
    "Telegram app api_hash, obtained at https://my.telegram.org/apps" + _api_txt
)

REGISTRATION_AUTH_CODE_TIMEOUT: int = 60
REGISTRATION_AUTH_CODE_TIMEOUT__DOC = (
    "On registration, users will be prompted for a 2FA code they receive "
    "on other telegram clients."
)

GROUP_HISTORY_MAXIMUM_MESSAGES = 50
GROUP_HISTORY_MAXIMUM_MESSAGES__DOC = (
    "The number of messages to fetch from a group history. "
    "These messages and their attachments will be fetched on slidge startup."
)
