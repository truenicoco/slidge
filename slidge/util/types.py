"""
Typing stuff
"""

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Generic,
    Hashable,
    Literal,
    NamedTuple,
    Optional,
    TypedDict,
    TypeVar,
    Union,
)

from slixmpp import Message, Presence
from slixmpp.types import PresenceShows, PresenceTypes

if TYPE_CHECKING:
    from ..contact import LegacyContact
    from ..core.pubsub import PepItem
    from ..core.session import BaseSession
    from ..group.participant import LegacyMUC, LegacyParticipant

    AnyBaseSession = BaseSession[Any, Any]
else:
    AnyBaseSession = None


class URL(str):
    pass


LegacyGroupIdType = TypeVar("LegacyGroupIdType", bound=Hashable)
"""
Type of the unique identifier for groups, usually a str or an int,
but anything hashable should work.
"""
LegacyMessageType = TypeVar("LegacyMessageType", bound=Hashable)
LegacyThreadType = TypeVar("LegacyThreadType", bound=Hashable)
LegacyUserIdType = TypeVar("LegacyUserIdType", bound=Hashable)

LegacyContactType = TypeVar("LegacyContactType", bound="LegacyContact")
LegacyMUCType = TypeVar("LegacyMUCType", bound="LegacyMUC")
LegacyParticipantType = TypeVar("LegacyParticipantType", bound="LegacyParticipant")

PepItemType = TypeVar("PepItemType", bound="PepItem")

Recipient = Union["LegacyMUC", "LegacyContact"]
RecipientType = TypeVar("RecipientType", bound=Recipient)
Sender = Union["LegacyContact", "LegacyParticipant"]
AvatarType = Union[bytes, str, Path]
LegacyFileIdType = Union[int, str]
AvatarIdType = Union[LegacyFileIdType, URL]

ChatState = Literal["active", "composing", "gone", "inactive", "paused"]
ProcessingHint = Literal["no-store", "markable", "store"]
Marker = Literal["acknowledged", "received", "displayed"]
FieldType = Literal[
    "boolean",
    "fixed",
    "text-single",
    "jid-single",
    "jid-multi",
    "list-single",
    "list-multi",
    "text-private",
]
MucAffiliation = Literal["owner", "admin", "member", "outcast", "none"]
MucRole = Literal["visitor", "participant", "moderator", "none"]
# https://xmpp.org/registrar/disco-categories.html#client
ClientType = Literal[
    "bot", "console", "game", "handheld", "pc", "phone", "sms", "tablet", "web"
]


@dataclass
class MessageReference(Generic[LegacyMessageType]):
    """
    A "message reply", ie a "quoted message" (:xep:`0461`)

    At the very minimum, the legacy message ID attribute must be set, but to
    ensure that the quote is displayed in all XMPP clients, the author must also
    be set (use the string "user" if the slidge user is the author of the referenced
    message).
    The body is used as a fallback for XMPP clients that do not support :xep:`0461`
    of that failed to find the referenced message.
    """

    legacy_id: LegacyMessageType
    author: Optional[Union[Literal["user"], "LegacyParticipant", "LegacyContact"]] = (
        None
    )
    body: Optional[str] = None


@dataclass
class LegacyAttachment:
    """
    A file attachment to a message

    At the minimum, one of the ``path``, ``steam``, ``data`` or ``url`` attribute
    has to be set

    To be used with :meth:`.LegacyContact.send_files` or
    :meth:`.LegacyParticipant.send_files`
    """

    path: Optional[Union[Path, str]] = None
    name: Optional[Union[str]] = None
    stream: Optional[IO[bytes]] = None
    data: Optional[bytes] = None
    content_type: Optional[str] = None
    legacy_file_id: Optional[Union[str, int]] = None
    url: Optional[str] = None
    caption: Optional[str] = None
    """
    A caption for this specific image. For a global caption for a list of attachments,
    use the ``body`` parameter of :meth:`.AttachmentMixin.send_files`
    """

    def __post_init__(self):
        if not any(
            x is not None for x in (self.path, self.stream, self.data, self.url)
        ):
            raise TypeError("There is not data in this attachment", self)


class MucType(IntEnum):
    """
    The type of group, private, public, anonymous or not.
    """

    GROUP = 0
    """
    A private group, members-only and non-anonymous, eg a family group.
    """
    CHANNEL = 1
    """
    A public group, aka an anonymous channel.
    """
    CHANNEL_NON_ANONYMOUS = 2
    """
    A public group where participants' legacy IDs are visible to everybody.
    """


PseudoPresenceShow = Union[PresenceShows, Literal[""]]


class ResourceDict(TypedDict):
    show: PseudoPresenceShow
    status: str
    priority: int


MessageOrPresenceTypeVar = TypeVar(
    "MessageOrPresenceTypeVar", bound=Union[Message, Presence]
)


class LinkPreview(NamedTuple):
    about: str
    title: Optional[str]
    description: Optional[str]
    url: Optional[str]
    image: Optional[str]
    type: Optional[str]
    site_name: Optional[str]


class Mention(NamedTuple):
    contact: "LegacyContact"
    start: int
    end: int


class Hat(NamedTuple):
    uri: str
    title: str


class UserPreferences(TypedDict):
    sync_avatar: bool
    sync_presence: bool


class MamMetadata(NamedTuple):
    id: str
    sent_on: datetime


class HoleBound(NamedTuple):
    id: int | str
    timestamp: datetime


class CachedPresence(NamedTuple):
    last_seen: Optional[datetime] = None
    ptype: Optional[PresenceTypes] = None
    pstatus: Optional[str] = None
    pshow: Optional[PresenceShows] = None


class Sticker(NamedTuple):
    path: Path
    content_type: Optional[str]
    hashes: dict[str, str]
