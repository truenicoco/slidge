import asyncio
import functools
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi
import qrcode
from aiosignald import SignaldAPI
from slixmpp import JID, Iq, Message
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0004 import Form
from slixmpp.plugins.xep_0004 import FormField as AdhocFormField

from slidge import *
from slidge.util import is_valid_phone_number

from ...core.adhoc import RegistrationType

if TYPE_CHECKING:
    from .session import Session

from . import config, txt


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

    CHAT_COMMANDS = {
        "add_device": "_chat_command_add_device",
        "get_identities": "_chat_command_get_identities",
    }

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

    def add_adhoc_commands(self):
        self.adhoc.add_command(
            node="link",
            name="Link slidge to your signal account",
            handler=self._handle_link_slidge,
            only_nonusers=True,
        )
        self.adhoc.add_command(
            node="linked_devices",
            name="Get linked devices",
            handler=self._handle_linked_devices,
            only_users=True,
        )
        self.adhoc.add_command(
            node="add_device",
            name="Link a new device",
            handler=self._handle_add_device1,
            only_users=True,
        )

    async def _handle_link_slidge(self, iq: Iq, adhoc_session: dict[str, Any]):
        user = user_store.get_by_stanza(iq)
        if user is not None:
            raise XMPPError(
                "bad-request", text="You are already registered to this gateway."
            )

        form = self["xep_0004"].make_form(
            "form",
            "Enter a device name to identify your slidge session in the official signal app. "
            "Prepare to scan the QR code that you will see on the next step.",
        )
        form.add_field(
            var="phone",
            ftype="text-single",
            label="Your phone number in international format",
            required=True,
        )
        form.add_field(
            var="device",
            ftype="text-single",
            label="Name of this device",
            value="slidge",
            required=True,
        )

        adhoc_session["payload"] = form
        adhoc_session["has_next"] = True
        adhoc_session["next"] = self._handle_link_slidge2

        return adhoc_session

    async def _handle_link_slidge2(self, form: Form, adhoc_session: dict[str, Any]):
        resp = await (await self.signal).generate_linking_uri()
        qr_text = resp.uri

        qr = qrcode.make(qr_text)
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            qr.save(f.name)
            img_url = await self.plugin["xep_0363"].upload_file(
                filename=Path(f.name), ifrom=global_config.UPLOAD_REQUESTER
            )

        msg = self.make_message(mto=adhoc_session["from"])
        msg.set_from(self.boundjid.bare)
        msg["oob"]["url"] = img_url
        msg["body"] = img_url
        msg.send()

        msg = self.make_message(mto=adhoc_session["from"])
        msg.set_from(self.boundjid.bare)
        msg["body"] = qr_text
        msg.send()
        form_values = form.get_values()

        form = self.plugin["xep_0004"].make_form(
            title="Flash this",
            instructions="Flash this QR in the official signal app",
        )
        img = AdhocFormField()
        img["media"]["height"] = "200"
        img["media"]["width"] = "200"
        img["media"]["alt"] = "The thing to flash"
        img["media"].add_uri(img_url, itype="image/png")
        form.append(img)

        adhoc_session["payload"] = form
        adhoc_session["has_next"] = True
        adhoc_session["next"] = self._handle_link_slidge3
        adhoc_session["device"] = form_values["device"]
        adhoc_session["phone"] = form_values["phone"]
        adhoc_session["signal_session_id"] = resp.session_id
        return adhoc_session

    async def _handle_link_slidge3(self, _payload, adhoc_session: dict[str, Any]):
        try:
            await (await self.signal).finish_link(
                device_name=adhoc_session["device"],
                session_id=adhoc_session["signal_session_id"],
            )
        except sigexc.ScanTimeoutError:
            raise XMPPError(
                "not-authorized", "You took too much time to scan. Please retry."
            )
        except sigexc.SignaldException as e:
            raise XMPPError("not-authorized", f"Something went wrong: {e}.")
        user_store.add(adhoc_session["from"], {"phone": adhoc_session["phone"]})

        adhoc_session["has_next"] = False
        adhoc_session["next"] = None
        adhoc_session["payload"] = None
        adhoc_session["notes"] = [("info", "Success!")]
        return adhoc_session

    async def _handle_linked_devices(self, iq: Iq, adhoc_session: dict[str, Any]):
        user = user_store.get_by_stanza(iq)
        if user is None:
            raise XMPPError("subscription-required")

        devices = await (await self.signal).get_linked_devices(
            account=user.registration_form["phone"]
        )

        # does not work in gajim https://dev.gajim.org/gajim/gajim/-/issues/10857 is fixed
        form = self["xep_0004"].make_form("result", "Linked devices")
        form.add_reported("id", label="ID", type="fixed")
        form.add_reported("name", label="Name", type="fixed")
        form.add_reported("created", label="Created", type="fixed")
        form.add_reported("last_seen", label="Last seen", type="fixed")
        for d in devices.devices:
            form.add_item(
                {
                    "name": d.name,
                    "id": str(d.id),
                    "created": datetime.fromtimestamp(d.created / 1000).isoformat(),
                    "last_seen": datetime.fromtimestamp(d.lastSeen / 1000).isoformat(),
                }
            )

        adhoc_session["payload"] = form
        adhoc_session["has_next"] = False

        return adhoc_session

    async def _handle_add_device1(self, iq: Iq, adhoc_session: dict[str, Any]):
        user = user_store.get_by_stanza(iq)
        if user is None:
            raise XMPPError("subscription-required")

        form = self["xep_0004"].make_form(
            "form", "Link a new device to your signal account"
        )
        form.add_field(
            var="uri",
            ftype="text-single",
            label="Linking URI. Use a QR code reader app to get it from official signal clients.",
            required=True,
        )

        adhoc_session["payload"] = form
        adhoc_session["has_next"] = True
        adhoc_session["next"] = self._handle_add_device2

        return adhoc_session

    async def _handle_add_device2(self, stanza: Form, adhoc_session: dict[str, Any]):
        user = user_store.get_by_jid(adhoc_session["from"])
        if user is None:
            raise XMPPError("subscription-required")

        values = stanza.get_values()
        uri = values.get("uri")

        await (await self.signal).add_device(
            account=user.registration_form["phone"], uri=uri
        )

        adhoc_session["notes"] = [
            (
                "info",
                "Your new device is now correctly linked to your signal account",
            )
        ]
        adhoc_session["has_next"] = False

        return adhoc_session

    @staticmethod
    async def _chat_command_add_device(
        *args, msg: Message, session: Optional["Session"] = None
    ):
        if session is None:
            msg.reply("I don't know you, so don't talk to me").send()
            return
        if len(args) == 0:
            uri = await session.input("URI?")
        elif len(args) > 1:
            msg.reply("Syntax error! Use 'add_device [LINKING_URI]'").send()
            return
        else:
            uri = args[0]
        await session.add_device(uri)

    @staticmethod
    async def _chat_command_get_identities(
        *args, msg: Message, session: Optional["Session"] = None
    ):
        if session is None:
            msg.reply("I don't know you, so don't talk to me").send()
            return
        if len(args) == 0:
            uuid = await session.input("UUID?")
        elif len(args) > 1:
            msg.reply("Syntax error! Use 'get_identities [UUID]'").send()
            return
        else:
            uuid = args[0]
        await (await session.contacts.by_legacy_id(uuid)).get_identities()

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
                etype="modify",
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


log = logging.getLogger(__name__)
