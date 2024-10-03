import logging

from slixmpp import JID, Presence
from slixmpp.exceptions import XMPPError

from ...util.util import merge_resources
from ..session import BaseSession
from .util import DispatcherMixin, exceptions_to_xmpp_errors


class _IsDirectedAtComponent(Exception):
    def __init__(self, session: BaseSession):
        self.session = session


class PresenceHandlerMixin(DispatcherMixin):
    def __init__(self, xmpp):
        super().__init__(xmpp)

        xmpp.add_event_handler("presence_subscribe", self._handle_subscribe)
        xmpp.add_event_handler("presence_subscribed", self._handle_subscribed)
        xmpp.add_event_handler("presence_unsubscribe", self._handle_unsubscribe)
        xmpp.add_event_handler("presence_unsubscribed", self._handle_unsubscribed)
        xmpp.add_event_handler("presence_probe", self._handle_probe)
        xmpp.add_event_handler("presence", self.on_presence)

    async def __get_contact(self, pres: Presence):
        sess = await self._get_session(pres)
        pto = pres.get_to()
        if pto == self.xmpp.boundjid.bare:
            raise _IsDirectedAtComponent(sess)
        await sess.contacts.ready
        return await sess.contacts.by_jid(pto)

    @exceptions_to_xmpp_errors
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

    @exceptions_to_xmpp_errors
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

    @exceptions_to_xmpp_errors
    async def _handle_subscribed(self, pres: Presence):
        try:
            contact = await self.__get_contact(pres)
        except _IsDirectedAtComponent:
            return

        await contact.on_friend_accept()

    @exceptions_to_xmpp_errors
    async def _handle_unsubscribed(self, pres: Presence):
        try:
            contact = await self.__get_contact(pres)
        except _IsDirectedAtComponent:
            return

        if contact.is_friend:
            contact.is_friend = False
            await contact.on_friend_delete(pres["status"])

    @exceptions_to_xmpp_errors
    async def _handle_probe(self, pres: Presence):
        try:
            contact = await self.__get_contact(pres)
        except _IsDirectedAtComponent:
            session = await self._get_session(pres)
            session.send_cached_presence(pres.get_from())
            return
        if contact.is_friend:
            contact.send_last_presence(force=True)
        else:
            reply = pres.reply()
            reply["type"] = "unsubscribed"
            reply.send()

    @exceptions_to_xmpp_errors
    async def on_presence(self, p: Presence):
        if p.get_plugin("muc_join", check=True):
            # handled in on_groupchat_join
            # without this early return, since we switch from and to in this
            # presence stanza, on_groupchat_join ends up trying to instantiate
            # a MUC with the user's JID, which in turn leads to slidge sending
            # a (error) presence from=the user's JID, which terminates the
            # XML stream.
            return

        session = await self._get_session(p)

        pto = p.get_to()
        if pto == self.xmpp.boundjid.bare:
            session.log.debug("Received a presence from %s", p.get_from())
            if (ptype := p.get_type()) not in _USEFUL_PRESENCES:
                return
            if not session.user.preferences.get("sync_presence", False):
                session.log.debug("User does not want to sync their presence")
                return
            # NB: get_type() returns either a proper presence type or
            #     a presence show if available. Weird, weird, weird slix.
            resources = self.xmpp.roster[self.xmpp.boundjid.bare][
                p.get_from()
            ].resources
            await session.on_presence(
                p.get_from().resource,
                ptype,  # type: ignore
                p["status"],
                resources,
                merge_resources(resources),
            )
            if p.get_type() == "available":
                await self.xmpp.pubsub.on_presence_available(p, None)
            return

        if p.get_type() == "available":
            try:
                contact = await session.contacts.by_jid(pto)
            except XMPPError:
                contact = None
            if contact is not None:
                await self.xmpp.pubsub.on_presence_available(p, contact)
                return

        muc = session.bookmarks.by_jid_only_if_exists(JID(pto.bare))

        if muc is not None and p.get_type() == "unavailable":
            return muc.on_presence_unavailable(p)

        if muc is None or p.get_from().resource not in muc.get_user_resources():
            return

        if pto.resource == muc.user_nick:
            # Ignore presence stanzas with the valid nick.
            # even if joined to the group, we might receive those from clients,
            # when setting a status message, or going away, etc.
            return

        # We can't use XMPPError here because XMPPError does not have a way to
        # add the <x xmlns="http://jabber.org/protocol/muc" /> element

        error_stanza = p.error()
        error_stanza.set_to(p.get_from())
        error_stanza.set_from(pto)
        error_stanza.enable("muc_join")  # <x xmlns="http://jabber.org/protocol/muc" />
        error_stanza.enable("error")
        error_stanza["error"]["type"] = "cancel"
        error_stanza["error"]["by"] = muc.jid
        error_stanza["error"]["condition"] = "not-acceptable"
        error_stanza["error"][
            "text"
        ] = "Slidge does not let you change your nickname in groups."
        error_stanza.send()


_USEFUL_PRESENCES = {"available", "unavailable", "away", "chat", "dnd", "xa"}

log = logging.getLogger(__name__)
