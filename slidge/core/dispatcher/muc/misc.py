import logging

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
        xmpp.add_event_handler("groupchat_message_error", self.__on_group_chat_error)

    async def __on_group_chat_error(self, msg: Message):
        condition = msg["error"].get_condition()
        if condition not in KICKABLE_ERRORS:
            return

        try:
            muc = await self.get_muc_from_stanza(msg)
        except XMPPError as e:
            log.debug("Not removing resource", exc_info=e)
            return
        mfrom = msg.get_from()
        resource = mfrom.resource
        try:
            muc.remove_user_resource(resource)
        except KeyError:
            # this actually happens quite frequently on for both beagle and monal
            # (not sure why?), but is of no consequence
            log.debug("%s was not in the resources of %s", resource, muc)
        else:
            log.info(
                "Removed %s from the resources of %s because of error", resource, muc
            )

    @exceptions_to_xmpp_errors
    async def on_ibr_remove(self, iq: Iq):
        if iq.get_to() == self.xmpp.boundjid.bare:
            return

        if iq["type"] == "set" and iq["register"]["remove"]:
            muc = await self.get_muc_from_stanza(iq)
            await muc.session.on_leave_group(muc.legacy_id)
            iq.reply().send()
            await muc.session.bookmarks.remove(
                muc, "You left this chat from an XMPP client."
            )
            return

        raise XMPPError("feature-not-implemented")

    @exceptions_to_xmpp_errors
    async def on_groupchat_join(self, p: Presence):
        if not self.xmpp.GROUPS:
            raise XMPPError(
                "feature-not-implemented",
                "This gateway does not implement multi-user chats.",
            )
        muc = await self.get_muc_from_stanza(p)
        await muc.join(p)

    @exceptions_to_xmpp_errors
    async def on_groupchat_direct_invite(self, msg: Message):
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

        session = await self._get_session(msg, logged=True)
        contact = await session.contacts.by_jid(msg.get_to())
        muc = await session.bookmarks.by_jid(jid)

        await session.on_invitation(contact, muc, invite["reason"] or None)

    @exceptions_to_xmpp_errors
    async def on_groupchat_subject(self, msg: Message):
        muc = await self.get_muc_from_stanza(msg)
        if not muc.HAS_SUBJECT:
            raise XMPPError(
                "bad-request",
                "There are no room subject in here. "
                "Use the room configuration to update its name or description",
            )
        await muc.on_set_subject(msg["subject"])


KICKABLE_ERRORS = {
    "gone",
    "internal-server-error",
    "item-not-found",
    "jid-malformed",
    "recipient-unavailable",
    "redirect",
    "remote-server-not-found",
    "remote-server-timeout",
    "service-unavailable",
    "malformed error",
}

log = logging.getLogger(__name__)
