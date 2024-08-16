from slixmpp import JID, Message
from slixmpp.xmlstream import StanzaBase

from ....group.room import LegacyMUC
from ....util.types import Recipient
from ..util import DispatcherMixin, _get_entity, exceptions_to_xmpp_errors


class MarkerMixin(DispatcherMixin):
    def __init__(self, xmpp) -> None:
        super().__init__(xmpp)
        xmpp.add_event_handler("marker_displayed", self.on_marker_displayed)
        xmpp.add_event_handler(
            "message_displayed_synchronization_publish",
            self.on_message_displayed_synchronization_publish,
        )

    @exceptions_to_xmpp_errors
    async def on_marker_displayed(self, msg: StanzaBase) -> None:
        assert isinstance(msg, Message)
        session = await self._get_session(msg)

        e: Recipient = await _get_entity(session, msg)
        legacy_thread = await self._xmpp_to_legacy_thread(session, msg, e)
        displayed_msg_id = msg["displayed"]["id"]
        if not isinstance(e, LegacyMUC) and self.xmpp.MARK_ALL_MESSAGES:
            to_mark = e.get_msg_xmpp_id_up_to(displayed_msg_id)  # type: ignore
            if to_mark is None:
                session.log.debug("Can't mark all messages up to %s", displayed_msg_id)
                to_mark = [displayed_msg_id]
        else:
            to_mark = [displayed_msg_id]
        for xmpp_id in to_mark:
            await session.on_displayed(
                e, self._xmpp_msg_id_to_legacy(session, xmpp_id), legacy_thread
            )
            if isinstance(e, LegacyMUC):
                await e.echo(msg, None)

    @exceptions_to_xmpp_errors
    async def on_message_displayed_synchronization_publish(
        self, msg: StanzaBase
    ) -> None:
        assert isinstance(msg, Message)
        chat_jid = JID(msg["pubsub_event"]["items"]["item"]["id"])
        if chat_jid.server != self.xmpp.boundjid.bare:
            return

        session = await self._get_session(msg, timeout=None)

        if chat_jid == self.xmpp.boundjid.bare:
            return

        chat = await session.get_contact_or_group_or_participant(chat_jid)
        if not isinstance(chat, LegacyMUC):
            session.log.debug("Ignoring non-groupchat MDS event")
            return

        stanza_id = msg["pubsub_event"]["items"]["item"]["displayed"]["stanza_id"]["id"]
        await session.on_displayed(
            chat, self._xmpp_msg_id_to_legacy(session, stanza_id)
        )
