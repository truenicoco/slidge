import hashlib
import io
import logging
from copy import copy
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Optional
from uuid import uuid4

from PIL import Image
from slixmpp import JID, Iq, Message, Presence
from slixmpp.plugins.xep_0082 import parse as str_to_datetime
from slixmpp.xmlstream import ET

from ...util import ABCSubclassableOnceAtMost
from ...util.error import XMPPError
from ...util.types import (
    AvatarType,
    LegacyGroupIdType,
    LegacyMessageType,
    LegacyParticipantType,
)
from .. import config
from ..mixins.base import ReactionRecipientMixin, ThreadRecipientMixin
from ..mixins.disco import ChatterDiscoMixin
from ..mixins.lock import NamedLockMixin
from .archive import MessageArchive

if TYPE_CHECKING:
    from ..contact import LegacyContact
    from ..gateway import BaseGateway
    from ..session import BaseSession


class MucType(int, Enum):
    GROUP = 0
    CHANNEL = 1


ADMIN_NS = "http://jabber.org/protocol/muc#admin"


class LegacyMUC(
    Generic[LegacyGroupIdType, LegacyMessageType, LegacyParticipantType],
    NamedLockMixin,
    ChatterDiscoMixin,
    ReactionRecipientMixin,
    ThreadRecipientMixin,
    metaclass=ABCSubclassableOnceAtMost,
):
    subject_date: Optional[datetime] = None
    n_participants: Optional[int] = None
    max_history_fetch = 100
    description = ""

    type = MucType.CHANNEL
    is_group = True

    DISCO_TYPE = "text"
    DISCO_CATEGORY = "conference"
    DISCO_NAME = "unnamed-room"

    STABLE_ARCHIVE = False
    """
    Because legacy events like reactions, editions, etc. don't all map to a stanza
    with a proper legacy ID, slidge usually cannot guarantee the stability of the archive
    across restarts.
    
    Set this to True if you know what you're doing, but realistically, this can't
    be set to True until archive is permanently stored on disk by slidge.
    
    This is just a flag on archive responses that most clients ignore anyway.
    """

    def __init__(self, session: "BaseSession", legacy_id: LegacyGroupIdType, jid: JID):
        super().__init__()
        from .participant import LegacyParticipant

        self.session = session
        self.xmpp: "BaseGateway" = session.xmpp
        self.user = session.user
        self.log = logging.getLogger(f"{self.user.bare_jid}:muc:{self.jid}")

        self.legacy_id = legacy_id
        self.jid = jid

        self.user_resources = set[str]()

        self.Participant = LegacyParticipant.get_self_or_unique_subclass()

        self.xmpp.add_event_handler(
            "presence_unavailable", self._on_presence_unavailable
        )

        self._subject = ""
        self.subject_setter = "unknown"

        self.archive: MessageArchive = MessageArchive()
        self.user_nick = self.user.jid.node

        self._participants_by_nicknames = dict[str, LegacyParticipantType]()
        self._participants_by_contacts = dict["LegacyContact", LegacyParticipantType]()
        self._avatar: Optional[AvatarType] = None
        self._avatar_hash: Optional[str] = None

        self.__participants_filled = False
        self.__history_filled = False

    def __repr__(self):
        return f"<MUC '{self.legacy_id}'/'{self.jid}'>"

    async def __fill_participants(self):
        async with self.lock("fill participants"):
            if self.__participants_filled:
                return
            self.__participants_filled = True
            await self.fill_participants()

    async def __fill_history(self):
        async with self.lock("fill history"):
            if self.__history_filled:
                log.debug("History has already been fetched %s", self)
                return
            log.info("Fetching history for %s", self)
            oldest = self.archive.get_oldest_message()
            if oldest:
                await self.backfill(oldest.id, oldest.when)
            else:
                await self.backfill()
            self.__history_filled = True

    async def handle_admin(self, iq: Iq):
        affiliation = iq["mucadmin_query"]["item"]["affiliation"]

        if not affiliation:
            raise XMPPError("bad-request")

        reply = iq.reply()
        reply.enable("mucadmin_query")
        for participant in await self.get_participants():
            if not participant.affiliation == affiliation:
                continue
            reply["mucadmin_query"].append(participant.mucadmin_item())
        if affiliation == "member":
            participant = await self.get_user_participant()
            reply["mucadmin_query"].append(participant.mucadmin_item())
        reply.send()

    @property
    def avatar(self):
        return self._avatar

    @avatar.setter
    def avatar(self, a: Optional[AvatarType]):
        if a != self._avatar:
            self.xmpp.loop.create_task(self.__set_avatar(a))

    async def __set_avatar(self, a: Optional[AvatarType]):
        if isinstance(a, str):
            async with self.xmpp.http.get(a) as r:  # type:ignore
                b = await r.read()
        elif isinstance(a, bytes):
            b = a
        elif isinstance(a, Path):
            b = a.read_bytes()
        elif a is None:
            self._avatar = None
            self._avatar_hash = None
            self._send_room_presence()
            return
        else:
            raise TypeError("Avatar must be bytes, a Path or a str (URL)", a)

        img = Image.open(io.BytesIO(b))
        if (size := config.AVATAR_SIZE) and any(x > size for x in img.size):
            img.thumbnail((size, size))
            log.debug("Resampled image to %s", img.size)
            with io.BytesIO() as f:
                img.save(f, format="PNG")
                b = f.getvalue()

        vcard = self.xmpp.plugin["xep_0054"].make_vcard()
        vcard["PHOTO"]["BINVAL"] = b
        vcard["PHOTO"]["TYPE"] = "image/" + img.format.lower()
        await self.xmpp.plugin["xep_0054"].api["set_vcard"](self.jid, None, None, vcard)
        self._avatar = a
        self._avatar_hash = hashlib.sha1(b).hexdigest()
        self._send_room_presence()

    @property
    def name(self):
        return self.DISCO_NAME

    @name.setter
    def name(self, n: str):
        self.DISCO_NAME = n

    def _on_presence_unavailable(self, p: Presence):
        pto = p.get_to()
        if pto.bare != self.jid.bare:
            return

        pfrom = p.get_from()
        if pfrom.bare != self.user.bare_jid:
            return
        if (resource := pfrom.resource) in (resources := self.user_resources):
            if pto.resource != self.user_nick:
                self.log.debug(
                    "Received 'leave group' request but with wrong nickname. %s", p
                )
            resources.remove(resource)
        else:
            self.log.debug(
                "Received 'leave group' request but resource was not listed. %s", p
            )

    async def backfill(
        self,
        oldest_message_id: Optional[LegacyMessageType] = None,
        oldest_message_date: Optional[datetime] = None,
    ):
        """
        Override this if the legacy network provide server-side archive.
        In it, send history messages using ``self.get_participant().send*``,
        with the ``archive_only=True`` kwarg.

        You only need to fetch messages older than ``oldest_message_id``.

        :param oldest_message_id: The oldest message ID already present in the archive
        :param oldest_message_date: The oldest message date already present in the archive
        """
        return

    async def update_info(self):
        """
        Fetch information about this group from the legacy network

        This is awaited on MUC instantiation, and should be overridden to
        update the attributes of the group chat, like title, subject, number
        of participants etc.
        """
        pass

    @property
    def subject(self):
        return self._subject

    @subject.setter
    def subject(self, s: str):
        if s != self._subject:
            self.update_subject(s)
        self._subject = s

    def update_subject(self, subject: Optional[str] = None):
        self._subject = subject or ""
        for r in self.user_resources:
            to = copy(self.user.jid)
            to.resource = r
            self._make_subject_message(to).send()

    def features(self):
        features = [
            "http://jabber.org/protocol/muc",
            "http://jabber.org/protocol/muc#stable_id",
            "http://jabber.org/protocol/muc#self-ping-optimization",
            "urn:xmpp:mam:2",
            "urn:xmpp:mam:2#extended",
            "urn:xmpp:sid:0",
            "muc_persistent",
            "vcard-temp",
            "urn:xmpp:ping",
        ]
        if self.type == MucType.GROUP:
            features.extend(["muc_membersonly", "muc_nonanonymous", "muc_hidden"])
        elif self.type == MucType.CHANNEL:
            features.extend(["muc_open", "muc_semianonymous", "muc_public"])
        return features

    def extended_features(self):
        is_group = self.type == MucType.GROUP

        form = self.xmpp.plugin["xep_0004"].make_form(ftype="result")

        form.add_field(
            "FORM_TYPE", "hidden", value="http://jabber.org/protocol/muc#roominfo"
        )
        form.add_field("muc#roomconfig_persistentroom", "boolean", value=True)
        form.add_field("muc#roomconfig_changesubject", "boolean", value=False)
        form.add_field("muc#maxhistoryfetch", value=str(self.max_history_fetch))
        form.add_field("muc#roominfo_subjectmod", "boolean", value=False)

        if (n := self.n_participants) is not None:
            form.add_field("muc#roominfo_occupants", value=str(n))

        if d := self.description:
            form.add_field("muc#roominfo_description", value=d)

        if s := self.subject:
            form.add_field("muc#roominfo_subject", value=s)

        form.add_field("muc#roomconfig_membersonly", "boolean", value=is_group)
        form.add_field("muc#roomconfig_whois", "boolean", value=is_group)
        form.add_field("muc#roomconfig_publicroom", "boolean", value=not is_group)
        form.add_field("muc#roomconfig_allowpm", "boolean", value=False)

        return form

    def _make_subject_message(self, user_full_jid: JID):
        subject_setter = copy(self.jid)
        log.debug("subject setter: %s", self.subject_setter)
        subject_setter.resource = self.subject_setter
        msg = self.xmpp.make_message(
            mto=user_full_jid,
            mfrom=subject_setter,
            mtype="groupchat",
        )
        msg["delay"].set_stamp(self.subject_date or datetime.now().astimezone())
        msg["subject"] = self.subject or str(self.DISCO_NAME)
        return msg

    def shutdown(self):
        user_jid = copy(self.jid)
        user_jid.resource = self.user_nick
        for user_full_jid in self.user_full_jids():
            presence = self.xmpp.make_presence(
                pfrom=user_jid, pto=user_full_jid, ptype="unavailable"
            )
            presence["muc"]["affiliation"] = "none"
            presence["muc"]["role"] = "none"
            presence["muc"]["status_codes"] = {110, 332}
            presence.send()

    def handle_ping(self, iq: Iq):
        if iq.get_from().resource in self.user_resources:
            iq.reply().send()
        else:
            raise XMPPError("not-acceptable", etype="cancel", by=self.jid)

    def user_full_jids(self):
        for r in self.user_resources:
            j = copy(self.user.jid)
            j.resource = r
            yield j

    @property
    def user_muc_jid(self):
        user_muc_jid = copy(self.jid)
        user_muc_jid.resource = self.user_nick
        return user_muc_jid

    def _legacy_to_xmpp(self, legacy_id: LegacyMessageType):
        return self.session.sent.get(
            legacy_id
        ) or self.session.legacy_msg_id_to_xmpp_msg_id(legacy_id)

    async def echo(self, m: Message, legacy_msg_id: Optional[LegacyMessageType] = None):
        self.log.debug("Echoing %s -- %s", m, legacy_msg_id)

        origin_id = m.get_origin_id()
        self.log.debug(f"Origin: %r ", origin_id)

        m.set_from(self.user_muc_jid)
        self.archive.add(m)

        msg = copy(m)
        msg.set_id(m.get_id())
        if origin_id:
            # because of slixmpp internal magic, we need to do this to ensure the origin_id
            # is present
            set_origin_id(msg, origin_id)
        if legacy_msg_id:
            msg["stanza_id"]["id"] = str(legacy_msg_id)
        else:
            msg["stanza_id"]["id"] = str(uuid4())
        msg["stanza_id"]["by"] = self.jid

        for user_full_jid in self.user_full_jids():
            self.log.debug("Echoing to %s", user_full_jid)
            msg = copy(msg)
            msg.set_to(user_full_jid)

            msg.send()

    def _send_room_presence(self, user_full_jid: Optional[JID] = None):
        if user_full_jid is None:
            tos = self.user_full_jids()
        else:
            tos = [user_full_jid]
        for to in tos:
            p = self.xmpp.make_presence(pfrom=self.jid, pto=to)
            if self._avatar_hash:
                p["vcard_temp_update"]["photo"] = self._avatar_hash
            else:
                p["vcard_temp_update"]["photo"] = ""
            p.send()

    async def join(self, join_presence: Presence):
        user_full_jid = join_presence.get_from()
        requested_nickname = join_presence.get_to().resource
        client_resource = user_full_jid.resource

        if not requested_nickname or not client_resource:
            raise XMPPError("jid-malformed", by=self.jid)

        self.log.debug(
            "Resource %s of %s wants to join room %s with nickname %s",
            client_resource,
            self.user,
            self.legacy_id,
            requested_nickname,
        )

        await self.__fill_history()
        await self.__fill_participants()

        if self._avatar_hash:
            self._send_room_presence(user_full_jid)

        for participant in self._participants_by_nicknames.values():
            participant.send_initial_presence(full_jid=user_full_jid)

        user_nick = self.user_nick
        user_participant = await self.get_user_participant()
        user_participant.send_initial_presence(
            user_full_jid,
            presence_id=join_presence["id"],
            nick_change=user_nick != requested_nickname,
        )

        history_params = join_presence["muc_join"]["history"]
        maxchars = int_or_none(history_params["maxchars"])
        maxstanzas = int_or_none(history_params["maxstanzas"])
        seconds = int_or_none(history_params["seconds"])
        try:
            since = self.xmpp.plugin["xep_0082"].parse(history_params["since"])
        except ValueError:
            since = None
        if seconds:
            since = datetime.now() - timedelta(seconds=seconds)
        if equals_zero(maxchars) or equals_zero(maxstanzas):
            log.debug("Joining client does not want any old-school MUC history-on-join")
        else:
            log.debug("Filling history %s")
            await self._fill_history(
                user_full_jid,
                maxchars=maxchars,
                maxstanzas=maxstanzas,
                since=since,
            )
        self._make_subject_message(user_full_jid).send()
        self.user_resources.add(client_resource)

    async def get_user_participant(self) -> "LegacyParticipantType":
        return self.Participant(self, self.user_nick, is_user=True)

    def __store_participant(self, p: "LegacyParticipantType"):
        # we don't want to update the participant list before
        # we call fill_participants() (legacy API call)
        if self.__participants_filled:
            self._participants_by_nicknames[p.nickname] = p  # type:ignore
            if p.contact:
                self._participants_by_contacts[p.contact] = p

    async def get_participant(self, nickname: str) -> "LegacyParticipantType":
        p = self._participants_by_nicknames.get(nickname)
        if p is None:
            p = self.Participant(self, nickname)
            self.__store_participant(p)
        return p

    def get_system_participant(self):
        return self.Participant(self, is_system=True)

    async def get_participant_by_contact(
        self, c: "LegacyContact"
    ) -> "LegacyParticipantType":
        p = self._participants_by_contacts.get(c)
        if p is None:
            p = self.Participant(self, c.name)
            p.contact = c
            self.__store_participant(p)
        return p

    async def get_participants(self):
        await self.__fill_participants()
        return self._participants_by_nicknames.values()

    def remove_participant(self, p: "LegacyParticipantType"):
        if p.contact is not None:
            del self._participants_by_contacts[p.contact]
        del self._participants_by_nicknames[p.nickname]  # type:ignore
        p.leave()

    async def fill_participants(self):
        """
        In here, call self.get_participant() or self.get_participant_by_contact()
        to make an initial list of participant.
        This should not include the participant of the user, only other participants.
        """
        pass

    async def _fill_history(
        self,
        full_jid: JID,
        maxchars: Optional[int] = None,
        maxstanzas: Optional[int] = None,
        seconds: Optional[int] = None,
        since: Optional[datetime] = None,
    ):
        """
        Old-style history join

        :param full_jid:
        :param maxchars:
        :param maxstanzas:
        :param seconds:
        :param since:
        :return:
        """
        if since is None:
            if seconds is None:
                start_date = datetime.now(tz=timezone.utc) - timedelta(days=1)
            else:
                start_date = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)
        else:
            start_date = since or datetime.now(tz=timezone.utc) - timedelta(days=1)

        history_messages = list(
            self.archive.get_all(start_date=start_date, end_date=None)
        )

        if maxstanzas:
            history_messages = history_messages[-maxstanzas:]

        for h_msg in history_messages:
            msg = h_msg.stanza_component_ns
            msg["delay"]["stamp"] = h_msg.when
            msg.set_to(full_jid)
            msg.send()

    async def send_mam(self, iq: Iq):
        await self.__fill_history()

        form_values = iq["mam"]["form"].get_values()

        start_date = str_to_datetime_or_none(form_values.get("start"))
        end_date = str_to_datetime_or_none(form_values.get("end"))

        after_id = form_values.get("after-id")
        before_id = form_values.get("before-id")

        sender = form_values.get("with")

        ids = form_values.get("ids") or ()

        if max_str := iq["mam"]["rsm"]["max"]:
            try:
                max_results = int(max_str)
            except ValueError:
                max_results = None
        else:
            max_results = None

        after_id_rsm = iq["mam"]["rsm"]["after"]
        after_id = after_id_rsm or after_id

        before_rsm = iq["mam"]["rsm"]["before"]
        if before_rsm is True and max_results is not None:
            last_page_n = max_results
        else:
            last_page_n = None

        first = None
        last = None
        count = 0

        it = self.archive.get_all(
            start_date, end_date, before_id, after_id, ids, last_page_n, sender
        )

        if iq["mam"]["flip_page"]:
            it = reversed(list(it))

        for history_msg in it:
            last = xmpp_id = history_msg.id
            if first is None:
                first = xmpp_id

            wrapper_msg = self.xmpp.make_message(mfrom=self.jid, mto=iq.get_from())
            wrapper_msg["mam_result"]["queryid"] = iq["mam"]["queryid"]
            wrapper_msg["mam_result"]["id"] = xmpp_id
            wrapper_msg["mam_result"].append(history_msg.forwarded())

            wrapper_msg.send()
            count += 1

            if max_results and count == max_results:
                break

        if max_results:
            try:
                next(it)
            except StopIteration:
                complete = True
            else:
                complete = False
        else:
            complete = True

        reply = iq.reply()
        if not self.STABLE_ARCHIVE:
            reply["mam_fin"]["stable"] = "false"
        if complete:
            reply["mam_fin"]["complete"] = "true"
        reply["mam_fin"]["rsm"]["first"] = first
        reply["mam_fin"]["rsm"]["last"] = last
        reply["mam_fin"]["rsm"]["count"] = str(count)
        reply.send()

    async def send_mam_metadata(self, iq: Iq):
        await self.__fill_history()
        await self.archive.send_metadata(iq)


def set_origin_id(msg: Message, origin_id: str):
    sub = ET.Element("{urn:xmpp:sid:0}origin-id")
    sub.attrib["id"] = origin_id
    msg.xml.append(sub)


def int_or_none(x):
    try:
        return int(x)
    except ValueError:
        return None


def equals_zero(x):
    if x is None:
        return False
    else:
        return x == 0


def str_to_datetime_or_none(date: Optional[str]):
    if date is None:
        return
    try:
        return str_to_datetime(date)
    except ValueError:
        return None


log = logging.getLogger(__name__)
