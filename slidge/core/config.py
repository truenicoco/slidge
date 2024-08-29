from datetime import timedelta
from pathlib import Path
from typing import Optional

from slixmpp import JID as JIDType


class _TimedeltaSeconds(timedelta):
    def __new__(cls, s: str):
        return super().__new__(cls, seconds=int(s))


# REQUIRED, so not default value

LEGACY_MODULE: str
LEGACY_MODULE__DOC = (
    "Importable python module containing (at least) "
    "a BaseGateway and a LegacySession subclass"
)

SERVER: str = "localhost"
SERVER__DOC = (
    "The XMPP server's host name. Defaults to localhost, which is the "
    "standard way of running slidge, on the same host as the XMPP server. "
    "The 'Jabber Component Protocol' (XEP-0114) does not mention encryption, "
    "so you *should* provide encryption another way, eg via port forwarding, if "
    "you change this."
)
SERVER__SHORT = "s"

SECRET: str
SECRET__DOC = "The gateway component's secret (required to connect to the XMPP server)"

JID: JIDType
JID__DOC = "The gateway component's JID"
JID__SHORT = "j"

PORT: str = "5347"
PORT__DOC = "The XMPP server's port for incoming component connections"
PORT__SHORT = "p"

# Dynamic default (depends on other values)

HOME_DIR: Path
HOME_DIR__DOC = (
    "Directory where slidge will writes it persistent data and cache. "
    "Defaults to /var/lib/slidge/${SLIDGE_JID}. "
)
HOME_DIR__DYNAMIC_DEFAULT = True

DB_URL: str
DB_URL__DOC = (
    "Database URL, see <https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls>. "
    "Defaults to sqlite:///${HOME_DIR}/slidge.sqlite"
)
DB_URL__DYNAMIC_DEFAULT = True

USER_JID_VALIDATOR: str
USER_JID_VALIDATOR__DOC = (
    "Regular expression to restrict users that can register to the gateway, by JID. "
    "Defaults to .*@${SLIDGE_SERVER}, but since SLIDGE_SERVER is usually localhost, "
    "you probably want to change that to .*@example.com"
)
USER_JID_VALIDATOR__DYNAMIC_DEFAULT = True

# Optional, so default value + type hint if default is None

ADMINS: tuple[JIDType, ...] = ()
ADMINS__DOC = "JIDs of the gateway admins"


UPLOAD_SERVICE: Optional[str] = None
UPLOAD_SERVICE__DOC = (
    "JID of an HTTP upload service the gateway can use. "
    "This is optional, as it should be automatically determined via service"
    "discovery."
)

SECRET_KEY: Optional[str] = None
SECRET_KEY__DOC = "Encryption for disk storage. Deprecated."

NO_ROSTER_PUSH = False
NO_ROSTER_PUSH__DOC = "Do not fill users' rosters with legacy contacts automatically"

ROSTER_PUSH_PRESENCE_SUBSCRIPTION_REQUEST_FALLBACK = True
ROSTER_PUSH_PRESENCE_SUBSCRIPTION_REQUEST_FALLBACK__DOC = (
    "If True, legacy contacts will send a presence request subscription "
    "when privileged roster push does not work, eg, if XEP-0356 (privileged "
    "entity) is not available for the component."
)

AVATAR_SIZE = 200
AVATAR_SIZE__DOC = (
    "Maximum image size (width and height), image ratio will be preserved"
)

USE_ATTACHMENT_ORIGINAL_URLS = False
USE_ATTACHMENT_ORIGINAL_URLS__DOC = (
    "For legacy plugins in which attachments are publicly downloadable URLs, "
    "let XMPP clients directly download them from this URL. Note that this will "
    "probably leak your client IP to the legacy network."
)

UPLOAD_REQUESTER: Optional[str] = None
UPLOAD_REQUESTER__DOC = (
    "Set which JID should request the upload slots. Defaults to the component JID."
)

NO_UPLOAD_PATH: Optional[str] = None
NO_UPLOAD_PATH__DOC = (
    "Instead of using the XMPP server's HTTP upload component, copy files to this dir. "
    "You need to set NO_UPLOAD_URL_PREFIX too if you use this option, and configure "
    "an web server to serve files in this dir."
)

NO_UPLOAD_URL_PREFIX: Optional[str] = None
NO_UPLOAD_URL_PREFIX__DOC = (
    "Base URL that servers files in the dir set in the no-upload-path option, "
    "eg https://example.com:666/slidge-attachments/"
)

NO_UPLOAD_METHOD: str = "copy"
NO_UPLOAD_METHOD__DOC = (
    "Whether to 'copy', 'move', 'hardlink' or 'symlink' the files in no-upload-path."
)

NO_UPLOAD_FILE_READ_OTHERS = False
NO_UPLOAD_FILE_READ_OTHERS__DOC = (
    "After writing a file in NO_UPLOAD_PATH, change its permission so that 'others' can"
    " read it."
)

IGNORE_DELAY_THRESHOLD = _TimedeltaSeconds("300")
IGNORE_DELAY_THRESHOLD__DOC = (
    "Threshold, in seconds, below which the <delay> information is stripped "
    "out of emitted stanzas."
)

PARTIAL_REGISTRATION_TIMEOUT = 3600
PARTIAL_REGISTRATION_TIMEOUT__DOC = (
    "Timeout before registration and login. Only useful for legacy networks where "
    "a single step registration process is not enough."
)

LAST_SEEN_FALLBACK = False
LAST_SEEN_FALLBACK__DOC = (
    "When using XEP-0319 (Last User Interaction in Presence), use the presence status"
    " to display the last seen information in the presence status. Useful for clients"
    " that do not implement XEP-0319. Because of implementation details, this can increase"
    " RAM usage and might be deprecated in the future. Ask your client dev for XEP-0319"
    " support ;o)."
)

QR_TIMEOUT = 60
QR_TIMEOUT__DOC = "Timeout for QR code flashing confirmation."

LAST_MESSAGE_CORRECTION_RETRACTION_WORKAROUND = False
LAST_MESSAGE_CORRECTION_RETRACTION_WORKAROUND__DOC = (
    "If the legacy service does not support last message correction but supports"
    " message retractions, slidge can 'retract' the edited message when you edit from"
    " an XMPP client, as a workaround. This may only work for editing messages"
    " **once**. If the legacy service does not support retractions and this is set to"
    " true, when XMPP clients attempt to correct, this will send a new message."
)

FIX_FILENAME_SUFFIX_MIME_TYPE = False
FIX_FILENAME_SUFFIX_MIME_TYPE__DOC = (
    "Fix the Filename suffix based on the Mime Type of the file. Some clients (eg"
    " Conversations) may not inline files that have a wrong suffix for the MIME Type."
    " Therefore the MIME Type of the file is checked, if the suffix is not valid for"
    " that MIME Type, a valid one will be picked."
)

LOG_FILE: Optional[Path] = None
LOG_FILE__DOC = "Log to a file instead of stdout/err"

LOG_FORMAT: str = "%(levelname)s:%(name)s:%(message)s"
LOG_FORMAT__DOC = (
    "Optionally, a format string for logging messages. Refer to "
    "https://docs.python.org/3/library/logging.html#logrecord-attributes "
    "for available options."
)

MAM_MAX_DAYS = 7
MAM_MAX_DAYS__DOC = "Maximum number of days for group archive retention."

CORRECTION_EMPTY_BODY_AS_RETRACTION = True
CORRECTION_EMPTY_BODY_AS_RETRACTION__DOC = (
    "Treat last message correction to empty message as a retraction. "
    "(this is what cheogram do for retraction)"
)

ATTACHMENT_MAXIMUM_FILE_NAME_LENGTH = 200
ATTACHMENT_MAXIMUM_FILE_NAME_LENGTH__DOC = (
    "Some legacy network provide ridiculously long filenames, strip above this limit, "
    "preserving suffix."
)

ALWAYS_INVITE_WHEN_ADDING_BOOKMARKS = True
ALWAYS_INVITE_WHEN_ADDING_BOOKMARKS__DOC = (
    "Send an invitation to join MUCs when adding them to the bookmarks. While this "
    "should not be necessary, it helps with clients that do not support :xep:`0402` "
    "or that do not respect the auto-join flag."
)

AVATAR_RESAMPLING_THREADS = 2
AVATAR_RESAMPLING_THREADS__DOC = (
    "Number of additional threads to use for avatar resampling. Even in a single-core "
    "context, this makes avatar resampling non-blocking."
)

DEV_MODE = False
DEV_MODE__DOC = (
    "Enables an interactive python shell via chat commands, for admins."
    "Not safe to use in prod, but great during dev."
)

STRIP_LEADING_EMOJI_ADHOC = False
STRIP_LEADING_EMOJI_ADHOC__DOC = (
    "Strip the leading emoji in ad-hoc command names, if present, in case you "
    "are a emoji-hater."
)
