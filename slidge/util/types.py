from pathlib import Path
from typing import Hashable, TypeVar, Union

AvatarType = Union[bytes, str, Path]
LegacyUserIdType = TypeVar("LegacyUserIdType", bound=Hashable)
LegacyMessageType = TypeVar("LegacyMessageType", bound=Hashable)
