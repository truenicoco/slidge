import asyncio
import logging
import tempfile
from asyncio import iscoroutinefunction
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import qrcode
from slixmpp import JID, Iq
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0004 import Form, FormField
from slixmpp.types import JidStr

from ..util.db import GatewayUser, user_store
from ..util.xep_0030.stanza.items import DiscoItems
from . import config

if TYPE_CHECKING:
    from .gateway import BaseGateway
    from .session import BaseSession


class RegistrationType(int, Enum):
    SINGLE_STEP_FORM = 0
    QRCODE = 10
    TWO_FACTOR_CODE = 20


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


class TwoFactorNotRequired(Exception):
    pass


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
        self.add_command(
            node="jabber:iq:register",
            name="Register to the gateway",
            handler=self._handle_register,
            only_nonusers=True,
        )
        self.add_command(
            node="unregister",
            name="Unregister to the gateway",
            handler=self._handle_unregister,
            only_users=True,
        )
        self.add_command(
            node="sync-contacts",
            name="Sync XMPP roster",
            handler=self._handle_contact_sync,
            only_users=True,
        )

    async def get_items(self, jid: JID, node: str, iq: Iq):
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
            elif restricted_item in self._only_nonusers and user:
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
        form.add_reported("jid", label="JID", type="jid-single")
        form.add_reported("joined", label="Join date", type="text")
        for u in user_store.get_all():
            d = u.registration_date
            if d is None:
                joined = ""
            else:
                joined = d.isoformat(timespec="seconds")
            form.add_item({"jid": u.bare_jid, "joined": joined})

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

    async def _handle_register(self, iq: Iq, adhoc_session: dict[str, Any]):
        reg_iq = await self.xmpp.make_registration_form(None, None, None, iq)
        form = reg_iq["register"]["form"]
        adhoc_session["payload"] = form
        adhoc_session["next"] = self._handle_register2
        adhoc_session["has_next"] = True
        adhoc_session["session"] = adhoc_session

        return adhoc_session

    async def _handle_register2(self, form: Form, adhoc_session: dict[str, Any]):

        form_values = form.get_values()
        two_fa_needed = True
        try:
            await self.xmpp.user_prevalidate(adhoc_session["from"], form_values)
        except ValueError as e:
            raise XMPPError("bad-request", str(e))
        except TwoFactorNotRequired:
            if self.xmpp.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE:
                two_fa_needed = False
            else:
                raise

        adhoc_session["user"] = user = GatewayUser(
            bare_jid=adhoc_session["from"].bare,
            registration_form=form_values,
            registration_date=datetime.now(),
        )
        adhoc_session["registration_form"] = form_values

        if self.xmpp.REGISTRATION_TYPE == RegistrationType.SINGLE_STEP_FORM or (
            self.xmpp.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE
            and not two_fa_needed
        ):
            adhoc_session["payload"] = None
            adhoc_session["next"] = None
            adhoc_session["notes"] = [("info", "Success!")]
            adhoc_session["has_next"] = False
            adhoc_session["completed"] = True
            user.commit()
            self.xmpp.event("user_register", Iq(sfrom=adhoc_session["from"]))

        elif self.xmpp.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE:
            form = self.xmpp["xep_0004"].make_form(
                title=self.xmpp.REGISTRATION_2FA_TITLE,
                instructions=self.xmpp.REGISTRATION_2FA_INSTRUCTIONS,
            )
            form.add_field("code", ftype="text-single", label="Code", required=True)
            adhoc_session["payload"] = form
            adhoc_session["next"] = self._handle_two_factor_code
            adhoc_session["has_next"] = True
            adhoc_session["completed"] = False

        elif self.xmpp.REGISTRATION_TYPE == RegistrationType.QRCODE:
            self.xmpp.qr_pending_registrations[  # type:ignore
                user.bare_jid
            ] = self.xmpp.loop.create_future()
            qr_text = await self.xmpp.get_qr_text(user)
            qr = qrcode.make(qr_text)
            with tempfile.NamedTemporaryFile(suffix=".png") as f:
                qr.save(f.name)
                img_url = await self.xmpp.plugin["xep_0363"].upload_file(
                    filename=Path(f.name), ifrom=config.UPLOAD_REQUESTER
                )

            msg = self.xmpp.make_message(mto=user.bare_jid)
            msg.set_from(self.xmpp.boundjid.bare)
            msg["oob"]["url"] = img_url
            msg["body"] = img_url
            msg.send()

            msg = self.xmpp.make_message(mto=user.bare_jid)
            msg.set_from(self.xmpp.boundjid.bare)
            msg["body"] = qr_text
            msg.send()

            form = self.xmpp["xep_0004"].make_form(
                title="Flash this",
                instructions="Flash this QR in the appropriate place",
            )
            img = FormField()
            img["media"]["height"] = "200"
            img["media"]["width"] = "200"
            img["media"]["alt"] = "The thing to flash"
            img["media"].add_uri(img_url, itype="image/png")
            form.append(img)
            form.add_field(ftype="fixed", value=qr_text, label="Content of the QR")
            form.add_field(ftype="fixed", value=img_url, label="QR image")

            adhoc_session["payload"] = form
            adhoc_session["next"] = self._handle_qr
            adhoc_session["has_next"] = True
            adhoc_session["completed"] = False

        return adhoc_session

    async def _handle_two_factor_code(
        self, form_code: Form, adhoc_session: dict[str, Any]
    ):
        log.debug("form %s", form_code)
        log.debug("session %s", adhoc_session)
        code: str = form_code.get_values().get("code")
        if code is None:
            raise XMPPError("bad-request", text="Please fill the code field")

        user = adhoc_session["user"]
        await self.xmpp.validate_two_factor_code(user, code)
        user.commit()
        self.xmpp.event("user_register", Iq(sfrom=adhoc_session["from"]))

        adhoc_session["notes"] = [("info", "Looks like we're all good")]
        adhoc_session["payload"] = None
        adhoc_session["next"] = None
        adhoc_session["has_next"] = False
        adhoc_session["completed"] = True

        return adhoc_session

    async def _handle_qr(self, _args, adhoc_session: dict[str, Any]):
        log.debug("handle QR: %s", _args)
        user = adhoc_session["user"]
        try:
            await asyncio.wait_for(
                self.xmpp.qr_pending_registrations[user.bare_jid],  # type:ignore
                config.QR_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise XMPPError(
                "remote-server-timeout",
                "It does not seem that the QR code was correctly used.",
            )
        user.commit()
        self.xmpp.event("user_register", Iq(sfrom=adhoc_session["from"]))

        adhoc_session["notes"] = [("info", "Looks like we're all good")]
        adhoc_session["payload"] = None
        adhoc_session["next"] = None
        adhoc_session["has_next"] = False
        adhoc_session["completed"] = True

        return adhoc_session

    async def _handle_unregister(self, iq: Iq, adhoc_session: dict[str, Any]):
        await self.xmpp.plugin["xep_0077"].api["user_remove"](None, None, iq["from"])
        await self.xmpp.session_cls.kill_by_jid(iq.get_from())
        adhoc_session["notes"] = [("info", "Bye bye!")]
        adhoc_session["has_next"] = False
        adhoc_session["completed"] = True

        return adhoc_session

    async def _handle_contact_sync(self, iq: Iq, adhoc_session: dict[str, Any]):
        session: "BaseSession" = self.xmpp.get_session_from_stanza(iq)  # type:ignore

        roster_iq = await self.xmpp["xep_0356"].get_roster(session.user.bare_jid)

        contacts = session.contacts.known_contacts()

        added = 0
        removed = 0
        updated = 0
        for item in roster_iq["roster"]:
            groups = set(item["groups"])
            if self.xmpp.ROSTER_GROUP in groups:
                contact = contacts.pop(item["jid"], None)
                if contact is None:
                    if len(groups) == 1:
                        await self.xmpp["xep_0356"].set_roster(
                            session.user.jid, {item["jid"]: {"subscription": "remove"}}
                        )
                        removed += 1
                    else:
                        groups.remove(self.xmpp.ROSTER_GROUP)
                        await self.xmpp["xep_0356"].set_roster(
                            session.user.jid,
                            {
                                item["jid"]: {
                                    "subscription": item["subscription"],
                                    "name": item["name"],
                                    "groups": groups,
                                }
                            },
                        )
                        updated += 1
                else:
                    if contact.name != item["name"]:
                        log.debug("%s vs %s", contact.name, item["name"])
                        await contact.add_to_roster()
                        updated += 1

        # we popped before so this only acts on slidge contacts not in the xmpp roster
        for contact in contacts.values():
            added += 1
            await contact.add_to_roster()

        adhoc_session["notes"] = [
            ("info", f"{added} added, {removed} removed, {updated} updated")
        ]
        adhoc_session["has_next"] = False
        adhoc_session["completed"] = True

        return adhoc_session


def is_admin(jid: JidStr):
    return JID(jid).bare in config.ADMINS


def is_user(jid: JidStr):
    return user_store.get_by_jid(JID(jid))


def is_not_user(jid: JidStr):
    return not is_user(jid)


log = logging.getLogger(__name__)
