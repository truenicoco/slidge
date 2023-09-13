import logging
from copy import copy
from typing import TYPE_CHECKING, Awaitable, Callable, Union

from slixmpp import JID, Message, Presence
from slixmpp.exceptions import XMPPError

from ... import LegacyContact
from ...util.sql import db
from ...util.types import Recipient, RecipientType
from ...util.util import merge_resources, remove_emoji_variation_selector_16
from .. import config
from ..muc.room import LegacyMUC
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
        ):
            xmpp.add_event_handler(
                event, _exceptions_to_xmpp_errors(getattr(self, "on_" + event))
            )

    async def _dispatch(self, m: Union[Message, Presence], cb: Callable):
        xmpp = self.xmpp
        if m.get_from().server == xmpp.boundjid.bare:
            log.debug("Ignoring echo")
            return
        if m.get_to() == xmpp.boundjid.bare and isinstance(m, Message):
            log.debug("Ignoring message to component")
            return
        s = xmpp.get_session_from_stanza(m)
        await s.wait_for_ready()
        try:
            await cb(s, m)
        except XMPPError:
            raise
        except NotImplementedError:
            log.debug("Legacy module does not implement %s", cb)
        except Exception as e:
            s.log.error("Failed to handle incoming stanza: %s", m, exc_info=e)
            raise XMPPError("internal-server-error", str(e))

    async def __get_session(self, stanza: Union[Message, Presence]) -> BaseSession:
        xmpp = self.xmpp
        if stanza.get_from().server == xmpp.boundjid.bare:
            log.debug("Ignoring echo")
            raise Ignore
        if stanza.get_to() == xmpp.boundjid.bare and isinstance(stanza, Message):
            log.debug("Ignoring message to component")
            raise Ignore
        session = xmpp.get_session_from_stanza(stanza)
        await session.wait_for_ready()
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

        session, entity, thread = await self.__get_session_entity_thread(msg)

        e: Recipient = await _get_entity(session, msg)
        log.debug("Entity %r", e)

        if msg.get_plugin("oob", check=True) is not None:
            url = msg["oob"]["url"]
        else:
            url = None

        text = msg["body"]
        if msg.get_plugin("feature_fallback", check=True) and (
            isinstance(e, LegacyMUC) or e.REPLIES
        ):
            text = msg["feature_fallback"].get_stripped_body()
            reply_fallback = msg["feature_fallback"].get_fallback_body()
        else:
            reply_fallback = None

        reply_to = None
        if msg.get_plugin("reply", check=True):
            try:
                reply_to_msg_xmpp_id = _xmpp_msg_id_to_legacy(
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
                    if reply_to_jid.bare != session.user.jid.bare:
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
        else:
            reply_to_msg_xmpp_id = None
            reply_to = None

        kwargs = dict(
            reply_to_msg_id=reply_to_msg_xmpp_id,
            reply_to_fallback_text=reply_fallback,
            reply_to=reply_to,
            thread=thread,
        )

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
                    legacy_msg_id = await session.send_text(e, url, **kwargs)
                else:
                    legacy_msg_id = await session.send_file(
                        e, url, http_response=response, **kwargs
                    )
        elif text:
            legacy_msg_id = await session.send_text(e, text, **kwargs)
        else:
            log.debug("Ignoring %s", msg.get_id())
            return

        if isinstance(e, LegacyMUC):
            await e.echo(msg, legacy_msg_id)
            if legacy_msg_id is not None:
                session.muc_sent_msg_ids[legacy_msg_id] = msg.get_id()
        else:
            self.__ack(msg)
            if legacy_msg_id is not None:
                session.sent[legacy_msg_id] = msg.get_id()
                if session.MESSAGE_IDS_ARE_THREAD_IDS and (t := msg["thread"]):
                    session.threads[t] = legacy_msg_id

    async def on_groupchat_message(self, msg: Message):
        await self.on_legacy_message(msg)

    async def on_message_correction(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        xmpp_id = msg["replace"]["id"]
        if isinstance(entity, LegacyMUC):
            legacy_id = session.muc_sent_msg_ids.inverse.get(xmpp_id)
        else:
            legacy_id = _xmpp_msg_id_to_legacy(session, xmpp_id)

        if legacy_id is None:
            log.debug("Did not find legacy ID to correct")
            new_legacy_msg_id = await session.send_text(
                entity, "Correction:" + msg["body"], thread=thread
            )
        elif (
            not msg["body"].strip()
            and config.CORRECTION_EMPTY_BODY_AS_RETRACTION
            and entity.RETRACTION
        ):
            await session.retract(entity, legacy_id, thread=thread)
            new_legacy_msg_id = None
        elif entity.CORRECTION:
            new_legacy_msg_id = await session.correct(
                entity, msg["body"], legacy_id, thread=thread
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
                    await session.retract(entity, legacy_id, thread=thread)

            new_legacy_msg_id = await session.send_text(
                entity, "Correction: " + msg["body"], thread=thread
            )

        if isinstance(entity, LegacyMUC):
            if new_legacy_msg_id is not None:
                session.muc_sent_msg_ids[new_legacy_msg_id] = msg.get_id()
            await entity.echo(msg, new_legacy_msg_id)
        else:
            self.__ack(msg)
            if new_legacy_msg_id is not None:
                session.sent[new_legacy_msg_id] = msg.get_id()

    async def on_message_retract(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        if not entity.RETRACTION:
            raise XMPPError(
                "bad-request",
                "This legacy service does not support message retraction.",
            )
        xmpp_id: str = msg["apply_to"]["id"]
        legacy_id = _xmpp_msg_id_to_legacy(session, xmpp_id)
        if legacy_id:
            await session.retract(entity, legacy_id, thread=thread)
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
            await session.displayed(
                e, _xmpp_msg_id_to_legacy(session, xmpp_id), legacy_thread
            )
            if isinstance(e, LegacyMUC):
                await e.echo(msg, None)

    async def on_chatstate_active(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        await session.active(entity, thread)

    async def on_chatstate_inactive(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        await session.inactive(entity, thread)

    async def on_chatstate_composing(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        await session.composing(entity, thread)

    async def on_chatstate_paused(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        await session.paused(entity, thread)

    async def on_reactions(self, msg: Message):
        session, entity, thread = await self.__get_session_entity_thread(msg)
        react_to: str = msg["reactions"]["id"]
        legacy_id = _xmpp_msg_id_to_legacy(session, react_to)

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
        if entity.REACTIONS_SINGLE_EMOJI and len(emojis) > 1:
            error_msg = "Maximum 1 emoji/message"

        if not error_msg and (subset := await entity.available_emojis(legacy_id)):
            if not set(emojis).issubset(subset):
                error_msg = (
                    f"You can only react with the following emojis: {''.join(subset)}"
                )

        if error_msg:
            session.send_gateway_message(error_msg)
            if not isinstance(entity, LegacyMUC):
                # no need to carbon for groups, we just don't echo the stanza
                entity.react(legacy_id, carbon=True)  # type: ignore
            await session.react(entity, legacy_id, [], thread=thread)
            raise XMPPError("not-acceptable", text=error_msg)

        await session.react(entity, legacy_id, emojis, thread=thread)
        if isinstance(entity, LegacyMUC):
            await entity.echo(msg, None)
        else:
            self.__ack(msg)

        multi = db.attachment_get_associated_xmpp_ids(react_to)
        if not multi:
            return

        if isinstance(entity, LegacyMUC):
            for xmpp_id in multi:
                mc = copy(msg)
                mc["reactions"]["id"] = xmpp_id
                await entity.echo(mc)
        elif isinstance(entity, LegacyContact):
            for xmpp_id in multi:
                entity.react(legacy_id, emojis, xmpp_id=xmpp_id, carbon=True)

    async def on_presence(self, p: Presence):
        session = await self.__get_session(p)

        if p.get_to() != self.xmpp.boundjid.bare:
            return
        # NB: get_type() returns either a proper presence type or
        #     a presence show if available. Weird, weird, weird slix.
        if (ptype := p.get_type()) not in _USEFUL_PRESENCES:
            return
        resources = self.xmpp.roster[self.xmpp.boundjid.bare][p.get_from()].resources
        session.log.debug("Received a presence from %s", p.get_from())
        await session.presence(
            p.get_from().resource,
            ptype,  # type: ignore
            p["status"],
            resources,
            merge_resources(resources),
        )

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


def _xmpp_msg_id_to_legacy(session: "BaseSession", xmpp_id: str):
    sent = session.sent.inverse.get(xmpp_id)
    if sent:
        return sent

    multi = db.attachment_get_legacy_id_for_xmpp_id(xmpp_id)
    if multi:
        return multi

    try:
        return session.xmpp_msg_id_to_legacy_msg_id(xmpp_id)
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
        return session.threads.get(xmpp_thread)

    async with session.thread_creation_lock:
        legacy_thread = session.threads.get(xmpp_thread)
        if legacy_thread is None:
            legacy_thread = await recipient.create_thread(xmpp_thread)
            session.threads[xmpp_thread] = legacy_thread
    return legacy_thread


async def _get_entity(session: "BaseSession", m: Message) -> RecipientType:
    session.raise_if_not_logged()
    if m.get_type() == "groupchat":
        muc = await session.bookmarks.by_jid(m.get_to())
        r = m.get_from().resource
        if r not in muc.user_resources:
            session.xmpp.loop.create_task(muc.kick_resource(r))
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
            log.debug("Legacy module does not implement %s", cb)
        except Exception as e:
            log.error("Failed to handle incoming stanza: %s", stanza, exc_info=e)
            raise XMPPError("internal-server-error", str(e))

    return wrapped


_USEFUL_PRESENCES = {"available", "unavailable", "away", "chat", "dnd", "xa"}


log = logging.getLogger(__name__)
