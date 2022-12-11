import functools
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi
from slixmpp.exceptions import XMPPError

from slidge import *

from .util import AttachmentSenderMixin

if TYPE_CHECKING:
    from .group import Participant
    from .session import Session


class Contact(AttachmentSenderMixin, LegacyContact["Session", str]):
    CORRECTION = False

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        # keys = msg timestamp; vals = single character emoji
        self.user_reactions = dict[int, str]()
        self.profile_fetched = False

    @functools.cached_property
    def signal_address(self):
        return sigapi.JsonAddressv1(uuid=self.legacy_id)

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

    async def update_info(self, profile: Optional[sigapi.Profilev1] = None):
        if profile is None:
            try:
                profile = await (await self.session.signal).get_profile(
                    account=self.session.phone, address=self.signal_address
                )
            except sigexc.ProfileUnavailableError as e:
                log.debug("Could not fetch the profile of a contact: %s", e.message)
                return
            self.profile_fetched = True
        nick = profile.name or profile.profile_name
        if nick is not None:
            nick = nick.replace("\u0000", " ")
            self.name = nick
        if profile.avatar is not None:
            self.avatar = Path(profile.avatar)

        address = await (await self.session.signal).resolve_address(
            account=self.session.phone,
            partial=sigapi.JsonAddressv1(uuid=self.legacy_id),
        )

        self.set_vcard(full_name=nick, phone=address.number, note=profile.about)

    async def update_and_add(self):
        await self.update_info()
        await self.add_to_roster()


class Roster(LegacyRoster["Session", Contact, str]):
    async def by_uuid(self, uuid: str):
        return await self.by_json_address(sigapi.JsonAddressv1(uuid=uuid))

    async def by_json_address(self, address: sigapi.JsonAddressv1):
        c = await self.by_legacy_id(address.uuid)
        if not c.added_to_roster or not c.profile_fetched:
            await c.update_and_add()
        return c

    # @staticmethod
    async def jid_username_to_legacy_id(self, jid_username: str):
        if jid_username in self.session.bookmarks.known_groups:
            raise XMPPError("bad-request", "This is a group ID, not a contact ID")


log = logging.getLogger(__name__)
