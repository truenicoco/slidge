import logging
import string
import stringprep
import uuid
import warnings
from copy import copy
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Optional, Union

from slixmpp import JID, InvalidJID, Message, Presence
from slixmpp.plugins.xep_0045.stanza import MUCAdminItem
from slixmpp.stringprep import StringprepError, resourceprep
from slixmpp.types import MessageTypes, OptJid
from slixmpp.util.stringprep_profiles import StringPrepError, prohibit_output

from ...util import SubclassableOnce, strip_illegal_chars
from ...util.sql import CachedPresence
from ...util.types import LegacyMessageType, MucAffiliation, MucRole
from ..contact import LegacyContact
from ..mixins import ChatterDiscoMixin, MessageMixin, PresenceMixin

if TYPE_CHECKING:
    from .room import LegacyMUC


def strip_non_printable(nickname: str):
    new = (
        "".join(x for x in nickname if x in string.printable)
        + f"-slidge-{hash(nickname)}"
    )
    warnings.warn(f"Could not use {nickname} as a nickname, using {new}")
    return new


class LegacyParticipant(
    PresenceMixin,
    MessageMixin,
    ChatterDiscoMixin,
    metaclass=SubclassableOnce,
):
    mtype: MessageTypes = "groupchat"
    _can_send_carbon = False
    USE_STANZA_ID = True
    STRIP_SHORT_DELAY = False

    def __init__(
        self,
        muc: "LegacyMUC",
        nickname: Optional[str] = None,
        is_user=False,
        is_system=False,
    ):
        super().__init__()
        self.muc = muc
        self.session = session = muc.session
        self.user = session.user
        self.xmpp = session.xmpp
        self.role: MucRole = "participant"
        self.affiliation: MucAffiliation = "member"
        self.is_user: bool = is_user
        self.is_system: bool = is_system

        self._nickname = nickname

        self.__update_jid(nickname)
        log.debug("Instantiation of: %r", self)

        self.contact: Optional["LegacyContact"] = None
        # we track if we already sent a presence for this participant.
        # if we didn't, we send it before the first message.
        # this way, event in plugins that don't map "user has joined" events,
        # we send a "join"-presence from the participant before the first message
        self.__presence_sent = False
        self.log = logging.getLogger(f"{self.user.bare_jid}:{self.jid}")

    def __repr__(self):
        return f"<Participant '{self.nickname}'/'{self.jid}' of '{self.muc}'>"

    def __update_jid(self, nickname: Optional[str]):
        j: JID = copy(self.muc.jid)

        if self.is_system:
            self.jid = j
            return

        if nickname:
            nickname = self._nickname_no_illegal = strip_illegal_chars(nickname)
        else:
            warnings.warn(
                "Only the system participant is allowed to not have a nickname"
            )
            nickname = f"unnamed-{uuid.uuid4()}"

        assert isinstance(nickname, str)

        try:
            # workaround for https://codeberg.org/poezio/slixmpp/issues/3480
            prohibit_output(nickname, [stringprep.in_table_a1])
            resourceprep(nickname)
        except (StringPrepError, StringprepError):
            nickname = nickname.encode("punycode").decode()

        # at this point there still might be control chars
        try:
            j.resource = nickname
        except InvalidJID:
            j.resource = strip_non_printable(nickname)

        self.jid = j

    @property
    def nickname(self):
        return self._nickname

    @nickname.setter
    def nickname(self, new_nickname: str):
        old = self._nickname
        if new_nickname == old:
            return

        cache = getattr(self, "_last_presence", None)
        if cache:
            last_seen = cache.last_seen
            kwargs = cache.presence_kwargs
        else:
            last_seen = None
            kwargs = {}

        kwargs["status_codes"] = {303}

        p = self._make_presence(ptype="unavailable", last_seen=last_seen, **kwargs)
        # in this order so pfrom=old resource and we actually use the escaped nick
        # in the muc/item/nick element
        self.__update_jid(new_nickname)
        p["muc"]["item"]["nick"] = self.jid.resource
        self._send(p)

        self._nickname = new_nickname

        kwargs["status_codes"] = set()
        p = self._make_presence(ptype="available", last_seen=last_seen, **kwargs)
        self.__add_nick_element(p)
        self._send(p)

        if old:
            self.muc.rename_participant(old, new_nickname)

    def _make_presence(
        self,
        *,
        last_seen: Optional[datetime] = None,
        status_codes: Optional[set[int]] = None,
        user_full_jid: Optional[JID] = None,
        **presence_kwargs,
    ):
        p = super()._make_presence(last_seen=last_seen, **presence_kwargs)
        p["muc"]["affiliation"] = self.affiliation
        p["muc"]["role"] = self.role
        codes = status_codes or set()
        if self.is_user:
            codes.add(110)
        if not self.muc.is_anonymous:
            if self.is_user and user_full_jid:
                p["muc"]["jid"] = user_full_jid
                codes.add(100)
            elif self.contact:
                p["muc"]["jid"] = self.contact.jid
                if a := self.contact.get_avatar():
                    p["vcard_temp_update"]["photo"] = a.id
            else:
                warnings.warn(
                    f"Private group but no 1:1 JID associated to '{self}'",
                )

        p["muc"]["status_codes"] = codes
        return p

    @property
    def DISCO_NAME(self):
        return self.nickname

    def __send_presence_if_needed(
        self, stanza: Union[Message, Presence], full_jid: JID, archive_only: bool
    ):
        if (
            archive_only
            or self.is_system
            or self.is_user
            or self.__presence_sent
            or stanza["subject"]
        ):
            return
        if isinstance(stanza, Message):
            self.send_initial_presence(full_jid)

    @cached_property
    def __occupant_id(self):
        if self.contact:
            return self.contact.jid
        elif self.is_user:
            return "slidge-user"
        elif self.is_system:
            return "room"
        else:
            return str(uuid.uuid4())

    def _send(
        self,
        stanza: Union[Message, Presence],
        full_jid: Optional[JID] = None,
        archive_only=False,
        **send_kwargs,
    ):
        stanza["occupant-id"]["id"] = self.__occupant_id
        if isinstance(stanza, Presence):
            self.__presence_sent = True
        if full_jid:
            stanza["to"] = full_jid
            self.__send_presence_if_needed(stanza, full_jid, archive_only)
            if self.is_user:
                assert stanza.stream is not None
                stanza.stream.send(stanza, use_filters=False)
            else:
                stanza.send()
        else:
            if isinstance(stanza, Message):
                self.muc.archive.add(stanza, self)
            if archive_only:
                return
            for user_full_jid in self.muc.user_full_jids():
                stanza = copy(stanza)
                stanza["to"] = user_full_jid
                self.__send_presence_if_needed(stanza, user_full_jid, archive_only)
                stanza.send()

    def mucadmin_item(self):
        item = MUCAdminItem()
        item["nick"] = self.nickname
        item["affiliation"] = self.affiliation
        item["role"] = self.role
        if not self.muc.is_anonymous:
            if self.is_user:
                item["jid"] = self.user.bare_jid
            elif self.contact:
                item["jid"] = self.contact.jid.bare
            else:
                warnings.warn(
                    (
                        f"Public group but no contact JID associated to {self.jid} in"
                        f" {self}"
                    ),
                )
        return item

    def __add_nick_element(self, p: Presence):
        if (nick := self._nickname_no_illegal) != self.jid.resource:
            n = self.xmpp.plugin["xep_0172"].stanza.UserNick()
            n["nick"] = nick
            p.append(n)

    def _get_last_presence(self) -> Optional[CachedPresence]:
        own = super()._get_last_presence()
        if own is None and self.contact:
            return self.contact._get_last_presence()
        return own

    def send_initial_presence(
        self,
        full_jid: JID,
        nick_change=False,
        presence_id: Optional[str] = None,
    ):
        """
        Called when the user joins a MUC, as a mechanism
        to indicate to the joining XMPP client the list of "participants".

        Can be called this to trigger a "participant has joined the group" event.

        :param full_jid: Set this to only send to a specific user XMPP resource.
        :param nick_change: Used when the user joins and the MUC renames them (code 210)
        :param presence_id: set the presence ID. used internally by slidge
        """
        #  MUC status codes: https://xmpp.org/extensions/xep-0045.html#registrar-statuscodes
        codes = set()
        if nick_change:
            codes.add(210)

        if self.is_user:
            # the "initial presence" of the user has to be vanilla, as it is
            # a crucial part of the MUC join sequence for XMPP clients.
            kwargs = {}
        else:
            cache = self._get_last_presence()
            self.log.debug("Join muc, initial presence: %s", cache)
            if cache:
                ptype = cache.ptype
                if ptype == "unavailable":
                    return
                kwargs = dict(
                    last_seen=cache.last_seen, pstatus=cache.pstatus, pshow=cache.pshow
                )
            else:
                kwargs = {}
        p = self._make_presence(
            status_codes=codes,
            user_full_jid=full_jid,
            **kwargs,  # type:ignore
        )
        if presence_id:
            p["id"] = presence_id
        self.__add_nick_element(p)
        self._send(p, full_jid)

    def leave(self):
        """
        To be called only by room. To remove a participant, call
        Room.remove_participant(self) instead.
        """
        p = self._make_presence(ptype="unavailable")
        self._send(p)

    def get_disco_info(self, jid: OptJid = None, node: Optional[str] = None):
        if self.contact is not None:
            return self.contact.get_disco_info()
        return super().get_disco_info()

    def moderate(self, legacy_msg_id: LegacyMessageType, reason: Optional[str] = None):
        m = self._make_message()
        m["apply_to"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        m["apply_to"]["moderated"].enable("retract")
        m["apply_to"]["moderated"]["by"] = self.jid
        if reason:
            m["apply_to"]["moderated"]["reason"] = reason
        self._send(m)

    def set_room_subject(
        self,
        subject: str,
        full_jid: Optional[JID] = None,
        when: Optional[datetime] = None,
        update_muc=True,
    ):
        if when is None:
            when = datetime.now().astimezone()

        if update_muc:
            self.muc._subject = subject  # type: ignore
            self.muc.subject_setter = self
            self.muc.subject_date = when

        msg = self._make_message()
        msg["delay"].set_stamp(when)
        msg["delay"]["from"] = self.muc.jid
        msg["subject"] = subject or str(self.muc.name)
        self._send(msg, full_jid)


log = logging.getLogger(__name__)
