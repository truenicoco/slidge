import asyncio
import functools
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union, cast

import aiohttp
import aiosignald.exc as sigexc
import aiosignald.generated as sigapi
from slixmpp.exceptions import XMPPError

from slidge import *
from slidge.core.muc.room import MucType
from slidge.util.util import is_valid_phone_number

if TYPE_CHECKING:
    from .contact import Contact, Roster
    from .gateway import Gateway
    from .group import Bookmarks, MUC, Participant

from . import config, txt


def handle_unregistered_recipient(func):
    @functools.wraps(func)
    async def wrapped(*a, **kw):
        try:
            return await func(*a, **kw)
        except (
            sigexc.UnregisteredUserError,
            sigexc.IllegalArgumentException,
            sigexc.InternalError,
            sigexc.InvalidGroupError,
        ) as e:
            raise XMPPError(
                "item-not-found",
                text=e.message,
            )

    return wrapped


class Session(
    BaseSession["Gateway", int, "Roster", "Contact", "Bookmarks", "MUC", "Participant"]
):
    """
    Represents a signal account
    """

    def __init__(self, user: GatewayUser):
        """

        :param user:
        """
        super().__init__(user)
        self.phone = self.user.registration_form["phone"]
        if self.phone is None:
            raise RuntimeError
        self.signal = self.xmpp.signal
        self.xmpp.sessions_by_phone[self.phone] = self
        self.reaction_ack_futures: dict[tuple[int, str], asyncio.Future[None]] = {}
        self.user_uuid: asyncio.Future[str] = self.xmpp.loop.create_future()
        self.user_nick: asyncio.Future[str] = self.xmpp.loop.create_future()
        self.connected = self.xmpp.loop.create_future()
        self.sent_in_muc = dict[int, "MUC"]()

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> int:
        try:
            return int(i)
        except ValueError:
            raise NotImplementedError

    @handle_unregistered_recipient
    async def paused(self, c: "Contact"):
        await (await self.signal).typing(
            account=self.phone, typing=False, address=c.signal_address
        )

    async def correct(self, text: str, legacy_msg_id: Any, c: "Contact"):
        return await self.send_text("Correction: " + text, c)

    async def search(self, form_values: dict[str, str]):
        phone = form_values.get("phone")
        if phone is None:
            raise ValueError("Empty phone")

        try:
            address = await (await self.signal).resolve_address(
                account=self.phone,
                partial=sigapi.JsonAddressv1(number=phone),
            )
        except sigexc.UnregisteredUserError:
            return

        contact = await self.contacts.by_json_address(address)
        # the name will be updated once c.update_and_add(), triggered by by_json_address()
        # completes, but it's nicer to have a phone number instead of a UUID
        # in the meantime.
        contact.name = phone

        return SearchResult(
            fields=[FormField("phone"), FormField("jid", type="jid-single")],
            items=[{"phone": phone, "jid": contact.jid.bare}],
        )

    async def login(self):
        """
        Attempt to listen to incoming events for this account,
        or pursue the registration process if needed.
        """
        try:
            await (await self.signal).subscribe(account=self.phone)
        except sigexc.NoSuchAccountError:
            device = self.user.registration_form["device"]
            try:
                if device == "primary":
                    await self.register()
                elif device == "secondary":
                    await self.link()
                else:
                    # This should never happen
                    self.send_gateway_status("Disconnected", show="dnd")
                    raise TypeError("Unknown device type", device)
            except sigexc.SignaldException as e:
                self.xmpp.send_message(
                    mto=self.user.jid,
                    mbody=f"Something went wrong: {e}",
                    mfrom=self.xmpp.boundjid,
                )
                raise
            await (await self.signal).subscribe(account=self.phone)
        await self.connected
        sig = await self.signal
        profile = await sig.get_profile(
            account=self.phone, address=sigapi.JsonAddressv1(number=self.phone)
        )
        nick: str = profile.name or profile.profile_name or "SlidgeUser"
        if nick is not None:
            nick = nick.replace("\u0000", " ")
        self.user_nick.set_result(nick)
        self.user_uuid.set_result(profile.address.uuid)
        self.bookmarks.set_username(nick)
        await self.add_contacts_to_roster()
        await self.add_groups()
        return f"Connected as {self.phone}"

    async def on_websocket_connection_state(
        self, state: sigapi.WebSocketConnectionStatev1
    ):
        if (
            state.state == "CONNECTED"
            and state.socket == "IDENTIFIED"
            and not self.connected.done()
        ):
            self.connected.set_result(True)

    async def register(self):
        self.send_gateway_status("Registering…", show="dnd")
        try:
            await (await self.signal).register(self.phone)
        except sigexc.CaptchaRequiredError:
            self.send_gateway_status("Captcha required", show="dnd")
            captcha = await self.input(txt.CAPTCHA_REQUIRED)
            await (await self.signal).register(self.phone, captcha=captcha)
        sms_code = await self.input(
            f"Reply to this message with the code you have received by SMS at {self.phone}.",
        )
        await (await self.signal).verify(account=self.phone, code=sms_code)
        await (await self.signal).set_profile(
            account=self.phone, name=self.user.registration_form["name"]
        )
        self.send_gateway_message(txt.REGISTER_SUCCESS)

    async def send_linking_qrcode(self):
        self.send_gateway_status("QR scan needed", show="dnd")
        resp = await (await self.signal).generate_linking_uri()
        await self.send_qr(resp.uri)
        self.xmpp.send_message(
            mto=self.user.jid,
            mbody=f"Use this URI or QR code on another signal device to "
            f"finish linking your XMPP account\n{resp.uri}",
            mfrom=self.xmpp.boundjid,
        )
        return resp

    async def link(self):
        resp = await self.send_linking_qrcode()
        try:
            await (await self.signal).finish_link(
                device_name=self.user.registration_form["device_name"],
                session_id=resp.session_id,
            )
        except sigexc.ScanTimeoutError:
            while True:
                r = await self.input(txt.LINK_TIMEOUT)
                if r in ("cancel", "link"):
                    break
                else:
                    self.send_gateway_message("Please reply either 'link' or 'cancel'")
            if r == "cancel":
                raise
            elif r == "link":
                await self.link()  # TODO: set a max number of attempts
        except sigexc.SignaldException as e:
            self.xmpp.send_message(
                mto=self.user.jid,
                mbody=f"Something went wrong during the linking process: {e}.",
                mfrom=self.xmpp.boundjid,
            )
            raise
        else:
            self.send_gateway_message(txt.LINK_SUCCESS)

    async def logout(self):
        await (await self.signal).unsubscribe(account=self.phone)

    async def add_contacts_to_roster(self):
        """
        Populate a user's roster
        """
        profiles = await (await self.signal).list_contacts(account=self.phone)
        for profile in profiles.profiles:
            contact = await self.contacts.by_json_address(profile.address)
            await contact.update_info()
            await contact.add_to_roster()
            contact.online()

    async def add_groups(self):
        groups = await (await self.signal).list_groups(account=self.phone)
        self.log.debug("GROUPS: %r", groups)
        for group in groups.groups:
            muc = await self.bookmarks.by_legacy_id(group.id)
            muc.type = MucType.GROUP
            muc.DISCO_NAME = group.title
            muc.subject = group.description
            muc.description = group.description
            muc.n_participants = len(group.members)

    async def on_signal_message(self, msg: sigapi.IncomingMessagev1):
        """
        User has received 'something' from signal

        :param msg:
        """
        if (sync_msg := msg.sync_message) is not None:
            if sync_msg.contacts is not None and msg.sync_message.contactsComplete:
                log.debug("Received a sync contact updates")
                await self.add_contacts_to_roster()

            if (sent := sync_msg.sent) is None:
                # Probably a 'message read' marker
                log.debug("No sent message in this sync message")
                return
            sent_msg = sent.message
            if sent_msg.group or sent_msg.groupV2:
                return

            contact = await self.contacts.by_json_address(sent.destination)

            await contact.send_attachments(sent_msg.attachments, carbon=True)

            if (body := sent_msg.body) is not None:
                contact.send_text(
                    body=body,
                    when=datetime.fromtimestamp(sent_msg.timestamp / 1000),
                    legacy_id=sent_msg.timestamp,
                    carbon=True,
                )
            if (reaction := sent_msg.reaction) is not None:
                try:
                    fut = self.reaction_ack_futures.pop(
                        (reaction.targetSentTimestamp, reaction.emoji)
                    )
                except KeyError:
                    contact.react(
                        reaction.targetSentTimestamp,
                        () if reaction.remove else reaction.emoji,
                        carbon=True,
                    )
                else:
                    fut.set_result(None)
            if (delete := sent_msg.remoteDelete) is not None:
                contact.retract(delete.target_sent_timestamp, carbon=True)

        contact = await self.contacts.by_json_address(msg.source)

        if (data := msg.data_message) is not None:
            if data.group:
                return

            if data.groupV2:
                muc = await self.bookmarks.by_legacy_id(data.groupV2.id)
                entity = await muc.get_participant_by_contact(contact)
            else:
                entity = contact

            reply_self = False
            if (quote := data.quote) is None:
                reply_to_msg_id = None
                reply_to_fallback_text = None
                reply_to_author = None
            else:
                reply_to_msg_id = quote.id
                reply_to_fallback_text = quote.text
                reply_self = quote.author.uuid == msg.source.uuid

                if data.groupV2:
                    reply_to_author = await muc.get_participant(muc.user_nick)
                else:
                    reply_to_author = None

            kwargs = dict(
                reply_to_msg_id=reply_to_msg_id,
                reply_to_author=reply_to_author,
                reply_to_fallback_text=reply_to_fallback_text,
                reply_self=reply_self,
                when=datetime.fromtimestamp(msg.data_message.timestamp / 1000),
            )

            msg_id = data.timestamp
            text = data.body
            await entity.send_attachments(
                data.attachments, legacy_msg_id=None if text else msg_id, **kwargs
            )
            if text:
                entity.send_text(body=text, legacy_msg_id=msg_id, **kwargs)
            if (reaction := data.reaction) is not None:
                self.log.debug("Reaction: %s", reaction)
                if reaction.remove:
                    entity.react(reaction.targetSentTimestamp)
                else:
                    entity.react(reaction.targetSentTimestamp, reaction.emoji)
            if (delete := data.remoteDelete) is not None:
                entity.retract(delete.target_sent_timestamp)

        if (typing_message := msg.typing_message) is not None:
            if g := typing_message.group_id:
                muc = await self.bookmarks.by_legacy_id(g)
                entity = await muc.get_participant_by_contact(contact)
            else:
                entity = contact

            action = typing_message.action
            if action == "STARTED":
                entity.active()
                entity.composing()
            elif action == "STOPPED":
                entity.paused()

        if (receipt_message := msg.receipt_message) is not None:
            type_ = receipt_message.type
            if type_ == "DELIVERY":
                for t in msg.receipt_message.timestamps:
                    entity = await self.__get_entity_by_sent_msg_id(contact, t)
                    entity.received(t)
            elif type_ == "READ":
                # no need to mark all messages read, just the last one, see
                # "8.1. Optimizations" in XEP-0333
                t = max(msg.receipt_message.timestamps)
                entity = await self.__get_entity_by_sent_msg_id(contact, t)
                entity.displayed(t)

    async def __get_entity_by_sent_msg_id(self, contact: "Contact", t: int):
        self.log.debug("Looking for %s in %s", t, self.sent_in_muc)
        group = self.sent_in_muc.get(t)
        if group:
            return await group.get_participant_by_contact(contact)
        return contact

    @handle_unregistered_recipient
    async def send_text(
        self,
        text: str,
        chat: Union["Contact", "MUC"],
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text=None,
        reply_to: Optional[Union["Contact", "Participant"]] = None,
    ) -> int:
        address, group = self._get_args_from_entity(chat)
        if reply_to_msg_id is None:
            quote = None
        elif reply_to is None:
            quote = None
            self.log.warning(
                "An XMPP client did not include reply to=, so we cannot make a quote here."
            )
        else:
            quote = sigapi.JsonQuotev1(
                id=reply_to_msg_id,
                author=sigapi.JsonAddressv1(uuid=await self.user_uuid)
                if reply_to is None
                else reply_to.signal_address,
                text=reply_to_fallback_text or "",
            )
        response = await (await self.signal).send(
            account=self.phone,
            recipientAddress=address,
            recipientGroupId=group,
            messageBody=text,
            quote=quote,
        )
        result = response.results[0]
        log.debug("Result: %s", result)
        if result.networkFailure or result.proof_required_failure:
            raise XMPPError(str(result))
        elif result.identityFailure:
            chat = cast("Contact", chat)
            s = await self.signal
            identities = (
                await s.get_identities(
                    account=self.phone,
                    address=chat.signal_address,
                )
            ).identities
            ans = await self.input(
                f"The identity of {chat.legacy_id} has changed. "
                f"Do you want to trust all their identities and resend the message?"
            )
            if ans.lower().startswith("y"):
                for i in identities:
                    await (await self.signal).trust(
                        account=self.phone,
                        address=chat.signal_address,
                        safety_number=i.safety_number,
                    )
                await self.send_text(text, chat, reply_to_msg_id=reply_to_msg_id)
            else:
                raise XMPPError(str(result))
        legacy_msg_id = response.timestamp
        if group:
            self.sent_in_muc[legacy_msg_id] = cast("MUC", chat)
        return legacy_msg_id

    @handle_unregistered_recipient
    async def send_file(self, url: str, chat: "Contact", *, reply_to_msg_id=None):
        s = await self.signal
        address, group = self._get_args_from_entity(chat)
        async with aiohttp.ClientSession() as client:
            async with client.get(url=url) as r:
                with tempfile.TemporaryDirectory(
                    dir=config.SIGNALD_SOCKET.parent,
                ) as d:
                    os.chmod(d, 0o777)
                    with open(Path(d) / r.url.name, "wb") as f:
                        f.write(await r.content.read())
                        os.chmod(
                            f.name, 0o666
                        )  # temp file is 0600 https://stackoverflow.com/a/10541972/5902284
                        signal_r = await s.send(
                            account=self.phone,
                            recipientAddress=address,
                            recipientGroupId=group,
                            attachments=[sigapi.JsonAttachmentv1(filename=f.name)],
                        )
                        return signal_r.timestamp

    async def active(self, c: "Contact"):
        pass

    async def inactive(self, c: "Contact"):
        pass

    @staticmethod
    def _get_args_from_entity(e):
        if e.is_group:
            address = None
            group = e.legacy_id
        else:
            address = e.signal_address
            group = None
        return address, group

    @handle_unregistered_recipient
    async def composing(self, c: "Contact"):
        self.log.debug("COMPOSING %s", c)
        address, group = self._get_args_from_entity(c)
        await (await self.signal).typing(
            account=self.phone,
            address=address,
            group=group,
            typing=True,
        )

    @handle_unregistered_recipient
    async def displayed(self, legacy_msg_id: int, entity: Union["Contact", "MUC"]):
        if entity.is_group:
            entity = cast("MUC", entity)
            address = entity.sent.get(legacy_msg_id)
            if address is None:
                self.log.debug(
                    "Ignoring read mark %s in %s", legacy_msg_id, entity.sent
                )
                return
        else:
            entity = cast("Contact", entity)
            address = entity.signal_address

        await (await self.signal).mark_read(
            account=self.phone,
            to=address,
            timestamps=[legacy_msg_id],
        )

    @handle_unregistered_recipient
    async def react(
        self, legacy_msg_id: int, emojis: list[str], c: Union["Contact", "MUC"]
    ):
        address, group = self._get_args_from_entity(c)
        if group:
            return
        c = cast("Contact", c)

        remove = len(emojis) == 0
        if remove:
            try:
                emoji = c.user_reactions.pop(legacy_msg_id)
            except KeyError:
                self.send_gateway_message(
                    f"Slidge failed to remove your reactions on message '{legacy_msg_id}'"
                )
                self.log.warning("Could not find the emoji to remove reaction")
                return
        else:
            emoji = emojis[-1]
            if len(emojis) > 1:
                self.send_gateway_message("Only one reaction per message on signal")
                c.react(legacy_msg_id, emoji, carbon=True)
            c.user_reactions[legacy_msg_id] = emoji

        response = await (await self.signal).react(
            username=self.phone,
            recipientAddress=c.signal_address,
            recipientGroupId=group,
            reaction=sigapi.JsonReactionv1(
                emoji=emoji,
                remove=remove,
                targetAuthor=sigapi.JsonAddressv1(number=self.phone)
                if legacy_msg_id in self.sent
                else c.signal_address,
                targetSentTimestamp=legacy_msg_id,
            ),
        )
        result = response.results[0]
        if (
            result.networkFailure
            or result.identityFailure
            or result.proof_required_failure
        ):
            raise XMPPError(str(result))
        f = self.reaction_ack_futures[
            (legacy_msg_id, emoji)
        ] = self.xmpp.loop.create_future()
        await f

    @handle_unregistered_recipient
    async def retract(self, legacy_msg_id: int, c: "Contact"):
        address, group = self._get_args_from_entity(c)
        try:
            await (await self.signal).remote_delete(
                account=self.phone,
                address=address,
                group=group,
                timestamp=legacy_msg_id,
            )
        except sigexc.SignaldException as e:
            raise XMPPError(text=f"Something went wrong during remote delete: {e}")

    async def add_device(self, uri: str):
        try:
            await (await self.signal).add_device(account=self.phone, uri=uri)
        except sigexc.SignaldException as e:
            self.send_gateway_message(f"Problem: {e}")
        else:
            self.send_gateway_message("Linking OK")


log = logging.getLogger(__name__)
