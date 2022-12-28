from mimetypes import guess_extension

import aiosignald.generated as sigapi


class AttachmentSenderMixin:
    async def send_attachments(
        self, attachments: list[sigapi.JsonAttachmentv1], legacy_msg_id: int, **kwargs
    ):
        last_attachment_i = len(attachments) - 1
        for i, attachment in enumerate(attachments):
            filename = get_filename(attachment)
            with open(attachment.storedFilename, "rb") as f:
                await self.send_file(  # type:ignore
                    filename=filename,
                    input_file=f,
                    content_type=attachment.contentType,
                    legacy_msg_id=legacy_msg_id if i == last_attachment_i else None,
                    caption=attachment.caption,
                    **kwargs,
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
