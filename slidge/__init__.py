from .db import user_store, GatewayUser
from .gateway import BaseGateway
from .legacy.base import LegacyContact, BaseLegacyClient

import slixmpp.plugins.xep_0333


# patch for https://lab.louiz.org/poezio/slixmpp/-/issues/3469
def send_marker(self, mto, id: str, marker: str, thread=None, *, mfrom=None):
    if marker not in ("displayed", "received", "acknowledged"):
        raise ValueError("Invalid marker: %s" % marker)
    msg = self.xmpp.make_message(mto=mto, mfrom=mfrom)
    if thread:
        msg["thread"] = thread
    msg[marker]["id"] = id
    msg.send()


slixmpp.plugins.xep_0333.XEP_0333.send_marker = send_marker
