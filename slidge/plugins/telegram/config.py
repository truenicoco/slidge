from pathlib import Path

TDLIB_PATH: Path
TDLIB_PATH__DOC = "Defaults to ${SLIDGE_HOME_DIR}/tdlib"
TDLIB_PATH__DYNAMIC_DEFAULT = True

TDLIB_KEY: str = "NOT_SECURE"
TDLIB_KEY__DOC = "Key used to encrypt tdlib persistent DB"
