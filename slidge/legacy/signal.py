"""
Gateway to the signal network, using signald. Only supports registering a new number currently.
Linking to an existing account will be implemented once file upload works.
"""
import datetime
import logging
from argparse import ArgumentParser
from typing import Dict, Optional, Hashable, List

from slixmpp import Message, JID, Presence, Iq
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0100 import LegacyError

from pysignald_async import SignaldAPI, SignaldException
import pysignald_async.generated as sigapi

from slidge import *


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = "Enter your phone number, starting with +"
    REGISTRATION_FIELDS = [
        RegistrationField(
            name="phone", label="Phone number (ex: +123456789)", required=True
        )
    ]

    ROSTER_GROUP = "Signal"

    COMPONENT_NAME = "Signal"

    async def on_gateway_message(self, msg: Message):
        log.debug("Gateway msg: %s", msg)
        user = user_store.get_by_stanza(msg)
        try:
            f = self.input_futures.pop(user.bare_jid)
        except KeyError:
            cmd = msg["body"]
            if cmd == "add_device":
                await self.add_device(user)
            else:
                self.send_message(mto=msg.get_from(), mbody="Come again?")
        else:
            f.set_result(msg["body"])

    async def add_device(self, user: GatewayUser):
        uri = await self.input(user, "URI?")
        session = Signal.sessions_by_phone.get(user.registration_form["phone"])

        try:
            await signal.add_device(account=session.phone, uri=uri)
        except SignaldException as e:
            self.send_message(mto=user.jid, mbody=f"Problem: {e}")
        else:
            self.send_message(mto=user.jid, mbody=f"Linking OK")


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

        :param msg:
        :param _payload:
        """
        session = Signal.sessions_by_phone[msg.account]
        await session.on_signal_message(msg)


class Contact(LegacyContact):
    def __init__(
        self,
        session: "BaseSession",
        phone: str,
        jid_username: str,
    ):
        super().__init__(session, phone, jid_username)
        log.debug("JID: %s", self.jid_username)
        self._uuid = None

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
    def __init__(self, user: GatewayUser):
        """

        :param user:
        """
        super().__init__(user)
        self.phone: str = self.user.registration_form["phone"]
        Signal.sessions_by_phone[self.phone] = self

    async def login(self, p: Presence = None):
        """
        Attempt to listen to incoming events for this account,
        and offer to pursue the registration process
        if this is not automatically possible.
        """
        try:
            await signal.subscribe(account=self.phone)
        except SignaldException as e:
            log.exception(e)
            await self.link_or_register()
        else:
            self.logged = True
            await self.add_contacts_to_roster()

    async def logout(self, p: Presence):
        pass

    async def link_or_register(self):
        """
        Finish the registration (or linking) process, using direct messages from the gateway to the user
        """
        choice = await self.xmpp.input(self.user, "[link] or [register]?")
        if choice == "link":
            uri = await signal.generate_linking_uri()
            self.xmpp.send_message(mto=self.user.jid, mbody=f"{uri.uri}")
            try:
                await signal.finish_link(
                    device_name="slidge", session_id=uri.session_id
                )
                await self.login()
            except SignaldException as e:
                self.xmpp.send_message(
                    mto=self.user.jid, mbody=f"Something went wrong: {e}"
                )
                return

        elif choice == "register":
            try:
                await signal.register(self.phone)
            except SignaldException as e:
                if e.type == "CaptchaRequiredError":
                    captcha = await self.xmpp.input(
                        self.user,
                        "1.Go to https://signalcaptchas.org/registration/generate.html\n"
                        "2.Copy after signalcaptcha://",
                    )
                    try:
                        await signal.register(self.phone, captcha=captcha)
                    except SignaldException as e:
                        self.xmpp.send_message(
                            mto=self.user.jid, mbody=f"Something went wrong: {e}"
                        )
                        return
            sms_code = await self.xmpp.input(self.user, "Enter the SMS code")
            await signal.verify(account=self.phone, code=sms_code)
            name = await self.xmpp.input(self.user, "Enter your name")
            await signal.set_profile(account=self.phone, name=name)
            await self.login()
        else:
            raise LegacyError(choice)

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
            sent_msg = contact.send_text(body=msg.data_message.body, chat_state=None)
            self.unread_by_user[sent_msg.get_id()] = msg.data_message.timestamp

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
                    try:
                        msg = self.unacked.pop(t)
                    except KeyError:
                        return
                    contact.ack(msg)
                    contact.received(msg)
            elif type_ == "READ":
                for t in msg.receipt_message.timestamps:
                    try:
                        msg = self.unread.pop(t)
                    except KeyError:
                        return
                    contact.displayed(msg)

    async def send_text(self, t: str, c: Contact) -> Hashable:
        response = await signal.send(
            account=self.phone,
            recipientAddress=c.signal_address,
            messageBody=m["body"],
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


class LegacyClient(BaseLegacyClient):
    xmpp: Gateway

    def __init__(self, xmpp: Gateway):
        super().__init__(xmpp)
        self.xmpp.add_event_handler("session_start", self.connect_signal)
        self.socket = "/signald/signald.sock"

    def config(self, argv: List[str]):
        parser = ArgumentParser()
        parser.add_argument("--socket")
        args = parser.parse_args(argv)
        if args.socket is not None:
            self.socket = args.socket

    async def connect_signal(self, *_):
        """
        Establish connection to the signald socker
        """
        global signal
        _, signal = await self.xmpp.loop.create_unix_connection(Signal, self.socket)
        signal.xmpp = self.xmpp

    async def validate(self, user_jid: JID, registration_form):
        """
        Just validate any registration to the gateway, we'll handle things via direct messages.
        """
        pass

    async def unregister(self, user: GatewayUser, iq: Iq):
        pass


log = logging.getLogger(__name__)

signal: Optional[Signal] = None
