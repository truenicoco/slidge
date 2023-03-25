from datetime import datetime
from mimetypes import guess_extension
from typing import TYPE_CHECKING, Optional

import aiosignald.generated as sigapi

from slidge.core.mixins.message import ContentMessageMixin
from slidge.util.types import LegacyAttachment, MessageReference

if TYPE_CHECKING:
    from .group import MUC
    from .session import Session


class AttachmentSenderMixin(ContentMessageMixin):
    muc: "MUC"
    session: "Session"

    async def __get_reference(self, quote: Optional[sigapi.JsonQuotev1]):
        if quote is None:
            return

        reply_to = MessageReference(
            legacy_id=quote.id,
            body=quote.text,
        )
        if muc := getattr(self, "muc", None):
            reply_to.author = await muc.get_participant_by_legacy_id(quote.author.uuid)
        else:
            reply_to.author = await self.session.contacts.by_json_address(quote.author)

        return reply_to

    async def send_signal_msg(self, data: sigapi.JsonDataMessagev1, carbon=False):
        await self.send_files(
            attachments=[Attachment.from_json(a) for a in data.attachments],
            legacy_msg_id=data.timestamp,
            when=datetime.fromtimestamp(data.timestamp / 1000),
            reply_to=await self.__get_reference(data.quote),
            carbon=carbon,
            body=data.body,
        )
        if (reaction := data.reaction) is not None:
            if reaction.remove:
                self.react(reaction.targetSentTimestamp, carbon=carbon)
            else:
                self.react(
                    reaction.targetSentTimestamp, [reaction.emoji], carbon=carbon
                )
        if (delete := data.remoteDelete) is not None:
            self.retract(delete.target_sent_timestamp, carbon=carbon)


class Attachment(LegacyAttachment):
    @staticmethod
    def from_json(json: sigapi.JsonAttachmentv1):
        return Attachment(
            name=get_filename(json),
            path=json.storedFilename,
            content_type=json.contentType,
            legacy_file_id=json.key,
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
