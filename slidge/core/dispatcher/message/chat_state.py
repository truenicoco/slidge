from slixmpp import Message
from slixmpp.xmlstream import StanzaBase

from ..util import DispatcherMixin, exceptions_to_xmpp_errors


class ChatStateMixin(DispatcherMixin):
    def __init__(self, xmpp) -> None:
        super().__init__(xmpp)
        xmpp.add_event_handler("chatstate_active", self.on_chatstate_active)
        xmpp.add_event_handler("chatstate_inactive", self.on_chatstate_inactive)
        xmpp.add_event_handler("chatstate_composing", self.on_chatstate_composing)
        xmpp.add_event_handler("chatstate_paused", self.on_chatstate_paused)

    @exceptions_to_xmpp_errors
    async def on_chatstate_active(self, msg: StanzaBase) -> None:
        assert isinstance(msg, Message)
        if msg["body"]:
            # if there is a body, it's handled in on_legacy_message()
            return
        session, entity, thread = await self._get_session_entity_thread(msg)
        await session.on_active(entity, thread)

    @exceptions_to_xmpp_errors
    async def on_chatstate_inactive(self, msg: StanzaBase) -> None:
        assert isinstance(msg, Message)
        session, entity, thread = await self._get_session_entity_thread(msg)
        await session.on_inactive(entity, thread)

    @exceptions_to_xmpp_errors
    async def on_chatstate_composing(self, msg: StanzaBase) -> None:
        assert isinstance(msg, Message)
        session, entity, thread = await self._get_session_entity_thread(msg)
        await session.on_composing(entity, thread)

    @exceptions_to_xmpp_errors
    async def on_chatstate_paused(self, msg: StanzaBase) -> None:
        assert isinstance(msg, Message)
        session, entity, thread = await self._get_session_entity_thread(msg)
        await session.on_paused(entity, thread)
