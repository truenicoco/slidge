# This module contains patches for slixmpp; some have pending requests upstream
# and should be removed on the next slixmpp release.
import logging
from collections import defaultdict

import slixmpp.plugins
from slixmpp import Message
from slixmpp.plugins.xep_0050 import XEP_0050, Command
from slixmpp.plugins.xep_0356.privilege import _VALID_ACCESSES, XEP_0356
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

# ruff: noqa: F401


# TODO: Remove me once https://codeberg.org/poezio/slixmpp/pulls/3541 makes it
#       to a slixmpp release
def _handle_privilege(self, msg: StanzaBase):
    """
    Called when the XMPP server advertise the component's privileges.

    Stores the privileges in this instance's granted_privileges attribute (a dict)
    and raises the privileges_advertised event
    """
    permissions = self.granted_privileges[msg.get_from()]
    for perm in msg["privilege"]["perms"]:
        access = perm["access"]
        if access == "iq":
            if not perm.get_plugin("namespace", check=True):
                permissions.iq = defaultdict(lambda: perm["type"])
            else:
                for ns in perm["namespaces"]:
                    permissions.iq[ns["ns"]] = ns["type"]
        elif access in _VALID_ACCESSES:
            setattr(permissions, access, perm["type"])
        else:
            log.warning("Received an invalid privileged access: %s", access)
    log.debug("Privileges: %s", self.granted_privileges)
    self.xmpp.event("privileges_advertised")


XEP_0356._handle_privilege = _handle_privilege


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
log = logging.getLogger(__name__)
