import asyncio
import functools
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from uuid import UUID

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi

from slidge import LegacyContact, LegacyRoster, XMPPError

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
                if (
                    profile.name
                    or profile.profile_name  # in theory .name would be enough but
                    or profile.contact_name  # I think this is a signald bug...
                    or profile.address.number
                ):
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
            nick = (
                profile.profile_name or profile.contact_name or profile.address.number
            )
        else:
            nick = (
                profile.contact_name or profile.profile_name or profile.address.number
            )
        if nick is not None:
            nick = nick.replace("\u0000", " ")
            self.name = nick
        if profile.avatar is not None:
            path = Path(profile.avatar)
            await self.set_avatar(path, path.name)

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
        if not is_valid_uuid(jid_username):
            raise XMPPError(
                "bad-request",
                (
                    f"The identifier {jid_username} is not a valid signal account"
                    " identifier"
                ),
            )

        check = (await self.session.signal).is_identifier_registered(
            account=self.session.phone, identifier=jid_username
        )
        if (await check).value:
            return jid_username
        else:
            raise XMPPError(
                "item-not-found", f"No account identified by {jid_username}"
            )

    async def fill(self):
        session = self.session
        profiles = await (await session.signal).list_contacts(account=session.phone)
        for profile in profiles.profiles:
            # contacts are added automatically if their profile could be resolved
            try:
                await self.by_json_address(profile.address)
            except XMPPError as e:
                self.log.warning(
                    "Something is wrong the signald contact: %s", profile, exc_info=e
                )


# from https://stackoverflow.com/a/33245493/5902284
def is_valid_uuid(uuid_to_test, version=4):
    """
    Check if uuid_to_test is a valid UUID.

     Parameters
    ----------
    uuid_to_test : str
    version : {1, 2, 3, 4}

     Returns
    -------
    `True` if uuid_to_test is a valid UUID, otherwise `False`.

     Examples
    --------
    >>> is_valid_uuid('c9bf9e57-1685-4c89-bafb-ff5af830be8a')
    True
    >>> is_valid_uuid('c9bf9e58')
    False
    """

    try:
        uuid_obj = UUID(uuid_to_test, version=version)
    except ValueError:
        return False
    return str(uuid_obj) == uuid_to_test


log = logging.getLogger(__name__)
