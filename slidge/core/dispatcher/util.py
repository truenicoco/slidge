import logging
from functools import wraps
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

from slixmpp import JID, Iq, Message, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.xmlstream import StanzaBase

from ...util.types import Recipient, RecipientType
from ..session import BaseSession

if TYPE_CHECKING:
    from slidge import BaseGateway
    from slidge.group import LegacyMUC


class Ignore(BaseException):
    pass


class DispatcherMixin:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

    async def _get_session(
        self,
        stanza: Message | Presence | Iq,
        timeout: int | None = 10,
        wait_for_ready=True,
        logged=False,
    ) -> BaseSession:
        xmpp = self.xmpp
        if stanza.get_from().server == xmpp.boundjid.bare:
            log.debug("Ignoring echo")
            raise Ignore
        if (
            isinstance(stanza, Message)
            and stanza.get_type() == "chat"
            and stanza.get_to() == xmpp.boundjid.bare
        ):
            log.debug("Ignoring message to component")
            raise Ignore
        session = await self._get_session_from_jid(
            stanza.get_from(), timeout, wait_for_ready, logged
        )
        if isinstance(stanza, Message) and _ignore(session, stanza):
            raise Ignore
        return session

    async def _get_session_from_jid(
        self,
        jid: JID,
        timeout: int | None = 10,
        wait_for_ready=True,
        logged=False,
    ) -> BaseSession:
        session = self.xmpp.get_session_from_jid(jid)
        if session is None:
            raise XMPPError("registration-required")
        if logged:
            session.raise_if_not_logged()
        if wait_for_ready:
            await session.wait_for_ready(timeout)
        return session

    async def get_muc_from_stanza(self, iq: Iq | Message | Presence) -> "LegacyMUC":
        ito = iq.get_to()
        if ito == self.xmpp.boundjid.bare:
            raise XMPPError("bad-request", text="This is only handled for MUCs")

        session = await self._get_session(iq, logged=True)
        muc = await session.bookmarks.by_jid(ito)
        return muc

    def _xmpp_msg_id_to_legacy(self, session: "BaseSession", xmpp_id: str):
        sent = self.xmpp.store.sent.get_legacy_id(session.user_pk, xmpp_id)
        if sent is not None:
            return self.xmpp.LEGACY_MSG_ID_TYPE(sent)

        multi = self.xmpp.store.multi.get_legacy_id(session.user_pk, xmpp_id)
        if multi:
            return self.xmpp.LEGACY_MSG_ID_TYPE(multi)

        try:
            return session.xmpp_to_legacy_msg_id(xmpp_id)
        except XMPPError:
            raise
        except Exception as e:
            log.debug("Couldn't convert xmpp msg ID to legacy ID.", exc_info=e)
            raise XMPPError(
                "internal-server-error", "Couldn't convert xmpp msg ID to legacy ID."
            )

    async def _get_session_entity_thread(
        self, msg: Message
    ) -> tuple["BaseSession", Recipient, int | str]:
        session = await self._get_session(msg)
        e: Recipient = await _get_entity(session, msg)
        legacy_thread = await self._xmpp_to_legacy_thread(session, msg, e)
        return session, e, legacy_thread

    async def _xmpp_to_legacy_thread(
        self, session: "BaseSession", msg: Message, recipient: RecipientType
    ):
        xmpp_thread = msg["thread"]
        if not xmpp_thread:
            return None

        if session.MESSAGE_IDS_ARE_THREAD_IDS:
            return self._xmpp_msg_id_to_legacy(session, xmpp_thread)

        legacy_thread_str = session.xmpp.store.sent.get_legacy_thread(
            session.user_pk, xmpp_thread
        )
        if legacy_thread_str is not None:
            return session.xmpp.LEGACY_MSG_ID_TYPE(legacy_thread_str)
        async with session.thread_creation_lock:
            legacy_thread = await recipient.create_thread(xmpp_thread)
            session.xmpp.store.sent.set_thread(
                session.user_pk, str(legacy_thread), xmpp_thread
            )
            return legacy_thread


def _ignore(session: "BaseSession", msg: Message):
    i = msg.get_id()
    if i.startswith("slidge-carbon-"):
        return True
    if i not in session.ignore_messages:
        return False
    session.log.debug("Ignored sent carbon: %s", i)
    session.ignore_messages.remove(i)
    return True


async def _get_entity(session: "BaseSession", m: Message) -> RecipientType:
    session.raise_if_not_logged()
    if m.get_type() == "groupchat":
        muc = await session.bookmarks.by_jid(m.get_to())
        r = m.get_from().resource
        if r not in muc.get_user_resources():
            session.create_task(muc.kick_resource(r))
            raise XMPPError("not-acceptable", "You are not connected to this chat")
        return muc
    else:
        return await session.contacts.by_jid(m.get_to())


StanzaType = TypeVar("StanzaType", bound=StanzaBase)
HandlerType = Callable[[Any, StanzaType], Awaitable[None]]


def exceptions_to_xmpp_errors(cb: HandlerType) -> HandlerType:
    @wraps(cb)
    async def wrapped(*args):
        try:
            await cb(*args)
        except Ignore:
            pass
        except XMPPError:
            raise
        except NotImplementedError:
            log.debug("NotImplementedError raised in %s", cb)
            raise XMPPError(
                "feature-not-implemented", "Not implemented by the legacy module"
            )
        except Exception as e:
            log.error("Failed to handle incoming stanza: %s", args, exc_info=e)
            raise XMPPError("internal-server-error", str(e))

    return wrapped


log = logging.getLogger(__name__)
