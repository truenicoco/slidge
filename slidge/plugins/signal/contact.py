import logging
from typing import TYPE_CHECKING, Optional

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi
from slixmpp.exceptions import XMPPError

from slidge import *

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact["Session"]):
    CORRECTION = False

    def __init__(
        self,
        session: "Session",
        phone: str,
        jid_username: str,
    ):
        super().__init__(session, phone, jid_username)
        log.debug("JID: %s", self.jid_username)
        self._uuid: Optional[str] = None

    @property
    def phone(self):
        return self.legacy_id

    @phone.setter
    def phone(self, p):
        if p is not None:
            self.session.contacts.contacts_by_legacy_id[p] = self
            self.legacy_id = p
            self.jid_username = p

    @property
    def uuid(self):
        return self._uuid

    @uuid.setter
    def uuid(self, u: str):
        if u is not None:
            log.debug("UUID: %s, %s", u, self)
            self.session.contacts.contacts_by_uuid[u] = self
        self._uuid = u

    @property
    def signal_address(self):
        return sigapi.JsonAddressv1(number=self.phone, uuid=self.uuid)

    async def get_identities(self):
        s = await self.session.signal
        log.debug("%s, %s", type(self.session.phone), type(self.signal_address))
        try:
            r = await s.get_identities(
                account=self.session.phone,
                address=self.signal_address,
            )
        except sigexc.UnregisteredUserError:
            raise XMPPError("not-found")
        identities = r.identities
        self.session.send_gateway_message(str(identities))


class Roster(LegacyRoster[Contact, "Session"]):
    def __init__(self, session):
        super().__init__(session)
        self.contacts_by_uuid: dict[str, Contact] = {}
        self.contacts_by_legacy_id = self._contacts_by_legacy_id
        self.contacts_by_bare_jid = self._contacts_by_bare_jid

    def by_jid(self, contact_jid):
        if (c := self.contacts_by_legacy_id.get(contact_jid.user)) is None:
            return super().by_jid(contact_jid)
        else:
            return c

    def by_phone(self, phone: str):
        return self.by_legacy_id(phone)

    async def by_uuid(self, uuid: str):
        try:
            return self.contacts_by_uuid[uuid]
        except KeyError:
            address = await (await self.session.signal).resolve_address(
                account=self.session.phone, partial=sigapi.JsonAddressv1(uuid=uuid)
            )
            return await self.by_json_address(address)

    async def by_json_address(self, address: sigapi.JsonAddressv1):
        uuid = address.uuid
        phone = address.number

        if uuid is None and phone is None:
            raise TypeError(address)

        if uuid is None:
            return self.by_phone(phone)

        if phone is None:
            return await self.by_uuid(uuid)

        contact_phone = self._contacts_by_legacy_id.get(phone)
        contact_uuid = self.contacts_by_uuid.get(uuid)

        if contact_phone is None and contact_uuid is None:
            c = self.by_phone(phone)
            c.uuid = uuid
            return c

        if contact_phone is None and contact_uuid is not None:
            contact_uuid.phone = phone
            return contact_uuid

        if contact_uuid is None and contact_phone is not None:
            contact_phone.uuid = uuid
            return contact_phone

        if contact_phone is not contact_uuid:
            raise RuntimeError(address, contact_phone, contact_uuid)

        return contact_phone


log = logging.getLogger(__name__)
