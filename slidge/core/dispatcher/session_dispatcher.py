import logging
from typing import TYPE_CHECKING

from slixmpp import Message
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.plugins.xep_0084.stanza import Info

from ..session import BaseSession
from .caps import CapsMixin
from .disco import DiscoMixin
from .message import MessageMixin
from .muc import MucMixin
from .presence import PresenceHandlerMixin
from .registration import RegistrationMixin
from .search import SearchMixin
from .util import exceptions_to_xmpp_errors
from .vcard import VCardMixin

if TYPE_CHECKING:
    from slidge.core.gateway import BaseGateway


class SessionDispatcher(
    CapsMixin,
    DiscoMixin,
    RegistrationMixin,
    MessageMixin,
    MucMixin,
    PresenceHandlerMixin,
    SearchMixin,
    VCardMixin,
):
    def __init__(self, xmpp: "BaseGateway"):
        super().__init__(xmpp)
        xmpp.add_event_handler(
            "avatar_metadata_publish", self.on_avatar_metadata_publish
        )

    @exceptions_to_xmpp_errors
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
            except (IqError, IqTimeout) as e:
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


log = logging.getLogger(__name__)
