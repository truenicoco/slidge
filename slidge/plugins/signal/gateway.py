import asyncio
import functools
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi
import qrcode
from aiosignald import SignaldAPI
from slixmpp import JID

from slidge import *
from slidge.core.command import Command, CommandAccess, Form, FormField, TableResult
from slidge.core.command.register import RegistrationType
from slidge.util import is_valid_phone_number

if TYPE_CHECKING:
    from .session import Session

from . import config, txt


class Link(Command):
    NAME = "Link your signal account to slidge"
    HELP = (
        "Use an existing signal account with slidge as a 'secondary device'. "
        "You will need to keep your 'primary' device running. "
        "If you wish to use slidge exclusively, you should 'register a new "
        "signal account' instead"
    )
    CHAT_COMMAND = NODE = "link"
    ACCESS = CommandAccess.NON_USER

    xmpp: "Gateway"

    async def run(self, _session, ifrom: JID, *args):
        return Form(
            title="Linking your signal account to slidge.",
            instructions="Fill this form and prepare the official signal app. "
            "You will have to scan a QR code from it.",
            fields=[
                FormField(
                    "phone",
                    label="Your phone number in international format (starting with +)",
                    required=True,
                ),
                FormField(
                    "device",
                    label="A 'device' name, to recognize this signal session.",
                    required=True,
                ),
            ],
            handler=self.show_code,
        )

    async def show_code(self, form_values: dict, _session, ifrom):
        resp = await (await self.xmpp.signal).generate_linking_uri()
        qr_text = resp.uri
        qr = qrcode.make(qr_text)
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            qr.save(f.name)
            img_url = await self.xmpp.send_file(f.name, mto=ifrom)
        self.xmpp.send_text(qr_text, mto=ifrom)
        return Form(
            title="Flash this",
            instructions="Flash this QR in the appropriate place.",
            fields=[
                FormField(
                    "qr_img",
                    type="fixed",
                    value=qr_text,
                    image_url=img_url,
                ),
                FormField(
                    "qr_text",
                    type="fixed",
                    value=qr_text,
                    label="Text encoded in the QR code",
                ),
                FormField(
                    "qr_img_url",
                    type="fixed",
                    value=img_url,
                    label="URL of the QR code image",
                ),
            ],
            handler=self.finish,
            handler_args=[
                resp.session_id,
                form_values.get("phone"),
                form_values.get("device"),
            ],
        )

    async def finish(self, _form_values, _session, ifrom, session_id, phone, device):
        try:
            await (await self.xmpp.signal).finish_link(
                device_name=device,
                session_id=session_id,
            )
        except sigexc.ScanTimeoutError:
            raise XMPPError(
                "not-authorized", "You took too much time to scan. Please retry."
            )
        except sigexc.SignaldException as e:
            raise XMPPError("not-authorized", f"Something went wrong: {e}.")

        user_store.add(ifrom, {"phone": phone})


class LinkedDevices(Command):
    NAME = "List the devices linked to your signal account"
    HELP = "List all the devices that have access to your signal account"
    CHAT_COMMAND = NODE = "signal-devices"
    ACCESS = CommandAccess.USER_LOGGED

    xmpp: "Gateway"

    async def run(self, session, ifrom: JID, *args):
        assert session is not None
        devices = await (await self.xmpp.signal).get_linked_devices(
            account=session.user.registration_form["phone"]
        )

        return TableResult(
            description="Your signal devices",
            fields=[
                FormField("id", label="Device ID"),
                FormField("name", label="Device name"),
                FormField("created", label="Created"),
                FormField("last_seen", label="Last seen"),
            ],
            items=[
                {
                    "name": d.name,
                    "id": str(d.id),
                    "created": fmt_time(d.created),
                    "last_seen": fmt_time(d.lastSeen),
                }
                for d in devices.devices
            ],
        )


class LinkDevice(Command):
    NAME = "Link a new device to your signal account"
    HELP = "Use slidge to add a device to your signal account"
    CHAT_COMMAND = NODE = "add-device"
    ACCESS = CommandAccess.USER_LOGGED

    xmpp: "Gateway"

    async def run(self, session, ifrom: JID, *args):
        assert session is not None
        if len(args) == 1:
            return await self.finish({"uri": args[0]}, session, ifrom)
        return Form(
            title=self.NAME,
            instructions=self.HELP,
            fields=[
                FormField(
                    "uri",
                    label="Linking URI.  Use a QR code reader app to get it from official signal clients.",
                    required=True,
                )
            ],
            handler=self.finish,
        )

    async def finish(self, form_values, session: "Session", _ifrom):
        uri = form_values["uri"]
        await (await self.xmpp.signal).add_device(
            account=session.user.registration_form["phone"], uri=uri
        )
        return "Your new device is now linked to your signal account."


class Gateway(BaseGateway):
    COMPONENT_NAME = "Signal (slidge)"
    COMPONENT_TYPE = "signal"
    COMPONENT_AVATAR = (
        "https://upload.wikimedia.org/wikipedia/commons/5/56/Logo_Signal..png"
    )
    REGISTRATION_INSTRUCTIONS = txt.REGISTRATION_INSTRUCTIONS
    REGISTRATION_FIELDS = txt.REGISTRATION_FIELDS
    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE

    ROSTER_GROUP = "Signal"

    SEARCH_FIELDS = [
        FormField(var="phone", label="Phone number", required=True),
    ]

    signal: asyncio.Future["Signal"]
    sessions_by_phone: dict[str, "Session"] = {}

    GROUPS = True

    def __init__(self):
        super().__init__()
        self.signal: asyncio.Future[Signal] = self.loop.create_future()
        self.loop.create_task(self.connect_signal(config.SIGNALD_SOCKET))

    async def connect_signal(self, socket: Path):
        """
        Establish connection to the signald socker
        """
        log.debug("Connecting to signald...")
        _, signal = await self.loop.create_unix_connection(
            functools.partial(Signal, self), str(socket)
        )
        self.signal.set_result(signal)
        await signal.on_con_lost
        log.error("Signald UNIX socket connection lost!")
        raise RuntimeError("Signald socket connection lost")

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        phone = registration_form.get("phone")
        if not is_valid_phone_number(phone):
            raise ValueError("Not a valid phone number")
        for u in user_store.get_all():
            if u.registration_form.get("phone") == phone:
                raise XMPPError(
                    "not-allowed",
                    text="Someone is already using this phone number on this server.\n",
                )
        signal = await self.signal
        try:
            await signal.register(phone, captcha=registration_form.get("captcha"))
        except sigexc.CaptchaRequiredError:
            raise XMPPError(
                "not-acceptable",
                "Please fill the captcha to register your phone number.",
            )

    async def validate_two_factor_code(self, user: GatewayUser, code: str):
        signal = await self.signal
        phone = user.registration_form.get("phone")
        await signal.verify(phone, code)
        await signal.set_profile(account=phone, name=user.registration_form.get("name"))

    async def unregister(self, user: GatewayUser):
        try:
            await (await self.signal).delete_account(
                account=user.registration_form.get("phone"), server=False
            )
        except sigexc.NoSuchAccountError:
            # if user unregisters before completing the registration process,
            # NoSuchAccountError is raised by signald
            pass

        log.info("Removed user: %s", user)


# noinspection PyPep8Naming,GrazieInspection
class Signal(SignaldAPI):
    """
    Extends :class:`.SignaldAPI` with handlers for events we are interested in.
    """

    def __init__(self, xmpp: Gateway):
        super().__init__()
        self.sessions_by_phone = xmpp.sessions_by_phone

    async def handle_WebSocketConnectionState(
        self, state: sigapi.WebSocketConnectionStatev1, payload
    ):
        """
        We should not care much about this since

        :param state:
        :param payload:
        """
        session = self.sessions_by_phone[payload["account"]]
        await session.on_websocket_connection_state(state)

    async def handle_ListenerState(self, state: sigapi.ListenerStatev1, payload):
        """
        Deprecated in signald and replaced by WebSocketConnectionState
        Just here to avoid cluttering logs with unhandled events warnings

        :param state:
        :param payload:
        """
        pass

    async def handle_IncomingMessage(self, msg: sigapi.IncomingMessagev1, _payload):
        """
        Dispatch a signald message to the proper session.

        Can be a lot of other things than an actual message, still need to figure
        things out to cover all cases.

        :param msg: the data!
        :param _payload:
        """
        session = self.sessions_by_phone[msg.account]
        await session.on_signal_message(msg)


def fmt_time(t: float):
    return datetime.fromtimestamp(t / 1000).isoformat(timespec="minutes")


log = logging.getLogger(__name__)
