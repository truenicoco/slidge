import logging
from typing import TYPE_CHECKING

from slixmpp import JID, Message, Presence
from slixmpp.exceptions import IqError, XMPPError
from slixmpp.plugins.xep_0084.stanza import Info

from ...util.util import merge_resources
from ..session import BaseSession
from .chat_state import ChatStateMixin
from .marker import MarkerMixin
from .message import MessageMixin
from .muc import MucMixin
from .util import exceptions_to_xmpp_errors
from .vcard import VCardMixin

if TYPE_CHECKING:
    from .base import BaseGateway


class SessionDispatcher(
    MucMixin, ChatStateMixin, MarkerMixin, MessageMixin, VCardMixin
):
    def __init__(self, xmpp: "BaseGateway"):
        super().__init__(xmpp)

        for event in ("presence", "avatar_metadata_publish"):
            xmpp.add_event_handler(
                event, exceptions_to_xmpp_errors(getattr(self, "on_" + event))
            )

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

        # We can't use XMPPError here because from must be room@slidge/VALID-USER-NICK

        error_from = JID(muc.jid)
        error_from.resource = muc.user_nick
        error_stanza = p.error()
        error_stanza.set_to(p.get_from())
        error_stanza.set_from(error_from)
        error_stanza.enable("muc_join")
        error_stanza.enable("error")
        error_stanza["error"]["type"] = "cancel"
        error_stanza["error"]["by"] = muc.jid
        error_stanza["error"]["condition"] = "not-acceptable"
        error_stanza["error"][
            "text"
        ] = "Slidge does not let you change your nickname in groups."
        error_stanza.send()

    async def on_avatar_metadata_publish(self, m: Message):
        session = await self._get_session(m, timeout=None)
        if not session.user.preferences.get("sync_avatar", False):
            session.log.debug("User does not want to sync their avatar")
            return
        info = m["pubsub_event"]["items"]["item"]["avatar_metadata"]["info"]

        await self.on_avatar_metadata_info(session, info)

    async def on_avatar_metadata_info(self, session: BaseSession, info: Info):
        hash_ = info["id"]

        if session.user.avatar_hash == hash_:
            session.log.debug("We already know this avatar hash")
            return
        self.xmpp.store.users.set_avatar_hash(session.user_pk, None)

        if hash_:
            try:
                iq = await self.xmpp.plugin["xep_0084"].retrieve_avatar(
                    session.user_jid, hash_, ifrom=self.xmpp.boundjid.bare
                )
            except IqError as e:
                session.log.warning("Could not fetch the user's avatar: %s", e)
                return
            bytes_ = iq["pubsub"]["items"]["item"]["avatar_data"]["value"]
            type_ = info["type"]
            height = info["height"]
            width = info["width"]
        else:
            bytes_ = type_ = height = width = hash_ = None
        try:
            await session.on_avatar(bytes_, hash_, type_, width, height)
        except NotImplementedError:
            pass
        except Exception as e:
            # If something goes wrong here, replying an error stanza will to the
            # avatar update will likely not show in most clients, so let's send
            # a normal message from the component to the user.
            session.send_gateway_message(
                f"Something went wrong trying to set your avatar: {e!r}"
            )


_USEFUL_PRESENCES = {"available", "unavailable", "away", "chat", "dnd", "xa"}


log = logging.getLogger(__name__)
