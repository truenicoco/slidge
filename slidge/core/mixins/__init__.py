"""
Mixins
"""

from .avatar import AvatarMixin
from .disco import ChatterDiscoMixin
from .message import MessageCarbonMixin, MessageMixin
from .presence import PresenceMixin


class FullMixin(ChatterDiscoMixin, MessageMixin, PresenceMixin):
    pass


class FullCarbonMixin(ChatterDiscoMixin, MessageCarbonMixin, PresenceMixin):
    pass


__all__ = ("AvatarMixin",)
