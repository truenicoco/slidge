# This module contains patches for slixmpp; some have pending requests upstream
# and should be removed on the next slixmpp release.

# ruff: noqa: F401

from asyncio import Semaphore

import slixmpp.plugins
from slixmpp import Message, Presence
from slixmpp.plugins.xep_0050 import XEP_0050, Command
from slixmpp.plugins.xep_0115 import XEP_0115
from slixmpp.xmlstream import StanzaBase

from . import (
    link_preview,
    xep_0077,
    xep_0100,
    xep_0153,
    xep_0264,
    xep_0292,
    xep_0313,
    xep_0317,
    xep_0356,
    xep_0356_old,
    xep_0461,
)


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


process_caps_original = XEP_0115._process_caps
caps_semaphore = Semaphore()


async def process_caps_wrapper(self, pres: Presence):
    async with caps_semaphore:
        await process_caps_original(self, pres)


XEP_0115._process_caps = process_caps_wrapper


slixmpp.plugins.PLUGINS.extend(
    [
        "link_preview",
        "xep_0264",
        "xep_0292_provider",
        "xep_0317",
        "xep_0356_old",
    ]
)


Message.reply = reply  # type: ignore
