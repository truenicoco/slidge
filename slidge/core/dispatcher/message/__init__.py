from .chat_state import ChatStateMixin
from .marker import MarkerMixin
from .message import MessageContentMixin


class MessageMixin(ChatStateMixin, MarkerMixin, MessageContentMixin):
    pass


__all__ = ("MessageMixin",)
