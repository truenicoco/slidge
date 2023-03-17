import logging
from typing import TYPE_CHECKING, Callable, Union

from slixmpp import Message, Presence

from ...util.error import XMPPError
from ..session import BaseSession

if TYPE_CHECKING:
    from .base import BaseGateway


class SessionDispatcher:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

        # fmt: off
        async def msg(m): await self._dispatch(m, BaseSession.send_from_msg)
        async def disp(m): await self._dispatch(m, BaseSession.displayed_from_msg)
        async def active(m): await self._dispatch(m, BaseSession.active_from_msg)
        async def inactive(m): await self._dispatch(m, BaseSession.inactive_from_msg)
        async def composing(m): await self._dispatch(m, BaseSession.composing_from_msg)
        async def paused(m): await self._dispatch(m, BaseSession.paused_from_msg)
        async def correct(m): await self._dispatch(m, BaseSession.correct_from_msg)
        async def react(m): await self._dispatch(m, BaseSession.react_from_msg)
        async def retract(m): await self._dispatch(m, BaseSession.retract_from_msg)
        async def groupchat_join(p): await self._dispatch(p, BaseSession.join_groupchat)
        # fmt: on

        xmpp.add_event_handler("legacy_message", msg)
        xmpp.add_event_handler("marker_displayed", disp)
        xmpp.add_event_handler("chatstate_active", active)
        xmpp.add_event_handler("chatstate_inactive", inactive)
        xmpp.add_event_handler("chatstate_composing", composing)
        xmpp.add_event_handler("chatstate_paused", paused)
        xmpp.add_event_handler("message_correction", correct)
        xmpp.add_event_handler("reactions", react)
        xmpp.add_event_handler("message_retract", retract)

        xmpp.add_event_handler("groupchat_join", groupchat_join)
        xmpp.add_event_handler("groupchat_message", msg)

    async def _dispatch(self, m: Union[Message, Presence], cb: Callable):
        xmpp = self.xmpp
        if m.get_from().server == xmpp.boundjid.bare:
            log.debug("Ignoring echo")
            return
        if m.get_to() == xmpp.boundjid.bare:
            log.debug("Ignoring message to component")
            return
        s = xmpp.get_session_from_stanza(m)
        try:
            await cb(s, m)
        except XMPPError:
            raise
        except Exception as e:
            s.log.error("Failed to handle incoming stanza: %s", m, exc_info=e)
            raise XMPPError("internal-server-error", str(e))


log = logging.getLogger(__name__)
