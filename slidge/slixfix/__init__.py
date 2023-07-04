"""
This module contains patches for slixmpp; some have pending requests upstream
and should be removed on the next slixmpp release.
"""

# ruff: noqa: F401

import slixmpp.plugins
from slixmpp import Message
from slixmpp.plugins.xep_0050 import XEP_0050, Command
from slixmpp.xmlstream import StanzaBase

from . import (
    xep_0054,
    xep_0077,
    xep_0100,
    xep_0153,
    xep_0292,
    xep_0313,
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


slixmpp.plugins.PLUGINS.extend(
    [
        "xep_0292_provider",
        "xep_0356_old",
        "xep_0385",
        "xep_0447",
    ]
)


Message.reply = reply  # type: ignore
