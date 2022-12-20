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

SERVER: str
SERVER__DOC = "The XMPP server's host name."
SERVER__SHORT = "s"

SECRET: str
SECRET__DOC = (
    "The gateway component's secret (required to connect" " to the XMPP server)"
)

JID: JIDType
JID__DOC = "The gateway component's JID"
JID__SHORT = "j"

PORT: str = "5347"
PORT__DOC = "The XMPP server's port for incoming component connections"
PORT__SHORT = "p"

# Dynamic default (depends on other values)

HOME_DIR: Path
HOME_DIR__DOC = (
    "Shelve file used to store persistent user data. "
    "Defaults to /var/lib/slidge/${SLIDGE_JID}. "
)
HOME_DIR__DYNAMIC_DEFAULT = True

USER_JID_VALIDATOR: str
USER_JID_VALIDATOR__DOC = (
    "Regular expression to restrict user that can register to the gateway by JID. "
    "Defaults to .*@${SLIDGE_SERVER}, forbids the gateway to JIDs "
    "not using the same XMPP server as the gateway"
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
SECRET_KEY__DOC = "Encryption for disk storage"

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

UPLOAD_REQUESTER: Optional[str] = None
UPLOAD_REQUESTER__DOC = (
    "Set which JID should request the upload slots. Defaults to the component JID."
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

LAST_SEEN_FALLBACK = True
LAST_SEEN_FALLBACK__DOC = (
    "When using XEP-0319 (Last User Interaction in Presence), use the presence status "
    "to display the last seen information in the presence status. Useful for clients that "
    "do not implement XEP-0319."
)

QR_TIMEOUT = 60
QR_TIMEOUT__DOC = "Timeout for QR code flashing confirmation."
