import logging
from copy import copy
from xml.etree import ElementTree

from slixmpp import JID, Message
from slixmpp.exceptions import XMPPError

from ....contact.contact import LegacyContact
from ....group.participant import LegacyParticipant
from ....group.room import LegacyMUC
from ....util.types import LinkPreview, Recipient
from ....util.util import dict_to_named_tuple, remove_emoji_variation_selector_16
from ... import config
from ...session import BaseSession
from ..util import DispatcherMixin, exceptions_to_xmpp_errors


class MessageContentMixin(DispatcherMixin):
    def __init__(self, xmpp):
        super().__init__(xmpp)
        xmpp.add_event_handler("legacy_message", self.on_legacy_message)
        xmpp.add_event_handler("message_correction", self.on_message_correction)
        xmpp.add_event_handler("message_retract", self.on_message_retract)
        xmpp.add_event_handler("groupchat_message", self.on_groupchat_message)
        xmpp.add_event_handler("reactions", self.on_reactions)

    async def on_groupchat_message(self, msg: Message) -> None:
        await self.on_legacy_message(msg)

    @exceptions_to_xmpp_errors
    async def on_legacy_message(self, msg: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param msg:
        :return:
        """
        # we MUST not use `if m["replace"]["id"]` because it adds the tag if not
        # present. this is a problem for MUC echoed messages
        if msg.get_plugin("replace", check=True) is not None:
            # ignore last message correction (handled by a specific method)
            return
        if msg.get_plugin("apply_to", check=True) is not None:
            # ignore message retraction (handled by a specific method)
            return
        if msg.get_plugin("reactions", check=True) is not None:
            # ignore message reaction fallback.
            # the reaction itself is handled by self.react_from_msg().
            return
        if msg.get_plugin("retract", check=True) is not None:
            # ignore message retraction fallback.
            # the retraction itself is handled by self.on_retract
            return
        cid = None
        if msg.get_plugin("html", check=True) is not None:
            body = ElementTree.fromstring("<body>" + msg["html"].get_body() + "</body>")
            p = body.findall("p")
            if p is not None and len(p) == 1:
                if p[0].text is None or not p[0].text.strip():
                    images = p[0].findall("img")
                    if len(images) == 1:
                        # no text, single img ⇒ this is a sticker
                        # other cases should be interpreted as "custom emojis" in text
                        src = images[0].get("src")
                        if src is not None and src.startswith("cid:"):
                            cid = src.removeprefix("cid:")

        session, entity, thread = await self._get_session_entity_thread(msg)

        if msg.get_plugin("oob", check=True) is not None:
            url = msg["oob"]["url"]
        else:
            url = None

        if msg.get_plugin("reply", check=True):
            text, reply_to_msg_id, reply_to, reply_fallback = await self.__get_reply(
                msg, session, entity
            )
        else:
            text = msg["body"]
            reply_to_msg_id = None
            reply_to = None
            reply_fallback = None

        if msg.get_plugin("link_previews", check=True):
            link_previews = [
                dict_to_named_tuple(p, LinkPreview) for p in msg["link_previews"]
            ]
        else:
            link_previews = []

        if url:
            legacy_msg_id = await self.__send_url(
                url,
                session,
                entity,
                reply_to_msg_id=reply_to_msg_id,
                reply_to_fallback_text=reply_fallback,
                reply_to=reply_to,
                thread=thread,
            )
        elif cid:
            legacy_msg_id = await self.__send_bob(
                msg.get_from(),
                cid,
                session,
                entity,
                reply_to_msg_id=reply_to_msg_id,
                reply_to_fallback_text=reply_fallback,
                reply_to=reply_to,
                thread=thread,
            )
        elif text:
            if isinstance(entity, LegacyMUC):
                mentions = {"mentions": await entity.parse_mentions(text)}
            else:
                mentions = {}
            legacy_msg_id = await session.on_text(
                entity,
                text,
                reply_to_msg_id=reply_to_msg_id,
                reply_to_fallback_text=reply_fallback,
                reply_to=reply_to,
                thread=thread,
                link_previews=link_previews,
                **mentions,
            )
        else:
            log.debug("Ignoring %s", msg.get_id())
            return

        if isinstance(entity, LegacyMUC):
            await entity.echo(msg, legacy_msg_id)
            if legacy_msg_id is not None:
                self.xmpp.store.sent.set_group_message(
                    session.user_pk, str(legacy_msg_id), msg.get_id()
                )
        else:
            self.__ack(msg)
            if legacy_msg_id is not None:
                self.xmpp.store.sent.set_message(
                    session.user_pk, str(legacy_msg_id), msg.get_id()
                )
                if session.MESSAGE_IDS_ARE_THREAD_IDS and (t := msg["thread"]):
                    self.xmpp.store.sent.set_thread(
                        session.user_pk, t, str(legacy_msg_id)
                    )

    @exceptions_to_xmpp_errors
    async def on_message_correction(self, msg: Message):
        if msg.get_plugin("retract", check=True) is not None:
            # ignore message retraction fallback (fallback=last msg correction)
            return
        session, entity, thread = await self._get_session_entity_thread(msg)
        xmpp_id = msg["replace"]["id"]
        if isinstance(entity, LegacyMUC):
            legacy_id_str = self.xmpp.store.sent.get_group_legacy_id(
                session.user_pk, xmpp_id
            )
            if legacy_id_str is None:
                legacy_id = self._xmpp_msg_id_to_legacy(session, xmpp_id)
            else:
                legacy_id = self.xmpp.LEGACY_MSG_ID_TYPE(legacy_id_str)
        else:
            legacy_id = self._xmpp_msg_id_to_legacy(session, xmpp_id)

        if isinstance(entity, LegacyMUC):
            mentions = await entity.parse_mentions(msg["body"])
        else:
            mentions = None

        if previews := msg["link_previews"]:
            link_previews = [dict_to_named_tuple(p, LinkPreview) for p in previews]
        else:
            link_previews = []

        if legacy_id is None:
            log.debug("Did not find legacy ID to correct")
            new_legacy_msg_id = await session.on_text(
                entity,
                "Correction:" + msg["body"],
                thread=thread,
                mentions=mentions,
                link_previews=link_previews,
            )
        elif (
            not msg["body"].strip()
            and config.CORRECTION_EMPTY_BODY_AS_RETRACTION
            and entity.RETRACTION
        ):
            await session.on_retract(entity, legacy_id, thread=thread)
            new_legacy_msg_id = None
        elif entity.CORRECTION:
            new_legacy_msg_id = await session.on_correct(
                entity,
                msg["body"],
                legacy_id,
                thread=thread,
                mentions=mentions,
                link_previews=link_previews,
            )
        else:
            session.send_gateway_message(
                "Last message correction is not supported by this legacy service. "
                "Slidge will send your correction as new message."
            )
            if (
                config.LAST_MESSAGE_CORRECTION_RETRACTION_WORKAROUND
                and entity.RETRACTION
                and legacy_id is not None
            ):
                if legacy_id is not None:
                    session.send_gateway_message(
                        "Slidge will attempt to retract the original message you wanted"
                        " to edit."
                    )
                    await session.on_retract(entity, legacy_id, thread=thread)

            new_legacy_msg_id = await session.on_text(
                entity,
                "Correction: " + msg["body"],
                thread=thread,
                mentions=mentions,
                link_previews=link_previews,
            )

        if isinstance(entity, LegacyMUC):
            if new_legacy_msg_id is not None:
                self.xmpp.store.sent.set_group_message(
                    session.user_pk, new_legacy_msg_id, msg.get_id()
                )
            await entity.echo(msg, new_legacy_msg_id)
        else:
            self.__ack(msg)
            if new_legacy_msg_id is not None:
                self.xmpp.store.sent.set_message(
                    session.user_pk, new_legacy_msg_id, msg.get_id()
                )

    @exceptions_to_xmpp_errors
    async def on_message_retract(self, msg: Message):
        session, entity, thread = await self._get_session_entity_thread(msg)
        if not entity.RETRACTION:
            raise XMPPError(
                "bad-request",
                "This legacy service does not support message retraction.",
            )
        xmpp_id: str = msg["retract"]["id"]
        legacy_id = self._xmpp_msg_id_to_legacy(session, xmpp_id)
        if legacy_id:
            await session.on_retract(entity, legacy_id, thread=thread)
            if isinstance(entity, LegacyMUC):
                await entity.echo(msg, None)
        else:
            log.debug("Ignored retraction from user")
        self.__ack(msg)

    @exceptions_to_xmpp_errors
    async def on_reactions(self, msg: Message):
        session, entity, thread = await self._get_session_entity_thread(msg)
        react_to: str = msg["reactions"]["id"]

        special_msg = session.SPECIAL_MSG_ID_PREFIX and react_to.startswith(
            session.SPECIAL_MSG_ID_PREFIX
        )

        if special_msg:
            legacy_id = react_to
        else:
            legacy_id = self._xmpp_msg_id_to_legacy(session, react_to)

        if not legacy_id:
            log.debug("Ignored reaction from user")
            raise XMPPError(
                "internal-server-error",
                "Could not convert the XMPP msg ID to a legacy ID",
            )

        emojis = [
            remove_emoji_variation_selector_16(r["value"]) for r in msg["reactions"]
        ]
        error_msg = None
        entity = entity

        if not special_msg:
            if entity.REACTIONS_SINGLE_EMOJI and len(emojis) > 1:
                error_msg = "Maximum 1 emoji/message"

            if not error_msg and (subset := await entity.available_emojis(legacy_id)):
                if not set(emojis).issubset(subset):
                    error_msg = f"You can only react with the following emojis: {''.join(subset)}"

        if error_msg:
            session.send_gateway_message(error_msg)
            if not isinstance(entity, LegacyMUC):
                # no need to carbon for groups, we just don't echo the stanza
                entity.react(legacy_id, carbon=True)  # type: ignore
            await session.on_react(entity, legacy_id, [], thread=thread)
            raise XMPPError("not-acceptable", text=error_msg)

        await session.on_react(entity, legacy_id, emojis, thread=thread)
        if isinstance(entity, LegacyMUC):
            await entity.echo(msg, None)
        else:
            self.__ack(msg)

        multi = self.xmpp.store.multi.get_xmpp_ids(session.user_pk, react_to)
        if not multi:
            return
        multi = [m for m in multi if react_to != m]

        if isinstance(entity, LegacyMUC):
            for xmpp_id in multi:
                mc = copy(msg)
                mc["reactions"]["id"] = xmpp_id
                await entity.echo(mc)
        elif isinstance(entity, LegacyContact):
            for xmpp_id in multi:
                entity.react(legacy_id, emojis, xmpp_id=xmpp_id, carbon=True)

    def __ack(self, msg: Message):
        if not self.xmpp.PROPER_RECEIPTS:
            self.xmpp.delivery_receipt.ack(msg)

    async def __get_reply(
        self, msg: Message, session: BaseSession, entity: Recipient
    ) -> tuple[
        str, str | int | None, LegacyContact | LegacyParticipant | None, str | None
    ]:
        try:
            reply_to_msg_id = self._xmpp_msg_id_to_legacy(session, msg["reply"]["id"])
        except XMPPError:
            session.log.debug(
                "Could not determine reply-to legacy msg ID, sending quote instead."
            )
            return msg["body"], None, None, None

        reply_to_jid = JID(msg["reply"]["to"])
        reply_to = None
        if msg["type"] == "chat":
            if reply_to_jid.bare != session.user_jid.bare:
                try:
                    reply_to = await session.contacts.by_jid(reply_to_jid)
                except XMPPError:
                    pass
        elif msg["type"] == "groupchat":
            nick = reply_to_jid.resource
            try:
                muc = await session.bookmarks.by_jid(reply_to_jid)
            except XMPPError:
                pass
            else:
                if nick != muc.user_nick:
                    reply_to = await muc.get_participant(
                        reply_to_jid.resource, store=False
                    )

        if msg.get_plugin("fallback", check=True) and (
            isinstance(entity, LegacyMUC) or entity.REPLIES
        ):
            text = msg["fallback"].get_stripped_body(self.xmpp["xep_0461"].namespace)
            try:
                reply_fallback = msg["reply"].get_fallback_body()
            except AttributeError:
                reply_fallback = None
        else:
            text = msg["body"]
            reply_fallback = None

        return text, reply_to_msg_id, reply_to, reply_fallback

    async def __send_url(
        self, url: str, session: BaseSession, entity: Recipient, **kwargs
    ) -> int | str | None:
        async with self.xmpp.http.get(url) as response:
            if response.status >= 400:
                session.log.warning(
                    "OOB url cannot be downloaded: %s, sending the URL as text"
                    " instead.",
                    response,
                )
                return await session.on_text(entity, url, **kwargs)

            return await session.on_file(entity, url, http_response=response, **kwargs)

    async def __send_bob(
        self, from_: JID, cid: str, session: BaseSession, entity: Recipient, **kwargs
    ) -> int | str | None:
        sticker = self.xmpp.store.bob.get_sticker(cid)
        if sticker is None:
            await self.xmpp.plugin["xep_0231"].get_bob(from_, cid)
            sticker = self.xmpp.store.bob.get_sticker(cid)
        assert sticker is not None
        return await session.on_sticker(entity, sticker, **kwargs)


log = logging.getLogger(__name__)
