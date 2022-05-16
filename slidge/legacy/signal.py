"""
Gateway to the signal network, using signald. Only supports registering a new number currently.
Linking to an existing account will be implemented once file upload works.
"""
import datetime
import logging
from typing import Dict, Optional

from slixmpp import Message, JID, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0100 import LegacyError

from pysignald_async import SignaldAPI, SignaldException
import pysignald_async.generated as sigapi

from slidge import (
    GatewayUser,
    user_store,
    BaseGateway,
    LegacyContact,
    BaseLegacyClient,
    RegistrationField,
)


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
        """
        Not implemented yet
        """
        uri = await self.input(user, "URI?")
        session = Signal.sessions_by_phone.get(user.registration_form["phone"])

        try:
            await session.signal.add_device(account=session.phone, uri=uri)
        except SignaldException as e:
            self.send_message(mto=user.jid, mbody=f"Problem: {e}")
        else:
            self.send_message(mto=user.jid, mbody=f"Linking OK")


# noinspection PyPep8Naming
class Signal(SignaldAPI):
    """
    Extends :class:`.SignaldAPI` with handlers for events we are interested in.
    """

    sessions_by_phone: Dict[str, "SignalSession"] = {}

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
            session.connected = True
            await session.add_contacts()

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


class SignalSession:
    """
    Represents a signal account
    """

    def __init__(self, user: GatewayUser, signal: Signal, xmpp: Gateway):
        """

        :param user:
        :param signal:
        :param xmpp:
        """
        self.user = user
        self.xmpp = xmpp

        self.phone: str = self.user.registration_form["phone"]
        self.signal = signal

        self.connected = False
        self.contacts: Dict[str, LegacyContact] = {}
        self.contacts_by_uuid: Dict[str, LegacyContact] = {}
        self.unacked: Dict[int, Message] = {}
        self.unread: Dict[int, Message] = {}
        self.unread_by_user: Dict[str, int] = {}

        Signal.sessions_by_phone[self.phone] = self

    async def subscribe(self):
        """
        Attempt to listen to incoming events for this account, and offer to pursue the registration process
        if this is not automatically possible.
        """
        self.connected = True
        try:
            await self.signal.subscribe(account=self.phone)
        except SignaldException as e:
            log.exception(e)
            await self.link_or_register()
        else:
            return

    async def link_or_register(self):
        """
        Finish the registration (or linking) process, using direct messages from the gateway to the user
        """
        choice = await self.xmpp.input(self.user, "[link] or [register]?")
        if choice == "link":
            uri = await self.signal.generate_linking_uri()
            self.xmpp.send_message(mto=self.user.jid, mbody=f"{uri.uri}")
            try:
                await self.signal.finish_link(
                    device_name="slidge", session_id=uri.session_id
                )
                await self.subscribe()
            except SignaldException as e:
                self.xmpp.send_message(
                    mto=self.user.jid, mbody=f"Something went wrong: {e}"
                )
                return

        elif choice == "register":
            try:
                await self.signal.register(self.phone)
            except SignaldException as e:
                if e.type == "CaptchaRequiredError":
                    captcha = await self.xmpp.input(
                        self.user,
                        "1.Go to https://signalcaptchas.org/registration/generate.html\n"
                        "2.Copy after signalcaptcha://",
                    )
                    try:
                        await self.signal.register(self.phone, captcha=captcha)
                    except SignaldException as e:
                        self.xmpp.send_message(
                            mto=self.user.jid, mbody=f"Something went wrong: {e}"
                        )
                        return
            sms_code = await self.xmpp.input(self.user, "Enter the SMS code")
            await self.signal.verify(account=self.phone, code=sms_code)
            name = await self.xmpp.input(self.user, "Enter your name")
            await self.signal.set_profile(account=self.phone, name=name)
            await self.subscribe()
        else:
            raise LegacyError(choice)

    async def add_contacts(self):
        """
        Populate a user's roster
        """
        profiles = await self.signal.list_contacts(account=self.phone)
        for profile in profiles.profiles:
            full_profile = await self.signal.get_profile(
                account=self.phone, address=profile.address
            )
            if full_profile.avatar is None:
                avatar = None
            else:
                with open(full_profile.avatar, "rb") as f:
                    avatar = f.read()
            contact = self.contact(
                profile.address.number, profile.name, avatar, profile.address.uuid
            )
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
            dest = msg.sync_message.sent.destination
            contact = self.contact(dest.number, uuid=dest.uuid)
            sent_msg = msg.sync_message.sent.message
            contact.carbon(
                body=sent_msg.body,
                date=datetime.datetime.fromtimestamp(sent_msg.timestamp / 1000),
            )

        contact = self.contact(msg.source.number, uuid=msg.source.uuid)

        if msg.data_message is not None:
            sent_msg = contact.send_message(body=msg.data_message.body, chat_state=None)
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
                    self.xmpp.ack(msg)
                    contact.ack(msg)
                    contact.received(msg)
            elif type_ == "READ":
                for t in msg.receipt_message.timestamps:
                    try:
                        msg = self.unread.pop(t)
                    except KeyError:
                        return
                    contact.displayed(msg)

    def contact(
        self,
        phone: Optional[str] = None,
        name: Optional[str] = None,
        avatar: Optional[bytes] = None,
        uuid: Optional[str] = None,
    ):
        """
        Helper to build a :class:`.LegacyContact` attached to this session's :class:`.GatewayUser`

        :param phone: phone number of the contact
        :param name: name of the contact (for roster population)
        :param avatar: picture of the contact
        :param uuid: The UUID of the contact (useful for carbons, where phone number
            does not necessarily appear)
        """
        if phone is None:
            if uuid is None:
                raise TypeError("Must specify either uuid or phone")
            else:
                c = self.contacts_by_uuid.get(uuid)
                if c is None:
                    raise KeyError(uuid)
                    # log.warning("Cannot find phone number with UUID: %s", uuid)
                    # return
                return c

        c = self.contacts.get(phone)

        if c is None:
            self.contacts[phone] = c = LegacyContact(
                self.user, phone, name, avatar, extra_info=uuid
            )
            if uuid is not None:
                self.contacts_by_uuid[uuid] = c

        if uuid is not None and c.extra_info is None:
            c.extra_info = uuid
            self.contacts_by_uuid[uuid] = c

        return c


class LegacyClient(BaseLegacyClient):
    signal: SignaldAPI = None
    xmpp: Gateway

    def __init__(self, xmpp: Gateway):
        super().__init__(xmpp)
        self.xmpp.add_event_handler("marker_displayed", on_user_displayed)
        self.xmpp.add_event_handler("session_start", self.connect_signal)
        self.xmpp.add_event_handler("chatstate_composing", self.on_user_composing)

    async def connect_signal(self, *_):
        """
        Establish connection to the signald socker
        """
        _, s = await self.xmpp.loop.create_unix_connection(
            Signal, "/signald/signald.sock"
        )
        s.xmpp = self.xmpp
        self.signal: Signal = s

    async def validate(self, user_jid: JID, registration_form):
        """
        Just validate any registration to the gateway, we'll handle things via direct messages.
        """
        pass

    async def login(self, p: Presence):
        """
        Starts listening to incoming messages for a user, if not already doing it.

        :param p: presence sent by the gateway user
        """
        user = user_store.get_by_stanza(p)
        log.debug("%s", user)
        session = sessions.get(user)

        if session is None:
            session = sessions[user] = SignalSession(user, self.signal, self.xmpp)

        if session.connected:
            return

        await session.subscribe()

    async def logout(self, p: Presence):
        """
        No-op here, we want to stay connected even if the user loses the XMPP connection

        :param p:
        """
        pass

    async def on_message(self, msg: Message):
        """
        Handle a message sent by a user through the gateway

        :param msg:
        """
        user = user_store.get_by_stanza(msg)
        user_phone = user.registration_form["phone"]
        response = await self.signal.send(
            account=user_phone,
            recipientAddress=sigapi.JsonAddressv1(number=msg.get_to().user),
            messageBody=msg["body"],
        )
        result = response.results[0]
        log.debug("Result: %s", result)
        if result.success is not None:
            session = Signal.sessions_by_phone.get(user_phone)
            session.unacked[response.timestamp] = msg
            session.unread[response.timestamp] = msg
        if (
            result.networkFailure
            or result.identityFailure
            or result.identityFailure
            or result.proof_required_failure
        ):
            raise XMPPError(str(result))

    async def on_user_composing(self, msg: Message):
        """
        Transmit a "typing notification" from a user to a contact

        :param msg: Message sent by the user
        """
        user = user_store.get_by_stanza(msg)
        await self.signal.typing(
            account=user.registration_form["phone"],
            address=sigapi.JsonAddressv1(number=msg.get_to().user),
            typing=True,
        )


async def on_user_displayed(msg: Message):
    session = sessions.get(user_store.get_by_stanza(msg))
    signal_id = session.unread_by_user.pop(msg["displayed"]["id"])
    await session.signal.mark_read(
        account=session.phone,
        to=sigapi.JsonAddressv1(number=msg.get_to().user),
        timestamps=[signal_id],
    )


sessions: Dict[GatewayUser, SignalSession] = {}
log = logging.getLogger(__name__)
