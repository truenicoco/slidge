import asyncio
import typing
import logging
from copy import copy
from typing import List, Set, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

from slixmpp import ComponentXMPP, JID
from slixmpp import Presence, Message

from slidge.database import User
from slidge.util import escape


class Occupant:
    def __init__(
        self,
        nick: str,
        role: str = "participant",
        affiliation: str = "member",
        legacy_id: Optional[str] = None,
    ):
        from slidge.gateway import BaseGateway

        self.nick: str = nick
        self.role: str = role
        self.affiliation: str = affiliation
        self.legacy_id: Optional[str] = legacy_id

        self.muc: Optional[LegacyMuc] = None

    @property
    def xmpp(self):
        return self.muc.xmpp

    def make_join_presence(self):
        pfrom = self.muc.jid
        pfrom.resource = self.nick
        presence = self.xmpp.make_presence(pfrom=pfrom)
        presence["muc"]["affiliation"] = self.affiliation
        presence["muc"]["role"] = self.role
        return presence

    def make_message(self, body):
        message = self.xmpp.Message()
        jid = self.muc.jid
        jid.resource = self.nick
        message["type"] = "groupchat"
        message["body"] = body
        message["from"] = jid
        return message


class Occupants:
    def __init__(self, muc):
        from slidge.gateway import BaseGateway

        self.muc: LegacyMuc = muc

        self._occupants: typing.Dict[str, Occupant] = {}

    @property
    def xmpp(self):
        return self.muc.xmpp

    def __iter__(self):
        return iter(self._occupants.values())

    def add(self, occupant: Occupant):
        if occupant.nick in self._occupants:
            log.error("Occupant with this nickname already present, replacing him")
        occupant.muc = self.muc
        self._occupants[occupant.nick] = occupant

    def by_nick(self, nick):
        try:
            return self._occupants[nick]
        except KeyError:
            log.info("Requested a non-listed occupant, creating it")
            occupant = Occupant(nick=nick)
            self.add(occupant)
            return occupant


class LegacyMuc:
    def __init__(self, legacy_id: str):
        from slidge.gateway import BaseGateway

        self.xmpp: Optional[BaseGateway] = None

        self.legacy_id: str = legacy_id

        self.subject: Optional[str] = None
        self.subject_changer: Optional[str] = "slidge"

        self.user_nickname: Optional[str] = None
        self.user: Optional[User] = None
        self.user_resources: List[str] = []
        self.user_affiliation = "member"
        self.user_role = "participant"

        self.occupants: Occupants = Occupants(self)

        self.history = History(legacy_muc=self)
        self.anonymous = True

    def _features(self):
        res = [
            "http://jabber.org/protocol/muc",
            "http://jabber.org/protocol/muc#stable_id",
            "muc_open",
            "muc_hidden",
            "muc_unmoderated",
        ]
        # TODO: add XEP-0128, cf https://xmpp.org/extensions/xep-0045.html#example-10
        if not self.anonymous:
            res.append("muc_nonanonymous")
        return res

    def _extended_info(self):
        form = self.xmpp["xep_0004"].make_form(ftype="result")
        form.add_field(
            var="FORM_TYPE",
            type="hidden",
            value="http://jabber.org/protocol/muc#roominfo",
        )
        form.add_field(
            var="muc#roominfo_subject",
            label="Current Discussion Topic",
            value=self.subject,
        )
        form.add_field(
            var="muc#maxhistoryfetch",
            label="Maximum Number of History Messages Returned by Room",
            value=str(self.history.max_history_fetch),
        )
        return form

    def make_disco(self):
        self.xmpp["xep_0030"].add_identity(
            name=self.subject, category="conference", jid=self.jid, itype="text"
        )
        for f in self._features():
            self.xmpp["xep_0030"].add_feature(f, jid=self.jid)
        self.xmpp["xep_0030"].set_extended_info(
            jid=self.jid, data=self._extended_info()
        )

    @property
    def legacy(self):
        return self.xmpp.legacy_client

    @property
    def jid(self) -> JID:
        return JID(f"{self.escaped_id}@{self.xmpp.boundjid.bare}")

    @property
    def escaped_id(self) -> str:
        return escape(self.legacy_id)

    def make_presence(self, **kwargs):
        return self.xmpp.make_presence(pto=self.user.jid, **kwargs)

    async def user_join(self, presence: Presence, sync_occupants=True):
        full_user_jid = presence["from"]
        requested_nick = presence["to"].resource

        if sync_occupants:
            await self.sync_occupants()

        self.user_resources.append(full_user_jid.resource)

        for occupant in self.occupants:
            presence = occupant.make_join_presence()
            presence["to"] = full_user_jid
            presence.send()

        pfrom = self.jid
        if self.user_nickname is not None:
            pfrom.resource = self.user_nickname
        else:
            pfrom.resource = requested_nick
            self.user_nickname = pfrom.resource

        presence = self.xmpp.make_presence(pto=full_user_jid, pfrom=pfrom)
        presence["muc"]["status_codes"] = {110, 210}
        presence["muc"]["affiliation"] = self.user_affiliation
        presence["muc"]["role"] = "participant"
        presence.send()

        self.history.send(full_user_jid)
        self.send_subject(full_user_jid)

    async def user_leaves(self, presence: Presence):
        full_user_jid = presence["from"]

        pfrom = self.jid
        pfrom.resource = self.user_nickname
        self_presence = self.xmpp.make_presence(pto=full_user_jid, pfrom=pfrom)
        self_presence["muc"]["status_codes"] = {110}
        self_presence["muc"]["affiliation"] = self.user_affiliation
        # https://xmpp.org/extensions/xep-0045.html#example-82
        # Maybe the role shouldn't always be none here?
        self_presence["muc"]["role"] = "none"
        self_presence["muc"]["jid"] = full_user_jid
        self_presence["type"] = "unavailable"
        self_presence.send()

        resource = full_user_jid.resource
        self.user_resources.remove(resource)

        if self.xmpp.config["gateway"].getboolean("really-leave-legacy-muc"):
            if len(self.user_resources) == 0:
                await self.legacy.leave_muc(self)

    def echo_message(self, message: Message):
        new_message = copy(message)
        new_jid = JID(self.jid)
        new_jid.resource = self.user_nickname
        new_message["to"] = new_message["from"]
        new_message["from"] = new_jid
        new_message.send()
        self.history.append(new_message, datetime.now())

    def carbon(self, body: str):
        """
        Called when the jabber user sends a MUC message from the official
        client.
        """
        self.to_user(nick=self.user_nickname, body=body)

    def to_user(self, nick: str, body: str):
        message = self.occupants.by_nick(nick).make_message(body)

        to = copy(self.user.jid)
        for resource in self.user_resources:
            to.resource = resource
            message["to"] = to
            message.send()

        self.history.append(message, datetime.now())

    async def from_user(self, msg: Message):
        await self.legacy.send_muc_message(
            user=self.user, msg=msg, legacy_group_id=self.legacy_id
        )
        self.echo_message(msg)

    async def sync_occupants(self):
        occupants = await self.legacy.muc_occupants(self.user, self.legacy_id)
        for occupant in occupants:
            if isinstance(occupant, Occupant):
                self.occupants.add(occupant)
            else:
                self.occupants.add(Occupant(nick=occupant))

    def send_subject(self, jid: JID):
        from_ = copy(self.jid)
        from_.resource = self.subject_changer

        # for resource in self.user_resources:
        msg = self.xmpp.Message()
        msg["type"] = "groupchat"
        msg["from"] = from_
        msg["to"] = jid
        if self.subject is None:
            msg["subject"] = self.legacy_id
        else:
            msg["subject"] = self.subject
        # TODO: use a delay here as recommended in XEP-0045
        # msg.enable("delay")
        # msg["delay"].set_from(self.jid)
        # msg["delay"].set_stamp("2002-10-13T23:58:37Z")
        msg.send()

    async def shutdown(self):
        for r in self.user_resources:
            to = self.user.jid
            to.resource = r
            pfrom = self.jid
            pfrom.resource = self.user_nickname
            presence = self.xmpp.make_presence(
                pto=to,
                pfrom=pfrom,
                ptype="unavailable",
            )
            presence.enable("muc")
            presence["muc"]["role"] = "none"
            presence["muc"]["affiliation"] = "none"
            presence["muc"]["status_codes"] = {110, 332}
            presence.send()

    def send_invitation(self):
        self.xmpp["xep_0249"].send_invitation(
            jid=self.user.jid,
            roomjid=self.jid,
            reason=f"This is the group {muc.subject}",
        )


class LegacyMucList:
    """
    List of the legacy MUCs a user is part of on the legacy network.
    By default, an XMPP going offline does not trigger leaving the group
    he is part of on the legacy network, and we cannot force the user to
    join the legacy groups on login, just send invitations that can be declined.
    """

    def __init__(self):
        from slidge.gateway import BaseGateway

        self.xmpp: Optional[BaseGateway] = None
        self.user: Optional[User] = None
        self._mucs_by_jid_node: typing.Dict[str, LegacyMuc] = {}
        self._mucs_by_legacy_id: typing.Dict[str, LegacyMuc] = {}

    def __iter__(self):
        return iter(self._mucs_by_jid_node.values())

    @property
    def legacy(self):
        return self.xmpp.legacy_client

    def add(self, muc: LegacyMuc):
        muc.xmpp = self.xmpp
        muc.user = self.user

        self._mucs_by_legacy_id[muc.legacy_id] = muc
        self._mucs_by_jid_node[muc.jid.node] = muc

    def by_legacy_id(self, legacy_id: str) -> LegacyMuc:
        return self._mucs_by_legacy_id[legacy_id]

    def by_jid_node(self, escaped_id: str) -> LegacyMuc:
        return self._mucs_by_jid_node[escaped_id]

    async def sync(self):
        for muc in await self.legacy.muc_list(user=self.user):
            self.add(muc)
            muc.make_disco()

    def send_invitations(self):
        for muc in self:
            muc.send_invitation()

    async def shutdown(self):
        for muc in self:
            await muc.shutdown()


@dataclass
class History:
    legacy_muc: typing.Optional[LegacyMuc] = None
    messages: List[Message] = field(default_factory=list)
    # TODO: actually implement something related to this max_history
    max_history_fetch: int = 200

    def __iter__(self):
        return iter(self.messages)

    def append(self, msg: Message, date: datetime):
        stamp = date.isoformat()[:19] + "Z"
        msg = copy(msg)
        msg.enable("delay")
        msg["delay"].set_from(self.legacy_muc.jid)
        msg["delay"].set_stamp(stamp)
        self.messages.append(msg)

    def send(self, jid: JID):
        for msg in self:
            msg = copy(msg)
            msg["to"] = jid
            msg.send()


log = logging.getLogger(__name__)
