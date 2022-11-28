import logging
from asyncio import iscoroutinefunction
from functools import wraps
from typing import TYPE_CHECKING, Any

from slixmpp import JID, Iq
from slixmpp.exceptions import XMPPError

from ..util.db import user_store
from . import config

if TYPE_CHECKING:
    from .gateway import BaseGateway
    from .session import BaseSession


def admin_only(func):
    # fmt: off
    if iscoroutinefunction(func):
        @wraps(func)
        async def wrapped(self, iq: Iq, session: dict[str, Any]):
            if iq.get_from().bare not in config.ADMINS:
                raise XMPPError("not-authorized")
            return await func(self, iq, session)
    else:
        @wraps(func)
        def wrapped(self, iq: Iq, session: dict[str, Any]):
            if iq.get_from().bare not in config.ADMINS:
                raise XMPPError("not-authorized")
            return func(self, iq, session)
    # fmt: on
    return wrapped


class AdhocProvider:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        self.forms = xmpp.plugin["xep_0004"]
        xmpp.add_event_handler("session_start", self.session_start)

    async def session_start(self, _event):
        # weird slix behaviour: if we add commands *before* session_start,
        # the items are reset (on session_bind)
        adhoc = self.xmpp.plugin["xep_0050"]
        adhoc.add_command(
            node="info", name="List registered users", handler=self._handle_info
        )
        adhoc.add_command(
            node="delete_user", name="Delete a user", handler=self._handle_user_delete
        )
        adhoc.add_command(
            node="search", name="Search for contacts", handler=self._handle_search
        )

    @admin_only
    def _handle_info(self, _iq: Iq, session: dict[str, Any]):
        """
        List registered users for admins
        """
        form = self.xmpp["xep_0004"].make_form("result", "Component info")
        form.add_field(
            ftype="jid-multi",
            label="Users",
            value=[u.bare_jid for u in user_store.get_all()],
        )

        session["payload"] = form
        session["has_next"] = False

        return session

    @admin_only
    def _handle_user_delete(self, _iq: Iq, adhoc_session: dict[str, Any]):
        form = self.xmpp["xep_0004"].make_form(
            title="Delete user",
            instructions="Enter the bare JID(s) of the user(s) you want to delete",
        )
        form.add_field("user_jid", ftype="jid-single", label="User JID")

        adhoc_session["payload"] = form
        adhoc_session["has_next"] = True
        adhoc_session["next"] = self._handle_user_delete2

        return adhoc_session

    async def _handle_user_delete2(self, form, adhoc_session: dict[str, Any]):
        form_values = form.get_values()
        try:
            user_jid = JID(form_values.get("user_jid"))
        except ValueError:
            raise XMPPError("bad-request", text="This JID is invalid")

        user = user_store.get_by_jid(user_jid)
        if user is None:
            raise XMPPError("item-not-found", text=f"There is no user '{user_jid}'")

        log.debug("Admin requested unregister of %s", user_jid)

        await self.xmpp.session_cls.kill_by_jid(user_jid)
        user_store.remove_by_jid(user_jid)

        adhoc_session["notes"] = [("info", "Success!")]
        adhoc_session["has_next"] = False

        return adhoc_session

    async def _handle_search(self, iq: Iq, adhoc_session: dict[str, Any]):
        """
        Jabber search, but as an adhoc command (search form)
        """
        user = user_store.get_by_jid(iq.get_from())
        if user is None:
            raise XMPPError(
                "not-authorized", text="Search is only allowed for registered users"
            )

        session: "BaseSession" = self.xmpp.get_session_from_stanza(iq)  # type:ignore

        reply = await self.xmpp.search_get_form(None, None, ifrom=iq.get_from(), iq=iq)
        adhoc_session["payload"] = reply["search"]["form"]
        adhoc_session["next"] = self._handle_search2
        adhoc_session["has_next"] = True
        adhoc_session["session"] = session

        return adhoc_session

    async def _handle_search2(self, form, adhoc_session: dict[str, Any]):
        """
        Jabber search, but as an adhoc command (results)
        """

        search_results = await adhoc_session["session"].search(form.get_values())

        form = self.xmpp.plugin["xep_0004"].make_form(
            "result", "Contact search results"
        )
        if search_results is None:
            raise XMPPError("item-not-found", text="No contact was found")

        for field in search_results.fields:
            form.add_reported(field.var, label=field.label, type=field.type)
        for item in search_results.items:
            form.add_item(item)

        adhoc_session["next"] = None
        adhoc_session["has_next"] = False
        adhoc_session["payload"] = form

        return adhoc_session


log = logging.getLogger(__name__)
