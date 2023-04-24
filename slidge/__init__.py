import warnings

import slixmpp.plugins
from slixmpp import Message
from slixmpp.xmlstream import StanzaBase

from .core import config as global_config
from .core.command import FormField, SearchResult
from .core.contact import LegacyContact, LegacyRoster
from .core.gateway import BaseGateway
from .core.muc import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType
from .core.session import BaseSession
from .util import (  # noqa: F401
    xep_0030,
    xep_0050,
    xep_0054,
    xep_0077,
    xep_0100,
    xep_0153,
    xep_0234,
    xep_0292,
    xep_0313,
    xep_0356,
    xep_0356_old,
    xep_0372,
    xep_0402,
    xep_0446,
    xep_0447,
    xep_0461,
)
from .util.db import GatewayUser, user_store
from .util.util import addLoggingLevel


def formatwarning(message, category, filename, lineno, line=""):
    return f"{filename}:{lineno}:{category.__name__}:{message}\n"


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


Message.reply = reply  # type: ignore

warnings.formatwarning = formatwarning


slixmpp.plugins.PLUGINS.extend(
    [
        "xep_0234",
        "xep_0292_provider",
        "xep_0356_old",
        "xep_0372",
        "xep_0385",
        "xep_0402",
        "xep_0461",
        "xep_0446",
        "xep_0447",
    ]
)

__all__ = [
    "BaseGateway",
    "BaseSession",
    "GatewayUser",
    "LegacyBookmarks",
    "LegacyMUC",
    "LegacyContact",
    "LegacyParticipant",
    "LegacyRoster",
    "MucType",
    "FormField",
    "SearchResult",
    "user_store",
    "global_config",
]

addLoggingLevel()
