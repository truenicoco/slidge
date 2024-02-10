import asyncio
import logging
from typing import TYPE_CHECKING, Generic, Optional, Type

from slixmpp import JID
from slixmpp.jid import JID_UNESCAPE_TRANSFORMATIONS, _unescape_node

from ..core.mixins.lock import NamedLockMixin
from ..util import SubclassableOnce
from ..util.types import LegacyContactType, LegacyUserIdType
from .contact import LegacyContact

if TYPE_CHECKING:
    from ..core.session import BaseSession


class ContactIsUser(Exception):
    pass


class LegacyRoster(
    Generic[LegacyUserIdType, LegacyContactType],
    NamedLockMixin,
    metaclass=SubclassableOnce,
):
    """
    Virtual roster of a gateway user, that allows to represent all
    of their contacts as singleton instances (if used properly and not too bugged).

    Every :class:`.BaseSession` instance will have its own :class:`.LegacyRoster` instance
    accessible via the :attr:`.BaseSession.contacts` attribute.

    Typically, you will mostly use the :meth:`.LegacyRoster.by_legacy_id` function to
    retrieve a contact instance.

    You might need to override :meth:`.LegacyRoster.legacy_id_to_jid_username` and/or
    :meth:`.LegacyRoster.jid_username_to_legacy_id` to incorporate some custom logic
    if you need some characters when translation JID user parts and legacy IDs.
    """

    def __init__(self, session: "BaseSession"):
        self._contact_cls: Type[LegacyContactType] = (
            LegacyContact.get_self_or_unique_subclass()
        )
        self._contact_cls.xmpp = session.xmpp

        self.session = session
        self._contacts_by_bare_jid: dict[str, LegacyContactType] = {}
        self._contacts_by_legacy_id: dict[LegacyUserIdType, LegacyContactType] = {}
        self.log = logging.getLogger(f"{self.session.user.bare_jid}:roster")
        self.user_legacy_id: Optional[LegacyUserIdType] = None
        self.ready: asyncio.Future[bool] = self.session.xmpp.loop.create_future()
        super().__init__()

    def __repr__(self):
        return f"<Roster of {self.session.user}>"

    def __iter__(self):
        return iter(self._contacts_by_legacy_id.values())

    async def __finish_init_contact(
        self, legacy_id: LegacyUserIdType, jid_username: str, *args, **kwargs
    ):
        c = self._contact_cls(self.session, legacy_id, jid_username, *args, **kwargs)
        async with self.lock(("finish", c)):
            if legacy_id in self._contacts_by_legacy_id:
                self.log.debug("Already updated %s", c)
                return c
            await c.avatar_wrap_update_info()
            self._contacts_by_legacy_id[legacy_id] = c
            self._contacts_by_bare_jid[c.jid.bare] = c
        return c

    def known_contacts(self, only_friends=True) -> dict[str, LegacyContactType]:
        if only_friends:
            return {j: c for j, c in self._contacts_by_bare_jid.items() if c.is_friend}
        return self._contacts_by_bare_jid

    async def by_jid(self, contact_jid: JID) -> LegacyContactType:
        # """
        # Retrieve a contact by their JID
        #
        # If the contact was not instantiated before, it will be created
        # using :meth:`slidge.LegacyRoster.jid_username_to_legacy_id` to infer their
        # legacy user ID.
        #
        # :param contact_jid:
        # :return:
        # """
        username = contact_jid.node
        async with self.lock(("username", username)):
            bare = contact_jid.bare
            c = self._contacts_by_bare_jid.get(bare)
            if c is None:
                legacy_id = await self.jid_username_to_legacy_id(username)
                log.debug("Contact %s not found", contact_jid)
                if self.get_lock(("legacy_id", legacy_id)):
                    log.debug("Already updating %s", contact_jid)
                    return await self.by_legacy_id(legacy_id)
                c = await self.__finish_init_contact(legacy_id, username)
            return c

    async def by_legacy_id(
        self, legacy_id: LegacyUserIdType, *args, **kwargs
    ) -> LegacyContactType:
        """
        Retrieve a contact by their legacy_id

        If the contact was not instantiated before, it will be created
        using :meth:`slidge.LegacyRoster.legacy_id_to_jid_username` to infer their
        legacy user ID.

        :param legacy_id:
        :param args: arbitrary additional positional arguments passed to the contact constructor.
            Requires subclassing LegacyContact.__init__ to accept those.
            This is useful for networks where you fetch the contact list and information
            about these contacts in a single request
        :param kwargs: arbitrary keyword arguments passed to the contact constructor
        :return:
        """
        if legacy_id == self.user_legacy_id:
            raise ContactIsUser
        async with self.lock(("legacy_id", legacy_id)):
            c = self._contacts_by_legacy_id.get(legacy_id)
            if c is None:
                username = await self.legacy_id_to_jid_username(legacy_id)
                log.debug("Contact %s not found", legacy_id)
                if self.get_lock(("username", username)):
                    log.debug("Already updating %s", username)
                    jid = JID()
                    jid.node = username
                    jid.domain = self.session.xmpp.boundjid.bare
                    return await self.by_jid(jid)
                c = await self.__finish_init_contact(
                    legacy_id, username, *args, **kwargs
                )
            return c

    async def by_stanza(self, s) -> LegacyContact:
        # """
        # Retrieve a contact by the destination of a stanza
        #
        # See :meth:`slidge.Roster.by_legacy_id` for more info.
        #
        # :param s:
        # :return:
        # """
        return await self.by_jid(s.get_to())

    async def legacy_id_to_jid_username(self, legacy_id: LegacyUserIdType) -> str:
        """
        Convert a legacy ID to a valid 'user' part of a JID

        Should be overridden for cases where the str conversion of
        the legacy_id is not enough, e.g., if it is case-sensitive or contains
        forbidden characters not covered by :xep:`0106`.

        :param legacy_id:
        """
        return str(legacy_id).translate(ESCAPE_TABLE)

    async def jid_username_to_legacy_id(self, jid_username: str) -> LegacyUserIdType:
        """
        Convert a JID user part to a legacy ID.

        Should be overridden in case legacy IDs are not strings, or more generally
        for any case where the username part of a JID (unescaped with to the mapping
        defined by :xep:`0106`) is not enough to identify a contact on the legacy network.

        Default implementation is an identity operation

        :param jid_username: User part of a JID, ie "user" in "user@example.com"
        :return: An identifier for the user on the legacy network.
        """
        return _unescape_node(jid_username)

    async def fill(self):
        """
        Populate slidge's "virtual roster".

        Override this and in it, ``await self.by_legacy_id(contact_id)``
        for the every legacy contacts of the user for which you'd like to
        set an avatar, nickname, vcard…

        Await ``Contact.add_to_roster()`` in here to add the contact to the
        user's XMPP roster.
        """
        pass


ESCAPE_TABLE = "".maketrans({v: k for k, v in JID_UNESCAPE_TRANSFORMATIONS.items()})
log = logging.getLogger(__name__)
