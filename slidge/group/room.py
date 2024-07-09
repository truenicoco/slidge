import json
import logging
import re
import string
import warnings
from copy import copy
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Generic, Optional, Self, Union
from uuid import uuid4

from slixmpp import JID, Iq, Message, Presence
from slixmpp.exceptions import IqError, XMPPError
from slixmpp.jid import _unescape_node
from slixmpp.plugins.xep_0004 import Form
from slixmpp.plugins.xep_0060.stanza import Item
from slixmpp.plugins.xep_0082 import parse as str_to_datetime
from slixmpp.xmlstream import ET

from ..contact.contact import LegacyContact
from ..contact.roster import ContactIsUser
from ..core import config
from ..core.mixins import StoredAttributeMixin
from ..core.mixins.avatar import AvatarMixin
from ..core.mixins.disco import ChatterDiscoMixin
from ..core.mixins.lock import NamedLockMixin
from ..core.mixins.recipient import ReactionRecipientMixin, ThreadRecipientMixin
from ..db.models import Room
from ..util import ABCSubclassableOnceAtMost
from ..util.types import (
    LegacyGroupIdType,
    LegacyMessageType,
    LegacyParticipantType,
    LegacyUserIdType,
    Mention,
    MucAffiliation,
    MucType,
)
from ..util.util import deprecated
from .archive import MessageArchive
from .participant import LegacyParticipant

if TYPE_CHECKING:
    from ..core.gateway import BaseGateway
    from ..core.session import BaseSession

ADMIN_NS = "http://jabber.org/protocol/muc#admin"


class LegacyMUC(
    Generic[
        LegacyGroupIdType, LegacyMessageType, LegacyParticipantType, LegacyUserIdType
    ],
    StoredAttributeMixin,
    AvatarMixin,
    NamedLockMixin,
    ChatterDiscoMixin,
    ReactionRecipientMixin,
    ThreadRecipientMixin,
    metaclass=ABCSubclassableOnceAtMost,
):
    """
    A room, a.k.a. a Multi-User Chat.

    MUC instances are obtained by calling :py:meth:`slidge.group.bookmarks.LegacyBookmarks`
    on the user's :py:class:`slidge.core.session.BaseSession`.
    """

    max_history_fetch = 100

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

    KEEP_BACKFILLED_PARTICIPANTS = False
    """
    Set this to ``True`` if the participant list is not full after calling
    ``fill_participants()``. This is a workaround for networks with huge
    participant lists which do not map really well the MUCs where all presences
    are sent on join.
    It allows to ensure that the participants that last spoke (within the
    ``fill_history()`` method are effectively participants, thus making possible
    for XMPP clients to fetch their avatars.
    """

    _ALL_INFO_FILLED_ON_STARTUP = False
    """
    Set this to true if the fill_participants() / fill_participants() design does not
    fit the legacy API, ie, no lazy loading of the participant list and history.
    """

    HAS_DESCRIPTION = True
    """
    Set this to false if the legacy network does not allow setting a description
    for the group. In this case the description field will not be present in the
    room configuration form.
    """

    HAS_SUBJECT = True
    """
    Set this to false if the legacy network does not allow setting a subject
    (sometimes also called topic) for the group. In this case, as a subject is
    recommended by :xep:`0045` ("SHALL"), the description (or the group name as
    ultimate fallback) will be used as the room subject.
    By setting this to false, an error will be returned when the :term:`User`
    tries to set the room subject.
    """

    _avatar_pubsub_broadcast = False
    _avatar_bare_jid = True
    archive: MessageArchive

    def __init__(self, session: "BaseSession", legacy_id: LegacyGroupIdType, jid: JID):
        self.session = session
        self.xmpp: "BaseGateway" = session.xmpp
        self.log = logging.getLogger(f"{self.user_jid.bare}:muc:{jid}")

        self.legacy_id = legacy_id
        self.jid = jid

        self._user_resources = set[str]()

        self.Participant = LegacyParticipant.get_self_or_unique_subclass()

        self._subject = ""
        self._subject_setter: Union[str, None, "LegacyContact", "LegacyParticipant"] = (
            None
        )

        self.pk: Optional[int] = None
        self._user_nick: Optional[str] = None

        self._participants_filled = False
        self.__history_filled = False
        self._description = ""
        self._subject_date: Optional[datetime] = None

        self.__participants_store = self.xmpp.store.participants
        self.__store = self.xmpp.store.rooms

        self._n_participants: Optional[int] = None

        super().__init__()

    @property
    def n_participants(self):
        return self._n_participants

    @n_participants.setter
    def n_participants(self, n_participants: Optional[int]):
        if self._n_participants == n_participants:
            return
        self._n_participants = n_participants
        assert self.pk is not None
        self.__store.update_n_participants(self.pk, n_participants)

    @property
    def user_jid(self):
        return self.session.user_jid

    def __repr__(self):
        return f"<MUC {self.legacy_id}/{self.jid}/{self.name}>"

    @property
    def subject_date(self) -> Optional[datetime]:
        return self._subject_date

    @subject_date.setter
    def subject_date(self, when: Optional[datetime]) -> None:
        self._subject_date = when
        assert self.pk is not None
        self.__store.update_subject_date(self.pk, when)

    def __send_configuration_change(self, codes):
        part = self.get_system_participant()
        part.send_configuration_change(codes)

    @property
    def user_nick(self):
        return self._user_nick or self.session.bookmarks.user_nick or self.user_jid.node

    @user_nick.setter
    def user_nick(self, nick: str):
        self._user_nick = nick

    def add_user_resource(self, resource: str) -> None:
        self._user_resources.add(resource)
        assert self.pk is not None
        self.__store.set_resource(self.pk, self._user_resources)

    def get_user_resources(self) -> set[str]:
        return self._user_resources

    def remove_user_resource(self, resource: str) -> None:
        self._user_resources.remove(resource)
        assert self.pk is not None
        self.__store.set_resource(self.pk, self._user_resources)

    async def __fill_participants(self):
        async with self.lock("fill participants"):
            if self._participants_filled:
                return
            self._participants_filled = True
            try:
                await self.fill_participants()
            except NotImplementedError:
                pass

    async def __fill_history(self):
        async with self.lock("fill history"):
            if self.__history_filled:
                log.debug("History has already been fetched %s", self)
                return
            log.debug("Fetching history for %s", self)
            for msg in self.archive:
                try:
                    legacy_id = self.session.xmpp_to_legacy_msg_id(msg.id)
                    oldest_date = msg.when
                except Exception as e:
                    # not all archived stanzas have a valid legacy msg ID, eg
                    # reactions, corrections, message with multiple attachments…
                    self.log.debug(f"Could not convert during history back-filling {e}")
                else:
                    break
            else:
                legacy_id = None
                oldest_date = None
            try:
                await self.backfill(legacy_id, oldest_date)
            except NotImplementedError:
                return
            self.__history_filled = True

    @property
    def name(self):
        return self.DISCO_NAME

    @name.setter
    def name(self, n: str):
        if self.DISCO_NAME == n:
            return
        self.DISCO_NAME = n
        self.__send_configuration_change((104,))

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, d: str):
        if self._description == d:
            return
        self._description = d
        assert self.pk is not None
        self.__store.update_description(self.pk, d)
        self.__send_configuration_change((104,))

    def on_presence_unavailable(self, p: Presence):
        pto = p.get_to()
        if pto.bare != self.jid.bare:
            return

        pfrom = p.get_from()
        if pfrom.bare != self.user_jid.bare:
            return
        if (resource := pfrom.resource) in self._user_resources:
            if pto.resource != self.user_nick:
                self.log.debug(
                    "Received 'leave group' request but with wrong nickname. %s", p
                )
            self.remove_user_resource(resource)
        else:
            self.log.debug(
                "Received 'leave group' request but resource was not listed. %s", p
            )

    async def update_info(self):
        """
        Fetch information about this group from the legacy network

        This is awaited on MUC instantiation, and should be overridden to
        update the attributes of the group chat, like title, subject, number
        of participants etc.

        To take advantage of the slidge avatar cache, you can check the .avatar
        property to retrieve the "legacy file ID" of the cached avatar. If there
        is no change, you should not call
        :py:meth:`slidge.core.mixins.avatar.AvatarMixin.set_avatar()` or
        attempt to modify
        the :attr:.avatar property.
        """
        raise NotImplementedError

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
        raise NotImplementedError

    async def fill_participants(self):
        """
        In here, call self.get_participant(), self.get_participant_by_contact(),
        of self.get_user_participant() to make an initial list of participants.
        """
        raise NotImplementedError

    @property
    def subject(self):
        return self._subject

    @subject.setter
    def subject(self, s: str):
        if s == self._subject:
            return
        self.session.create_task(
            self.__get_subject_setter_participant()
        ).add_done_callback(
            lambda task: task.result().set_room_subject(
                s, None, self.subject_date, False
            )
        )
        self._subject = s
        assert self.pk is not None
        self.__store.update_subject(self.pk, s)

    @property
    def is_anonymous(self):
        return self.type == MucType.CHANNEL

    @property
    def subject_setter(self):
        return self._subject_setter

    @subject_setter.setter
    def subject_setter(self, subject_setter):
        self._subject_setter = subject_setter
        self.__store.update(self)

    async def __get_subject_setter_participant(self):
        who = self.subject_setter

        if isinstance(who, LegacyParticipant):
            return who
        elif isinstance(who, str):
            return await self.get_participant(who, store=False)
        elif isinstance(self.subject_setter, LegacyContact):
            return await self.get_participant_by_contact(who)
        else:
            return self.get_system_participant()

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
            "urn:xmpp:occupant-id:0",
            "jabber:iq:register",
            self.xmpp.plugin["xep_0425"].stanza.NS,
        ]
        if self.type == MucType.GROUP:
            features.extend(["muc_membersonly", "muc_nonanonymous", "muc_hidden"])
        elif self.type == MucType.CHANNEL:
            features.extend(["muc_open", "muc_semianonymous", "muc_public"])
        elif self.type == MucType.CHANNEL_NON_ANONYMOUS:
            features.extend(["muc_open", "muc_nonanonymous", "muc_public"])
        return features

    async def extended_features(self):
        is_group = self.type == MucType.GROUP

        form = self.xmpp.plugin["xep_0004"].make_form(ftype="result")

        form.add_field(
            "FORM_TYPE", "hidden", value="http://jabber.org/protocol/muc#roominfo"
        )
        form.add_field("muc#roomconfig_persistentroom", "boolean", value=True)
        form.add_field("muc#roomconfig_changesubject", "boolean", value=False)
        form.add_field("muc#maxhistoryfetch", value=str(self.max_history_fetch))
        form.add_field("muc#roominfo_subjectmod", "boolean", value=False)

        if self._ALL_INFO_FILLED_ON_STARTUP or self._participants_filled:
            n: Optional[int] = len(await self.get_participants())
        else:
            n = self._n_participants
        if n is not None:
            form.add_field("muc#roominfo_occupants", value=str(n))

        if d := self.description:
            form.add_field("muc#roominfo_description", value=d)

        if s := self.subject:
            form.add_field("muc#roominfo_subject", value=s)

        if self._set_avatar_task:
            await self._set_avatar_task
            avatar = self.get_avatar()
            if avatar and (h := avatar.id):
                form.add_field(
                    "{http://modules.prosody.im/mod_vcard_muc}avatar#sha1", value=h
                )
                form.add_field("muc#roominfo_avatarhash", "text-multi", value=[h])

        form.add_field("muc#roomconfig_membersonly", "boolean", value=is_group)
        form.add_field(
            "muc#roomconfig_whois",
            "list-single",
            value="moderators" if self.is_anonymous else "anyone",
        )
        form.add_field("muc#roomconfig_publicroom", "boolean", value=not is_group)
        form.add_field("muc#roomconfig_allowpm", "boolean", value=False)

        r = [form]

        if reaction_form := await self.restricted_emoji_extended_feature():
            r.append(reaction_form)

        return r

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

    def user_full_jids(self):
        for r in self._user_resources:
            j = copy(self.user_jid)
            j.resource = r
            yield j

    @property
    def user_muc_jid(self):
        user_muc_jid = copy(self.jid)
        user_muc_jid.resource = self.user_nick
        return user_muc_jid

    def _legacy_to_xmpp(self, legacy_id: LegacyMessageType):
        return self.xmpp.store.sent.get_group_xmpp_id(
            self.session.user_pk, str(legacy_id)
        ) or self.session.legacy_to_xmpp_msg_id(legacy_id)

    async def echo(
        self, msg: Message, legacy_msg_id: Optional[LegacyMessageType] = None
    ):
        origin_id = msg.get_origin_id()

        msg.set_from(self.user_muc_jid)
        msg.set_id(msg.get_id())
        if origin_id:
            # because of slixmpp internal magic, we need to do this to ensure the origin_id
            # is present
            set_origin_id(msg, origin_id)
        if legacy_msg_id:
            msg["stanza_id"]["id"] = self.session.legacy_to_xmpp_msg_id(legacy_msg_id)
        else:
            msg["stanza_id"]["id"] = str(uuid4())
        msg["stanza_id"]["by"] = self.jid
        msg["occupant-id"]["id"] = "slidge-user"

        self.archive.add(msg, await self.get_user_participant())

        for user_full_jid in self.user_full_jids():
            self.log.debug("Echoing to %s", user_full_jid)
            msg = copy(msg)
            msg.set_to(user_full_jid)

            msg.send()

    def _get_cached_avatar_id(self):
        assert self.pk is not None
        return self.xmpp.store.rooms.get_avatar_legacy_id(self.pk)

    def _post_avatar_update(self) -> None:
        if self.pk is None or self._avatar_pk is None:
            return
        self.xmpp.store.rooms.set_avatar(self.pk, self._avatar_pk)
        self.__send_configuration_change((104,))
        self._send_room_presence()

    def _send_room_presence(self, user_full_jid: Optional[JID] = None):
        if user_full_jid is None:
            tos = self.user_full_jids()
        else:
            tos = [user_full_jid]
        for to in tos:
            p = self.xmpp.make_presence(pfrom=self.jid, pto=to)
            if (avatar := self.get_avatar()) and (h := avatar.id):
                p["vcard_temp_update"]["photo"] = h
            else:
                p["vcard_temp_update"]["photo"] = ""
            p.send()

    async def join(self, join_presence: Presence):
        user_full_jid = join_presence.get_from()
        requested_nickname = join_presence.get_to().resource
        client_resource = user_full_jid.resource

        if client_resource in self._user_resources:
            self.log.debug("Received join from a resource that is already joined.")

        self.add_user_resource(client_resource)

        if not requested_nickname or not client_resource:
            raise XMPPError("jid-malformed", by=self.jid)

        self.log.debug(
            "Resource %s of %s wants to join room %s with nickname %s",
            client_resource,
            self.user_jid,
            self.legacy_id,
            requested_nickname,
        )

        await self.__fill_participants()

        assert self.pk is not None
        for db_participant in self.xmpp.store.participants.get_all(
            self.pk, user_included=False
        ):
            participant = self.Participant.from_store(self.session, db_participant)
            participant.send_initial_presence(full_jid=user_full_jid)

        user_nick = self.user_nick
        user_participant = await self.get_user_participant()
        if not user_participant.is_user:  # type:ignore
            self.log.warning("is_user flag not set participant on user_participant")
            user_participant.is_user = True  # type:ignore
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
            self.log.debug("Old school history fill")
            await self.__fill_history()
            await self.__old_school_history(
                user_full_jid,
                maxchars=maxchars,
                maxstanzas=maxstanzas,
                since=since,
            )
        (await self.__get_subject_setter_participant()).set_room_subject(
            self._subject if self.HAS_SUBJECT else (self.description or self.name),
            user_full_jid,
            self.subject_date,
        )
        if t := self._set_avatar_task:
            await t
        self._send_room_presence(user_full_jid)

    async def get_user_participant(self, **kwargs) -> "LegacyParticipantType":
        """
        Get the participant representing the gateway user

        :param kwargs: additional parameters for the :class:`.Participant`
            construction (optional)
        :return:
        """
        p = await self.get_participant(self.user_nick, is_user=True, **kwargs)
        self.__store_participant(p)
        return p

    def __store_participant(self, p: "LegacyParticipantType") -> None:
        # we don't want to update the participant list when we're filling history
        if not self.KEEP_BACKFILLED_PARTICIPANTS and self.get_lock("fill history"):
            return
        assert self.pk is not None
        p.pk = self.__participants_store.add(self.pk, p.nickname)
        self.__participants_store.update(p)

    async def get_participant(
        self,
        nickname: str,
        raise_if_not_found=False,
        fill_first=False,
        store=True,
        **kwargs,
    ) -> "LegacyParticipantType":
        """
        Get a participant by their nickname.

        In non-anonymous groups, you probably want to use
        :meth:`.LegacyMUC.get_participant_by_contact` instead.

        :param nickname: Nickname of the participant (used as resource part in the MUC)
        :param raise_if_not_found: Raise XMPPError("item-not-found") if they are not
            in the participant list (internal use by slidge, plugins should not
            need that)
        :param fill_first: Ensure :meth:`.LegacyMUC.fill_participants()` has been called first
             (internal use by slidge, plugins should not need that)
        :param store: persistently store the user in the list of MUC participants
        :param kwargs: additional parameters for the :class:`.Participant`
            construction (optional)
        :return:
        """
        if fill_first:
            await self.__fill_participants()
        assert self.pk is not None
        with self.xmpp.store.session():
            stored = self.__participants_store.get_by_nickname(
                self.pk, nickname
            ) or self.__participants_store.get_by_resource(self.pk, nickname)
            if stored is not None:
                return self.Participant.from_store(self.session, stored)

        if raise_if_not_found:
            raise XMPPError("item-not-found")
        p = self.Participant(self, nickname, **kwargs)
        if store:
            self.__store_participant(p)
        if (
            not self.get_lock("fill participants")
            and not self.get_lock("fill history")
            and self._participants_filled
            and not p.is_user
            and not p.is_system
        ):
            p.send_affiliation_change()
        return p

    def get_system_participant(self) -> "LegacyParticipantType":
        """
        Get a pseudo-participant, representing the room itself

        Can be useful for events that cannot be mapped to a participant,
        e.g. anonymous moderation events, or announces from the legacy
        service
        :return:
        """
        return self.Participant(self, is_system=True)

    async def get_participant_by_contact(
        self, c: "LegacyContact", **kwargs
    ) -> "LegacyParticipantType":
        """
        Get a non-anonymous participant.

        This is what should be used in non-anonymous groups ideally, to ensure
        that the Contact jid is associated to this participant

        :param c: The :class:`.LegacyContact` instance corresponding to this contact
        :param kwargs: additional parameters for the :class:`.Participant`
            construction (optional)
        :return:
        """
        await self.session.contacts.ready
        assert self.pk is not None
        assert c.contact_pk is not None
        with self.__store.session():
            stored = self.__participants_store.get_by_contact(self.pk, c.contact_pk)
            if stored is not None:
                return self.Participant.from_store(
                    self.session, stored, muc=self, contact=c
                )

        nickname = c.name or _unescape_node(c.jid_username)
        if not self.__store.nickname_is_available(self.pk, nickname):
            self.log.debug("Nickname conflict")
            nickname = f"{nickname} ({c.jid_username})"
        p = self.Participant(self, nickname, **kwargs)
        p.contact = c

        # FIXME: this is not great but given the current design,
        #        during participants fill and history backfill we do not
        #        want to send presence, because we might update affiliation
        #        and role afterwards.
        # We need a refactor of the MUC class… later™
        self.__store_participant(p)
        if not self.get_lock("fill participants") and not self.get_lock("fill history"):
            p.send_last_presence(force=True, no_cache_online=True)
        return p

    async def get_participant_by_legacy_id(
        self, legacy_id: LegacyUserIdType, **kwargs
    ) -> "LegacyParticipantType":
        try:
            c = await self.session.contacts.by_legacy_id(legacy_id)
        except ContactIsUser:
            return await self.get_user_participant(**kwargs)
        return await self.get_participant_by_contact(c, **kwargs)

    async def get_participants(self, fill_first=True):
        """
        Get all known participants of the group, ensure :meth:`.LegacyMUC.fill_participants`
        has been awaited once before. Plugins should not use that, internal
        slidge use only.
        :return:
        """
        if fill_first:
            await self.__fill_participants()
        assert self.pk is not None
        return [
            self.Participant.from_store(self.session, s)
            for s in self.__participants_store.get_all(self.pk)
        ]

    def remove_participant(self, p: "LegacyParticipantType", kick=False, ban=False):
        """
        Call this when a participant leaves the room

        :param p: The participant
        :param kick: Whether the participant left because they were kicked
        :param ban: Whether the participant left because they were banned
        """
        if kick and ban:
            raise TypeError("Either kick or ban")
        self.__participants_store.delete(p.pk)
        if kick:
            codes = {307}
        elif ban:
            codes = {301}
        else:
            codes = None
        presence = p._make_presence(ptype="unavailable", status_codes=codes)
        p._affiliation = "outcast" if ban else "none"
        p._role = "none"
        p._send(presence)

    def rename_participant(self, old_nickname: str, new_nickname: str):
        assert self.pk is not None
        with self.xmpp.store.session():
            stored = self.__participants_store.get_by_nickname(self.pk, old_nickname)
            if stored is None:
                self.log.debug("Tried to rename a participant that we didn't know")
                return
            p = self.Participant.from_store(self.session, stored)
            if p.nickname == old_nickname:
                p.nickname = new_nickname

    async def __old_school_history(
        self,
        full_jid: JID,
        maxchars: Optional[int] = None,
        maxstanzas: Optional[int] = None,
        seconds: Optional[int] = None,
        since: Optional[datetime] = None,
    ):
        """
        Old-style history join (internal slidge use)

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

        for h_msg in self.archive.get_all(
            start_date=start_date, end_date=None, last_page_n=maxstanzas
        ):
            msg = h_msg.stanza_component_ns
            msg["delay"]["stamp"] = h_msg.when
            msg.set_to(full_jid)
            self.xmpp.send(msg, False)

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
            start_date,
            end_date,
            before_id,
            after_id,
            ids,
            last_page_n,
            sender,
            bool(iq["mam"]["flip_page"]),
        )

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

    async def kick_resource(self, r: str):
        """
        Kick a XMPP client of the user. (slidge internal use)

        :param r: The resource to kick
        """
        pto = self.user_jid
        pto.resource = r
        p = self.xmpp.make_presence(
            pfrom=(await self.get_user_participant()).jid, pto=pto
        )
        p["type"] = "unavailable"
        p["muc"]["affiliation"] = "none"
        p["muc"]["role"] = "none"
        p["muc"]["status_codes"] = {110, 333}
        p.send()

    async def add_to_bookmarks(self, auto_join=True, invite=False, preserve=True):
        """
        Add the MUC to the user's XMPP bookmarks (:xep:`0402')

        This requires that slidge has the IQ privileged set correctly
        on the XMPP server

        :param auto_join: whether XMPP clients should automatically join
            this MUC on startup. In theory, XMPP clients will receive
            a "push" notification when this is called, and they will
            join if they are online.
        :param invite: send an invitation to join this MUC emanating from
            the gateway. While this should not be strictly necessary,
            it can help for clients that do not support :xep:`0402`, or
            that have 'do not honor bookmarks auto-join' turned on in their
            settings.
        :param preserve: preserve auto-join and bookmarks extensions
            set by the user outside slidge
        """
        item = Item()
        item["id"] = self.jid

        iq = Iq(stype="get", sfrom=self.user_jid, sto=self.user_jid)
        iq["pubsub"]["items"]["node"] = self.xmpp["xep_0402"].stanza.NS
        iq["pubsub"]["items"].append(item)

        is_update = False
        if preserve:
            try:
                ans = await self.xmpp["xep_0356"].send_privileged_iq(iq)
                is_update = len(ans["pubsub"]["items"]) == 1
                # this below creates the item if it wasn't here already
                # (slixmpp annoying magic)
                item = ans["pubsub"]["items"]["item"]
                item["id"] = self.jid
            except IqError:
                item["conference"]["autojoin"] = auto_join
            except PermissionError:
                warnings.warn(
                    "IQ privileges (XEP0356) are not set, we cannot fetch the user bookmarks"
                )
            else:
                # if the bookmark is already present, we preserve it as much as
                # possible, especially custom <extensions>
                self.log.debug("Existing: %s", item)
                # if it's an update, we do not touch the auto join flag
                if not is_update:
                    item["conference"]["autojoin"] = auto_join
        else:
            item["conference"]["autojoin"] = auto_join

        item["conference"]["nick"] = self.user_nick
        iq = Iq(stype="set", sfrom=self.user_jid, sto=self.user_jid)
        iq["pubsub"]["publish"]["node"] = self.xmpp["xep_0402"].stanza.NS
        iq["pubsub"]["publish"].append(item)

        iq["pubsub"]["publish_options"] = _BOOKMARKS_OPTIONS

        try:
            await self.xmpp["xep_0356"].send_privileged_iq(iq)
        except PermissionError:
            warnings.warn(
                "IQ privileges (XEP0356) are not set, we cannot add bookmarks for the user"
            )
            # fallback by forcing invitation
            invite = True
        except IqError as e:
            warnings.warn(
                f"Something went wrong while trying to set the bookmarks: {e}"
            )
            # fallback by forcing invitation
            invite = True

        if invite or (config.ALWAYS_INVITE_WHEN_ADDING_BOOKMARKS and not is_update):
            self.session.send_gateway_invite(
                self, reason="This group could not be added automatically for you"
            )

    async def on_avatar(
        self, data: Optional[bytes], mime: Optional[str]
    ) -> Optional[Union[int, str]]:
        """
        Called when the user tries to set the avatar of the room from an XMPP
        client.

        If the set avatar operation is completed, should return a legacy image
        unique identifier. In this case the MUC avatar will be immediately
        updated on the XMPP side.

        If data is not None and this method returns None, then we assume that
        self.set_avatar() will be called elsewhere, eg triggered by a legacy
        room update event.

        :param data: image data or None if the user meant to remove the avatar
        :param mime: the mime type of the image. Since this is provided by
            the XMPP client, there is no guarantee that this is valid or
            correct.
        :return: A unique avatar identifier, which will trigger
            :py:meth:`slidge.group.room.LegacyMUC.set_avatar`. Alternatively, None, if
            :py:meth:`.LegacyMUC.set_avatar` is meant to be awaited somewhere else.
        """
        raise NotImplementedError

    admin_set_avatar = deprecated("LegacyMUC.on_avatar", on_avatar)

    async def on_set_affiliation(
        self,
        contact: "LegacyContact",
        affiliation: MucAffiliation,
        reason: Optional[str],
        nickname: Optional[str],
    ):
        """
        Triggered when the user requests changing the affiliation of a contact
        for this group,

        Examples: promotion them to moderator, kick (affiliation=none),
        ban (affiliation=outcast).

        :param contact: The contact whose affiliation change is requested
        :param affiliation: The new affiliation
        :param reason: A reason for this affiliation change
        :param nickname:
        """
        raise NotImplementedError

    async def on_set_config(
        self,
        name: Optional[str],
        description: Optional[str],
    ):
        """
        Triggered when the user requests changing the room configuration.
        Only title and description can be changed at the moment.

        The legacy module is responsible for updating :attr:`.title` and/or
        :attr:`.description` of this instance.

        If :attr:`.HAS_DESCRIPTION` is set to False, description will always
        be ``None``.

        :param name: The new name of the room.
        :param description: The new description of the room.
        """
        raise NotImplementedError

    async def on_destroy_request(self, reason: Optional[str]):
        """
        Triggered when the user requests room destruction.

        :param reason: Optionally, a reason for the destruction
        """
        raise NotImplementedError

    async def parse_mentions(self, text: str) -> list[Mention]:
        with self.__store.session():
            await self.__fill_participants()
            assert self.pk is not None
            participants = {
                p.nickname: p for p in self.__participants_store.get_all(self.pk)
            }

            if len(participants) == 0:
                return []

            result = []
            for match in re.finditer(
                "|".join(
                    sorted(
                        [re.escape(nick) for nick in participants.keys()],
                        key=lambda nick: len(nick),
                        reverse=True,
                    )
                ),
                text,
            ):
                span = match.span()
                nick = match.group()
                if span[0] != 0 and text[span[0] - 1] not in _WHITESPACE_OR_PUNCTUATION:
                    continue
                if span[1] == len(text) or text[span[1]] in _WHITESPACE_OR_PUNCTUATION:
                    participant = self.Participant.from_store(
                        self.session, participants[nick]
                    )
                    if contact := participant.contact:
                        result.append(
                            Mention(contact=contact, start=span[0], end=span[1])
                        )
        return result

    async def on_set_subject(self, subject: str) -> None:
        """
        Triggered when the user requests changing the room subject.

        The legacy module is responsible for updating :attr:`.subject` of this
        instance.

        :param subject: The new subject for this room.
        """
        raise NotImplementedError

    @classmethod
    def from_store(cls, session, stored: Room, *args, **kwargs) -> Self:
        muc = cls(
            session,
            cls.xmpp.LEGACY_ROOM_ID_TYPE(stored.legacy_id),
            stored.jid,
            *args,  # type: ignore
            **kwargs,  # type: ignore
        )
        muc.pk = stored.id
        muc.type = stored.muc_type  # type: ignore
        if stored.name:
            muc.DISCO_NAME = stored.name
        if stored.description:
            muc._description = stored.description
        if (data := stored.extra_attributes) is not None:
            muc.deserialize_extra_attributes(data)
        muc._subject = stored.subject or ""
        if stored.subject_date is not None:
            muc._subject_date = stored.subject_date.replace(tzinfo=timezone.utc)
        muc._participants_filled = stored.participants_filled
        muc.__history_filled = True
        if stored.user_resources is not None:
            muc._user_resources = set(json.loads(stored.user_resources))
        if stored.subject_setter is not None:
            muc.subject_setter = (
                LegacyParticipant.get_self_or_unique_subclass().from_store(
                    session,
                    stored.subject_setter,
                    muc=muc,
                )
            )
        muc.archive = MessageArchive(muc.pk, session.xmpp.store.mam)
        muc._set_avatar_from_store(stored)
        return muc


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


def bookmarks_form():
    form = Form()
    form["type"] = "submit"
    form.add_field(
        "FORM_TYPE",
        value="http://jabber.org/protocol/pubsub#publish-options",
        ftype="hidden",
    )
    form.add_field("pubsub#persist_items", value="1")
    form.add_field("pubsub#max_items", value="max")
    form.add_field("pubsub#send_last_published_item", value="never")
    form.add_field("pubsub#access_model", value="whitelist")
    return form


_BOOKMARKS_OPTIONS = bookmarks_form()
_WHITESPACE_OR_PUNCTUATION = string.whitespace + string.punctuation

log = logging.getLogger(__name__)
