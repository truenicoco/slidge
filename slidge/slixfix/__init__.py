# This module contains patches for slixmpp; some have pending requests upstream
# and should be removed on the next slixmpp release.

# ruff: noqa: F401

import slixmpp.plugins
from slixmpp import Iq, Message
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0050 import XEP_0050, Command
from slixmpp.plugins.xep_0231 import XEP_0231
from slixmpp.xmlstream import StanzaBase

from . import (  # xep_0356,
    link_preview,
    xep_0077,
    xep_0100,
    xep_0153,
    xep_0264,
    xep_0292,
    xep_0313,
    xep_0317,
    xep_0356_old,
    xep_0424,
    xep_0490,
)


async def _handle_bob_iq(self, iq: Iq):
    cid = iq["bob"]["cid"]

    if iq["type"] == "result":
        await self.api["set_bob"](iq["from"], None, iq["to"], args=iq["bob"])
        self.xmpp.event("bob", iq)
    elif iq["type"] == "get":
        data = await self.api["get_bob"](iq["to"], None, iq["from"], args=cid)

        if data is None:
            raise XMPPError(
                "item-not-found",
                f"Bits of binary '{cid}' is not available.",
            )

        if isinstance(data, Iq):
            data["id"] = iq["id"]
            data.send()
            return

        iq = iq.reply()
        iq.append(data)
        iq.send()


XEP_0231._handle_bob_iq = _handle_bob_iq


def session_bind(self, jid):
    self.xmpp["xep_0030"].add_feature(Command.namespace)
    # awful hack to for the disco items: we need to comment this line
    # related issue: https://todo.sr.ht/~nicoco/slidge/131
    # self.xmpp['xep_0030'].set_items(node=Command.namespace, items=tuple())


XEP_0050.session_bind = session_bind  # type:ignore


def reply(self, body=None, clear=True):
    """
    Overrides slixmpp's Message.reply(), since it strips to sender's resource
    for mtype=groupchat, and we do not want that, because when we raise an XMPPError,
    we actually want to preserve the resource.
    (this is called in RootStanza.exception() to handle XMPPErrors)
    """
    new_message = StanzaBase.reply(self, clear)
    new_message["thread"] = self["thread"]
    new_message["parent_thread"] = self["parent_thread"]

    del new_message["id"]
    if self.stream is not None and self.stream.use_message_ids:
        new_message["id"] = self.stream.new_id()

    if body is not None:
        new_message["body"] = body
    return new_message


slixmpp.plugins.PLUGINS.extend(
    [
        "link_preview",
        "xep_0264",
        "xep_0292_provider",
        "xep_0317",
        "xep_0356_old",
        "xep_0490",
    ]
)


Message.reply = reply  # type: ignore
