from typing import Generic, Type

from slixmpp import JID
from slixmpp.jid import _unescape_node

from slidge.core.contact import ESCAPE_TABLE
from slidge.util import SubclassableOnce
from slidge.util.types import LegacyGroupIdType, LegacyMUCType, SessionType

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

    def set_username(self, nick: str):
        self._muc_class.user_nick = nick

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
            self.log.debug("MUC CLASS: %s", self._muc_class)

            self._mucs_by_legacy_id[legacy_id] = muc
            self._mucs_by_bare_jid[jid.bare] = muc
        return muc
