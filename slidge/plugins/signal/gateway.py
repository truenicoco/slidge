import asyncio
import functools
import logging
from argparse import ArgumentParser
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi
from aiosignald import SignaldAPI
from slixmpp import JID, Iq, Message
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0004 import Form

from slidge import *

if TYPE_CHECKING:
    from .session import Session

from . import txt


class Gateway(BaseGateway):
    COMPONENT_NAME = "Signal (slidge)"
    COMPONENT_TYPE = "signal"
    COMPONENT_AVATAR = (
        "https://upload.wikimedia.org/wikipedia/commons/5/56/Logo_Signal..png"
    )
    REGISTRATION_INSTRUCTIONS = txt.REGISTRATION_INSTRUCTIONS
    REGISTRATION_FIELDS = txt.REGISTRATION_FIELDS

    ROSTER_GROUP = "Signal"

    signal: asyncio.Future["Signal"]
    signal_socket: str
    sessions_by_phone: dict[str, "Session"] = {}

    CHAT_COMMANDS = {
        "add_device": "_chat_command_add_device",
        "get_identities": "_chat_command_get_identities",
    }

    def __init__(self, args):
        super(Gateway, self).__init__(args)
        self.signal: asyncio.Future[Signal] = self.loop.create_future()

    def config(self, argv: list[str]):
        args = get_parser().parse_args(argv)
        self.signal_socket = socket = args.socket
        self.loop.create_task(self.connect_signal(socket))

    async def connect_signal(self, socket: str):
        """
        Establish connection to the signald socker
        """
        log.debug("Connecting to signald...")
        _, signal = await self.loop.create_unix_connection(
            functools.partial(Signal, self), socket
        )
        self.signal.set_result(signal)
        await signal.on_con_lost
        log.error("Signald UNIX socket connection lost!")
        raise RuntimeError("Signald socket connection lost")

    def add_adhoc_commands(self):
        self["xep_0050"].add_command(
            node="linked_devices",
            name="Get linked devices",
            handler=self._handle_linked_devices,
        )
        self["xep_0050"].add_command(
            node="add_device",
            name="Link a new device",
            handler=self._handle_add_device1,
        )

    async def _handle_linked_devices(self, iq: Iq, adhoc_session: dict[str, Any]):
        user = user_store.get_by_stanza(iq)
        if user is None:
            raise XMPPError("subscription-required")

        devices = await (await self.signal).get_linked_devices(
            account=user.registration_form["phone"]
        )

        # TODO: uncomment this when https://dev.gajim.org/gajim/gajim/-/issues/10857 is fixed
        # There are probably other clients that handle this just fine and this would make more sense
        # to use this, but I think targeting gajim compatibility when there are easy workarounds
        # is OK
        # form = self["xep_0004"].make_form("result", "Linked devices")
        # form.add_reported("id", label="ID", type="fixed")
        # form.add_reported("name", label="Name", type="fixed")
        # form.add_reported("created", label="Created", type="fixed")
        # form.add_reported("last_seen", label="Last seen", type="fixed")
        # for d in devices.devices:
        #     form.add_item(
        #         {
        #             "name": d.name,
        #             "id": str(d.id),
        #             "created": datetime.fromtimestamp(d.created / 1000).isoformat(),
        #             "last_seen": datetime.fromtimestamp(d.lastSeen / 1000).isoformat(),
        #         }
        #     )
        #
        # adhoc_session["payload"] = form
        adhoc_session["notes"] = [
            (
                "info",
                f"Name: {d.name} / "
                f"ID: {d.id} / "
                f"Created: {datetime.fromtimestamp(d.created / 1000).isoformat()} / "
                f"Last seen: {datetime.fromtimestamp(d.lastSeen / 1000).isoformat()}",
            )
            for d in devices.devices
        ]
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
        log.debug("ARGS: %s", args)
        if session is None:
            msg.reply("I don't know you, so don't talk to me").send()
            return
        if len(args) == 0:
            phone = await session.input("phone number?")
        elif len(args) > 1:
            msg.reply("Syntax error! Use 'get_identities [PHONE_NUMBER]'").send()
            return
        else:
            phone = args[0]
        await session.contacts.by_phone(phone).get_identities()

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        phone = registration_form.get("phone")
        for u in user_store.get_all():
            if u.registration_form.get("phone") == phone:
                raise XMPPError(
                    "not-allowed",
                    text="Someone is already using this phone number on this server.\n",
                )
        if registration_form.get("device") == "primary" and not registration_form.get(
            "name"
        ):
            raise ValueError(txt.NAME_REQUIRED)

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

    async def handle_ListenerState(self, state: sigapi.ListenerStatev1, payload):
        """
        Connection state for an account.

        :param state: State of the connection
        :param payload: The raw payload sent by signald
        """

        phone = payload["account"]
        if state.connected:
            session = self.sessions_by_phone[phone]
            await session.add_contacts_to_roster()

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


def get_parser():
    parser = ArgumentParser()
    parser.add_argument("--socket", default="/signald/signald.sock")
    return parser


log = logging.getLogger(__name__)
