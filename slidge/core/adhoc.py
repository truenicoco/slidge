import logging
from asyncio import iscoroutinefunction
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Optional

from slixmpp import JID, Iq
from slixmpp.exceptions import XMPPError
from slixmpp.types import JidStr

from ..util.db import user_store
from ..util.xep_0030.stanza.items import DiscoItems
from . import config

if TYPE_CHECKING:
    from .gateway import BaseGateway
    from .session import BaseSession


@dataclass
class RestrictedItem:
    bare_jid: str
    node: str
    name: str

    def __hash__(self):
        return hash(self.bare_jid + self.node + self.name)


def restrict(func, condition):
    # fmt: off
    if iscoroutinefunction(func):
        @wraps(func)
        async def wrapped(iq: Iq, session: dict[str, Any]):
            if not condition(iq.get_from().bare):
                raise XMPPError("not-authorized")
            return await func(iq, session)
    else:
        @wraps(func)
        def wrapped(iq: Iq, session: dict[str, Any]):
            log.debug("WRAPPED: %s", locals())
            if not condition(iq.get_from().bare):
                raise XMPPError("not-authorized")
            return func(iq, session)
    # fmt: on
    return wrapped


class AdhocProvider:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

        self._only_admin = set[RestrictedItem]()
        self._only_users = set[RestrictedItem]()
        self._only_nonusers = set[RestrictedItem]()

        xmpp.plugin["xep_0030"].set_node_handler(
            "get_items",
            jid=xmpp.boundjid,
            node=self.xmpp.plugin["xep_0050"].stanza.Command.namespace,
            handler=self.get_items,
        )

        self.add_commands()

    def add_commands(self):
        self.add_command(
            node="info",
            name="List registered users",
            handler=self._handle_info,
            only_admin=True,
            only_users=False,
        )
        self.add_command(
            node="delete_user",
            name="Delete a user",
            handler=self._handle_user_delete,
            only_admin=True,
            only_users=False,
        )
        self.add_command(
            node="search",
            name="Search for contacts",
            handler=self._handle_search,
            only_users=True,
        )

    async def get_items(self, jid, node, iq):
        all_items = self.xmpp.plugin["xep_0030"].static.get_items(jid, node, None, None)
        log.debug("Static items: %r", all_items)
        if not all_items:
            return all_items

        ifrom = iq.get_from()
        admin = is_admin(ifrom)
        user = is_user(ifrom)

        filtered_items = DiscoItems()
        filtered_items["node"] = self.xmpp.plugin["xep_0050"].stanza.Command.namespace
        for item in all_items:
            restricted_item = RestrictedItem(
                bare_jid=jid.bare, node=item["node"], name=item["name"]
            )
            if restricted_item in self._only_admin and not admin:
                continue
            elif restricted_item in self._only_users and not user:
                continue
            elif restricted_item is self._only_nonusers and user:
                continue

            filtered_items.append(item)

        return filtered_items

    def add_command(
        self,
        node: str,
        name: str,
        handler: Callable,
        jid: Optional[JID] = None,
        only_admin=False,
        only_users=False,
        only_nonusers=False,
    ):
        if jid is None:
            jid = self.xmpp.boundjid
        elif not isinstance(jid, JID):
            jid = JID(jid)
        item = RestrictedItem(bare_jid=jid.bare, node=node, name=name)
        if only_admin:
            self._only_admin.add(item)
        if only_users:
            self._only_users.add(item)
        if only_nonusers:
            self._only_nonusers.add(item)

        if only_users:
            handler = restrict(handler, is_user)
        elif only_admin:
            handler = restrict(handler, is_admin)
        elif only_nonusers:
            handler = restrict(handler, is_not_user)

        self.xmpp.plugin["xep_0050"].add_command(
            jid=jid, node=node, name=name, handler=handler
        )

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


def is_admin(jid: JidStr):
    return JID(jid).bare in config.ADMINS


def is_user(jid: JidStr):
    return user_store.get_by_jid(JID(jid))


def is_not_user(jid: JidStr):
    return not is_user(jid)


log = logging.getLogger(__name__)
