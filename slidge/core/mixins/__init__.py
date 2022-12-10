from .disco import BaseDiscoMixin, ChatterDiscoMixin
from .message import MessageCarbonMixin, MessageMixin
from .presence import PresenceMixin


class FullMixin(ChatterDiscoMixin, MessageMixin, PresenceMixin):
    pass


class FullCarbonMixin(ChatterDiscoMixin, MessageCarbonMixin, PresenceMixin):
    pass
