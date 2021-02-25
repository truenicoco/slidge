from datetime import datetime
from pathlib import Path
from configparser import ConfigParser
import asyncio
import typing
import time
import logging
import re
import tempfile

import qrcode
from slixmpp import Message, ComponentXMPP, Iq, Presence
from slixmpp.plugins.xep_0363 import HTTPError

from pysignald_async import SignaldAPI, SignaldException
from pysignald_async.api import JsonAddressv1, JsonMessageEnvelopev1, Profilev1

from slidge.api import (
    BaseGateway,
    BaseLegacyClient,
    sessions,
    Buddy,
    LegacyMuc,
    User,
    LegacyError,
)


class Gateway(BaseGateway):
    REGISTRATION_FIELDS = {"username"}
    REGISTRATION_INSTRUCTIONS = (
        "Enter your phone (+XXX…)\n"
        "You will then receive a chat message from the gateway with further "
        "instructions to validate your account."
    )

    COMPONENT_NAME = "Signal gateway"
    COMPONENT_TYPE = "signal"

    def __init__(self, config: ConfigParser, client_cls: "Client"):
        BaseGateway.__init__(self, config=config, client_cls=client_cls)
        self.add_event_handler("session_bind", self.connect_signald)
        self.add_event_handler("session_start", self.startup)

    async def startup(self, event):
        await self.signald_connected

    async def connect_signald(self, event=None):
        loop = self.loop
        self.signald_connected = loop.create_future()
        while True:
            try:
                _, signald = await loop.create_unix_connection(
                    SignaldAPI, path=self.config["legacy"]["socket"]
                )
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(5)
            else:
                break
        self.legacy_client.signald = signald
        signald.handle_envelope = self.legacy_client.handle_envelope
        signald.logger.setLevel(
            getattr(logging, self.config["legacy"].get("logging").upper())
        )
        signald.on_con_lost.add_done_callback(
            lambda fut: loop.create_task(self.connect_signald())
        )
        self.signald_connected.set_result(True)


class Client(BaseLegacyClient):
    def __init__(self, xmpp: typing.Optional[Gateway] = None):
        self.xmpp: typing.Optional[Gateway] = None
        self.signald: typing.Optional[SignaldAPI] = None
        self.timestamps: typing.Dict[int, Message] = {}
        self.sent_messages: typing.Dict[str, int] = {}

    async def account_is_present(self, phone: str):
        account_list = await self.signald.list_accounts()
        for account in account_list.accounts:
            if account.username == phone:
                return True
        return False

    async def validate(self, ifrom, reg):
        phone = reg["username"]
        if not bool(re.match(r"^\+\d+$", phone)):
            raise ValueError("This does not look like a valid phone number")
        if await self.account_is_present(phone=phone):
            if not self.config.getboolean("account-reuse"):
                raise ValueError(
                    "This phone is already used on this gateway. "
                    "Contact the gateway admin."
                )
        else:
            await self.link_or_register(phone, ifrom)

    async def link_or_register(self, phone, jid):
        xmpp = self.xmpp
        try:
            choice = await xmpp.prompt(
                jid=jid,
                mbody="Do you want to [register] a new signal account using "
                "your phone number or [link] the gateway as an additional device?",
            )
            if choice == "register":
                try:
                    await self.signald.register(username=phone)
                except SignaldException:
                    # TODO: handle failed captcha
                    captcha = await xmpp.prompt(
                        jid=jid,
                        mbody="Something went wront, signal probably wants a captcha. Go "
                        "to https://signalcaptchas.org/registration/generate.html "
                        "and copy the redirect URL here. More info at "
                        "https://gitlab.com/signald/signald/-/wikis/Captchas",
                    )
                    await self.signald.register(
                        username=phone,
                        captcha=captcha.replace("signalcaptcha://", ""),
                    )
                code = await xmpp.prompt(
                    jid=jid, mbody="Enter the code you received by SMS"
                )
                await self.signald.verify(username=phone, code=code)
                name = await xmpp.prompt(jid=jid, mbody="Enter a profile name")
                await self.signald.set_profile(account=phone, name=name)
            elif choice == "link":
                linking_uri = await self.signald.generate_linking_uri()
                xmpp.send_message(mto=jid, mbody=linking_uri.uri)
                img = qrcode.make(linking_uri.uri)
                with tempfile.NamedTemporaryFile(suffix=".png") as fp:
                    img.save(fp)
                    try:
                        url = await self.xmpp["xep_0363"].upload_file(
                            fp.name,
                            domain=self.xmpp.server_host,
                            timeout=10,
                        )
                    # FIXME: doesn't work for me (404 on the put URL), but uploading from gajim is fine :/
                    except Exception as e:
                        log.exception(e)
                        xmpp.send_message(
                            mto=jid,
                            mbody=f"could not send the image, gotta make your own QR code",
                        )
                    else:
                        xmpp.send_message(mto=jid, mbody=url)
                try:
                    account = await asyncio.wait_for(
                        self.signald.finish_link(
                            device_name=self.config.get("device-name", "signald"),
                            session_id=linking_uri.session_id,
                        ),
                        timeout=120,
                    )

                except asyncio.TimeoutError:
                    raise ValueError("Time out, please retry")
            else:
                raise ValueError("Didn't get it")
        except ValueError:
            raise
        except Exception as e:  # Too broad
            raise ValueError(f"Something didn't work out, please retry: {e}")

    async def login(self, user: User):
        await self.signald.subscribe(username=user.legacy_id)

    async def logout(self, user: User):
        await self.signald.unsubscribe(username=user.legacy_id)

    async def get_buddies(self, user: User) -> typing.List[Buddy]:
        profile_list = await self.signald.list_contacts(account=user.legacy_id)
        buddies = []
        for p in profile_list.profiles:
            profile = await self.signald.get_profile(
                account=user.legacy_id, address=p.address
            )
            avatar = profile.avatar
            if avatar is not None:
                avatar = Path(self.config.get("path")) / "avatars" / avatar

            buddy = Buddy(legacy_id=p.address.number)
            buddy.name = (resolve_name_from_profile(profile),)
            buddy.avatar = avatar

            buddies.append(buddy)
        return buddies

    async def muc_list(self, user: User) -> typing.List[LegacyMuc]:
        nickname = resolve_name_from_profile(
            await self.signald.get_profile(
                account=user.legacy_id, address=JsonAddressv1(number=user.legacy_id)
            )
        )
        muc_list = []
        for g in (await self.signald.list_groups(account=user.legacy_id)).groups:
            muc = LegacyMuc(legacy_id=g.id, subject=g.title, user_nickname=nickname)
            muc_list.append(muc)
        return muc_list

    async def muc_occupants(self, user: User, legacy_group_id: str) -> typing.List[str]:
        group = await self.signald.get_group(
            account=user.legacy_id, groupID=legacy_group_id
        )
        nicks = []
        for address in group.members:
            profile = await self.signald.get_profile(
                account=user.legacy_id, address=address
            )
            if profile.address.number == user.legacy_id:
                continue
            nickname = resolve_name_from_profile(profile)
            nicks.append(nickname)
        return nicks

    async def send_receipt(self, user: User, receipt: Message):
        # It seems signald does that automatically (?)
        pass

    async def send_message(self, user: User, legacy_buddy_id: str, msg: Message):
        timestamp_ms = time.time_ns() // 1_000_000
        try:
            await self.signald.send(
                username=user.legacy_id,
                recipientAddress=JsonAddressv1(number=legacy_buddy_id),
                messageBody=msg["body"],
                timestamp=timestamp_ms,
            )
        except SignaldException as e:
            raise LegacyError(e.msg)
        else:
            self.timestamps[timestamp_ms] = msg

    async def send_muc_message(self, user: User, msg: Message, legacy_group_id: str):
        try:
            await self.signald.send(
                username=user.legacy_id,
                recipientGroupId=legacy_group_id,
                messageBody=msg["body"],
            )
        except SignaldException as e:
            raise LegacyError(e.msg)

    async def send_composing(self, user: User, legacy_buddy_id: str):
        # TODO: handle the (2?) receipts this triggers
        try:
            await self.signald.get_response(
                {
                    "type": "typing_started",
                    "username": user.legacy_id,
                    "recipientAddress": {"number": legacy_buddy_id},
                }
            )
        except SignaldException as e:
            raise LegacyError(e.msg)

    async def send_pause(self, user: User, legacy_buddy_id: str):
        # TODO: handle the (2?) receipts this triggers
        try:
            await self.signald.get_response(
                {
                    "type": "typing_stopped",
                    "username": user.legacy_id,
                    "recipientAddress": {"number": legacy_buddy_id},
                }
            )
        except SignaldException as e:
            raise LegacyError(e.msg)

    async def send_read_mark(self, user: User, legacy_buddy_id: str, msg_id: str):
        try:
            timestamp = self.sent_messages.pop(msg_id)
        except KeyError:
            log.debug("Ignoring read mark for msg we didn't send")
            return
        await self.signald.mark_read(
            account=user.legacy_id,
            to=JsonAddressv1(number=legacy_buddy_id),
            timestamps=[timestamp],
        )

    def handle_envelope(self, envelope: JsonMessageEnvelopev1):
        asyncio.create_task(self.on_envelope(envelope))

    async def on_envelope(self, envelope: JsonMessageEnvelopev1):
        source_number = envelope.source.number
        legacy_id = envelope.username
        session = sessions.by_legacy_id(legacy_id)
        if envelope.typing is not None:
            action = envelope.typing.action
            if envelope.typing.groupId is not None:
                return  # No typing notif for groups
            if action == "STARTED":
                session.buddies.by_legacy_id(source_number).starts_typing()
            elif action == "STOPPED":
                session.buddies.by_legacy_id(source_number).stopped_typing()
        if envelope.dataMessage is not None:
            group = envelope.dataMessage.groupV2
            if group is None:
                if envelope.dataMessage.body is not None:
                    body = envelope.dataMessage.body
                    timestamp_ms = envelope.dataMessage.timestamp
                    msg = session.buddies.by_legacy_id(source_number).send_xmpp_message(body)
                    self.sent_messages[msg["id"]] = timestamp_ms
            else:
                fut = asyncio.create_task(
                    self.signald.get_profile(account=legacy_id, address=envelope.source)
                )
                if envelope.dataMessage.body is not None:
                    fut.add_done_callback(
                        lambda fut_profile: session.mucs.by_legacy_id(group.id).to_user(
                            nick=resolve_name_from_profile(fut_profile.result()),
                            body=envelope.dataMessage.body,
                        )
                    )
        if envelope.syncMessage is not None:
            sent = envelope.syncMessage.sent
            if sent is not None:
                if sent.message is not None:
                    if sent.message.body is not None:
                        group = sent.message.groupV2
                        if group is None:
                            session.buddies.by_legacy_id(
                                sent.destination.number
                            ).send_xmpp_carbon(
                                body=sent.message.body,
                                timestamp_iso=timestamp_to_str(sent.message.timestamp),
                            )
                        else:
                            session.mucs.by_legacy_id(group.id).carbon(
                                sent.message.body
                            )
        if envelope.type == "RECEIPT":
            # Weird to use the timestamp of the envelope here, but is seems to work
            try:
                msg = self.timestamps[envelope.timestamp]
            except KeyError:
                log.debug("Receipt for a message we didn't send")
            else:
                session.buddies.by_legacy_id(source_number).send_xmpp_ack(msg)
        if envelope.receipt is not None:
            if envelope.source is not None:
                if source_number is not None:
                    if envelope.receipt.type == "READ":
                        for timestamp in envelope.receipt.timestamps:
                            try:
                                msg = self.timestamps.pop(timestamp)
                            except KeyError:
                                log.debug("Read marker for a message we didn't send")
                            else:
                                session.buddies.by_legacy_id(source_number).send_xmpp_read(
                                    msg
                                )


def resolve_name_from_profile(profile: Profilev1) -> str:
    nickname = profile.name
    if nickname is None or nickname == "":
        nickname = profile.profile_name
        if nickname is None or nickname == "":
            nickname = profile.address.number
    return nickname


def timestamp_to_str(timestamp_ms: int) -> str:
    if timestamp_ms is None:
        return ""
    timestamp = timestamp_ms // 1000
    return datetime.fromtimestamp(timestamp).isoformat()[:19] + "Z"


log = logging.getLogger(__name__)
