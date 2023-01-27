import logging
from typing import Literal, Optional

import slixmpp.stanza.rootstanza
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.exceptions import XMPPError as Base
from slixmpp.stanza.error import Error
from slixmpp.types import JidStr
from slixmpp.xmlstream import ET

# workaround for https://lab.louiz.org/poezio/slixmpp/-/issues/3474
Error.namespace = "jabber:component:accept"


def exception(self, e):
    """
    Monkeypatch on slixmpp to use the 'by' attribute
    """
    if isinstance(e, IqError):
        # We received an Iq error reply, but it wasn't caught
        # locally. Using the condition/text from that error
        # response could leak too much information, so we'll
        # only use a generic error here.
        reply = self.reply()
        reply["error"]["condition"] = "undefined-condition"
        reply["error"]["text"] = "External error"
        reply["error"]["type"] = "cancel"
        log.warning("You should catch IqError exceptions")
        reply.send()
    elif isinstance(e, IqTimeout):
        reply = self.reply()
        reply["error"]["condition"] = "remote-server-timeout"
        reply["error"]["type"] = "wait"
        log.warning("You should catch IqTimeout exceptions")
        reply.send()
    elif isinstance(e, XMPPError):
        # We raised this deliberately
        keep_id = self["id"]
        reply = self.reply(clear=e.clear)
        reply["id"] = keep_id
        reply["error"]["condition"] = e.condition
        reply["error"]["text"] = e.text
        reply["error"]["type"] = e.etype
        if e.by:
            reply["error"]["by"] = e.by
        if e.extension is not None:
            # Extended error tag
            extxml = ET.Element(
                "{%s}%s" % (e.extension_ns, e.extension), e.extension_args
            )
            reply["error"].append(extxml)
        reply.send()
    else:
        # We probably didn't raise this on purpose, so send an error stanza
        keep_id = self["id"]
        reply = self.reply()
        reply["id"] = keep_id
        reply["error"]["condition"] = "undefined-condition"
        reply["error"]["text"] = "Slixmpp got into trouble."
        reply["error"]["type"] = "cancel"
        reply.send()
        # log the error
        log.exception("Error handling {%s}%s stanza", self.namespace, self.name)
        # Finally raise the exception to a global exception handler
        self.stream.exception(e)


slixmpp.stanza.rootstanza.RootStanza.exception = exception  # type:ignore


Conditions = Literal[
    "bad-request",
    "conflict",
    "feature-not-implemented",
    "forbidden",
    "gone",
    "internal-server-error",
    "item-not-found",
    "jid-malformed",
    "not-acceptable",
    "not-allowed",
    "not-authorized",
    "payment-required",
    "recipient-unavailable",
    "redirect",
    "registration-required",
    "remote-server-not-found",
    "remote-server-timeout",
    "resource-constraint",
    "service-unavailable",
    "subscription-required",
    "undefined-condition",
    "unexpected-request",
]

ErrorTypes = Literal["modify", "cancel", "auth", "wait", "cancel"]

TYPE_BY_CONDITION: dict[Conditions, ErrorTypes] = {
    "bad-request": "modify",
    "conflict": "cancel",
    "feature-not-implemented": "cancel",
    "forbidden": "auth",
    "gone": "modify",
    "internal-server-error": "wait",
    "item-not-found": "cancel",
    "jid-malformed": "modify",
    "not-acceptable": "modify",
    "not-allowed": "cancel",
    "not-authorized": "auth",
    "payment-required": "auth",
    "recipient-unavailable": "wait",
    "redirect": "modify",
    "registration-required": "auth",
    "remote-server-not-found": "cancel",
    "remote-server-timeout": "wait",
    "resource-constraint": "wait",
    "service-unavailable": "cancel",
    "subscription-required": "auth",
    "undefined-condition": "cancel",
    "unexpected-request": "modify",
}


class XMPPError(Base):
    """
    Improvements over Base: include by, automatically determine
    appropriate etype if not given
    """

    def __init__(
        self,
        condition: Conditions = "undefined-condition",
        text="",
        by: Optional[JidStr] = None,
        etype: Optional[ErrorTypes] = None,
    ):
        if etype is None:
            etype = TYPE_BY_CONDITION[condition]
        self.by = by
        super().__init__(condition=condition, text=text, etype=etype)


log = logging.getLogger(__name__)