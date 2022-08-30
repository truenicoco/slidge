import asyncio
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
import aiosignald.exc as sigexc
import aiosignald.generated as sigapi
from slixmpp.exceptions import XMPPError

from slidge import *

if TYPE_CHECKING:
    from .contact import Contact, Roster
    from .gateway import Gateway

from . import txt


class Session(BaseSession["Contact", "Roster", "Gateway"]):
    """
    Represents a signal account
    """

    # contacts: Roster

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

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> int:
        try:
            return int(i)
        except ValueError:
            raise NotImplementedError

    async def paused(self, c: "Contact"):
        await (await self.signal).typing(
            account=self.phone, typing=False, address=c.signal_address
        )

    async def correct(self, text: str, legacy_msg_id: Any, c: "Contact"):
        return await self.send_text("Correction: " + text, c)

    async def search(self, form_values: dict[str, str]):
        pass

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

        return f"Connected as {self.phone}"

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
            full_profile = await (await self.signal).get_profile(
                account=self.phone, address=profile.address
            )
            contact = await self.contacts.by_json_address(profile.address)
            contact.name = profile.name or profile.profile_name
            if contact.name is not None:
                contact.name = contact.name.replace("\u0000", "")
            if full_profile.avatar is not None:
                with open(full_profile.avatar, "rb") as f:
                    contact.avatar = f.read()
            await contact.add_to_roster()
            contact.online()

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
            contact = await self.contacts.by_json_address(sent.destination)
            sent_msg = sent.message

            if (body := sent_msg.body) is not None:
                contact.carbon(
                    body=body,
                    date=datetime.fromtimestamp(sent_msg.timestamp / 1000),
                )
            if (reaction := sent_msg.reaction) is not None:
                try:
                    fut = self.reaction_ack_futures.pop(
                        (reaction.targetSentTimestamp, reaction.emoji)
                    )
                except KeyError:
                    contact.carbon_react(
                        reaction.targetSentTimestamp,
                        () if reaction.remove else reaction.emoji,
                    )
                else:
                    fut.set_result(None)
            if (delete := sent_msg.remoteDelete) is not None:
                contact.carbon_retract(delete.target_sent_timestamp)

        contact = await self.contacts.by_json_address(msg.source)

        if (data := msg.data_message) is not None:
            if (quote := data.quote) is None:
                reply_to_msg_id = None
            else:
                reply_to_msg_id = quote.id
            for attachment in data.attachments:
                with open(attachment.storedFilename, "rb") as f:
                    await contact.send_file(
                        filename=attachment.customFilename,
                        input_file=f,
                        content_type=attachment.contentType,
                        legacy_msg_id=msg.data_message.timestamp,
                        reply_to_msg_id=reply_to_msg_id,
                    )
            if (body := data.body) is not None:
                contact.send_text(
                    body=body,
                    legacy_msg_id=msg.data_message.timestamp,
                    reply_to_msg_id=reply_to_msg_id,
                )
            if (reaction := data.reaction) is not None:
                self.log.debug("Reaction: %s", reaction)
                if reaction.remove:
                    contact.react(reaction.targetSentTimestamp)
                else:
                    contact.react(reaction.targetSentTimestamp, reaction.emoji)
            if (delete := data.remoteDelete) is not None:
                contact.retract(delete.target_sent_timestamp)

        if (typing_message := msg.typing_message) is not None:
            action = typing_message.action
            if action == "STARTED":
                contact.active()
                contact.composing()
            elif action == "STOPPED":
                contact.paused()

        if (receipt_message := msg.receipt_message) is not None:
            type_ = receipt_message.type
            if type_ == "DELIVERY":
                for t in msg.receipt_message.timestamps:
                    contact.received(t)
            elif type_ == "READ":
                for t in msg.receipt_message.timestamps:
                    contact.displayed(t)

    async def send_text(self, t: str, c: "Contact", *, reply_to_msg_id=None) -> int:
        if reply_to_msg_id is None:
            quote = None
        else:
            quote = sigapi.JsonQuotev1(
                id=reply_to_msg_id,
                author=c.signal_address,
                text=""  # not sure what this accomplishes? does not seem to have any effect,
                # but must not be None or NullPointerException
            )
        response = await (await self.signal).send(
            account=self.phone,
            recipientAddress=c.signal_address,
            messageBody=t,
            quote=quote,
        )
        result = response.results[0]
        log.debug("Result: %s", result)
        if result.networkFailure or result.proof_required_failure:
            raise XMPPError(str(result))
        elif result.identityFailure:
            s = await self.signal
            identities = (
                await s.get_identities(
                    account=self.phone,
                    address=c.signal_address,
                )
            ).identities
            ans = await self.input(
                f"The identity of {c.phone} has changed. "
                f"Do you want to trust all their identities and resend the message?"
            )
            if ans.lower().startswith("y"):
                for i in identities:
                    await (await self.signal).trust(
                        account=self.phone,
                        address=c.signal_address,
                        safety_number=i.safety_number,
                    )
                await self.send_text(t, c, reply_to_msg_id=reply_to_msg_id)
            else:
                raise XMPPError(str(result))
        return response.timestamp

    async def send_file(self, u: str, c: "Contact", *, reply_to_msg_id=None):
        s = await self.signal
        async with aiohttp.ClientSession() as client:
            async with client.get(url=u) as r:
                with tempfile.TemporaryDirectory(
                    dir=Path(self.xmpp.signal_socket).parent,
                ) as d:
                    os.chmod(d, 0o777)
                    with open(Path(d) / r.url.name, "wb") as f:
                        f.write(await r.content.read())
                        os.chmod(
                            f.name, 0o666
                        )  # temp file is 0600 https://stackoverflow.com/a/10541972/5902284
                        signal_r = await s.send(
                            account=self.phone,
                            recipientAddress=c.signal_address,
                            attachments=[sigapi.JsonAttachmentv1(filename=f.name)],
                        )
                        return signal_r.timestamp

    async def active(self, c: "Contact"):
        pass

    async def inactive(self, c: "Contact"):
        pass

    async def composing(self, c: "Contact"):
        await (await self.signal).typing(
            account=self.phone,
            address=c.signal_address,
            typing=True,
        )

    async def displayed(self, legacy_msg_id: int, c: "Contact"):
        await (await self.signal).mark_read(
            account=self.phone,
            to=c.signal_address,
            timestamps=[legacy_msg_id],
        )

    async def react(self, legacy_msg_id: int, emojis: list[str], c: "Contact"):
        remove = len(emojis) == 0
        if len(emojis) == 0:
            remove = True
            emoji = ""
        else:
            emoji = emojis[-1]
            if len(emojis) > 1:
                self.send_gateway_message("Only one reaction per message on signal")
                c.carbon_react(legacy_msg_id, emoji)

        response = await (await self.signal).react(
            username=self.phone,
            recipientAddress=c.signal_address,
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

    async def retract(self, legacy_msg_id: int, c: "Contact"):
        try:
            await (await self.signal).remote_delete(
                account=self.phone, address=c.signal_address, timestamp=legacy_msg_id
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
