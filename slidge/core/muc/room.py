import logging
from copy import copy
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, AsyncIterable, Generic, Optional
from uuid import uuid4

from slixmpp import JID, Iq, Message, Presence
from slixmpp.plugins.xep_0082 import parse as str_to_datetime
from slixmpp.stanza import Error as BaseError
from slixmpp.xmlstream import ET

from ...util import ABCSubclassableOnceAtMost
from ...util.types import (
    LegacyGroupIdType,
    LegacyMessageType,
    LegacyParticipantType,
    SessionType,
)
from ..mixins.base import ReactionRecipientMixin
from ..mixins.disco import ChatterDiscoMixin
from .archive import MessageArchive

if TYPE_CHECKING:
    from ..contact import LegacyContact
    from ..gateway import BaseGateway


class Error(BaseError):
    namespace = "jabber:component:accept"


class MucType(int, Enum):
    GROUP = 0
    CHANNEL = 1


ADMIN_NS = "http://jabber.org/protocol/muc#admin"


class LegacyMUC(
    Generic[SessionType, LegacyGroupIdType, LegacyParticipantType, LegacyMessageType],
    ChatterDiscoMixin,
    ReactionRecipientMixin,
    metaclass=ABCSubclassableOnceAtMost,
):
    user_nick = "SlidgeUser"
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

    def __init__(self, session: SessionType, legacy_id: LegacyGroupIdType, jid: JID):
        from .participant import LegacyParticipant

        self.session = session
        self.xmpp: "BaseGateway" = session.xmpp
        self.user = session.user
        self.log = session.log

        self.legacy_id = legacy_id
        self.jid = jid

        self.user_resources = set[str]()

        self.Participant = LegacyParticipant.get_self_or_unique_subclass()

        self.DISCO_NAME = str(legacy_id)

        self.xmpp.add_event_handler(
            "presence_unavailable", self._on_presence_unavailable
        )

        self._subject = ""
        self.subject_setter = "unknown"

        self.archive: MessageArchive = MessageArchive()

    def __repr__(self):
        return f"<MUC '{self.legacy_id}' - {self.jid}>"

    def _on_presence_unavailable(self, p: Presence):
        pto = p.get_to()
        if pto.bare != self.jid.bare:
            return

        pfrom = p.get_from()
        if pfrom.bare != self.user.bare_jid:
            return
        if (resource := pfrom.resource) in (resources := self.user_resources):
            if pto.resource != self.user_nick:
                self.log.warning(
                    "Received 'leave group' request but with wrong nickname. %s", p
                )
            resources.remove(resource)
        else:
            self.log.warning(
                "Received 'leave group' request but resource was not listed. %s", p
            )

    async def backfill(self):
        """
        Override this if the legacy network provide server-side archive.
        In it, send history messages using ``self.get_participant().send*``,
        with the ``archive_only=True`` kwarg.

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

    def _no_nickname_error(self, join_presence: Presence):
        presence = self.xmpp.make_presence(
            ptype="error", pto=join_presence.get_from(), pfrom=self.jid
        )
        # Error.namespace
        presence["id"] = join_presence["id"]
        error = Error()
        error["by"] = self.jid
        error["condition"] = "jid-malformed"
        error["type"] = "modify"
        presence.append(error)
        presence.send()

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
            reply = iq.reply()
            reply["type"] = "error"
            error = Error()
            error["by"] = self.jid
            error["condition"] = "not-acceptable"
            error["type"] = "cancel"
            reply.append(error)
            reply.send()

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

    async def join(self, join_presence: Presence):
        user_full_jid = join_presence.get_from()
        requested_nickname = join_presence.get_to().resource

        if not requested_nickname:
            self._no_nickname_error(join_presence)
            return

        client_resource = user_full_jid.resource
        if not client_resource:
            self._no_nickname_error(join_presence)
            return

        self.log.debug(
            "Resource %s of %s wants to join room %s with nickname %s",
            client_resource,
            self.user,
            self.legacy_id,
            requested_nickname,
        )

        async for participant in self.get_participants():
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

    async def get_user_participant(self) -> LegacyParticipantType:
        return self.Participant(self, self.user_nick, is_user=True)

    async def get_participant(self, nickname: str) -> LegacyParticipantType:
        return self.Participant(self, nickname)

    async def get_participant_by_contact(
        self, c: "LegacyContact"
    ) -> LegacyParticipantType:
        p = self.Participant(self, c.name)
        p.contact = c
        return p

    async def get_participants(self) -> AsyncIterable[LegacyParticipantType]:
        """
        This async generator should yield instances of Participants,
        but *not* the user participant.
        """
        yield NotImplemented

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
