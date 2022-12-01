from pathlib import Path
from typing import Hashable, TypeVar, Union

AvatarType = Union[bytes, str, Path]
LegacyUserIdType = Union[str, int]
LegacyContactIdType = Union[str, int]
LegacyMessageType = TypeVar("LegacyMessageType", bound=Hashable)
