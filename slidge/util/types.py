from pathlib import Path
from typing import TYPE_CHECKING, Hashable, Literal, TypeVar, Union

if TYPE_CHECKING:
    from ..core.contact import LegacyContact, LegacyRoster
    from ..core.gateway import BaseGateway
    from ..core.muc.bookmarks import LegacyBookmarks
    from ..core.muc.participant import LegacyParticipant
    from ..core.muc.room import LegacyMUC
    from ..core.pubsub.pubsub import PepItem
    from ..core.session import BaseSession


LegacyGroupIdType = TypeVar("LegacyGroupIdType", bound=Hashable)
LegacyMessageType = TypeVar("LegacyMessageType", bound=Hashable)
LegacyThreadType = TypeVar("LegacyThreadType", bound=Hashable)
LegacyUserIdType = TypeVar("LegacyUserIdType", bound=Hashable)

# BookmarksType = TypeVar("BookmarksType", bound="LegacyBookmarks")
LegacyContactType = TypeVar("LegacyContactType", bound="LegacyContact")
# GatewayType = TypeVar("GatewayType", bound="BaseGateway")
LegacyMUCType = TypeVar("LegacyMUCType", bound="LegacyMUC")
LegacyParticipantType = TypeVar("LegacyParticipantType", bound="LegacyParticipant")
# LegacyRosterType = TypeVar("LegacyRosterType", bound="LegacyRoster")
# SessionType = TypeVar("SessionType", bound="BaseSession")

PepItemType = TypeVar("PepItemType", bound="PepItem")

Recipient = Union["LegacyMUC", "LegacyContact"]
RecipientType = TypeVar("RecipientType", bound=Recipient)
Sender = Union["LegacyContact", "LegacyParticipant"]
AvatarType = Union[bytes, str, Path]

ChatState = Literal["active", "composing", "gone", "inactive", "paused"]
ProcessingHint = Literal["no-store", "markable", "store"]
Marker = Literal["acknowledged", "received", "displayed"]
PresenceShow = Literal["away", "chat", "dnd", "xa"]
FieldType = Literal[
    "boolean",
    "fixed",
    "text-single",
    "jid-single",
    "list-single",
    "list-multi",
    "text-private",
]
