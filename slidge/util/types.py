from pathlib import Path
from typing import TYPE_CHECKING, Hashable, Literal, TypeVar, Union

if TYPE_CHECKING:
    from ..core.contact import LegacyContact, LegacyRoster
    from ..core.gateway import BaseGateway
    from ..core.muc.bookmarks import LegacyBookmarks
    from ..core.muc.participant import LegacyParticipant
    from ..core.muc.room import LegacyMUC
    from ..core.pubsub import PepItem
    from ..core.session import BaseSession

BookmarksType = TypeVar("BookmarksType", bound="LegacyBookmarks")
GatewayType = TypeVar("GatewayType", bound="BaseGateway")
LegacyContactType = TypeVar("LegacyContactType", bound="LegacyContact")
LegacyGroupIdType = TypeVar("LegacyGroupIdType", bound=Hashable)
LegacyMessageType = TypeVar("LegacyMessageType", bound=Hashable)
LegacyMUCType = TypeVar("LegacyMUCType", bound="LegacyMUC")
LegacyParticipantType = TypeVar("LegacyParticipantType", bound="LegacyParticipant")
LegacyRosterType = TypeVar("LegacyRosterType", bound="LegacyRoster")
LegacyUserIdType = TypeVar("LegacyUserIdType", bound=Hashable)
PepItemType = TypeVar("PepItemType", bound="PepItem")
SessionType = TypeVar("SessionType", bound="BaseSession")
Chat = Union[LegacyMUCType, LegacyContactType]
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
