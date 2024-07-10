import asyncio
import logging
from typing import TYPE_CHECKING, Generic, Iterator, Optional, Type

from slixmpp import JID
from slixmpp.jid import JID_UNESCAPE_TRANSFORMATIONS, _unescape_node

from ..core.mixins.lock import NamedLockMixin
from ..db.store import ContactStore
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
        self.__store: ContactStore = session.xmpp.store.contacts

        self.session = session
        self.log = logging.getLogger(f"{self.session.user_jid.bare}:roster")
        self.user_legacy_id: Optional[LegacyUserIdType] = None
        self.ready: asyncio.Future[bool] = self.session.xmpp.loop.create_future()
        super().__init__()

    def __repr__(self):
        return f"<Roster of {self.session.user_jid}>"

    def __iter__(self) -> Iterator[LegacyContactType]:
        with self.__store.session():
            for stored in self.__store.get_all(user_pk=self.session.user_pk):
                yield self._contact_cls.from_store(self.session, stored)

    async def __finish_init_contact(
        self, legacy_id: LegacyUserIdType, jid_username: str, *args, **kwargs
    ):
        c = self._contact_cls(self.session, legacy_id, jid_username, *args, **kwargs)
        async with self.lock(("finish", c.legacy_id)):
            with self.__store.session():
                stored = self.__store.get_by_legacy_id(
                    self.session.user_pk, str(legacy_id)
                )
                if stored is not None and stored.updated:
                    self.log.debug("Already updated %s", c)
                    return self._contact_cls.from_store(self.session, stored)
                c.contact_pk = self.__store.add(
                    self.session.user_pk, c.legacy_id, c.jid
                )
                await c.avatar_wrap_update_info()
                self.__store.update(c)
        return c

    def known_contacts(self, only_friends=True) -> dict[str, LegacyContactType]:
        if only_friends:
            return {j: c for j, c in self if c.is_friend}  # type:ignore
        return {c.jid.bare: c for c in self}

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
            with self.__store.session():
                stored = self.__store.get_by_jid(self.session.user_pk, contact_jid)
                if stored is not None and stored.updated:
                    return self._contact_cls.from_store(self.session, stored)

            legacy_id = await self.jid_username_to_legacy_id(username)
            log.debug("Contact %s not found", contact_jid)
            if self.get_lock(("legacy_id", legacy_id)):
                log.debug("Already updating %s", contact_jid)
                return await self.by_legacy_id(legacy_id)
            return await self.__finish_init_contact(legacy_id, username)

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
            with self.__store.session():
                stored = self.__store.get_by_legacy_id(
                    self.session.user_pk, str(legacy_id)
                )
                if stored is not None and stored.updated:
                    return self._contact_cls.from_store(
                        self.session, stored, *args, **kwargs
                    )

            username = await self.legacy_id_to_jid_username(legacy_id)
            log.debug("Contact %s not found", legacy_id)
            if self.get_lock(("username", username)):
                log.debug("Already updating %s", username)
                jid = JID()
                jid.node = username
                jid.domain = self.session.xmpp.boundjid.bare
                return await self.by_jid(jid)
            return await self.__finish_init_contact(
                legacy_id, username, *args, **kwargs
            )

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
