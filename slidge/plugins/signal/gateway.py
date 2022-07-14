"""
Gateway to the signal network, using signald. Only supports registering a new number currently.
Linking to an existing account will be implemented once file upload works.
"""
import datetime
import logging
from argparse import ArgumentParser
from typing import Dict, Optional, Hashable, List, Any

from slixmpp import Message, Presence, JID
from slixmpp.exceptions import XMPPError

from aiosignald import SignaldAPI
import aiosignald.generated as sigapi
import aiosignald.exc as sigexc

from slidge import *
from slidge.legacy.contact import LegacyContactType

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

    def config(self, argv: List[str]):
        args = get_parser().parse_args(argv)

        global signald_socket
        signald_socket = args.socket
        self.loop.create_task(self.connect_signal())

    async def connect_signal(self, *_):
        """
        Establish connection to the signald socker
        """
        global signal
        log.debug("Connecting to signald...")
        _, signal = await self.loop.create_unix_connection(Signal, signald_socket)
        signal.xmpp = self

    async def on_gateway_message(self, msg: Message):
        log.debug("Gateway msg: %s", msg)
        user = user_store.get_by_stanza(msg)
        if user is None:
            raise XMPPError("Please register to the gateway first")
        try:
            f = self._input_futures.pop(user.bare_jid)
        except KeyError:
            cmd = msg["body"]
            if cmd == "add_device":
                await self.add_device(user)
            else:
                self.send_message(mto=msg.get_from(), mbody="Come again?", mtype="chat")
        else:
            f.set_result(msg["body"])

    async def add_device(self, user: GatewayUser):
        uri = await self.input(user, "URI?")
        session = Signal.sessions_by_phone[user.registration_form["phone"]]

        try:
            await signal.add_device(account=session.phone, uri=uri)
        except sigexc.SignaldException as e:
            self.send_message(mto=user.jid, mbody=f"Problem: {e}", mtype="chat")
        else:
            self.send_message(mto=user.jid, mbody=f"Linking OK", mtype="chat")

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
            await signal.delete_account(
                account=user.registration_form.get("phone"), server=False
            )
        except sigexc.NoSuchAccountError:
            # if user unregisters before completing the registration process,
            # NoSuchAccountError is raised by signald
            pass

        log.info("Removed user: %s", user)


# noinspection PyPep8Naming
class Signal(SignaldAPI):
    """
    Extends :class:`.SignaldAPI` with handlers for events we are interested in.
    """

    sessions_by_phone: Dict[str, "Session"] = {}

    @staticmethod
    async def handle_WebSocketConnectionState(
        state: sigapi.WebSocketConnectionStatev1, payload
    ):
        """
        Connection state for an account

        :param state: State of the connection
        :param payload: The raw payload sent by signald
        """
        phone = payload["account"]
        if state.state == "CONNECTED":
            session = Signal.sessions_by_phone[phone]
            await session.add_contacts_to_roster()

    @staticmethod
    async def handle_IncomingMessage(msg: sigapi.IncomingMessagev1, _payload):
        """
        Dispatch a signald message to the proper session.

        Can be a lot of other things than an actual message, still need to figure
        things out to cover all cases.

        :param msg: the data!
        :param _payload:
        """
        session = Signal.sessions_by_phone[msg.account]
        await session.on_signal_message(msg)


class Contact(LegacyContact):
    session: "Session"

    def __init__(
        self,
        session: "Session",
        phone: str,
        jid_username: str,
    ):
        super().__init__(session, phone, jid_username)
        log.debug("JID: %s", self.jid_username)
        self._uuid: Optional[str] = None

    @property
    def phone(self):
        return self.legacy_id

    @phone.setter
    def phone(self, p):
        if p is not None:
            self.session.contacts.contacts_by_legacy_id[p] = self
            self.legacy_id = p
            self.jid_username = p

    @property
    def uuid(self):
        return self._uuid

    @uuid.setter
    def uuid(self, u: str):
        if u is not None:
            log.debug("UUID: %s, %s", u, self)
            self.session.contacts.contacts_by_uuid[u] = self
        self._uuid = u

    @property
    def signal_address(self):
        return sigapi.JsonAddressv1(number=self.phone, uuid=self.uuid)


class Roster(LegacyRoster):
    session: "Session"
    contacts_by_legacy_id: Dict[str, Contact]

    def __init__(self, session):
        super().__init__(session)
        self.contacts_by_uuid: Dict[str, Contact] = {}

    def by_phone(self, phone: str):
        return self.by_legacy_id(phone)

    def by_uuid(self, uuid: str):
        try:
            return self.contacts_by_uuid[uuid]
        except KeyError:
            log.warning(f"Cannot find the contact corresponding to the UUID {uuid}")
            return Contact(self.session, "unknown_phone", "unknown_phone")

    def by_json_address(self, address: sigapi.JsonAddressv1):
        uuid = address.uuid
        phone = address.number

        if uuid is None and phone is None:
            raise TypeError(address)

        if uuid is None:
            return self.by_phone(phone)

        if phone is None:
            return self.by_uuid(uuid)

        contact_phone = self.contacts_by_legacy_id.get(phone)
        contact_uuid = self.contacts_by_uuid.get(uuid)

        if contact_phone is None and contact_uuid is None:
            c = self.by_phone(phone)
            c.uuid = uuid
            return c

        if contact_phone is None and contact_uuid is not None:
            contact_uuid.phone = phone
            return contact_uuid

        if contact_uuid is None and contact_phone is not None:
            contact_phone.uuid = uuid
            return contact_phone

        if contact_phone is not contact_uuid:
            raise RuntimeError(address, contact_phone, contact_uuid)

        return contact_phone


class Session(BaseSession):
    """
    Represents a signal account
    """

    contacts: Roster

    def __init__(self, user: GatewayUser):
        """

        :param user:
        """
        super().__init__(user)
        self.phone: str = self.user.registration_form["phone"]
        Signal.sessions_by_phone[self.phone] = self

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> int:
        try:
            return int(i)
        except ValueError:
            raise NotImplementedError

    async def paused(self, c: LegacyContactType):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContactType):
        pass

    async def search(self, form_values: Dict[str, str]):
        pass

    async def login(self, p: Presence = None):
        """
        Attempt to listen to incoming events for this account,
        or pursue the registration process if needed.
        """
        self.send_gateway_status("Connecting...", show="dnd")
        try:
            await signal.subscribe(account=self.phone)
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
                    mto=self.user.jid, mbody=f"Something went wrong: {e}"
                )
                raise
            await signal.subscribe(account=self.phone)

        self.send_gateway_status(f"Connected as {self.phone}")
        await self.add_contacts_to_roster()

    async def register(self):
        self.send_gateway_status("Registering…", show="dnd")
        try:
            await signal.register(self.phone)
        except sigexc.CaptchaRequiredError:
            self.send_gateway_status("Captcha required", show="dnd")
            captcha = await self.xmpp.input(self.user, txt.CAPTCHA_REQUIRED)
            await signal.register(self.phone, captcha=captcha)
        sms_code = await self.xmpp.input(
            self.user,
            f"Reply to this message with the code you have received by SMS at {self.phone}.",
        )
        await signal.verify(account=self.phone, code=sms_code)
        await signal.set_profile(
            account=self.phone, name=self.user.registration_form["name"]
        )
        self.send_gateway_message(txt.REGISTER_SUCCESS)

    async def send_linking_qrcode(self):
        self.send_gateway_status("QR scan needed", show="dnd")
        resp = await signal.generate_linking_uri()
        await self.send_qr(resp.uri)
        self.xmpp.send_message(
            mto=self.user.jid,
            mbody=f"Use this URI or QR code on another signal device to "
            f"finish linking your XMPP account\n{resp.uri}",
        )
        return resp

    async def link(self):
        resp = await self.send_linking_qrcode()
        try:
            await signal.finish_link(
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
            )
            raise
        else:
            self.send_gateway_message(txt.LINK_SUCCESS)

    async def logout(self, p: Optional[Presence]):
        pass

    async def add_contacts_to_roster(self):
        """
        Populate a user's roster
        """
        profiles = await signal.list_contacts(account=self.phone)
        for profile in profiles.profiles:
            full_profile = await signal.get_profile(
                account=self.phone, address=profile.address
            )
            contact = self.contacts.by_phone(profile.address.number)
            contact.uuid = profile.address.uuid
            contact.name = profile.name or profile.profile_name
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
        if msg.sync_message is not None:
            if msg.sync_message.sent is None:
                log.debug("Ignoring %s", msg)  # Probably a 'message read' marker
                return
            destination = msg.sync_message.sent.destination
            contact = self.contacts.by_json_address(destination)
            sent_msg = msg.sync_message.sent.message
            contact.carbon(
                body=sent_msg.body,
                date=datetime.datetime.fromtimestamp(sent_msg.timestamp / 1000),
            )

        contact = self.contacts.by_json_address(msg.source)

        if msg.data_message is not None:
            contact.send_text(
                body=msg.data_message.body,
                legacy_msg_id=msg.data_message.timestamp,
            )

        if msg.typing_message is not None:
            action = msg.typing_message.action
            if action == "STARTED":
                contact.active()
                contact.composing()
            elif action == "STOPPED":
                contact.paused()

        if msg.receipt_message is not None:
            type_ = msg.receipt_message.type
            if type_ == "DELIVERY":
                for t in msg.receipt_message.timestamps:
                    contact.received(t)
            elif type_ == "READ":
                for t in msg.receipt_message.timestamps:
                    contact.displayed(t)

    async def send_text(self, t: str, c: Contact) -> int:
        response = await signal.send(
            account=self.phone,
            recipientAddress=c.signal_address,
            messageBody=t,
        )
        result = response.results[0]
        log.debug("Result: %s", result)
        if (
            result.networkFailure
            or result.identityFailure
            or result.identityFailure
            or result.proof_required_failure
        ):
            raise XMPPError(str(result))
        return response.timestamp

    async def send_file(self, u: str, c: LegacyContact) -> Optional[Hashable]:
        pass

    async def active(self, c: LegacyContact):
        pass

    async def inactive(self, c: LegacyContact):
        pass

    async def composing(self, c: Contact):
        await signal.typing(
            account=self.phone,
            address=c.signal_address,
            typing=True,
        )

    async def displayed(self, legacy_msg_id: int, c: Contact):
        await signal.mark_read(
            account=self.phone,
            to=c.signal_address,
            timestamps=[legacy_msg_id],
        )


def get_parser():
    parser = ArgumentParser()
    parser.add_argument("--socket", default="/signald/signald.sock")
    return parser


log = logging.getLogger(__name__)

signal: Signal
signald_socket: Optional[str] = None
