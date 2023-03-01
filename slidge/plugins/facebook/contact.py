import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Optional

from maufbapi.types import mqtt as mqtt_t
from maufbapi.types.graphql import ParticipantNode, Thread
from maufbapi.types.mqtt.message import PresenceInfo

from slidge import LegacyContact, LegacyRoster, XMPPError

from .util import is_group_thread

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact[int]):
    CORRECTION = False
    REACTIONS_SINGLE_EMOJI = True
    session: "Session"

    AWAY_AFTER = datetime.timedelta(minutes=5)
    XA_AFTER = datetime.timedelta(hours=12)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._online_expire_task: Optional[asyncio.Task] = None

    async def _expire(self, last_seen: datetime.datetime):
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        elapsed = (now - last_seen).seconds
        await asyncio.sleep(self.AWAY_AFTER.seconds - elapsed)
        self.away(last_seen=last_seen)
        await asyncio.sleep((self.XA_AFTER - self.AWAY_AFTER).seconds)
        self.extended_away(last_seen=last_seen)

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
        threads = await self.session.api.fetch_thread_info(self.legacy_id, **kwargs)
        if len(threads) != 1:
            self.log.debug("Could not determine my profile! %s", threads)
            raise XMPPError(
                "internal-server-error",
                f"The messenger API returned {len(threads)} threads for this user.",
            )
        return threads[0]

    async def send_fb_sticker(self, sticker_id: int, legacy_msg_id: str, **kwargs):
        resp = await self.session.api.fetch_stickers([sticker_id])
        await self.send_file(
            file_url=resp.nodes[0].preview_image.uri,
            legacy_file_id=f"sticker-{sticker_id}",
            legacy_msg_id=legacy_msg_id,
            **kwargs,
        )

    async def update_info(self, refresh=False):
        if self.name and not refresh:
            return
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

    def update_presence(self, presence: PresenceInfo):
        # presence.status is not about being away or online.
        # possibly related to the which app is used? (web, android...)

        if (t := self._online_expire_task) and not t.done():
            t.cancel()

        last_seen = datetime.datetime.fromtimestamp(
            presence.last_seen, tz=datetime.timezone.utc
        )
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        how_long = now - last_seen
        if how_long < self.AWAY_AFTER:
            self.online(last_seen=last_seen)
            self._online_expire_task = self.xmpp.loop.create_task(
                self._expire(last_seen)
            )
        elif how_long < self.XA_AFTER:
            self.away(last_seen=last_seen)
        else:
            self.extended_away(last_seen=last_seen)


class Roster(LegacyRoster[int, Contact]):
    session: "Session"

    async def by_legacy_id_if_known(self, legacy_id: int):
        if legacy_id in self._contacts_by_legacy_id:
            return self.by_legacy_id(legacy_id)

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
