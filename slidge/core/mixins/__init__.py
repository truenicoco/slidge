"""
Mixins
"""

from typing import Optional

from .avatar import AvatarMixin
from .disco import ChatterDiscoMixin
from .message import MessageCarbonMixin, MessageMixin
from .presence import PresenceMixin


class FullMixin(ChatterDiscoMixin, MessageMixin, PresenceMixin):
    pass


class FullCarbonMixin(ChatterDiscoMixin, MessageCarbonMixin, PresenceMixin):
    pass


class StoredAttributeMixin:
    def serialize_extra_attributes(self) -> Optional[dict]:
        return None

    def deserialize_extra_attributes(self, data: dict) -> None:
        pass


__all__ = ("AvatarMixin", "FullCarbonMixin", "StoredAttributeMixin")
