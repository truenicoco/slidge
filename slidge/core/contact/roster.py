import logging
from typing import Generic, Type

from slixmpp import JID
from slixmpp.jid import JID_UNESCAPE_TRANSFORMATIONS, _unescape_node

from ...util import SubclassableOnce
from ...util.types import LegacyContactType, LegacyUserIdType, SessionType
from .contact import LegacyContact


class LegacyRoster(
    Generic[SessionType, LegacyContactType, LegacyUserIdType],
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

    def __init__(self, session: "SessionType"):
        self._contact_cls: Type[
            LegacyContactType
        ] = LegacyContact.get_self_or_unique_subclass()
        self._contact_cls.xmpp = session.xmpp

        self.session = session
        self._contacts_by_bare_jid: dict[str, LegacyContactType] = {}
        self._contacts_by_legacy_id: dict[LegacyUserIdType, LegacyContactType] = {}

    def __iter__(self):
        return iter(self._contacts_by_legacy_id.values())

    def known_contacts(self):
        return {
            j: c for j, c in self._contacts_by_bare_jid.items() if c.added_to_roster
        }

    async def by_jid(self, contact_jid: JID) -> LegacyContactType:
        """
        Retrieve a contact by their JID

        If the contact was not instantiated before, it will be created
        using :meth:`slidge.LegacyRoster.jid_username_to_legacy_id` to infer their
        legacy user ID.

        :param contact_jid:
        :return:
        """
        bare = contact_jid.bare
        c = self._contacts_by_bare_jid.get(bare)
        if c is None:
            jid_username = str(contact_jid.username)
            log.debug("Contact %s not found", contact_jid)
            c = self._contact_cls(
                self.session,
                await self.jid_username_to_legacy_id(jid_username),
                jid_username,
            )
            await c.update_caps()
            await c.update_info()
            self._contacts_by_legacy_id[c.legacy_id] = self._contacts_by_bare_jid[
                bare
            ] = c
        return c

    async def by_legacy_id(self, legacy_id: LegacyUserIdType) -> LegacyContactType:
        """
        Retrieve a contact by their legacy_id

        If the contact was not instantiated before, it will be created
        using :meth:`slidge.LegacyRoster.legacy_id_to_jid_username` to infer their
        legacy user ID.

        :param legacy_id:
        :return:
        """
        c = self._contacts_by_legacy_id.get(legacy_id)
        if c is None:
            log.debug("Contact %s not found in roster", legacy_id)
            c = self._contact_cls(
                self.session, legacy_id, await self.legacy_id_to_jid_username(legacy_id)
            )
            await c.update_caps()
            await c.update_info()
            self._contacts_by_bare_jid[c.jid.bare] = self._contacts_by_legacy_id[
                legacy_id
            ] = c
        return c

    async def by_stanza(self, s) -> LegacyContactType:
        """
        Retrieve a contact by the destination of a stanza

        See :meth:`slidge.Roster.by_legacy_id` for more info.

        :param s:
        :return:
        """
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


ESCAPE_TABLE = "".maketrans(
    {v: k for k, v in JID_UNESCAPE_TRANSFORMATIONS.items()}  # type:ignore
)
log = logging.getLogger(__name__)