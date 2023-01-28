from typing import Generic, Optional, Type

from slixmpp import JID
from slixmpp.jid import _unescape_node

from ...util import SubclassableOnce
from ...util.types import LegacyGroupIdType, LegacyMUCType, SessionType
from ..contact.roster import ESCAPE_TABLE
from .room import LegacyMUC


class LegacyBookmarks(
    Generic[SessionType, LegacyMUCType, LegacyGroupIdType], metaclass=SubclassableOnce
):
    def __init__(self, session: SessionType):
        self.session = session
        self.xmpp = session.xmpp
        self.user = session.user
        self.log = session.log

        self._mucs_by_legacy_id = dict[LegacyGroupIdType, LegacyMUC]()
        self._mucs_by_bare_jid = dict[str, LegacyMUC]()

        self._muc_class: Type[LegacyMUC] = LegacyMUC.get_self_or_unique_subclass()

        self._user_nick: str = self.session.user.jid.node

    @property
    def user_nick(self):
        return self._user_nick

    @user_nick.setter
    def user_nick(self, nick: str):
        self._user_nick = nick

    def __iter__(self):
        return iter(self._mucs_by_legacy_id.values())

    async def legacy_id_to_jid_local_part(self, legacy_id: LegacyGroupIdType):
        return str(legacy_id).translate(ESCAPE_TABLE)

    async def jid_local_part_to_legacy_id(self, local_part: str):
        return _unescape_node(local_part)

    async def by_jid(self, jid: JID):
        bare = jid.bare
        muc = self._mucs_by_bare_jid.get(bare)
        if muc is None:
            self.session.log.debug(
                "Attempting to create new MUC instance for JID %s", jid
            )
            local_part = jid.node
            legacy_id = await self.jid_local_part_to_legacy_id(local_part)
            self.session.log.debug("%r is group %r", local_part, legacy_id)
            muc = self._muc_class(self.session, legacy_id=legacy_id, jid=JID(bare))
            if not muc.user_nick:
                muc.user_nick = self._user_nick
            await muc.update_info()
            await muc.backfill()
            self.session.log.debug("MUC created: %r", muc)
            self._mucs_by_legacy_id[legacy_id] = muc
            self._mucs_by_bare_jid[bare] = muc
        else:
            self.session.log.debug("Found MUC: %s -- %s", muc, type(muc))
        return muc

    async def by_legacy_id(self, legacy_id: LegacyGroupIdType):
        muc = self._mucs_by_legacy_id.get(legacy_id)
        if muc is None:
            self.session.log.debug(
                "Create new MUC instance for legacy ID %s", legacy_id
            )
            local = await self.legacy_id_to_jid_local_part(legacy_id)
            jid = JID(f"{local}@{self.xmpp.boundjid}")
            muc = self._muc_class(
                self.session,
                legacy_id=legacy_id,
                jid=jid,
            )
            if not muc.user_nick:
                muc.user_nick = self._user_nick
            await muc.update_info()
            await muc.backfill()
            self.log.debug("MUC CLASS: %s", self._muc_class)

            self._mucs_by_legacy_id[legacy_id] = muc
            self._mucs_by_bare_jid[jid.bare] = muc
        return muc

    async def fill(self):
        """
        Establish a user's known groups.

        This has to be overridden in plugins with group support and at the
        minimum, this should ``await self.by_legacy_id(group_id)`` for all
        the groups a user is part of.

        Ideally, set the group subject, friendly, number, etc. in this method

        Slidge internals will call this on successful ``BaseSession.login()``

        """
        if self.xmpp.GROUPS:
            raise NotImplementedError(
                "The plugin advertised support for groups but"
                " LegacyBookmarks.fill() was not overridden."
            )
