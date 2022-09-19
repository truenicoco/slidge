import functools
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


class Roster(LegacyRoster[Contact, "Session"]):
    def by_json_address(self, address: sigapi.JsonAddressv1):
        return self.by_legacy_id(address.uuid)


log = logging.getLogger(__name__)
