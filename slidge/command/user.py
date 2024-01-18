# Commands available to users
from typing import TYPE_CHECKING, Any, Optional, Union, cast

from slixmpp import JID  # type:ignore[attr-defined]
from slixmpp.exceptions import XMPPError

from ..util.types import AnyBaseSession, LegacyGroupIdType
from .base import (
    Command,
    CommandAccess,
    Confirmation,
    Form,
    FormField,
    FormValues,
    SearchResult,
    TableResult,
)
from .categories import CONTACTS, GROUPS

if TYPE_CHECKING:
    pass


class Search(Command):
    NAME = "🔎 Search for contacts"
    HELP = "Search for contacts via this gateway"
    NODE = "search"
    CHAT_COMMAND = "find"
    ACCESS = CommandAccess.USER_LOGGED
    CATEGORY = CONTACTS

    async def run(
        self, session: Optional[AnyBaseSession], _ifrom: JID, *args: str
    ) -> Union[Form, SearchResult, None]:
        if args:
            assert session is not None
            return await session.on_search(
                {self.xmpp.SEARCH_FIELDS[0].var: " ".join(args)}
            )
        return Form(
            title=self.xmpp.SEARCH_TITLE,
            instructions=self.xmpp.SEARCH_INSTRUCTIONS,
            fields=self.xmpp.SEARCH_FIELDS,
            handler=self.search,
        )

    @staticmethod
    async def search(
        form_values: FormValues, session: Optional[AnyBaseSession], _ifrom: JID
    ) -> SearchResult:
        assert session is not None
        results = await session.on_search(form_values)  # type: ignore
        if results is None:
            raise XMPPError("item-not-found", "No contact was found")

        return results


class SyncContacts(Command):
    NAME = "🔄 Sync XMPP roster"
    HELP = (
        "Synchronize your XMPP roster with your legacy contacts. "
        "Slidge will only add/remove/modify contacts in its dedicated roster group"
    )
    NODE = CHAT_COMMAND = "sync-contacts"
    ACCESS = CommandAccess.USER_LOGGED
    CATEGORY = CONTACTS

    async def run(self, session: Optional[AnyBaseSession], _ifrom, *_) -> Confirmation:
        return Confirmation(
            prompt="Are you sure you want to sync your roster?",
            success=None,
            handler=self.sync,
        )

    async def sync(self, session: Optional[AnyBaseSession], _ifrom: JID) -> str:
        if session is None:
            raise RuntimeError
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
                        await contact.add_to_roster(force=True)
                        updated += 1

        # we popped before so this only acts on slidge contacts not in the xmpp roster
        for contact in contacts.values():
            added += 1
            await contact.add_to_roster()

        return f"{added} added, {removed} removed, {updated} updated"


class ListContacts(Command):
    NAME = HELP = "👤 List your legacy contacts"
    NODE = CHAT_COMMAND = "contacts"
    ACCESS = CommandAccess.USER_LOGGED
    CATEGORY = CONTACTS

    async def run(
        self, session: Optional[AnyBaseSession], _ifrom: JID, *_
    ) -> TableResult:
        assert session is not None
        await session.contacts.fill()
        contacts = sorted(
            session.contacts, key=lambda c: c.name.casefold() if c.name else ""
        )
        return TableResult(
            description="Your buddies",
            fields=[FormField("name"), FormField("jid", type="jid-single")],
            items=[{"name": c.name, "jid": c.jid.bare} for c in contacts],
        )


class ListGroups(Command):
    NAME = HELP = "👥 List your legacy groups"
    NODE = CHAT_COMMAND = "groups"
    ACCESS = CommandAccess.USER_LOGGED
    CATEGORY = GROUPS

    async def run(self, session, _ifrom, *_):
        assert session is not None
        await session.bookmarks.fill()
        groups = sorted(session.bookmarks, key=lambda g: g.DISCO_NAME.casefold())
        return TableResult(
            description="Your groups",
            fields=[FormField("name"), FormField("jid", type="jid-single")],
            items=[{"name": g.name, "jid": g.jid.bare} for g in groups],
            jids_are_mucs=True,
        )


class Login(Command):
    NAME = "🔐 Re-login to the legacy network"
    HELP = "Login to the legacy service"
    NODE = CHAT_COMMAND = "re-login"

    ACCESS = CommandAccess.USER_NON_LOGGED

    async def run(self, session: Optional[AnyBaseSession], _ifrom, *_):
        assert session is not None
        try:
            msg = await session.login()
        except Exception as e:
            session.send_gateway_status(f"Re-login failed: {e}", show="dnd")
            raise XMPPError(
                "internal-server-error", etype="wait", text=f"Could not login: {e}"
            )
        session.logged = True
        session.send_gateway_status(msg or "Re-connected", show="chat")
        session.send_gateway_message(msg or "Re-connected")
        return msg


class CreateGroup(Command):
    NAME = "🆕 New legacy group"
    HELP = "Create a group on the legacy service"
    NODE = CHAT_COMMAND = "create-group"
    CATEGORY = GROUPS

    ACCESS = CommandAccess.USER_LOGGED

    async def run(self, session: Optional[AnyBaseSession], _ifrom, *_):
        assert session is not None
        contacts = session.contacts.known_contacts(only_friends=True)
        return Form(
            title="Create a new group",
            instructions="Pick contacts that should be part of this new group",
            fields=[
                FormField(var="group_name", label="Name of the group", required=True),
                FormField(
                    var="contacts",
                    label="Contacts to add to the new group",
                    type="list-multi",
                    options=[
                        {"value": str(contact.jid), "label": contact.name}
                        for contact in sorted(contacts.values(), key=lambda c: c.name)
                    ],
                    required=False,
                ),
            ],
            handler=self.finish,
        )

    @staticmethod
    async def finish(form_values: FormValues, session: Optional[AnyBaseSession], *_):
        assert session is not None
        legacy_id: LegacyGroupIdType = await session.on_create_group(  # type:ignore
            cast(str, form_values["group_name"]),
            [
                await session.contacts.by_jid(JID(j))
                for j in form_values.get("contacts", [])  # type:ignore
            ],
        )
        muc = await session.bookmarks.by_legacy_id(legacy_id)
        return TableResult(
            description=f"Your new group: xmpp:{muc.jid}?join",
            fields=[FormField("name"), FormField("jid", type="jid-single")],
            items=[{"name": muc.name, "jid": muc.jid}],
            jids_are_mucs=True,
        )


class Unregister(Command):
    NAME = "❌ Unregister from the gateway"
    HELP = "Unregister from the gateway"
    NODE = CHAT_COMMAND = "unregister"
    ACCESS = CommandAccess.USER

    async def run(
        self, session: Optional[AnyBaseSession], _ifrom: JID, *_: Any
    ) -> Confirmation:
        return Confirmation(
            prompt=f"Are you sure you want to unregister from '{self.xmpp.boundjid}'?",
            success=f"You are not registered to '{self.xmpp.boundjid}' anymore.",
            handler=self.unregister,
        )

    async def unregister(self, session: Optional[AnyBaseSession], _ifrom: JID) -> str:
        assert session is not None
        await self.xmpp.unregister_user(session.user)
        return "OK"
