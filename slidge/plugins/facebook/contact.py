import logging
from typing import TYPE_CHECKING

from maufbapi.types import mqtt as mqtt_t
from maufbapi.types.graphql import ParticipantNode, Thread

from slidge import LegacyContact, LegacyRoster, XMPPError

from .util import is_group_thread

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact[int]):
    CORRECTION = False
    REACTIONS_SINGLE_EMOJI = True
    session: "Session"

    async def populate_from_participant(
        self, participant: ParticipantNode, update_avatar=True
    ):
        if self.legacy_id != int(participant.messaging_actor.id):
            raise XMPPError(
                "bad-request",
                f"Legacy ID {self.legacy_id} does not match participant {participant.messaging_actor.id}",
            )
        self.name = participant.messaging_actor.name
        if self.avatar is None or update_avatar:
            self.avatar = participant.messaging_actor.profile_pic_large.uri

    async def get_thread(self, **kwargs):
        return (await self.session.api.fetch_thread_info(self.legacy_id, **kwargs))[0]

    async def send_fb_sticker(self, sticker_id: int, legacy_msg_id: str, **kwargs):
        resp = await self.session.api.fetch_stickers([sticker_id])
        await self.send_file(
            file_url=resp.nodes[0].preview_image.uri,
            legacy_file_id=f"sticker-{sticker_id}",
            legacy_msg_id=legacy_msg_id,
            **kwargs,
        )

    async def update_info(self):
        t = await self.get_thread(msg_count=0)

        participant = self.session.contacts.get_friend_participant(
            t.all_participants.nodes
        )
        await self.populate_from_participant(participant)

    async def send_fb_message(self, msg: mqtt_t.Message, **kwargs):
        meta = msg.metadata
        kwargs["legacy_msg_id"] = msg.metadata.id

        sticker = msg.sticker
        if sticker is not None:
            return await self.send_fb_sticker(sticker, **kwargs)

        if msg.attachments:
            return await self.send_fb_attachment(msg, **kwargs)

        text = msg.text

        if text:
            self.send_text(text, **kwargs)

    async def send_fb_attachment(self, msg, **kwargs):
        attachments = msg.attachments
        meta = msg.metadata
        thread_key = meta.thread
        msg_id = meta.id
        last_attachment_i = len(attachments) - 1
        text = msg.text
        carbon = kwargs.pop("carbon", False)
        for i, a in enumerate(attachments):
            last = i == last_attachment_i
            url = await self.get_attachment_url(a, thread_key, msg_id)
            if url is None:
                log.warning("Unhandled attachment: %s", a)
                self.send_text(
                    "/me sent an attachment that slidge does not support", carbon=carbon
                )
                continue
            await self.send_file(
                file_name=a.file_name,
                content_type=a.mime_type,
                file_url=url,
                caption=text if last else None,
                legacy_file_id=a.media_id,
                carbon=carbon,
                **(kwargs if last else {}),
            )

    async def get_attachment_url(
        self, attachment: mqtt_t.Attachment, thread_key, msg_id
    ):
        try:
            if v := attachment.video_info:
                return v.download_url
            if a := attachment.audio_info:
                return a.url
            if i := attachment.image_info:
                return i.uri_map.get(0)
        except AttributeError:
            media_id = getattr(attachment, "media_id", None)
            if media_id:
                return await self.session.api.get_file_url(
                    thread_key.thread_fbid or thread_key.other_user_id,
                    msg_id,
                    media_id,
                )


class Roster(LegacyRoster[int, Contact]):
    session: "Session"

    async def by_thread_key(self, t: mqtt_t.ThreadKey):
        if is_group_thread(t):
            raise ValueError("Thread seems to be a group thread")
        return await self.by_legacy_id(t.other_user_id)

    async def by_thread(self, t: Thread):
        if t.is_group_thread:
            raise XMPPError(
                "bad-request", f"Legacy ID {t.id} is a group chat, not a contact"
            )

        participant = self.get_friend_participant(t.all_participants.nodes)
        contact = await self.by_legacy_id(int(participant.messaging_actor.id))
        await contact.populate_from_participant(participant)
        return contact

    def get_friend_participant(self, nodes: list[ParticipantNode]) -> ParticipantNode:
        if len(nodes) != 2:
            raise XMPPError(
                "internal-server-error",
                "This facebook thread has more than two participants. This is a slidge bug.",
            )

        for participant in nodes:
            if int(participant.id) != self.session.my_id:
                return participant
        else:
            raise XMPPError(
                "internal-server-error", "Couldn't find friend in thread participants"
            )


log = logging.getLogger(__name__)
