import asyncio
import functools
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi

from slidge import *

from . import config
from .util import AttachmentSenderMixin

if TYPE_CHECKING:
    # from .group import Participant
    from .session import Session


class Contact(AttachmentSenderMixin, LegacyContact[str]):
    CORRECTION = False
    REACTIONS_SINGLE_EMOJI = True
    session: "Session"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        # keys = msg timestamp; vals = single character emoji
        self.user_reactions = dict[int, str]()

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
            raise XMPPError("item-not-found")
        identities = r.identities
        self.session.send_gateway_message(str(identities))

    async def get_profile(self, max_attempts=10, sleep=1, exp=2):
        attempts = 0
        while attempts < max_attempts:
            try:
                profile = await (await self.session.signal).get_profile(
                    account=self.session.phone, address=self.signal_address
                )
            except sigexc.ProfileUnavailableError as e:
                log.debug(
                    "Could not fetch the profile of a contact: %s, retrying later...",
                    e.message,
                )
            else:
                if profile.profile_name or profile.name or profile.contact_name:
                    return profile
            attempts += 1
            await asyncio.sleep(sleep * attempts**exp)

    async def update_info(self, profile: Optional[sigapi.Profilev1] = None):
        if profile is None:
            profile = await self.get_profile()
            if profile is None:
                log.warning(
                    "Could not update avatar, nickname, and phone of %s",
                    self.signal_address,
                )
                return

        if config.PREFER_PROFILE_NAME:
            nick = profile.profile_name or profile.contact_name
        else:
            nick = profile.contact_name or profile.profile_name
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
        await self.add_to_roster()
        self.online()


class Roster(LegacyRoster[str, Contact]):
    session: "Session"

    async def by_uuid(self, uuid: str):
        return await self.by_json_address(sigapi.JsonAddressv1(uuid=uuid))

    async def by_json_address(self, address: sigapi.JsonAddressv1):
        return await self.by_legacy_id(address.uuid)

    async def jid_username_to_legacy_id(self, jid_username: str):
        check = (await self.session.signal).is_identifier_registered(
            account=self.session.phone, identifier=jid_username
        )
        try:
            if (await check).value:
                return jid_username
            else:
                raise XMPPError(
                    "item-not-found", f"No account identified by {jid_username}"
                )
        except sigexc.IllegalArgumentException:
            raise XMPPError(
                "bad-request",
                f"The identifier {jid_username} is not a valid signal account identifier",
            )

    async def fill(self):
        session = self.session
        profiles = await (await session.signal).list_contacts(account=session.phone)
        for profile in profiles.profiles:
            # contacts are added automatically if their profile could be resolved
            await self.by_json_address(profile.address)


log = logging.getLogger(__name__)
