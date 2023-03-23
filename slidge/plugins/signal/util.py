from datetime import datetime
from mimetypes import guess_extension
from typing import TYPE_CHECKING, Optional

import aiosignald.generated as sigapi

from slidge.core.mixins.message import ContentMessageMixin
from slidge.util.types import MessageReference

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

    async def send_attachments(
        self,
        attachments: list[sigapi.JsonAttachmentv1],
        legacy_msg_id: Optional[int],
        **kwargs,
    ):
        last_attachment_i = len(attachments) - 1
        for i, attachment in enumerate(attachments):
            filename = get_filename(attachment)
            await self.send_file(
                file_name=filename,
                file_path=attachment.storedFilename,
                content_type=attachment.contentType,
                legacy_msg_id=legacy_msg_id if i == last_attachment_i else None,
                caption=attachment.caption,
                legacy_file_id=attachment.key,
                **kwargs,
            )

    async def send_signal_msg(self, data: sigapi.JsonDataMessagev1):
        msg_id = data.timestamp
        text = data.body
        when = datetime.fromtimestamp(data.timestamp / 1000)
        reply_to = await self.__get_reference(data.quote)
        await self.send_attachments(
            data.attachments,
            legacy_msg_id=None if text else msg_id,
            when=when,
            reply_to=reply_to,
        )
        if text:
            self.send_text(
                body=text, legacy_msg_id=msg_id, when=when, reply_to=reply_to
            )
        if (reaction := data.reaction) is not None:
            if reaction.remove:
                self.react(reaction.targetSentTimestamp)
            else:
                self.react(reaction.targetSentTimestamp, reaction.emoji)
        if (delete := data.remoteDelete) is not None:
            self.retract(delete.target_sent_timestamp)


def get_filename(attachment: sigapi.JsonAttachmentv1):
    if f := attachment.customFilename:
        return f
    else:
        filename = attachment.id or "unnamed"
        ext = guess_extension(attachment.contentType)
        if ext is not None:
            filename += ext
        return filename
