from typing import TYPE_CHECKING

from slixmpp import JID, CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError

if TYPE_CHECKING:
    from .base import BaseGateway


class Search:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

        xmpp["xep_0055"].api.register(self.search_get_form, "search_get_form")
        xmpp["xep_0055"].api.register(self._search_query, "search_query")

        xmpp.plugin["xep_0030"].add_feature("jabber:iq:gateway")
        xmpp.register_handler(
            CoroutineCallback(
                "iq:gateway",
                StanzaPath("iq/gateway"),
                self._handle_gateway_iq,  # type: ignore
            )
        )

    async def search_get_form(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        """
        Prepare the search form using :attr:`.BaseSession.SEARCH_FIELDS`
        """
        user = self.xmpp.store.users.get(ifrom)
        if user is None:
            raise XMPPError(text="Search is only allowed for registered users")

        xmpp = self.xmpp

        reply = iq.reply()
        form = reply["search"]["form"]
        form["title"] = xmpp.SEARCH_TITLE
        form["instructions"] = xmpp.SEARCH_INSTRUCTIONS
        for field in xmpp.SEARCH_FIELDS:
            form.append(field.get_xml())
        return reply

    async def _search_query(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        """
        Handles a search request
        """
        user = self.xmpp.store.users.get(ifrom)
        if user is None:
            raise XMPPError(text="Search is only allowed for registered users")

        result = await self.xmpp.get_session_from_stanza(iq).on_search(
            iq["search"]["form"].get_values()
        )

        if not result:
            raise XMPPError("item-not-found", text="Nothing was found")

        reply = iq.reply()
        form = reply["search"]["form"]
        for field in result.fields:
            form.add_reported(field.var, label=field.label, type=field.type)
        for item in result.items:
            form.add_item(item)
        return reply

    async def _handle_gateway_iq(self, iq: Iq):
        if iq.get_to() != self.xmpp.boundjid.bare:
            raise XMPPError("bad-request", "This can only be used on the component JID")

        user = self.xmpp.store.users.get(iq.get_from())
        if user is None:
            raise XMPPError("not-authorized", "Register to the gateway first")

        if len(self.xmpp.SEARCH_FIELDS) > 1:
            raise XMPPError(
                "feature-not-implemented", "Use jabber search for this gateway"
            )

        field = self.xmpp.SEARCH_FIELDS[0]

        reply = iq.reply()
        if iq["type"] == "get":
            reply["gateway"]["desc"] = self.xmpp.SEARCH_TITLE
            reply["gateway"]["prompt"] = field.label
        elif iq["type"] == "set":
            prompt = iq["gateway"]["prompt"]
            session = self.xmpp.session_cls.from_user(user)
            result = await session.on_search({field.var: prompt})
            if result is None or not result.items:
                raise XMPPError(
                    "item-not-found", "No contact was found with the info you provided."
                )
            if len(result.items) > 1:
                raise XMPPError(
                    "bad-request", "Your search yielded more than one result."
                )
            reply["gateway"]["jid"] = result.items[0]["jid"]

        reply.send()
