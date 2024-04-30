import logging

from slixmpp import JID, Presence

from ..session import BaseSession


class _IsDirectedAtComponent(Exception):
    def __init__(self, session: BaseSession):
        self.session = session


class PresenceHandlerMixin:
    boundjid: JID

    def get_session_from_stanza(self, s) -> BaseSession:
        raise NotImplementedError

    async def __get_contact(self, pres: Presence):
        sess = await self.__get_session(pres)
        pto = pres.get_to()
        if pto == self.boundjid.bare:
            raise _IsDirectedAtComponent(sess)
        await sess.contacts.ready
        return await sess.contacts.by_jid(pto)

    async def __get_session(self, p: Presence):
        sess = self.get_session_from_stanza(p)
        return sess

    async def _handle_subscribe(self, pres: Presence):
        try:
            contact = await self.__get_contact(pres)
        except _IsDirectedAtComponent:
            pres.reply().send()
            return

        if contact.is_friend:
            pres.reply().send()
        else:
            await contact.on_friend_request(pres["status"])

    async def _handle_unsubscribe(self, pres: Presence):
        pres.reply().send()

        try:
            contact = await self.__get_contact(pres)
        except _IsDirectedAtComponent as e:
            e.session.send_gateway_message("Bye bye!")
            await e.session.kill_by_jid(e.session.user_jid)
            return

        contact.is_friend = False
        await contact.on_friend_delete(pres["status"])

    async def _handle_subscribed(self, pres: Presence):
        try:
            contact = await self.__get_contact(pres)
        except _IsDirectedAtComponent:
            return

        await contact.on_friend_accept()

    async def _handle_unsubscribed(self, pres: Presence):
        try:
            contact = await self.__get_contact(pres)
        except _IsDirectedAtComponent:
            return

        if contact.is_friend:
            contact.is_friend = False
            await contact.on_friend_delete(pres["status"])

    async def _handle_probe(self, pres: Presence):
        try:
            contact = await self.__get_contact(pres)
        except _IsDirectedAtComponent:
            session = await self.__get_session(pres)
            session.send_cached_presence(pres.get_from())
            return
        if contact.is_friend:
            contact.send_last_presence(force=True)
        else:
            reply = pres.reply()
            reply["type"] = "unsubscribed"
            reply.send()

    async def _handle_new_subscription(self, pres: Presence):
        pass

    async def _handle_removed_subscription(self, pres: Presence):
        pass


log = logging.getLogger(__name__)
