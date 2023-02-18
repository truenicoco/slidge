from typing import TYPE_CHECKING

from maufbapi.types import mqtt as mqtt_t
from maufbapi.types.graphql import ParticipantNode, Thread

from slidge import LegacyContact, LegacyRoster, XMPPError

from .util import is_group_thread

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact["Session", int]):
    CORRECTION = False
    REACTIONS_SINGLE_EMOJI = True

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

    async def send_fb_sticker(self, sticker_id: int, legacy_msg_id: str):
        resp = await self.session.api.fetch_stickers([sticker_id])
        await self.send_file(
            file_url=resp.nodes[0].preview_image.uri,
            legacy_file_id=f"sticker-{sticker_id}",
            legacy_msg_id=legacy_msg_id,
        )

    async def update_info(self):
        t = await self.get_thread(msg_count=0)

        participant = self.session.contacts.get_friend_participant(
            t.all_participants.nodes
        )
        await self.populate_from_participant(participant)


class Roster(LegacyRoster["Session", Contact, int]):
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
