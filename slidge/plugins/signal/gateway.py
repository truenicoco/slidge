import asyncio
import functools
import logging
from argparse import ArgumentParser
from typing import TYPE_CHECKING, Dict, List, Optional

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi
from aiosignald import SignaldAPI
from slixmpp import JID, Message
from slixmpp.exceptions import XMPPError

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
    sessions_by_phone: Dict[str, "Session"] = {}

    CHAT_COMMANDS = {"add_device": "_chat_command_add_device"}

    def config(self, argv: List[str]):
        args = get_parser().parse_args(argv)
        self.signal_socket = socket = args.socket
        self.signal = self.loop.create_future()
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

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
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

    async def handle_WebSocketConnectionState(
        self, state: sigapi.WebSocketConnectionStatev1, payload
    ):
        """
        Connection state for an account.

        :param state: State of the connection
        :param payload: The raw payload sent by signald
        """

        phone = payload["account"]
        if state.state == "CONNECTED":
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
