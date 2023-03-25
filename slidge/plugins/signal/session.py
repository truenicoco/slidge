import asyncio
import functools
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union, cast

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi

from slidge import BaseSession, FormField, GatewayUser, SearchResult, XMPPError
from slidge.util.util import is_valid_phone_number

if TYPE_CHECKING:
    from .contact import Contact, Roster
    from .gateway import Gateway
    from .group import Bookmarks, Participant, MUC

from . import config


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


Recipient = Union["Contact", "MUC"]


class Session(BaseSession[int, Recipient]):
    """
    Represents a signal account
    """

    xmpp: "Gateway"
    contacts: "Roster"
    bookmarks: "Bookmarks"

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
        self.user_uuid: asyncio.Future[str] = self.xmpp.loop.create_future()
        self.connected = self.xmpp.loop.create_future()
        self.sent_in_muc = dict[int, "MUC"]()

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> int:
        try:
            return int(i)
        except ValueError:
            raise XMPPError(
                "item-not-found", f"This is not a valid signal message timestamp: {i}"
            )

    @handle_unregistered_recipient
    async def paused(self, c: Recipient, thread=None):
        address, group = self._get_args_from_entity(c)
        await (await self.signal).typing(
            account=self.phone, typing=False, address=address, group=group
        )

    @handle_unregistered_recipient
    async def correct(self, c: Recipient, text: str, legacy_msg_id: Any, thread=None):
        pass

    async def search(self, form_values: dict[str, str]):
        phone = form_values.get("phone")
        if phone is None:
            raise XMPPError("bad-request", "Please enter a phone number")

        if not is_valid_phone_number(phone):
            raise XMPPError(
                "bad-request", "This does not look like a valid phone number"
            )

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
        await (await self.signal).subscribe(account=self.phone)
        await self.connected
        sig = await self.signal
        # TODO: store the account UUID on registration so we don't have to do that
        try:
            # sometimes doesn't work with own phone number
            profile = await sig.get_profile(
                account=self.phone, address=sigapi.JsonAddressv1(number=self.phone)
            )
        except sigexc.ProfileUnavailableError:
            accounts = await sig.list_accounts()
            for a in accounts.accounts:
                if a.address.number == self.phone:
                    profile = await sig.get_profile(
                        account=self.phone, address=a.address
                    )
                    break
            else:
                raise RuntimeError("Could not find the signal address of your account")
        nick: str = profile.name or profile.profile_name  # type: ignore
        if nick is not None:
            nick = nick.replace("\u0000", " ")
            self.bookmarks.user_nick = nick
        self.user_uuid.set_result(profile.address.uuid)

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

    async def logout(self):
        await (await self.signal).unsubscribe(account=self.phone)

    async def on_signal_message(self, msg: sigapi.IncomingMessagev1):
        """
        User has received 'something' from signal

        :param msg:
        """

        if sync_msg := msg.sync_message:
            await self.on_signal_sync_message(sync_msg)

        contact = await self.contacts.by_json_address(msg.source)

        if call := msg.call_message:
            self.on_signal_call_message(contact, call)

        if data := msg.data_message:
            await self.on_signal_data_message(contact, data)

        if receipt := msg.receipt_message:
            await self.on_signal_receipt(contact, receipt)

    async def on_signal_sync_message(self, sync_msg: sigapi.JsonSyncMessagev1):
        if sync_msg.contacts is not None and sync_msg.contactsComplete:
            log.debug("Received a sync contact updates")
            await self.contacts.fill()

        if (sent := sync_msg.sent) is None:
            # Probably a 'message read' marker
            log.debug("No sent message in this sync message")
            return
        sent_msg = sent.message
        if sent_msg.group:
            # group V1 not supported
            return
        elif g := sent_msg.groupV2:
            muc = await self.bookmarks.by_legacy_id(g.id)
            contact = await muc.get_user_participant()
        else:
            contact = await self.contacts.by_json_address(sent.destination)

        await self.on_signal_data_message(contact, sent_msg, carbon=True)

    @staticmethod
    def on_signal_call_message(contact: "Contact", _call_message: sigapi.CallMessagev1):
        contact.send_text(
            "/me tried to call you but this is not supported by this slidge-signal"
        )

    async def on_signal_data_message(
        self,
        contact: Union["Contact", "Participant"],
        data: sigapi.JsonDataMessagev1,
        carbon=False,
    ):
        if data.group:
            return

        if data.groupV2 and not carbon:
            muc = await self.bookmarks.by_legacy_id(data.groupV2.id)
            entity = await muc.get_participant_by_contact(contact)
        else:
            entity = contact
        await entity.send_signal_msg(data, carbon)

    async def on_signal_typing(
        self, contact: "Contact", typing_message: sigapi.TypingMessagev1
    ):
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

    async def on_signal_receipt(
        self, contact: "Contact", receipt_message: sigapi.ReceiptMessagev1
    ):
        type_ = receipt_message.type
        if type_ == "DELIVERY":
            for t in receipt_message.timestamps:
                entity = await self.__get_entity_by_sent_msg_id(contact, t)
                entity.received(t)
        elif type_ == "READ":
            # no need to mark all messages read, just the last one, see
            # "8.1. Optimizations" in XEP-0333
            t = max(receipt_message.timestamps)
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
        chat: Recipient,
        text: str,
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text=None,
        reply_to: Optional[Union["Contact", "Participant"]] = None,
        thread=None,
    ) -> int:
        address, group = self._get_args_from_entity(chat)
        if reply_to_msg_id is None:
            quote = None
        elif reply_to is None:
            quote = None
            self.log.warning(
                "An XMPP client did not include reply to=, so we cannot make a quote"
                " here."
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
            raise XMPPError("internal-server-error", str(result))
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
                "Do you want to trust all their identities and resend the message?"
            )
            if ans.lower().startswith("y"):
                for i in identities:
                    await (await self.signal).trust(
                        account=self.phone,
                        address=chat.signal_address,
                        safety_number=i.safety_number,
                    )
                await self.send_text(chat, text, reply_to_msg_id=reply_to_msg_id)
            else:
                raise XMPPError("internal-server-error", str(result))
        legacy_msg_id = response.timestamp
        if group:
            self.sent_in_muc[legacy_msg_id] = cast("MUC", chat)
        return legacy_msg_id

    @handle_unregistered_recipient
    async def send_file(
        self,
        chat: "Recipient",
        url: str,
        *,
        http_response,
        reply_to_msg_id=None,
        reply_to_fallback_text=None,
        reply_to: Optional[Union["Contact", "Participant"]] = None,
        thread=None,
    ):
        s = await self.signal
        address, group = self._get_args_from_entity(chat)
        with tempfile.TemporaryDirectory(
            dir=config.SIGNALD_SOCKET.parent,
        ) as d:
            os.chmod(d, 0o777)
            with open(Path(d) / http_response.url.name, "wb") as f:
                f.write(await http_response.content.read())
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

    async def active(self, c: Recipient, thread=None):
        pass

    async def inactive(self, c: Recipient, thread=None):
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
    async def composing(self, c: Recipient, thread=None):
        self.log.debug("COMPOSING %s", c)
        address, group = self._get_args_from_entity(c)
        await (await self.signal).typing(
            account=self.phone,
            address=address,
            group=group,
            typing=True,
        )

    @handle_unregistered_recipient
    async def displayed(self, entity: Recipient, legacy_msg_id: int, thread=None):
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
        self,
        chat: Recipient,
        legacy_msg_id: int,
        emojis: list[str],
        thread=None,
    ):
        address, group = self._get_args_from_entity(chat)
        if legacy_msg_id in self.sent:
            target_author = sigapi.JsonAddressv1(number=self.phone)
        else:
            if chat.is_group:
                chat = cast("MUC", chat)
                if legacy_msg_id in self.sent_in_muc:
                    target_author = sigapi.JsonAddressv1(number=self.phone)
                else:
                    target_author = chat.sent.get(legacy_msg_id)
                if target_author is None:
                    self.log.warning(
                        "Could not the message author to react to %s", legacy_msg_id
                    )
                    return
            else:
                chat = cast("Contact", chat)
                target_author = chat.signal_address

        if chat.is_group:
            recipient_address = None
        else:
            chat = cast("Contact", chat)
            recipient_address = chat.signal_address

        remove = len(emojis) == 0
        if remove:
            try:
                emoji = chat.user_reactions.pop(legacy_msg_id)
            except KeyError:
                self.send_gateway_message(
                    "Slidge failed to remove your reactions on message"
                    f" '{legacy_msg_id}'"
                )
                self.log.warning("Could not find the emoji to remove reaction")
                raise XMPPError(
                    "undefined-condition",
                    "Could not remove your reactions to this message",
                )
        else:
            emoji = emojis[-1]

        response = await (await self.signal).react(
            username=self.phone,
            recipientAddress=recipient_address,
            recipientGroupId=group,
            reaction=sigapi.JsonReactionv1(
                emoji=emoji,
                remove=remove,
                targetAuthor=target_author,
                targetSentTimestamp=legacy_msg_id,
            ),
        )
        result = response.results[0]
        if (
            result.networkFailure
            or result.identityFailure
            or result.proof_required_failure
        ):
            raise XMPPError("internal-server-error", str(result))
        chat.user_reactions[legacy_msg_id] = emoji

    @handle_unregistered_recipient
    async def retract(self, c: Recipient, legacy_msg_id: int, thread=None):
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
