from typing import Literal, Optional

from slixmpp.exceptions import XMPPError as Base
from slixmpp.stanza.error import Error

# workaround for https://lab.louiz.org/poezio/slixmpp/-/issues/3474
Error.namespace = "jabber:component:accept"

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
    def __init__(
        self,
        condition: Conditions = "undefined-condition",
        text="",
        etype: Optional[ErrorTypes] = None,
    ):
        if etype is None:
            etype = TYPE_BY_CONDITION[condition]
        super().__init__(condition=condition, text=text, etype=etype)
