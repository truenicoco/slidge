import functools
import logging
from mimetypes import guess_extension
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

    async def send_attachments(
        self,
        attachments: list[sigapi.JsonAttachmentv1],
        /,
        legacy_msg_id: int,
        reply_to_msg_id: int,
    ):
        for attachment in attachments:
            filename = get_filename(attachment)
            with open(attachment.storedFilename, "rb") as f:
                await self.send_file(
                    filename=filename,
                    input_file=f,
                    content_type=attachment.contentType,
                    legacy_msg_id=legacy_msg_id,
                    reply_to_msg_id=reply_to_msg_id,
                )


def get_filename(attachment: sigapi.JsonAttachmentv1):
    if f := attachment.customFilename:
        return f
    else:
        filename = attachment.id or "unnamed"
        ext = guess_extension(attachment.contentType)
        if ext is not None:
            filename += ext
        return filename


class Roster(LegacyRoster[Contact, "Session"]):
    def by_json_address(self, address: sigapi.JsonAddressv1):
        return self.by_legacy_id(address.uuid)


log = logging.getLogger(__name__)
