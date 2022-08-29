import logging
import typing

from slixmpp import JID, Iq, Message
from slixmpp.plugins.base import BasePlugin
from slixmpp.xmlstream.handler import Callback
from slixmpp.xmlstream.matcher import StanzaPath

from . import stanza

log = logging.getLogger(__name__)


class XEP_0356_OLD(BasePlugin):
    """
    XEP-0356: Privileged Entity

    Events:

    ::

        privileges_advertised  -- Received message/privilege from the server
    """

    name = "xep_0356_old"
    description = "XEP-0356: Privileged Entity (slidge - old namespace)"
    dependencies = {"xep_0297"}
    stanza = stanza

    granted_privileges = {"roster": "none", "message": "none", "presence": "none"}
    server_real_host = None

    def plugin_init(self):
        if not self.xmpp.is_component:
            log.error("XEP 0356 is only available for components")
            return

        stanza.register()

        self.xmpp.register_handler(
            Callback(
                "Privileges_old",
                StanzaPath("message/privilege_old"),
                self._handle_privilege,
            )
        )

    def plugin_end(self):
        self.xmpp.remove_handler("Privileges_old")

    def _handle_privilege(self, msg: Message):
        """
        Called when the XMPP server advertise the component's privileges.

        Stores the privileges in this instance's granted_privileges attribute (a dict)
        and raises the privileges_advertised event
        """
        for perm in msg["privilege_old"]["perms"]:
            self.granted_privileges[perm["access"]] = perm["type"]
        self.server_real_host = msg.get_from()
        log.debug(f"Privileges (old): {self.granted_privileges}")
        self.xmpp.event("privileges_advertised_old")

    def send_privileged_message(self, msg: Message):
        if self.granted_privileges["message"] == "outgoing":
            self._make_privileged_message(msg).send()
        else:
            raise PermissionError(
                "The server hasn't authorized us to send messages on behalf of other users"
            )

    def _make_privileged_message(self, msg: Message):
        stanza = self.xmpp.make_message(
            mto=self.server_real_host, mfrom=self.xmpp.boundjid.bare
        )
        stanza["privilege_old"]["forwarded"].append(msg)
        return stanza

    def _make_get_roster(self, jid: typing.Union[JID, str], **iq_kwargs):
        return self.xmpp.make_iq_get(
            queryxmlns="jabber:iq:roster",
            ifrom=self.xmpp.boundjid.bare,
            ito=jid,
            **iq_kwargs,
        )

    def _make_set_roster(
        self,
        jid: typing.Union[JID, str],
        roster_items: dict,
        **iq_kwargs,
    ):
        iq = self.xmpp.make_iq_set(
            ifrom=self.xmpp.boundjid.bare,
            ito=jid,
            **iq_kwargs,
        )
        iq["roster"]["items"] = roster_items
        return iq

    async def get_roster(self, jid: typing.Union[JID, str], **send_kwargs) -> Iq:
        """
        Return the roster of user on the server the component has privileged access to.

        Raises ValueError if the server did not advertise the corresponding privileges

        :param jid: user we want to fetch the roster from
        """
        if self.granted_privileges["roster"] not in ("get", "both"):
            log.error("The server did not grant us privileges to get rosters")
            raise ValueError
        else:
            return await self._make_get_roster(jid).send(**send_kwargs)

    async def set_roster(
        self, jid: typing.Union[JID, str], roster_items: dict, **send_kwargs
    ) -> Iq:
        """
        Return the roster of user on the server the component has privileged access to.

        Raises ValueError if the server did not advertise the corresponding privileges

        :param jid: user we want to add or modify roster items
        :param roster_items: a dict containing the roster items' JIDs as keys and
            nested dicts containing names, subscriptions and groups.
            Example:
            {
                "friend1@example.com": {
                    "name": "Friend 1",
                    "subscription": "both",
                    "groups": ["group1", "group2"],
                },
                "friend2@example.com": {
                    "name": "Friend 2",
                    "subscription": "from",
                    "groups": ["group3"],
            },
        }
        """
        if self.granted_privileges["roster"] not in ("set", "both"):
            log.error("The server did not grant us privileges to set rosters")
            raise ValueError
        else:
            return await self._make_set_roster(jid, roster_items).send(**send_kwargs)
