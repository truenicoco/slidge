import abc
import logging
from typing import TYPE_CHECKING, Generic, Type

from slixmpp import JID
from slixmpp.jid import _unescape_node

from ..contact.roster import ESCAPE_TABLE
from ..core.mixins.lock import NamedLockMixin
from ..util import SubclassableOnce
from ..util.types import LegacyGroupIdType, LegacyMUCType
from .room import LegacyMUC

if TYPE_CHECKING:
    from slidge.core.session import BaseSession


class LegacyBookmarks(
    Generic[LegacyGroupIdType, LegacyMUCType],
    NamedLockMixin,
    metaclass=SubclassableOnce,
):
    """
    This is instantiated once per :class:`~slidge.BaseSession`
    """

    def __init__(self, session: "BaseSession"):
        self.session = session
        self.xmpp = session.xmpp
        self.user = session.user

        self._mucs_by_legacy_id = dict[LegacyGroupIdType, LegacyMUCType]()
        self._mucs_by_bare_jid = dict[str, LegacyMUCType]()

        self._muc_class: Type[LegacyMUCType] = LegacyMUC.get_self_or_unique_subclass()

        self._user_nick: str = self.session.user.jid.node

        super().__init__()
        self.log = logging.getLogger(f"{self.user.bare_jid}:bookmarks")
        self.ready = self.session.xmpp.loop.create_future()
        if not self.xmpp.GROUPS:
            self.ready.set_result(True)

    @property
    def user_nick(self):
        return self._user_nick

    @user_nick.setter
    def user_nick(self, nick: str):
        self._user_nick = nick

    def __iter__(self):
        return iter(self._mucs_by_legacy_id.values())

    def __repr__(self):
        return f"<Bookmarks of {self.user}>"

    async def __finish_init_muc(self, legacy_id: LegacyGroupIdType, jid: JID):
        muc = self._muc_class(self.session, legacy_id=legacy_id, jid=jid)
        await muc.avatar_wrap_update_info()
        if not muc.user_nick:
            muc.user_nick = self._user_nick
        self.log.debug("MUC created: %r", muc)
        self._mucs_by_legacy_id[muc.legacy_id] = muc
        self._mucs_by_bare_jid[muc.jid.bare] = muc
        return muc

    async def legacy_id_to_jid_local_part(self, legacy_id: LegacyGroupIdType):
        return await self.legacy_id_to_jid_username(legacy_id)

    async def jid_local_part_to_legacy_id(self, local_part: str):
        return await self.jid_username_to_legacy_id(local_part)

    async def legacy_id_to_jid_username(self, legacy_id: LegacyGroupIdType):
        """
        The default implementation calls ``str()`` on the legacy_id and
        escape characters according to :xep:`0106`.

        You can override this class and implement a more subtle logic to raise
        an :class:`~slixmpp.exceptions.XMPPError` early

        :param legacy_id:
        :return:
        """
        return str(legacy_id).translate(ESCAPE_TABLE)

    async def jid_username_to_legacy_id(self, username: str):
        """

        :param username:
        :return:
        """
        return _unescape_node(username)

    async def by_jid(self, jid: JID) -> LegacyMUCType:
        bare = jid.bare
        async with self.lock(("bare", bare)):
            muc = self._mucs_by_bare_jid.get(bare)
            if muc is None:
                self.log.debug("Attempting to instantiate a new MUC for JID %s", jid)
                local_part = jid.node
                legacy_id = await self.jid_local_part_to_legacy_id(local_part)
                if self.get_lock(("legacy_id", legacy_id)):
                    self.log.debug("Not instantiating %s after all", jid)
                    return await self.by_legacy_id(legacy_id)
                self.log.debug("%r is group %r", local_part, legacy_id)
                muc = await self.__finish_init_muc(legacy_id, JID(bare))
            else:
                self.log.trace("Found an existing MUC instance: %s", muc)  # type:ignore
            return muc

    async def by_legacy_id(self, legacy_id: LegacyGroupIdType) -> LegacyMUCType:
        async with self.lock(("legacy_id", legacy_id)):
            muc = self._mucs_by_legacy_id.get(legacy_id)
            if muc is None:
                self.log.debug("Create new MUC instance for legacy ID %s", legacy_id)
                local = await self.legacy_id_to_jid_local_part(legacy_id)
                bare = f"{local}@{self.xmpp.boundjid}"
                jid = JID(bare)
                if self.get_lock(("bare", bare)):
                    self.log.debug("Not instantiating %s after all", legacy_id)
                    return await self.by_jid(jid)
                muc = await self.__finish_init_muc(legacy_id, jid)
            else:
                self.log.trace("Found an existing MUC instance: %s", muc)  # type:ignore

            return muc

    @abc.abstractmethod
    async def fill(self):
        """
        Establish a user's known groups.

        This has to be overridden in plugins with group support and at the
        minimum, this should ``await self.by_legacy_id(group_id)`` for all
        the groups a user is part of.

        Slidge internals will call this on successful :meth:`BaseSession.login`

        """
        if self.xmpp.GROUPS:
            raise NotImplementedError(
                "The plugin advertised support for groups but"
                " LegacyBookmarks.fill() was not overridden."
            )

    def remove(self, muc: LegacyMUC):
        try:
            del self._mucs_by_legacy_id[muc.legacy_id]
        except KeyError:
            self.log.warning("Removed a MUC that we didn't store by legacy ID")
        try:
            del self._mucs_by_bare_jid[muc.jid.bare]
        except KeyError:
            self.log.warning("Removed a MUC that we didn't store by JID")
        for part in muc._participants_by_contacts.values():
            try:
                part.contact.participants.remove(part)
            except KeyError:
                part.log.warning(
                    "That participant wasn't stored in the contact's participants attribute"
                )
