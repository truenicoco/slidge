from slixmpp import JID, CoroutineCallback, Iq, Message, Presence, StanzaPath
from slixmpp.exceptions import XMPPError

from ..util import DispatcherMixin, exceptions_to_xmpp_errors


class MucMiscMixin(DispatcherMixin):
    def __init__(self, xmpp):
        super().__init__(xmpp)
        xmpp.register_handler(
            CoroutineCallback(
                "ibr_remove", StanzaPath("/iq/register"), self.on_ibr_remove
            )
        )

        xmpp.add_event_handler("groupchat_join", self.on_groupchat_join)
        xmpp.add_event_handler(
            "groupchat_direct_invite", self.on_groupchat_direct_invite
        )
        xmpp.add_event_handler("groupchat_subject", self.on_groupchat_subject)

    @exceptions_to_xmpp_errors
    async def on_ibr_remove(self, iq: Iq):
        if iq.get_to() == self.xmpp.boundjid.bare:
            return

        session = await self._get_session(iq)
        session.raise_if_not_logged()

        if iq["type"] == "set" and iq["register"]["remove"]:
            muc = await session.bookmarks.by_jid(iq.get_to())
            await session.on_leave_group(muc.legacy_id)
            iq.reply().send()
            return

        raise XMPPError("feature-not-implemented")

    @exceptions_to_xmpp_errors
    async def on_groupchat_join(self, p: Presence):
        if not self.xmpp.GROUPS:
            raise XMPPError(
                "feature-not-implemented",
                "This gateway does not implement multi-user chats.",
            )
        session = await self._get_session(p)
        session.raise_if_not_logged()
        muc = await session.bookmarks.by_jid(p.get_to())
        await muc.join(p)

    @exceptions_to_xmpp_errors
    async def on_groupchat_direct_invite(self, msg: Message):
        session = await self._get_session(msg)
        session.raise_if_not_logged()

        invite = msg["groupchat_invite"]
        jid = JID(invite["jid"])

        if jid.domain != self.xmpp.boundjid.bare:
            raise XMPPError(
                "bad-request",
                "Legacy contacts can only be invited to legacy groups, not standard XMPP MUCs.",
            )

        if invite["password"]:
            raise XMPPError(
                "bad-request", "Password-protected groups are not supported"
            )

        contact = await session.contacts.by_jid(msg.get_to())
        muc = await session.bookmarks.by_jid(jid)

        await session.on_invitation(contact, muc, invite["reason"] or None)

    @exceptions_to_xmpp_errors
    async def on_groupchat_subject(self, msg: Message):
        session = await self._get_session(msg)
        session.raise_if_not_logged()
        muc = await session.bookmarks.by_jid(msg.get_to())
        if not muc.HAS_SUBJECT:
            raise XMPPError(
                "bad-request",
                "There are no room subject in here. "
                "Use the room configuration to update its name or description",
            )
        await muc.on_set_subject(msg["subject"])
