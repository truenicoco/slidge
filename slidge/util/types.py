from pathlib import Path
from typing import TYPE_CHECKING, Hashable, Literal, TypeVar, Union

if TYPE_CHECKING:
    from ..core.contact import LegacyContact, LegacyRoster
    from ..core.gateway import BaseGateway
    from ..core.pubsub import PepItem
    from ..core.session import BaseSession


GatewayType = TypeVar("GatewayType", bound="BaseGateway")
LegacyContactType = TypeVar("LegacyContactType", bound="LegacyContact")
LegacyMessageType = TypeVar("LegacyMessageType", bound=Hashable)
LegacyRosterType = TypeVar("LegacyRosterType", bound="LegacyRoster")
LegacyUserIdType = TypeVar("LegacyUserIdType", bound=Hashable)
PepItemType = TypeVar("PepItemType", bound="PepItem")
SessionType = TypeVar("SessionType", bound="BaseSession")

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
