import asyncio
import logging
import warnings
from typing import TYPE_CHECKING, AsyncIterator, Generic, Iterator, Optional, Type

from slixmpp import JID
from slixmpp.exceptions import IqError, IqTimeout, XMPPError
from slixmpp.jid import JID_UNESCAPE_TRANSFORMATIONS, _unescape_node

from ..core.mixins.lock import NamedLockMixin
from ..db.models import Contact
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
        self.__filling = False
        super().__init__()

    def __repr__(self):
        return f"<Roster of {self.session.user_jid}>"

    def __iter__(self) -> Iterator[LegacyContactType]:
        with self.__store.session():
            for stored in self.__store.get_all(user_pk=self.session.user_pk):
                yield self._contact_cls.from_store(self.session, stored)

    def known_contacts(self, only_friends=True) -> dict[str, LegacyContactType]:
        if only_friends:
            return {c.jid.bare: c for c in self if c.is_friend}
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
            legacy_id = await self.jid_username_to_legacy_id(username)
            log.debug("Contact %s not found", contact_jid)
            if self.get_lock(("legacy_id", legacy_id)):
                log.debug("Already updating %s", contact_jid)
                return await self.by_legacy_id(legacy_id)

            with self.__store.session():
                stored = self.__store.get_by_jid(self.session.user_pk, contact_jid)
                return await self.__update_contact(stored, legacy_id, username)

    def by_jid_only_if_exists(self, contact_jid: JID) -> LegacyContactType | None:
        with self.__store.session():
            stored = self.__store.get_by_jid(self.session.user_pk, contact_jid)
            if stored is not None and stored.updated:
                return self._contact_cls.from_store(self.session, stored)
        return None

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
            username = await self.legacy_id_to_jid_username(legacy_id)
            if self.get_lock(("username", username)):
                log.debug("Already updating %s", username)
                jid = JID()
                jid.node = username
                jid.domain = self.session.xmpp.boundjid.bare
                return await self.by_jid(jid)

            with self.__store.session():
                stored = self.__store.get_by_legacy_id(
                    self.session.user_pk, str(legacy_id)
                )
                return await self.__update_contact(
                    stored, legacy_id, username, *args, **kwargs
                )

    async def __update_contact(
        self,
        stored: Contact | None,
        legacy_id: LegacyUserIdType,
        username: str,
        *a,
        **kw,
    ) -> LegacyContactType:
        if stored is None:
            contact = self._contact_cls(self.session, legacy_id, username, *a, **kw)
        else:
            contact = self._contact_cls.from_store(self.session, stored, *a, **kw)
            if stored.updated:
                return contact

        try:
            with contact.updating_info():
                await contact.avatar_wrap_update_info()
        except XMPPError:
            raise
        except Exception as e:
            raise XMPPError("internal-server-error", str(e))
        contact._caps_ver = await contact.get_caps_ver(contact.jid)
        contact.contact_pk = self.__store.update(contact, commit=not self.__filling)
        return contact

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

    async def _fill(self):
        try:
            if hasattr(self.session.xmpp, "TEST_MODE"):
                # dirty hack to avoid mocking xmpp server replies to this
                # during tests
                raise PermissionError
            iq = await self.session.xmpp["xep_0356"].get_roster(
                self.session.user_jid.bare
            )
            user_roster = iq["roster"]["items"]
        except (PermissionError, IqError, IqTimeout):
            user_roster = None

        with self.__store.session() as orm:
            self.__filling = True
            async for contact in self.fill():
                if user_roster is None:
                    continue
                item = contact.get_roster_item()
                old = user_roster.get(contact.jid.bare)
                if old is not None and all(
                    old[k] == item[contact.jid.bare].get(k)
                    for k in ("subscription", "groups", "name")
                ):
                    self.log.debug("No need to update roster")
                    continue
                self.log.debug("Updating roster")
                try:
                    await self.session.xmpp["xep_0356"].set_roster(
                        self.session.user_jid.bare,
                        item,
                    )
                except (PermissionError, IqError, IqTimeout) as e:
                    warnings.warn(f"Could not add to roster: {e}")
                else:
                    contact._added_to_roster = True
            orm.commit()
        self.__filling = False

    async def fill(self) -> AsyncIterator[LegacyContact]:
        """
        Populate slidge's "virtual roster".

        This should yield contacts that are meant to be added to the user's
        roster, typically by using ``await self.by_legacy_id(contact_id)``.
        Setting the contact nicknames, avatar, etc. should be in
        :meth:`LegacyContact.update_info()`

        It's not mandatory to override this method, but it is recommended way
        to populate "friends" of the user. Calling
        ``await (await self.by_legacy_id(contact_id)).add_to_roster()``
        accomplishes the same thing, but doing it in here allows to batch
        DB queries and is better performance-wise.

        """
        return
        yield


ESCAPE_TABLE = "".maketrans({v: k for k, v in JID_UNESCAPE_TRANSFORMATIONS.items()})
log = logging.getLogger(__name__)
