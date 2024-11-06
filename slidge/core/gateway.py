"""
This module extends slixmpp.ComponentXMPP to make writing new LegacyClients easier
"""

import asyncio
import logging
import re
import tempfile
from copy import copy
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional, Sequence, Union

import aiohttp
import qrcode
from slixmpp import JID, ComponentXMPP, Iq, Message, Presence
from slixmpp.exceptions import IqError, IqTimeout, XMPPError
from slixmpp.plugins.xep_0060.stanza import OwnerAffiliation
from slixmpp.types import MessageTypes
from slixmpp.xmlstream.xmlstream import NotConnectedError

from slidge import command  # noqa: F401
from slidge.command.adhoc import AdhocProvider
from slidge.command.admin import Exec
from slidge.command.base import Command, FormField
from slidge.command.chat_command import ChatCommandProvider
from slidge.command.register import RegistrationType
from slidge.core import config
from slidge.core.dispatcher.session_dispatcher import SessionDispatcher
from slidge.core.mixins import MessageMixin
from slidge.core.pubsub import PubSubComponent
from slidge.core.session import BaseSession
from slidge.db import GatewayUser, SlidgeStore
from slidge.db.avatar import avatar_cache
from slidge.slixfix.delivery_receipt import DeliveryReceipt
from slidge.slixfix.roster import RosterBackend
from slidge.util import ABCSubclassableOnceAtMost
from slidge.util.types import AvatarType, MessageOrPresenceTypeVar
from slidge.util.util import timeit

if TYPE_CHECKING:
    pass


class BaseGateway(
    ComponentXMPP,
    MessageMixin,
    metaclass=ABCSubclassableOnceAtMost,
):
    """
    The gateway component, handling registrations and un-registrations.

    On slidge launch, a singleton is instantiated, and it will be made available
    to public classes such :class:`.LegacyContact` or :class:`.BaseSession` as the
    ``.xmpp`` attribute.

    Must be subclassed by a legacy module to set up various aspects of the XMPP
    component behaviour, such as its display name or welcome message, via
    class attributes :attr:`.COMPONENT_NAME` :attr:`.WELCOME_MESSAGE`.

    Abstract methods related to the registration process must be overriden
    for a functional :term:`Legacy Module`:

    - :meth:`.validate`
    - :meth:`.validate_two_factor_code`
    - :meth:`.get_qr_text`
    - :meth:`.confirm_qr`

    NB: Not all of these must be overridden, it depends on the
    :attr:`REGISTRATION_TYPE`.

    The other methods, such as :meth:`.send_text` or :meth:`.react` are the same
    as those of :class:`.LegacyContact` and :class:`.LegacyParticipant`, because
    the component itself is also a "messaging actor", ie, an :term:`XMPP Entity`.
    For these methods, you need to specify the JID of the recipient with the
    `mto` parameter.

    Since it inherits from :class:`slixmpp.componentxmpp.ComponentXMPP`,you also
    have a hand on low-level XMPP interactions via slixmpp methods, e.g.:

    .. code-block:: python

        self.send_presence(
            pfrom="somebody@component.example.com",
            pto="someonwelse@anotherexample.com",
        )

    However, you should not need to do so often since the classes of the plugin
    API provides higher level abstractions around most commonly needed use-cases, such
    as sending messages, or displaying a custom status.

    """

    COMPONENT_NAME: str = NotImplemented
    """Name of the component, as seen in service discovery by XMPP clients"""
    COMPONENT_TYPE: str = ""
    """Type of the gateway, should follow https://xmpp.org/registrar/disco-categories.html"""
    COMPONENT_AVATAR: Optional[AvatarType] = None
    """
    Path, bytes or URL used by the component as an avatar.
    """

    REGISTRATION_FIELDS: Sequence[FormField] = [
        FormField(var="username", label="User name", required=True),
        FormField(var="password", label="Password", required=True, private=True),
    ]
    """
    Iterable of fields presented to the gateway user when registering using :xep:`0077`
    `extended <https://xmpp.org/extensions/xep-0077.html#extensibility>`_ by :xep:`0004`.
    """
    REGISTRATION_INSTRUCTIONS: str = "Enter your credentials"
    """
    The text presented to a user that wants to register (or modify) their legacy account
    configuration.
    """
    REGISTRATION_TYPE: RegistrationType = RegistrationType.SINGLE_STEP_FORM
    """
    This attribute determines how users register to the gateway, ie, how they
    login to the :term:`legacy service <Legacy Service>`.
    The credentials are then stored persistently, so this process should happen
    once per user (unless they unregister).

    The registration process always start with a basic data form (:xep:`0004`)
    presented to the user.
    But the legacy login flow might require something more sophisticated, see
    :class:`.RegistrationType` for more details.
    """

    REGISTRATION_2FA_TITLE = "Enter your 2FA code"
    REGISTRATION_2FA_INSTRUCTIONS = (
        "You should have received something via email or SMS, or something"
    )
    REGISTRATION_QR_INSTRUCTIONS = "Flash this code or follow this link"

    PREFERENCES = [
        FormField(
            var="sync_presence",
            label="Propagate your XMPP presence to the legacy network.",
            value="true",
            required=True,
            type="boolean",
        ),
        FormField(
            var="sync_avatar",
            label="Propagate your XMPP avatar to the legacy network.",
            value="true",
            required=True,
            type="boolean",
        ),
    ]

    ROSTER_GROUP: str = "slidge"
    """
    Name of the group assigned to a :class:`.LegacyContact` automagically
    added to the :term:`User`'s roster with :meth:`.LegacyContact.add_to_roster`.
    """
    WELCOME_MESSAGE = (
        "Thank you for registering. Type 'help' to list the available commands, "
        "or just start messaging away!"
    )
    """
    A welcome message displayed to users on registration.
    This is useful notably for clients that don't consider component JIDs as a
    valid recipient in their UI, yet still open a functional chat window on
    incoming messages from components.
    """

    SEARCH_FIELDS: Sequence[FormField] = [
        FormField(var="first", label="First name", required=True),
        FormField(var="last", label="Last name", required=True),
        FormField(var="phone", label="Phone number", required=False),
    ]
    """
    Fields used for searching items via the component, through :xep:`0055` (jabber search).
    A common use case is to allow users to search for legacy contacts by something else than
    their usernames, eg their phone number.

    Plugins should implement search by overriding :meth:`.BaseSession.search`
    (restricted to registered users).

    If there is only one field, it can also be used via the ``jabber:iq:gateway`` protocol
    described in :xep:`0100`. Limitation: this only works if the search request returns
    one result item, and if this item has a 'jid' var.
    """
    SEARCH_TITLE: str = "Search for legacy contacts"
    """
    Title of the search form.
    """
    SEARCH_INSTRUCTIONS: str = ""
    """
    Instructions of the search form.
    """

    MARK_ALL_MESSAGES = False
    """
    Set this to True for :term:`legacy networks <Legacy Network>` that expects
    read marks for *all* messages and not just the latest one that was read
    (as most XMPP clients will only send a read mark for the latest msg).
    """

    PROPER_RECEIPTS = False
    """
    Set this to True if the legacy service provides a real equivalent of message delivery receipts
    (:xep:`0184`), meaning that there is an event thrown when the actual device of a contact receives
    a message. Make sure to call Contact.received() adequately if this is set to True.
    """

    GROUPS = False

    mtype: MessageTypes = "chat"
    is_group = False
    _can_send_carbon = False
    store: SlidgeStore
    avatar_pk: int

    AVATAR_ID_TYPE: Callable[[str], Any] = str
    """
    Modify this if the legacy network uses unique avatar IDs that are not strings.

    This is required because we store those IDs as TEXT in the persistent SQL DB.
    The callable specified here will receive is responsible for converting the
    serialised-as-text version of the avatar unique ID back to the proper type.
    Common example: ``int``.
    """
    # FIXME: do we really need this since we have session.xmpp_to_legacy_msg_id?
    #        (maybe we do)
    LEGACY_MSG_ID_TYPE: Callable[[str], Any] = str
    """
    Modify this if the legacy network uses unique message IDs that are not strings.

    This is required because we store those IDs as TEXT in the persistent SQL DB.
    The callable specified here will receive is responsible for converting the
    serialised-as-text version of the message unique ID back to the proper type.
    Common example: ``int``.
    """
    LEGACY_CONTACT_ID_TYPE: Callable[[str], Any] = str
    """
    Modify this if the legacy network uses unique contact IDs that are not strings.

    This is required because we store those IDs as TEXT in the persistent SQL DB.
    The callable specified here is responsible for converting the
    serialised-as-text version of the contact unique ID back to the proper type.
    Common example: ``int``.
    """
    LEGACY_ROOM_ID_TYPE: Callable[[str], Any] = str
    """
    Modify this if the legacy network uses unique room IDs that are not strings.

    This is required because we store those IDs as TEXT in the persistent SQL DB.
    The callable specified here is responsible for converting the
    serialised-as-text version of the room unique ID back to the proper type.
    Common example: ``int``.
    """

    http: aiohttp.ClientSession

    def __init__(self):
        self.log = log
        self.datetime_started = datetime.now()
        self.xmpp = self  # ugly hack to work with the BaseSender mixin :/
        self.default_ns = "jabber:component:accept"
        super().__init__(
            config.JID,
            config.SECRET,
            config.SERVER,
            config.PORT,
            plugin_whitelist=SLIXMPP_PLUGINS,
            plugin_config={
                "xep_0077": {
                    "form_fields": None,
                    "form_instructions": self.REGISTRATION_INSTRUCTIONS,
                    "enable_subscription": self.REGISTRATION_TYPE
                    == RegistrationType.SINGLE_STEP_FORM,
                },
                "xep_0100": {
                    "component_name": self.COMPONENT_NAME,
                    "type": self.COMPONENT_TYPE,
                },
                "xep_0184": {
                    "auto_ack": False,
                    "auto_request": False,
                },
                "xep_0363": {
                    "upload_service": config.UPLOAD_SERVICE,
                },
            },
            fix_error_ns=True,
        )
        self.loop.set_exception_handler(self.__exception_handler)
        self.loop.create_task(self.__set_http())
        self.has_crashed: bool = False
        self.use_origin_id = False

        self.jid_validator: re.Pattern = re.compile(config.USER_JID_VALIDATOR)
        self.qr_pending_registrations = dict[str, asyncio.Future[Optional[dict]]]()

        self.session_cls: BaseSession = BaseSession.get_unique_subclass()
        self.session_cls.xmpp = self

        from ..group.room import LegacyMUC

        LegacyMUC.get_self_or_unique_subclass().xmpp = self

        self.get_session_from_stanza: Callable[
            [Union[Message, Presence, Iq]], BaseSession
        ] = self.session_cls.from_stanza  # type: ignore
        self.get_session_from_user: Callable[[GatewayUser], BaseSession] = (
            self.session_cls.from_user
        )

        self.register_plugins()
        self.__register_slixmpp_events()
        self.__register_slixmpp_api()
        self.roster.set_backend(RosterBackend(self))

        self.register_plugin("pubsub", {"component_name": self.COMPONENT_NAME})
        self.pubsub: PubSubComponent = self["pubsub"]
        self.delivery_receipt: DeliveryReceipt = DeliveryReceipt(self)

        # with this we receive user avatar updates
        self.plugin["xep_0030"].add_feature("urn:xmpp:avatar:metadata+notify")

        self.plugin["xep_0030"].add_feature("urn:xmpp:chat-markers:0")

        if self.GROUPS:
            self.plugin["xep_0030"].add_feature("http://jabber.org/protocol/muc")
            self.plugin["xep_0030"].add_feature("urn:xmpp:mam:2")
            self.plugin["xep_0030"].add_feature("urn:xmpp:mam:2#extended")
            self.plugin["xep_0030"].add_feature(self.plugin["xep_0421"].namespace)
            self.plugin["xep_0030"].add_feature(self["xep_0317"].stanza.NS)
            self.plugin["xep_0030"].add_identity(
                category="conference",
                name=self.COMPONENT_NAME,
                itype="text",
                jid=self.boundjid,
            )

        # why does mypy need these type annotations? no idea
        self.__adhoc_handler: AdhocProvider = AdhocProvider(self)
        self.__chat_commands_handler: ChatCommandProvider = ChatCommandProvider(self)

        self.__dispatcher = SessionDispatcher(self)

        self.__register_commands()

        MessageMixin.__init__(self)  # ComponentXMPP does not call super().__init__()

    async def __set_http(self):
        self.http = aiohttp.ClientSession()
        if getattr(self, "_test_mode", False):
            return
        avatar_cache.http = self.http

    def __register_commands(self):
        for cls in Command.subclasses:
            if any(x is NotImplemented for x in [cls.CHAT_COMMAND, cls.NODE, cls.NAME]):
                log.debug("Not adding command '%s' because it looks abstract", cls)
                continue
            if cls is Exec:
                if config.DEV_MODE:
                    log.warning(r"/!\ DEV MODE ENABLED /!\\")
                else:
                    continue
            c = cls(self)
            log.debug("Registering %s", cls)
            self.__adhoc_handler.register(c)
            self.__chat_commands_handler.register(c)

    def __exception_handler(self, loop: asyncio.AbstractEventLoop, context):
        """
        Called when a task created by loop.create_task() raises an Exception

        :param loop:
        :param context:
        :return:
        """
        log.debug("Context in the exception handler: %s", context)
        exc = context.get("exception")
        if exc is None:
            log.debug("No exception in this context: %s", context)
        elif isinstance(exc, SystemExit):
            log.debug("SystemExit called in an asyncio task")
        else:
            log.error("Crash in an asyncio task: %s", context)
            log.exception("Crash in task", exc_info=exc)
            self.has_crashed = True
            loop.stop()

    def __register_slixmpp_events(self):
        self.del_event_handler("presence_subscribe", self._handle_subscribe)
        self.del_event_handler("presence_unsubscribe", self._handle_unsubscribe)
        self.del_event_handler("presence_subscribed", self._handle_subscribed)
        self.del_event_handler("presence_unsubscribed", self._handle_unsubscribed)
        self.del_event_handler(
            "roster_subscription_request", self._handle_new_subscription
        )
        self.del_event_handler("presence_probe", self._handle_probe)
        self.add_event_handler("session_start", self.__on_session_start)
        self.add_event_handler("disconnected", self.connect)

    def __register_slixmpp_api(self) -> None:
        self.plugin["xep_0231"].api.register(self.store.bob.get_bob, "get_bob")
        self.plugin["xep_0231"].api.register(self.store.bob.set_bob, "set_bob")
        self.plugin["xep_0231"].api.register(self.store.bob.del_bob, "del_bob")

    @property  # type: ignore
    def jid(self):
        # Override to avoid slixmpp deprecation warnings.
        return self.boundjid

    async def __on_session_start(self, event):
        log.debug("Gateway session start: %s", event)

        # prevents XMPP clients from considering the gateway as an HTTP upload
        disco = self.plugin["xep_0030"]
        await disco.del_feature(feature="urn:xmpp:http:upload:0", jid=self.boundjid)
        await self.plugin["xep_0115"].update_caps(jid=self.boundjid)

        if self.COMPONENT_AVATAR is not None:
            cached_avatar = await avatar_cache.convert_or_get(self.COMPONENT_AVATAR)
            self.avatar_pk = cached_avatar.pk
        else:
            cached_avatar = None

        for user in self.store.users.get_all():
            # TODO: before this, we should check if the user has removed us from their roster
            #       while we were offline and trigger unregister from there. Presence probe does not seem
            #       to work in this case, there must be another way. privileged entity could be used
            #       as last resort.
            try:
                await self["xep_0100"].add_component_to_roster(user.jid)
                await self.__add_component_to_mds_whitelist(user.jid)
            except (IqError, IqTimeout) as e:
                # TODO: remove the user when this happens? or at least
                # this can happen when the user has unsubscribed from the XMPP server
                log.warning(
                    "Error with user %s, not logging them automatically",
                    user,
                    exc_info=e,
                )
                continue
            self.send_presence(
                pto=user.jid.bare, ptype="probe"
            )  # ensure we get all resources for user
            session = self.session_cls.from_user(user)
            session.create_task(self.login_wrap(session))
            if cached_avatar is not None:
                await self.pubsub.broadcast_avatar(
                    self.boundjid.bare, session.user_jid, cached_avatar
                )

        log.info("Slidge has successfully started")

    async def __add_component_to_mds_whitelist(self, user_jid: JID):
        # Uses privileged entity to add ourselves to the whitelist of the PEP
        # MDS node so we receive MDS events
        iq_creation = Iq(sto=user_jid.bare, sfrom=user_jid, stype="set")
        iq_creation["pubsub"]["create"]["node"] = self["xep_0490"].stanza.NS

        try:
            await self["xep_0356"].send_privileged_iq(iq_creation)
        except PermissionError:
            log.warning(
                "IQ privileges not granted for pubsub namespace, we cannot "
                "create the MDS node of %s",
                user_jid,
            )
        except (IqError, IqTimeout) as e:
            # conflict this means the node already exists, we can ignore that
            if e.condition != "conflict":
                log.exception(
                    "Could not create the MDS node of %s", user_jid, exc_info=e
                )
        except Exception as e:
            log.exception(
                "Error while trying to create to the MDS node of %s",
                user_jid,
                exc_info=e,
            )

        iq_affiliation = Iq(sto=user_jid.bare, sfrom=user_jid, stype="set")
        iq_affiliation["pubsub_owner"]["affiliations"]["node"] = self[
            "xep_0490"
        ].stanza.NS

        aff = OwnerAffiliation()
        aff["jid"] = self.boundjid.bare
        aff["affiliation"] = "member"
        iq_affiliation["pubsub_owner"]["affiliations"].append(aff)

        try:
            await self["xep_0356"].send_privileged_iq(iq_affiliation)
        except PermissionError:
            log.warning(
                "IQ privileges not granted for pubsub#owner namespace, we cannot "
                "listen to the MDS events of %s",
                user_jid,
            )
        except Exception as e:
            log.exception(
                "Error while trying to subscribe to the MDS node of %s",
                user_jid,
                exc_info=e,
            )

    @timeit
    async def login_wrap(self, session: "BaseSession"):
        session.send_gateway_status("Logging in…", show="dnd")
        try:
            status = await session.login()
        except Exception as e:
            log.warning("Login problem for %s", session.user_jid, exc_info=e)
            log.exception(e)
            session.send_gateway_status(f"Could not login: {e}", show="busy")
            session.send_gateway_message(
                "You are not connected to this gateway! "
                f"Maybe this message will tell you why: {e}"
            )
            return

        log.info("Login success for %s", session.user_jid)
        session.logged = True
        session.send_gateway_status("Syncing contacts…", show="dnd")
        await session.contacts._fill()
        if not (r := session.contacts.ready).done():
            r.set_result(True)
        if self.GROUPS:
            session.send_gateway_status("Syncing groups…", show="dnd")
            await session.bookmarks.fill()
            if not (r := session.bookmarks.ready).done():
                r.set_result(True)
        for c in session.contacts:
            # we need to receive presences directed at the contacts, in
            # order to send pubsub events for their +notify features
            self.send_presence(pfrom=c.jid, pto=session.user_jid.bare, ptype="probe")
        if status is None:
            session.send_gateway_status("Logged in", show="chat")
        else:
            session.send_gateway_status(status, show="chat")
        if session.user.preferences.get("sync_avatar", False):
            session.create_task(self.fetch_user_avatar(session))
        else:
            self.xmpp.store.users.set_avatar_hash(session.user_pk, None)

    async def fetch_user_avatar(self, session: BaseSession):
        try:
            iq = await self.xmpp.plugin["xep_0060"].get_items(
                session.user_jid.bare,
                self.xmpp.plugin["xep_0084"].stanza.MetaData.namespace,
                ifrom=self.boundjid.bare,
            )
        except (IqError, IqTimeout):
            self.xmpp.store.users.set_avatar_hash(session.user_pk, None)
            return
        await self.__dispatcher.on_avatar_metadata_info(
            session, iq["pubsub"]["items"]["item"]["avatar_metadata"]["info"]
        )

    def _send(
        self, stanza: MessageOrPresenceTypeVar, **send_kwargs
    ) -> MessageOrPresenceTypeVar:
        stanza.set_from(self.boundjid.bare)
        if mto := send_kwargs.get("mto"):
            stanza.set_to(mto)
        stanza.send()
        return stanza

    def raise_if_not_allowed_jid(self, jid: JID):
        if not self.jid_validator.match(jid.bare):
            raise XMPPError(
                condition="not-allowed",
                text="Your account is not allowed to use this gateway.",
            )

    def send_raw(self, data: Union[str, bytes]):
        # overridden from XMLStream to strip base64-encoded data from the logs
        # to make them more readable.
        if log.isEnabledFor(level=logging.DEBUG):
            if isinstance(data, str):
                stripped = copy(data)
            else:
                stripped = data.decode("utf-8")
            # there is probably a way to do that in a single RE,
            # but since it's only for debugging, the perf penalty
            # does not matter much
            for el in LOG_STRIP_ELEMENTS:
                stripped = re.sub(
                    f"(<{el}.*?>)(.*)(</{el}>)",
                    "\1[STRIPPED]\3",
                    stripped,
                    flags=re.DOTALL | re.IGNORECASE,
                )
            log.debug("SEND: %s", stripped)
        if not self.transport:
            raise NotConnectedError()
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.transport.write(data)

    def get_session_from_jid(self, j: JID):
        try:
            return self.session_cls.from_jid(j)
        except XMPPError:
            pass

    def exception(self, exception: Exception):
        # """
        # Called when a task created by slixmpp's internal (eg, on slix events) raises an Exception.
        #
        # Stop the event loop and exit on unhandled exception.
        #
        # The default :class:`slixmpp.basexmpp.BaseXMPP` behaviour is just to
        # log the exception, but we want to avoid undefined behaviour.
        #
        # :param exception: An unhandled :class:`Exception` object.
        # """
        if isinstance(exception, IqError):
            iq = exception.iq
            log.error("%s: %s", iq["error"]["condition"], iq["error"]["text"])
            log.warning("You should catch IqError exceptions")
        elif isinstance(exception, IqTimeout):
            iq = exception.iq
            log.error("Request timed out: %s", iq)
            log.warning("You should catch IqTimeout exceptions")
        elif isinstance(exception, SyntaxError):
            # Hide stream parsing errors that occur when the
            # stream is disconnected (they've been handled, we
            # don't need to make a mess in the logs).
            pass
        else:
            if exception:
                log.exception(exception)
            self.loop.stop()
            exit(1)

    def re_login(self, session: "BaseSession"):
        async def w():
            session.cancel_all_tasks()
            await session.logout()
            await self.login_wrap(session)

        session.create_task(w())

    async def make_registration_form(self, _jid, _node, _ifrom, iq: Iq):
        self.raise_if_not_allowed_jid(iq.get_from())
        reg = iq["register"]
        user = self.store.users.get_by_stanza(iq)
        log.debug("User found: %s", user)

        form = reg["form"]
        form.add_field(
            "FORM_TYPE",
            ftype="hidden",
            value="jabber:iq:register",
        )
        form["title"] = f"Registration to '{self.COMPONENT_NAME}'"
        form["instructions"] = self.REGISTRATION_INSTRUCTIONS

        if user is not None:
            reg["registered"] = False
            form.add_field(
                "remove",
                label="Remove my registration",
                required=True,
                ftype="boolean",
                value=False,
            )

        for field in self.REGISTRATION_FIELDS:
            if field.var in reg.interfaces:
                val = None if user is None else user.get(field.var)
                if val is None:
                    reg.add_field(field.var)
                else:
                    reg[field.var] = val

        reg["instructions"] = self.REGISTRATION_INSTRUCTIONS

        for field in self.REGISTRATION_FIELDS:
            form.add_field(
                field.var,
                label=field.label,
                required=field.required,
                ftype=field.type,
                options=field.options,
                value=field.value if user is None else user.get(field.var, field.value),
            )

        reply = iq.reply()
        reply.set_payload(reg)
        return reply

    async def user_prevalidate(
        self, ifrom: JID, form_dict: dict[str, Optional[str]]
    ) -> Optional[Mapping]:
        # Pre validate a registration form using the content of self.REGISTRATION_FIELDS
        # before passing it to the plugin custom validation logic.
        for field in self.REGISTRATION_FIELDS:
            if field.required and not form_dict.get(field.var):
                raise ValueError(f"Missing field: '{field.label}'")

        return await self.validate(ifrom, form_dict)

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ) -> Optional[Mapping]:
        """
        Validate a user's initial registration form.

        Should raise the appropriate :class:`slixmpp.exceptions.XMPPError`
        if the registration does not allow to continue the registration process.

        If :py:attr:`REGISTRATION_TYPE` is a
        :attr:`.RegistrationType.SINGLE_STEP_FORM`,
        this method should raise something if it wasn't possible to successfully
        log in to the legacy service with the registration form content.

        It is also used for other types of :py:attr:`REGISTRATION_TYPE` too, since
        the first step is always a form. If :attr:`.REGISTRATION_FIELDS` is an
        empty list (ie, it declares no :class:`.FormField`), the "form" is
        effectively a confirmation dialog displaying
        :attr:`.REGISTRATION_INSTRUCTIONS`.

        :param user_jid: JID of the user that has just registered
        :param registration_form: A dict where keys are the :attr:`.FormField.var` attributes
            of the :attr:`.BaseGateway.REGISTRATION_FIELDS` iterable.
            This dict can be modified and will be accessible as the ``legacy_module_data``
            of the

        :return : A dict that will be stored as the persistent "legacy_module_data"
            for this user. If you don't return anything here, the whole registration_form
            content will be stored.
        """
        raise NotImplementedError

    async def validate_two_factor_code(
        self, user: GatewayUser, code: str
    ) -> Optional[dict]:
        """
        Called when the user enters their 2FA code.

        Should raise the appropriate :class:`slixmpp.exceptions.XMPPError`
        if the login fails, and return successfully otherwise.

        Only used when :attr:`REGISTRATION_TYPE` is
        :attr:`.RegistrationType.TWO_FACTOR_CODE`.

        :param user: The :class:`.GatewayUser` whose registration is pending
            Use their :attr:`.GatewayUser.bare_jid` and/or
            :attr:`.registration_form` attributes to get what you need.
        :param code: The code they entered, either via "chatbot" message or
            adhoc command

        :return : A dict which keys and values will be added to the persistent "legacy_module_data"
            for this user.
        """
        raise NotImplementedError

    async def get_qr_text(self, user: GatewayUser) -> str:
        """
        This is where slidge gets the QR code content for the QR-based
        registration process. It will turn it into a QR code image and send it
        to the not-yet-fully-registered :class:`.GatewayUser`.

        Only used in when :attr:`BaseGateway.REGISTRATION_TYPE` is
        :attr:`.RegistrationType.QRCODE`.

        :param user: The :class:`.GatewayUser` whose registration is pending
            Use their :attr:`.GatewayUser.bare_jid` and/or
            :attr:`.registration_form` attributes to get what you need.
        """
        raise NotImplementedError

    async def confirm_qr(
        self,
        user_bare_jid: str,
        exception: Optional[Exception] = None,
        legacy_data: Optional[dict] = None,
    ):
        """
        This method is meant to be called to finalize QR code-based registration
        flows, once the legacy service confirms the QR flashing.

        Only used in when :attr:`BaseGateway.REGISTRATION_TYPE` is
        :attr:`.RegistrationType.QRCODE`.

        :param user_bare_jid: The bare JID of the almost-registered
            :class:`GatewayUser` instance
        :param exception: Optionally, an XMPPError to be raised to **not** confirm
            QR code flashing.
        :param legacy_data: dict which keys and values will be added to the persistent
            "legacy_module_data" for this user.
        """
        fut = self.qr_pending_registrations[user_bare_jid]
        if exception is None:
            fut.set_result(legacy_data)
        else:
            fut.set_exception(exception)

    async def unregister_user(self, user: GatewayUser):
        self.send_presence(
            pshow="busy", pstatus="You are not registered to this gateway anymore."
        )
        await self.xmpp.plugin["xep_0077"].api["user_remove"](None, None, user.jid)
        await self.xmpp.session_cls.kill_by_jid(user.jid)

    async def unregister(self, user: GatewayUser):
        """
        Optionally override this if you need to clean additional
        stuff after a user has been removed from the persistent user store.

        By default, this just calls :meth:`BaseSession.logout`.

        :param user:
        """
        session = self.get_session_from_user(user)
        try:
            await session.logout()
        except NotImplementedError:
            pass

    async def input(
        self, jid: JID, text=None, mtype: MessageTypes = "chat", **msg_kwargs
    ) -> str:
        """
        Request arbitrary user input using a simple chat message, and await the result.

        You shouldn't need to call this directly bust instead use
        :meth:`.BaseSession.input` to directly target a user.

        :param jid: The JID we want input from
        :param text: A prompt to display for the user
        :param mtype: Message type
        :return: The user's reply
        """
        return await self.__chat_commands_handler.input(jid, text, mtype, **msg_kwargs)

    async def send_qr(self, text: str, **msg_kwargs):
        """
        Sends a QR Code to a JID

        You shouldn't need to call directly bust instead use
        :meth:`.BaseSession.send_qr` to directly target a user.

        :param text: The text that will be converted to a QR Code
        :param msg_kwargs: Optional additional arguments to pass to
            :meth:`.BaseGateway.send_file`, such as the recipient of the QR,
            code
        """
        qr = qrcode.make(text)
        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=config.NO_UPLOAD_METHOD != "move"
        ) as f:
            qr.save(f.name)
            await self.send_file(f.name, **msg_kwargs)

    def shutdown(self) -> list[asyncio.Task]:
        # """
        # Called by the slidge entrypoint on normal exit.
        #
        # Sends offline presences from all contacts of all user sessions and from
        # the gateway component itself.
        # No need to call this manually, :func:`slidge.__main__.main` should take care of it.
        # """
        log.debug("Shutting down")
        tasks = []
        for user in self.store.users.get_all():
            tasks.append(self.session_cls.from_jid(user.jid).shutdown())
            self.send_presence(ptype="unavailable", pto=user.jid)
        return tasks


SLIXMPP_PLUGINS = [
    "link_preview",  # https://wiki.soprani.ca/CheogramApp/LinkPreviews
    "xep_0030",  # Service discovery
    "xep_0045",  # Multi-User Chat
    "xep_0050",  # Adhoc commands
    "xep_0054",  # VCard-temp (for MUC avatars)
    "xep_0055",  # Jabber search
    "xep_0059",  # Result Set Management
    "xep_0066",  # Out of Band Data
    "xep_0071",  # XHTML-IM (for stickers and custom emojis maybe later)
    "xep_0077",  # In-band registration
    "xep_0084",  # User Avatar
    "xep_0085",  # Chat state notifications
    "xep_0100",  # Gateway interaction
    "xep_0106",  # JID Escaping
    "xep_0115",  # Entity capabilities
    "xep_0122",  # Data Forms Validation
    "xep_0153",  # vCard-Based Avatars (for MUC avatars)
    "xep_0172",  # User nickname
    "xep_0184",  # Message Delivery Receipts
    "xep_0199",  # XMPP Ping
    "xep_0221",  # Data Forms Media Element
    "xep_0231",  # Bits of Binary (for stickers and custom emojis maybe later)
    "xep_0249",  # Direct MUC Invitations
    "xep_0264",  # Jingle Content Thumbnails
    "xep_0280",  # Carbons
    "xep_0292_provider",  # VCard4
    "xep_0308",  # Last message correction
    "xep_0313",  # Message Archive Management
    "xep_0317",  # Hats
    "xep_0319",  # Last User Interaction in Presence
    "xep_0333",  # Chat markers
    "xep_0334",  # Message Processing Hints
    "xep_0356",  # Privileged Entity
    "xep_0356_old",  # Privileged Entity (old namespace)
    "xep_0363",  # HTTP file upload
    "xep_0385",  # Stateless in-line media sharing
    "xep_0402",  # PEP Native Bookmarks
    "xep_0421",  # Anonymous unique occupant identifiers for MUCs
    "xep_0424",  # Message retraction
    "xep_0425",  # Message moderation
    "xep_0444",  # Message reactions
    "xep_0447",  # Stateless File Sharing
    "xep_0461",  # Message replies
    "xep_0490",  # Message Displayed Synchronization
]

LOG_STRIP_ELEMENTS = ["data", "binval"]

log = logging.getLogger(__name__)
