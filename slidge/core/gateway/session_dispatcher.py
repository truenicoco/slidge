import logging
from copy import copy
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Union

from slixmpp import JID, CoroutineCallback, Iq, Message, Presence, StanzaPath
from slixmpp.exceptions import IqError, XMPPError
from slixmpp.plugins.xep_0004 import Form
from slixmpp.plugins.xep_0084.stanza import Info

from ... import LegacyContact
from ...group.room import LegacyMUC
from ...util.types import LinkPreview, Recipient, RecipientType
from ...util.util import (
    dict_to_named_tuple,
    merge_resources,
    remove_emoji_variation_selector_16,
)
from .. import config
from ..session import BaseSession

if TYPE_CHECKING:
    from .base import BaseGateway

HandlerType = Callable[[Union[Presence, Message]], Awaitable[None]]


class Ignore(BaseException):
    pass


class SessionDispatcher:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        self.http = xmpp.http

        xmpp.register_handler(
            CoroutineCallback(
                "MUCModerate",
                StanzaPath("iq/apply_to/moderate"),
                _exceptions_to_xmpp_errors(self.on_user_moderation),  # type:ignore
            )
        )
        xmpp.register_handler(
            CoroutineCallback(
                "MUCSetAffiliation",
                StanzaPath("iq@type=set/mucadmin_query"),
                _exceptions_to_xmpp_errors(self.on_user_set_affiliation),  # type:ignore
            )
        )
        xmpp.register_handler(
            CoroutineCallback(
                "muc#admin",
                StanzaPath("iq@type=get/mucowner_query"),
                _exceptions_to_xmpp_errors(self.on_muc_owner_query),  # type: ignore
            )
        )
        xmpp.register_handler(
            CoroutineCallback(
                "muc#admin",
                StanzaPath("iq@type=set/mucowner_query"),
                _exceptions_to_xmpp_errors(self.on_muc_owner_set),  # type: ignore
            )
        )
        xmpp.register_handler(
            CoroutineCallback(
                "ibr_remove",
                StanzaPath("/iq/register"),
                _exceptions_to_xmpp_errors(self.on_ibr_remove),  # type: ignore
            )
        )
        self.xmpp.register_handler(
            CoroutineCallback(
                "get_vcard",
                StanzaPath("iq@type=get/vcard"),
                _exceptions_to_xmpp_errors(self.on_get_vcard),  # type:ignore
            )
        )

        for event in (
            "legacy_message",
            "marker_displayed",
            "presence",
            "chatstate_active",
            "chatstate_inactive",
            "chatstate_composing",
            "chatstate_paused",
            "message_correction",
            "reactions",
            "message_retract",
            "groupchat_join",
            "groupchat_message",
            "groupchat_direct_invite",
            "groupchat_subject",
            "avatar_metadata_publish",
            "message_displayed_synchronization_publish",
        ):
            xmpp.add_event_handler(
                event, _exceptions_to_xmpp_errors(getattr(self, "on_" + event))
            )

    async def __get_session(
        self, stanza: Union[Message, Presence, Iq], timeout: Optional[int] = 10
    ) -> BaseSession:
        xmpp = self.xmpp
        if stanza.get_from().server == xmpp.boundjid.bare:
            log.debug("Ignoring echo")
            raise Ignore
        if (
            isinstance(stanza, Message)
            and stanza.get_type() == "chat"
            and stanza.get_to() == xmpp.boundjid.bare
        ):
            log.debug("Ignoring message to component")
            raise Ignore
        session = xmpp.get_session_from_stanza(stanza)
        await session.wait_for_ready(timeout)
        if isinstance(stanza, Message) and _ignore(session, stanza):
            raise Ignore
        return session

    def __ack(self, msg: Message):
        if not self.xmpp.PROPER_RECEIPTS:
            self.xmpp.delivery_receipt.ack(msg)

    async def __get_session_entity_thread(
        self, msg: Message
    ) -> tuple["BaseSession", Recipient, Union[int, str]]:
        session = await self.__get_session(msg)
        e: Recipient = await _get_entity(session, msg)
        legacy_thread = await _xmpp_to_legacy_thread(session, msg, e)
        return session, e, legacy_thread

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

        session, entity, thread = await self.__get_session_entity_thread(msg)

        e: Recipient = await _get_entity(session, msg)
        log.debug("Entity %r", e)

        if msg.get_plugin("oob", check=True) is not None:
            url = msg["oob"]["url"]
        else:
            url = None

        text = msg["body"]

        reply_to = None
        reply_fallback = None
        if msg.get_plugin("reply", check=True):
            try:
                reply_to_msg_xmpp_id = self._xmpp_msg_id_to_legacy(
                    session, msg["reply"]["id"]
                )
            except XMPPError:
                session.log.debug(
                    "Could not determine reply-to legacy msg ID, sending quote instead."
                )
                text = msg["body"]
                reply_fallback = None
                reply_to_msg_xmpp_id = None
            else:
                reply_to_jid = JID(msg["reply"]["to"])
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
                    isinstance(e, LegacyMUC) or e.REPLIES
                ):
                    text = msg["fallback"].get_stripped_body(
                        self.xmpp["xep_0461"].namespace
                    )
                    try:
                        reply_fallback = msg["reply"].get_fallback_body()
                    except AttributeError:
                        pass
        else:
            reply_to_msg_xmpp_id = None
            reply_to = None

        if msg.get_plugin("link_previews", check=True):
            pass

        kwargs = dict(
            reply_to_msg_id=reply_to_msg_xmpp_id,
            reply_to_fallback_text=reply_fallback,
            reply_to=reply_to,
            thread=thread,
        )

        if not url and isinstance(e, LegacyMUC):
            kwargs["mentions"] = await e.parse_mentions(text)

        if previews := msg["link_previews"]:
            kwargs["link_previews"] = [
                dict_to_named_tuple(p, LinkPreview) for p in previews
            ]

        if url:
            async with self.http.get(url) as response:
                if response.status >= 400:
                    session.log.warning(
                        (
                            "OOB url cannot be downloaded: %s, sending the URL as text"
                            " instead."
                        ),
                        response,
                    )
                    legacy_msg_id = await session.on_text(e, url, **kwargs)
                else:
                    legacy_msg_id = await session.on_file(
                        e, url, http_response=response, **kwargs
                    )
        elif text:
            legacy_msg_id = await session.on_text(e, text, **kwargs)
        else:
            log.debug("Ignoring %s", msg.get_id())
            return

        if isinstance(e, LegacyMUC):
            await e.echo(msg, legacy_msg_id)
            if legacy_msg_id is not None:
                self.xmpp.store.sent.set_group_message(
                    session.user_pk, legacy_msg_id, msg.get_id()
                )
        else:
            self.__ack(msg)
            if legacy_msg_id is not None:
                self.xmpp.store.sent.set_message(
                    session.user_pk, legacy_msg_id, msg.get_id()
                )
                if session.MESSAGE_IDS_ARE_THREAD_IDS and (t := msg["thread"]):
                    self.xmpp.store.sent.set_thread(session.user_pk, t, legacy_msg_id)

    async def on_groupchat_message(self, msg: Message):
        await self.on_legacy_message(msg)

    async def on_message_correction(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        xmpp_id = msg["replace"]["id"]
        if isinstance(entity, LegacyMUC):
            legacy_id = self.xmpp.store.sent.get_group_legacy_id(
                session.user_pk, xmpp_id
            )
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

    async def on_message_retract(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
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

    async def on_marker_displayed(self, msg: Message):
        session = await self.__get_session(msg)

        e: Recipient = await _get_entity(session, msg)
        legacy_thread = await _xmpp_to_legacy_thread(session, msg, e)
        displayed_msg_id = msg["displayed"]["id"]
        if not isinstance(e, LegacyMUC) and self.xmpp.MARK_ALL_MESSAGES:
            to_mark = e.get_msg_xmpp_id_up_to(displayed_msg_id)  # type: ignore
            if to_mark is None:
                session.log.debug("Can't mark all messages up to %s", displayed_msg_id)
                to_mark = [displayed_msg_id]
        else:
            to_mark = [displayed_msg_id]
        for xmpp_id in to_mark:
            await session.on_displayed(
                e, self._xmpp_msg_id_to_legacy(session, xmpp_id), legacy_thread
            )
            if isinstance(e, LegacyMUC):
                await e.echo(msg, None)

    async def on_chatstate_active(self, msg: Message):
        if msg["body"]:
            # if there is a body, it's handled in self.on_legacy_message()
            return
        session, entity, thread = await self.__get_session_entity_thread(msg)
        await session.on_active(entity, thread)

    async def on_chatstate_inactive(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        await session.on_inactive(entity, thread)

    async def on_chatstate_composing(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        await session.on_composing(entity, thread)

    async def on_chatstate_paused(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        await session.on_paused(entity, thread)

    async def on_reactions(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
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

    async def on_presence(self, p: Presence):
        if p.get_plugin("muc_join", check=True):
            # handled in on_groupchat_join
            # without this early return, since we switch from and to in this
            # presence stanza, on_groupchat_join ends up trying to instantiate
            # a MUC with the user's JID, which in turn leads to slidge sending
            # a (error) presence from=the user's JID, which terminates the
            # XML stream.
            return

        session = await self.__get_session(p)

        pto = p.get_to()
        if pto == self.xmpp.boundjid.bare:
            session.log.debug("Received a presence from %s", p.get_from())
            if (ptype := p.get_type()) not in _USEFUL_PRESENCES:
                return
            if not session.user.preferences.get("sync_presence", False):
                session.log.debug("User does not want to sync their presence")
                return
            # NB: get_type() returns either a proper presence type or
            #     a presence show if available. Weird, weird, weird slix.
            resources = self.xmpp.roster[self.xmpp.boundjid.bare][
                p.get_from()
            ].resources
            await session.on_presence(
                p.get_from().resource,
                ptype,  # type: ignore
                p["status"],
                resources,
                merge_resources(resources),
            )
            return

        muc = session.bookmarks.by_jid_only_if_exists(JID(pto.bare))

        if muc is not None and p.get_type() == "unavailable":
            return muc.on_presence_unavailable(p)

        if muc is None or p.get_from().resource not in muc.get_user_resources():
            return

        if pto.resource == muc.user_nick:
            # Ignore presence stanzas with the valid nick.
            # even if joined to the group, we might receive those from clients,
            # when setting a status message, or going away, etc.
            return

        # We can't use XMPPError here because from must be room@slidge/VALID-USER-NICK

        error_from = JID(muc.jid)
        error_from.resource = muc.user_nick
        error_stanza = p.error()
        error_stanza.set_to(p.get_from())
        error_stanza.set_from(error_from)
        error_stanza.enable("muc_join")
        error_stanza.enable("error")
        error_stanza["error"]["type"] = "cancel"
        error_stanza["error"]["by"] = muc.jid
        error_stanza["error"]["condition"] = "not-acceptable"
        error_stanza["error"][
            "text"
        ] = "Slidge does not let you change your nickname in groups."
        error_stanza.send()

    async def on_groupchat_join(self, p: Presence):
        if not self.xmpp.GROUPS:
            raise XMPPError(
                "feature-not-implemented",
                "This gateway does not implement multi-user chats.",
            )
        session = await self.__get_session(p)
        session.raise_if_not_logged()
        muc = await session.bookmarks.by_jid(p.get_to())
        await muc.join(p)

    async def on_message_displayed_synchronization_publish(self, msg: Message):
        session = await self.__get_session(msg, timeout=None)

        chat_jid = msg["pubsub_event"]["items"]["item"]["id"]

        if chat_jid == self.xmpp.boundjid.bare:
            return

        chat = await session.get_contact_or_group_or_participant(JID(chat_jid))
        if not isinstance(chat, LegacyMUC):
            session.log.debug("Ignoring non-groupchat MDS event")
            return

        stanza_id = msg["pubsub_event"]["items"]["item"]["displayed"]["stanza_id"]["id"]
        await session.on_displayed(
            chat, self._xmpp_msg_id_to_legacy(session, stanza_id)
        )

    async def on_avatar_metadata_publish(self, m: Message):
        session = await self.__get_session(m, timeout=None)
        if not session.user.preferences.get("sync_avatar", False):
            session.log.debug("User does not want to sync their avatar")
            return
        info = m["pubsub_event"]["items"]["item"]["avatar_metadata"]["info"]

        await self.on_avatar_metadata_info(session, info)

    async def on_avatar_metadata_info(self, session: BaseSession, info: Info):
        hash_ = info["id"]

        if session.user.avatar_hash == hash_:
            session.log.debug("We already know this avatar hash")
            return
        with self.xmpp.store.session() as orm_session:
            user = self.xmpp.store.users.get(session.user_jid)
            assert user is not None
            user.avatar_hash = hash_
            orm_session.add(user)
            orm_session.commit()

        if hash_:
            try:
                iq = await self.xmpp.plugin["xep_0084"].retrieve_avatar(
                    session.user_jid, hash_, ifrom=self.xmpp.boundjid.bare
                )
            except IqError as e:
                session.log.warning("Could not fetch the user's avatar: %s", e)
                return
            bytes_ = iq["pubsub"]["items"]["item"]["avatar_data"]["value"]
            type_ = info["type"]
            height = info["height"]
            width = info["width"]
        else:
            bytes_ = type_ = height = width = hash_ = None
        try:
            await session.on_avatar(bytes_, hash_, type_, width, height)
        except NotImplementedError:
            pass
        except Exception as e:
            # If something goes wrong here, replying an error stanza will to the
            # avatar update will likely not show in most clients, so let's send
            # a normal message from the component to the user.
            session.send_gateway_message(
                f"Something went wrong trying to set your avatar: {e!r}"
            )

    async def on_user_moderation(self, iq: Iq):
        session = await self.__get_session(iq)
        session.raise_if_not_logged()

        muc = await session.bookmarks.by_jid(iq.get_to())

        apply_to = iq["apply_to"]
        xmpp_id = apply_to["id"]
        if not xmpp_id:
            raise XMPPError("bad-request", "Missing moderated message ID")

        moderate = apply_to["moderate"]
        if not moderate["retract"]:
            raise XMPPError(
                "feature-not-implemented",
                "Slidge only implements moderation/retraction",
            )

        legacy_id = self._xmpp_msg_id_to_legacy(session, xmpp_id)
        await session.on_moderate(muc, legacy_id, moderate["reason"] or None)
        iq.reply(clear=True).send()

    async def on_user_set_affiliation(self, iq: Iq):
        session = await self.__get_session(iq)
        session.raise_if_not_logged()

        muc = await session.bookmarks.by_jid(iq.get_to())

        item = iq["mucadmin_query"]["item"]
        if item["jid"]:
            contact = await session.contacts.by_jid(JID(item["jid"]))
        else:
            part = await muc.get_participant(
                item["nick"], fill_first=True, raise_if_not_found=True
            )
            assert part.contact is not None
            contact = part.contact

        if item["affiliation"]:
            await muc.on_set_affiliation(
                contact,
                item["affiliation"],
                item["reason"] or None,
                item["nick"] or None,
            )
        elif item["role"] == "none":
            await muc.on_kick(contact, item["reason"] or None)

        iq.reply(clear=True).send()

    async def on_groupchat_direct_invite(self, msg: Message):
        session = await self.__get_session(msg)
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

    async def on_muc_owner_query(self, iq: Iq):
        session = await self.__get_session(iq)
        session.raise_if_not_logged()

        muc = await session.bookmarks.by_jid(iq.get_to())

        reply = iq.reply()

        form = Form(title="Slidge room configuration")
        form["instructions"] = (
            "Complete this form to modify the configuration of your room."
        )
        form.add_field(
            var="FORM_TYPE",
            type="hidden",
            value="http://jabber.org/protocol/muc#roomconfig",
        )
        form.add_field(
            var="muc#roomconfig_roomname",
            label="Natural-Language Room Name",
            type="text-single",
            value=muc.name,
        )
        if muc.HAS_DESCRIPTION:
            form.add_field(
                var="muc#roomconfig_roomdesc",
                label="Short Description of Room",
                type="text-single",
                value=muc.description,
            )

        muc_owner = iq["mucowner_query"]
        muc_owner.append(form)
        reply.append(muc_owner)
        reply.send()

    async def on_muc_owner_set(self, iq: Iq):
        session = await self.__get_session(iq)
        session.raise_if_not_logged()
        muc = await session.bookmarks.by_jid(iq.get_to())
        query = iq["mucowner_query"]

        if form := query.get_plugin("form", check=True):
            values = form.get_values()
            await muc.on_set_config(
                name=values.get("muc#roomconfig_roomname"),
                description=(
                    values.get("muc#roomconfig_roomdesc")
                    if muc.HAS_DESCRIPTION
                    else None
                ),
            )
            form["type"] = "result"
            clear = False
        elif destroy := query.get_plugin("destroy", check=True):
            reason = destroy["reason"] or None
            await muc.on_destroy_request(reason)
            user_participant = await muc.get_user_participant()
            user_participant._affiliation = "none"
            user_participant._role = "none"
            presence = user_participant._make_presence(ptype="unavailable", force=True)
            presence["muc"].enable("destroy")
            if reason is not None:
                presence["muc"]["destroy"]["reason"] = reason
            user_participant._send(presence)
            session.bookmarks.remove(muc)
            clear = True
        else:
            raise XMPPError("bad-request")

        iq.reply(clear=clear).send()

    async def on_groupchat_subject(self, msg: Message):
        session = await self.__get_session(msg)
        session.raise_if_not_logged()
        muc = await session.bookmarks.by_jid(msg.get_to())
        if not muc.HAS_SUBJECT:
            raise XMPPError(
                "bad-request",
                "There are no room subject in here. "
                "Use the room configuration to update its name or description",
            )
        await muc.on_set_subject(msg["subject"])

    async def on_ibr_remove(self, iq: Iq):
        if iq.get_to() == self.xmpp.boundjid.bare:
            return

        session = await self.__get_session(iq)
        session.raise_if_not_logged()

        if iq["type"] == "set" and iq["register"]["remove"]:
            muc = await session.bookmarks.by_jid(iq.get_to())
            await session.on_leave_group(muc.legacy_id)
            iq.reply().send()
            return

        raise XMPPError("feature-not-implemented")

    async def on_get_vcard(self, iq: Iq):
        session = await self.__get_session(iq)
        session.raise_if_not_logged()
        contact = await session.contacts.by_jid(iq.get_to())
        vcard = await contact.get_vcard()
        reply = iq.reply()
        if vcard:
            reply.append(vcard)
        else:
            reply.enable("vcard")
        reply.send()

    def _xmpp_msg_id_to_legacy(self, session: "BaseSession", xmpp_id: str):
        sent = self.xmpp.store.sent.get_legacy_id(session.user_pk, xmpp_id)
        if sent is not None:
            return self.xmpp.LEGACY_MSG_ID_TYPE(sent)

        multi = self.xmpp.store.multi.get_legacy_id(session.user_pk, xmpp_id)
        if multi:
            return self.xmpp.LEGACY_MSG_ID_TYPE(multi)

        try:
            return session.xmpp_to_legacy_msg_id(xmpp_id)
        except XMPPError:
            raise
        except Exception as e:
            log.debug("Couldn't convert xmpp msg ID to legacy ID.", exc_info=e)
            raise XMPPError(
                "internal-server-error", "Couldn't convert xmpp msg ID to legacy ID."
            )


def _ignore(session: "BaseSession", msg: Message):
    if (i := msg.get_id()) not in session.ignore_messages:
        return False
    session.log.debug("Ignored sent carbon: %s", i)
    session.ignore_messages.remove(i)
    return True


async def _xmpp_to_legacy_thread(
    session: "BaseSession", msg: Message, recipient: RecipientType
):
    xmpp_thread = msg["thread"]
    if not xmpp_thread:
        return

    if session.MESSAGE_IDS_ARE_THREAD_IDS:
        return session.xmpp.store.sent.get_legacy_thread(session.user_pk, xmpp_thread)

    async with session.thread_creation_lock:
        legacy_thread_str = session.xmpp.store.sent.get_legacy_thread(
            session.user_pk, xmpp_thread
        )
        if legacy_thread_str is None:
            legacy_thread = str(await recipient.create_thread(xmpp_thread))
            session.xmpp.store.sent.set_thread(
                session.user_pk, xmpp_thread, legacy_thread
            )
    return session.xmpp.LEGACY_MSG_ID_TYPE(legacy_thread)


async def _get_entity(session: "BaseSession", m: Message) -> RecipientType:
    session.raise_if_not_logged()
    if m.get_type() == "groupchat":
        muc = await session.bookmarks.by_jid(m.get_to())
        r = m.get_from().resource
        if r not in muc.get_user_resources():
            session.create_task(muc.kick_resource(r))
            raise XMPPError("not-acceptable", "You are not connected to this chat")
        return muc
    else:
        return await session.contacts.by_jid(m.get_to())


def _exceptions_to_xmpp_errors(cb: HandlerType) -> HandlerType:
    async def wrapped(stanza: Union[Presence, Message]):
        try:
            await cb(stanza)
        except Ignore:
            pass
        except XMPPError:
            raise
        except NotImplementedError:
            log.debug("NotImplementedError raised in %s", cb)
            raise XMPPError(
                "feature-not-implemented", "Not implemented by the legacy module"
            )
        except Exception as e:
            log.error("Failed to handle incoming stanza: %s", stanza, exc_info=e)
            raise XMPPError("internal-server-error", str(e))

    return wrapped


_USEFUL_PRESENCES = {"available", "unavailable", "away", "chat", "dnd", "xa"}


log = logging.getLogger(__name__)
