from .disco import DiscoMixin
from .message import MessageCarbonMixin, MessageMixin
from .presence import PresenceMixin


class FullMixin(DiscoMixin, MessageMixin, PresenceMixin):
    pass


class FullCarbonMixin(DiscoMixin, MessageCarbonMixin, PresenceMixin):
    pass
