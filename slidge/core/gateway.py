"""
This module extends slixmpp.ComponentXMPP to make writing new LegacyClients easier
"""
import asyncio
import logging
import re
import tempfile
from asyncio import Future
from copy import copy
from typing import TYPE_CHECKING, Callable, Collection, Optional, Sequence, Union

import aiohttp
import qrcode
from slixmpp import (
    JID,
    ComponentXMPP,
    CoroutineCallback,
    Iq,
    Message,
    Presence,
    StanzaPath,
)
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.types import MessageTypes
from slixmpp.xmlstream.xmlstream import NotConnectedError

from ..util import ABCSubclassableOnceAtMost
from ..util.db import GatewayUser, RosterBackend, user_store
from ..util.error import XMPPError
from ..util.types import AvatarType
from ..util.xep_0292.vcard4 import VCard4Provider
from . import config
from .adhoc import AdhocProvider
from .chat_command import ChatCommandProvider
from .command.base import Command, FormField
from .command.register import RegistrationType
from .disco import Disco
from .mixins import MessageMixin
from .pubsub import PubSubComponent
from .session import BaseSession

if TYPE_CHECKING:
    from .muc.room import LegacyMUC


class BaseGateway(ComponentXMPP, MessageMixin, metaclass=ABCSubclassableOnceAtMost):
    """
    Must be subclassed by a plugin to set up various aspects of the XMPP
    component behaviour, such as its display name or its registration process.

    On slidge launch, a singleton is instantiated, and it will be made available
    to public classes such :class:`.LegacyContact` or :class:`.BaseSession` as the
    ``.xmpp`` attribute.
    Since it inherits from :class:`slixmpp.componentxmpp.ComponentXMPP`, this gives you a hand
    on low-level XMPP interactions via slixmpp plugins, e.g.:

    .. code-block:: python

        self.send_presence(
            pfrom="somebody@component.example.com",
            pto="someonwelse@anotherexample.com",
        )

    However, you should not need to do so often since the classes of the plugin
    API provides higher level abstractions around most commonly needed use-cases, such
    as sending messages, or displaying a custom status.

    """

    REGISTRATION_FIELDS: Collection[FormField] = [
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
    REGISTRATION_TYPE = RegistrationType.SINGLE_STEP_FORM
    """
    SINGLE_STEP_FORM: 1 step, 1 form, compatible with :xep:`0077` (in-band registration)
    
    QRCODE: The registration requires flashing a QR code in an official client.
    See :meth:`.BaseGateway.`
    
    TWO_FACTOR_CODE: The registration requires confirming login with a 2FA code
    """

    COMPONENT_NAME: str = NotImplemented
    """Name of the component, as seen in service discovery by XMPP clients"""
    COMPONENT_TYPE: Optional[str] = ""
    """Type of the gateway, should ideally follow https://xmpp.org/registrar/disco-categories.html"""
    COMPONENT_AVATAR: Optional[AvatarType] = None
    """
    Path, bytes or URL used by the component as an avatar.
    """

    ROSTER_GROUP: str = "slidge"
    """
    Roster entries added by the plugin in the user's roster will be part of the group specified here.
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
    
    Plugins should implement search by overriding :meth:`.BaseSession.search`, effectively
    restricting search to registered users by default.
    
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

    WELCOME_MESSAGE = (
        "Thank you for registering. Type 'help' to list the available commands, "
        "or just start messaging away!"
    )
    """
    A welcome message displayed to users on registration.
    This is useful notably for clients that don't consider component JIDs as a valid recipient in their UI,
    yet still open a functional chat window on incoming messages from components.
    """

    MARK_ALL_MESSAGES = False
    """
    Set this to True for legacy networks that expects read marks for *all* messages and not just
    the latest one that was read (as most XMPP clients will only send a reak mark for the latest msg).
    """

    REGISTRATION_2FA_TITLE = "Enter your 2FA code"
    REGISTRATION_2FA_INSTRUCTIONS = (
        "You should have received something via email or SMS, or something"
    )
    REGISTRATION_QR_INSTRUCTIONS = "Flash this code or follow this link"

    GROUPS = False

    jid: JID  # type: ignore

    def __init__(self):
        self.xmpp = self  # ugly hack to work with the BaseSender mixin :/
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
                    "user_store": user_store,
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
        )
        self.loop.set_exception_handler(self.__exception_handler)
        self.http = aiohttp.ClientSession()
        self.has_crashed = False

        self.jid_validator = re.compile(config.USER_JID_VALIDATOR)

        self.session_cls: BaseSession = BaseSession.get_unique_subclass()
        self.session_cls.xmpp = self

        self.get_session_from_stanza: Callable[
            [Union[Message, Presence, Iq]], BaseSession
        ] = self.session_cls.from_stanza
        self.get_session_from_user: Callable[
            [GatewayUser], BaseSession
        ] = self.session_cls.from_user

        self.register_plugins()
        self.__register_slixmpp_api()
        self.__register_handlers()
        self._input_futures: dict[str, Future] = {}

        self.register_plugin("pubsub", {"component_name": self.COMPONENT_NAME})
        self.pubsub: PubSubComponent = self["pubsub"]
        self.vcard: VCard4Provider = self["xep_0292_provider"]
        if self.GROUPS:
            self.plugin["xep_0030"].add_feature("http://jabber.org/protocol/muc")
            self.plugin["xep_0030"].add_feature("urn:xmpp:mam:2")
            self.plugin["xep_0030"].add_feature("urn:xmpp:mam:2#extended")
            self.plugin["xep_0030"].add_identity(
                category="conference",
                name="Slidged rooms",
                itype="text",
                jid=self.boundjid,
            )

        self.adhoc = AdhocProvider(self)
        self.chat_commands = ChatCommandProvider(self)
        self._register_commands()

        self.disco = Disco(self)

        self.use_origin_id = False

        self.remove_handler("Ping")
        self.register_handler(
            CoroutineCallback(
                "Ping",
                StanzaPath("iq@type=get/ping"),
                self.__handle_ping,  # type:ignore
            )
        )
        self.register_handler(
            CoroutineCallback(
                "MAM_query",
                StanzaPath("iq@type=set/mam"),
                self.__handle_mam,  # type:ignore
            )
        )
        self.register_handler(
            CoroutineCallback(
                "MAM_get_from",
                StanzaPath("iq@type=get/mam"),
                self.__handle_mam_get_form,  # type:ignore
            )
        )
        self.register_handler(
            CoroutineCallback(
                "MAM_get_meta",
                StanzaPath("iq@type=get/mam_metadata"),
                self.__handle_mam_metadata,  # type:ignore
            )
        )

        self.qr_pending_registrations = dict[str, asyncio.Future[bool]]()

    def get_session_from_jid(self, j: JID):
        try:
            return self.session_cls.from_jid(j)
        except XMPPError:
            pass

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

    async def __handle_ping(self, iq: Iq):
        ito = iq.get_to()

        if ito == self.boundjid.bare:
            iq.reply().send()

        ifrom = iq.get_from()
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError("registration-required")

        session = self.get_session_from_user(user)
        session.raise_if_not_logged()

        try:
            muc = await session.bookmarks.by_jid(ito)
        except XMPPError:
            pass
        else:
            muc.handle_ping(iq)
            return

        try:
            await session.contacts.by_jid(ito)
        except XMPPError:
            pass
        else:
            iq.reply().send()
            return

        raise XMPPError(
            "item-not-found", f"This JID does not match anything slidge knows: {ito}"
        )

    mtype = "chat"  # type: ignore
    is_group = False

    def _register_commands(self):
        for cls in Command.subclasses:
            if any(x is NotImplemented for x in [cls.CHAT_COMMAND, cls.NODE, cls.NAME]):
                log.debug("Not adding command '%s' because it looks abstract", cls)
                continue
            c = cls(self)
            self.adhoc.register(c)
            self.chat_commands.register(c)

    def _send(self, stanza: Union[Message, Presence], **send_kwargs):
        stanza.set_from(self.boundjid.bare)
        if mto := send_kwargs.get("mto"):
            stanza.set_to(mto)
        stanza.send()

    async def get_muc_from_stanza(self, iq: Union[Iq, Message]) -> "LegacyMUC":
        ito = iq.get_to()

        if ito == self.boundjid.bare:
            raise XMPPError(
                text="No MAM on the component itself, use a JID with a resource"
            )

        ifrom = iq.get_from()
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError("registration-required")

        session = self.get_session_from_user(user)
        session.raise_if_not_logged()

        return await session.bookmarks.by_jid(ito)

    async def __handle_mam(self, iq: Iq):
        muc = await self.get_muc_from_stanza(iq)
        await muc.send_mam(iq)

    async def __handle_mam_get_form(self, iq: Iq):
        ito = iq.get_to()

        if ito == self.boundjid.bare:
            raise XMPPError(
                text="No MAM on the component itself, use a JID with a resource"
            )

        ifrom = iq.get_from()
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError("registration-required")

        session = self.get_session_from_user(user)

        await session.bookmarks.by_jid(ito)

        reply = iq.reply()
        form = self.plugin["xep_0004"].make_form()
        form.add_field(ftype="hidden", var="FORM_TYPE", value="urn:xmpp:mam:2")
        form.add_field(ftype="jid-single", var="with")
        form.add_field(ftype="text-single", var="start")
        form.add_field(ftype="text-single", var="end")
        form.add_field(ftype="text-single", var="before-id")
        form.add_field(ftype="text-single", var="after-id")
        form.add_field(ftype="boolean", var="include-groupchat")
        field = form.add_field(ftype="list-multi", var="ids")
        field["validate"]["datatype"] = "xs:string"
        field["validate"]["open"] = True
        reply["mam"].append(form)
        reply.send()

    async def __handle_mam_metadata(self, iq: Iq):
        muc = await self.get_muc_from_stanza(iq)
        await muc.send_mam_metadata(iq)

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
            log.warning("No exception in this context: %s", context)
        elif isinstance(exc, SystemExit):
            log.debug("SystemExit called in an asyncio task")
        else:
            log.error("Crash in an asyncio task: %s", context)
            log.exception("Crash in task", exc_info=exc)
            self.has_crashed = True
            loop.stop()

    def _raise_if_not_allowed_jid(self, jid: JID):
        if not self.jid_validator.match(jid.bare):
            raise XMPPError(
                condition="not-allowed",
                text="Your account is not allowed to use this gateway.",
            )

    def exception(self, exception: Exception):
        """
        Called when a task created by slixmpp's internal (eg, on slix events) raises an Exception.

        Stop the event loop and exit on unhandled exception.

        The default :class:`slixmpp.basexmpp.BaseXMPP` behaviour is just to
        log the exception, but we want to avoid undefined behaviour.

        :param exception: An unhandled :class:`Exception` object.
        """
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

    def __register_slixmpp_api(self):
        self["xep_0077"].api.register(
            user_store.get,
            "user_get",
        )
        self["xep_0077"].api.register(
            user_store.remove,
            "user_remove",
        )
        self["xep_0077"].api.register(
            self.make_registration_form, "make_registration_form"
        )
        self["xep_0077"].api.register(self._user_validate, "user_validate")
        self["xep_0077"].api.register(self._user_modify, "user_modify")

        self["xep_0055"].api.register(self.search_get_form, "search_get_form")
        self["xep_0055"].api.register(self._search_query, "search_query")

        self.roster.set_backend(RosterBackend)

    def __register_handlers(self):
        self.add_event_handler("session_start", self.__on_session_start)
        self.add_event_handler("disconnected", self.connect)
        self.add_event_handler("user_register", self._on_user_register)
        self.add_event_handler("user_unregister", self._on_user_unregister)
        self.add_event_handler("groupchat_message_error", self.__on_group_chat_error)

        async def get_session(m, cb):
            if m.get_from().server == self.boundjid.bare:
                log.debug("Ignoring echo")
                return
            if m.get_to() == self.boundjid.bare:
                log.debug("Ignoring message to component")
                return
            s = self.get_session_from_stanza(m)
            await cb(s, m)

        # fmt: off
        async def msg(m): await get_session(m, BaseSession.send_from_msg)
        async def disp(m): await get_session(m, BaseSession.displayed_from_msg)
        async def active(m): await get_session(m, BaseSession.active_from_msg)
        async def inactive(m): await get_session(m, BaseSession.inactive_from_msg)
        async def composing(m): await get_session(m, BaseSession.composing_from_msg)
        async def paused(m): await get_session(m, BaseSession.paused_from_msg)
        async def correct(m): await get_session(m, BaseSession.correct_from_msg)
        async def react(m): await get_session(m, BaseSession.react_from_msg)
        async def retract(m): await get_session(m, BaseSession.retract_from_msg)
        async def groupchat_join(p): await get_session(p, BaseSession.join_groupchat)
        # fmt: on

        self.add_event_handler("legacy_message", msg)
        self.add_event_handler("marker_displayed", disp)
        self.add_event_handler("chatstate_active", active)
        self.add_event_handler("chatstate_inactive", inactive)
        self.add_event_handler("chatstate_composing", composing)
        self.add_event_handler("chatstate_paused", paused)
        self.add_event_handler("message_correction", correct)
        self.add_event_handler("reactions", react)
        self.add_event_handler("message_retract", retract)

        self.add_event_handler("groupchat_join", groupchat_join)
        self.add_event_handler("groupchat_message", msg)

        self.register_handler(
            CoroutineCallback(
                f"muc#admin",
                StanzaPath(f"iq/mucadmin_query"),
                self._handle_admin,  # type: ignore
            )
        )

        self.plugin["xep_0030"].add_feature("jabber:iq:gateway")
        self.register_handler(
            CoroutineCallback(
                f"iq:gateway",
                StanzaPath(f"iq/gateway"),
                self._handle_gateway_iq,  # type: ignore
            )
        )
        self.plugin["xep_0030"].add_feature("urn:xmpp:ping")

    async def __on_group_chat_error(self, msg: Message):
        condition = msg["error"].get_condition()
        if condition not in KICKABLE_ERRORS:
            return

        try:
            muc = await self.get_muc_from_stanza(msg)
        except XMPPError as e:
            log.debug("Not removing resource", exc_info=e)
            return
        mfrom = msg.get_from()
        resource = mfrom.resource
        try:
            muc.user_resources.remove(resource)
        except KeyError:
            log.warning("%s was not in the resources of %s", resource, muc)
        else:
            log.info(
                "Removed %s from the resources of %s because of error", resource, muc
            )

    async def _handle_admin(self, iq: Iq):
        if iq["type"] != "get":
            raise XMPPError("not-authorized")

        muc = await self.get_muc_from_stanza(iq)
        await muc.handle_admin(iq)

    async def _handle_gateway_iq(self, iq: Iq):
        user = user_store.get_by_jid(iq.get_from())
        if user is None:
            raise XMPPError("not-authorized", "Register to the gateway first")

        if len(self.SEARCH_FIELDS) > 1:
            raise XMPPError(
                "feature-not-implemented", "Use jabber search for this gateway"
            )

        field = self.SEARCH_FIELDS[0]

        reply = iq.reply()
        if iq["type"] == "get":
            reply["gateway"]["desc"] = self.SEARCH_TITLE
            reply["gateway"]["prompt"] = field.label
        elif iq["type"] == "set":
            prompt = iq["gateway"]["prompt"]
            session = self.session_cls.from_user(user)
            result = await session.search({field.var: prompt})
            if result is None or not result.items:
                raise XMPPError(
                    "item-not-found", "No contact was found with the info you provided."
                )
            if len(result.items) > 1:
                raise XMPPError(
                    "bad-request", "Your search yielded more than one result."
                )
            reply["gateway"]["jid"] = result.items[0]["jid"]

        reply.send()

    async def __on_session_start(self, event):
        log.debug("Gateway session start: %s", event)

        # prevents XMPP clients from considering the gateway as an HTTP upload
        disco = self.plugin["xep_0030"]
        await disco.del_feature(feature="urn:xmpp:http:upload:0", jid=self.boundjid)
        await self.plugin["xep_0115"].update_caps(jid=self.boundjid)

        await self.pubsub.set_avatar(
            jid=self.boundjid.bare, avatar=self.COMPONENT_AVATAR
        )

        for user in user_store.get_all():
            # TODO: before this, we should check if the user has removed us from their roster
            #       while we were offline and trigger unregister from there. Presence probe does not seem
            #       to work in this case, there must be another way. privileged entity could be used
            #       as last resort.
            await self["xep_0100"].add_component_to_roster(user.jid)
            self.send_presence(
                pto=user.bare_jid, ptype="probe"
            )  # ensure we get all resources for user
            session = self.session_cls.from_user(user)
            self.loop.create_task(self._login_wrap(session))

        log.info("Slidge has successfully started")

    async def _login_wrap(self, session: "BaseSession"):
        session.send_gateway_status("Logging in…", show="dnd")
        try:
            status = await session.login()
        except Exception as e:
            log.warning(f"Login problem for %s: %r", session.user, e)
            log.exception(e)
            session.send_gateway_status(f"Could not login: {e}", show="busy")
            session.send_gateway_message(
                f"You are not connected to this gateway! "
                f"Maybe this message will tell you why: {e}"
            )
            return

        log.info(f"Login success for %s", session.user)
        session.logged = True
        session.send_gateway_status("Syncing contacts…", show="dnd")
        await session.contacts.fill()
        if self.GROUPS:
            session.send_gateway_status("Syncing groups…", show="dnd")
            await session.bookmarks.fill()
        for c in session.contacts:
            # we need to receive presences directed at the contacts, in
            # order to send pubsub events for their +notify features
            self.send_presence(pfrom=c.jid, pto=session.user.bare_jid, ptype="probe")
        if status is None:
            session.send_gateway_status("Logged in", show="chat")
        else:
            session.send_gateway_status(status, show="chat")

    def re_login(self, session: "BaseSession"):
        async def w():
            await session.logout()
            await self._login_wrap(session)

        self.loop.create_task(w())

    async def make_registration_form(self, _jid, _node, _ifrom, iq: Iq):
        self._raise_if_not_allowed_jid(iq.get_from())
        reg = iq["register"]
        user = user_store.get_by_stanza(iq)
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

    async def user_prevalidate(self, ifrom: JID, form_dict: dict[str, Optional[str]]):
        """
        Pre validate a registration form using the content of self.REGISTRATION_FIELDS
        before passing it to the plugin custom validation logic.
        """
        for field in self.REGISTRATION_FIELDS:
            if field.required and not form_dict.get(field.var):
                raise ValueError(f"Missing field: '{field.label}'")

        await self.validate(ifrom, form_dict)

    async def _user_validate(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        """
        SliXMPP internal API stuff
        """
        log.debug("User validate: %s", ifrom.bare)
        form_dict = {f.var: iq.get(f.var) for f in self.REGISTRATION_FIELDS}
        self._raise_if_not_allowed_jid(ifrom)
        await self.user_prevalidate(ifrom, form_dict)
        log.info("New user: %s", ifrom.bare)
        user_store.add(ifrom, form_dict)

    async def _user_modify(
        self, _gateway_jid, _node, ifrom: JID, form_dict: dict[str, Optional[str]]
    ):
        """
        SliXMPP internal API stuff
        """
        user = user_store.get_by_jid(ifrom)
        log.debug("Modify user: %s", user)
        await self.user_prevalidate(ifrom, form_dict)
        user_store.add(ifrom, form_dict)

    async def _on_user_register(self, iq: Iq):
        session = self.get_session_from_stanza(iq)
        for jid in config.ADMINS:
            self.send_message(
                mto=jid,
                mbody=f"{iq.get_from()} has registered",
                mtype="headline",
                mfrom=self.boundjid.bare,
            )
        session.send_gateway_message(self.WELCOME_MESSAGE)
        await self._login_wrap(session)

    async def _on_user_unregister(self, iq: Iq):
        await self.session_cls.kill_by_jid(iq.get_from())

    async def search_get_form(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        """
        Prepare the search form using :attr:`.BaseSession.SEARCH_FIELDS`
        """
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError(text="Search is only allowed for registered users")

        reply = iq.reply()
        form = reply["search"]["form"]
        form["title"] = self.SEARCH_TITLE
        form["instructions"] = self.SEARCH_INSTRUCTIONS
        for field in self.SEARCH_FIELDS:
            form.append(field.get_xml())
        return reply

    async def _search_query(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        """
        Handles a search request
        """
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError(text="Search is only allowed for registered users")

        result = await self.get_session_from_stanza(iq).search(
            iq["search"]["form"].get_values()
        )

        if not result:
            raise XMPPError("item-not-found", text="Nothing was found")

        reply = iq.reply()
        form = reply["search"]["form"]
        for field in result.fields:
            form.add_reported(field.var, label=field.label, type=field.type)
        for item in result.items:
            form.add_item(item)
        return reply

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        """
        Validate a registration form from a user.

        Since :xep:`0077` is pretty limited in terms of validation, it is OK to validate
        anything that looks good here and continue the legacy auth process via direct messages
        to the user (using :meth:`.BaseGateway.input` for instance).

        :param user_jid: JID of the user that has just registered
        :param registration_form: A dict where keys are the :attr:`.FormField.var` attributes
         of the :attr:`.BaseGateway.REGISTRATION_FIELDS` iterable
        """
        pass

    async def unregister_user(self, user: GatewayUser):
        await self.xmpp.plugin["xep_0077"].api["user_remove"](None, None, user.jid)
        await self.xmpp.session_cls.kill_by_jid(user.jid)

    async def unregister(self, user: GatewayUser):
        """
        Optionally override this if you need to clean additional
        stuff after a user has been removed from the permanent user_store.

        :param user:
        """
        pass

    async def input(
        self, jid: JID, text=None, mtype: MessageTypes = "chat", **msg_kwargs
    ) -> str:
        """
        Request arbitrary user input using a simple chat message, and await the result.

        You shouldn't need to call directly bust instead use :meth:`.BaseSession.input`
        to directly target a user.

        NB: When using this, the next message that the user sent to the component will
        not be transmitted to :meth:`.BaseGateway.on_gateway_message`, but rather intercepted.
        Await the coroutine to get its content.

        :param jid: The JID we want input from
        :param text: A prompt to display for the user
        :param mtype: Message type
        :return: The user's reply
        """
        return await self.chat_commands.input(jid, text, mtype, **msg_kwargs)

    async def send_qr(self, text: str, **msg_kwargs):
        """
        Sends a QR Code to a JID

        :param text: The text that will be converted to a QR Code
        :param msg_kwargs: Optional additional arguments to pass to :meth:`.BaseGateway.send_file`,
            such as the recipient of the QR code.
        """
        qr = qrcode.make(text)
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            qr.save(f.name)
            await self.send_file(f.name, **msg_kwargs)

    def shutdown(self):
        """
        Called by the slidge entrypoint on normal exit.

        Sends offline presences from all contacts of all user sessions and from
        the gateway component itself.
        No need to call this manually, :func:`slidge.__main__.main` should take care of it.
        """
        log.debug("Shutting down")
        for user in user_store.get_all():
            self.session_cls.from_jid(user.jid).shutdown()
            self.send_presence(ptype="unavailable", pto=user.jid)

    async def validate_two_factor_code(self, user: GatewayUser, code: str):
        """
        Called when the user enters their 2FA code.

        Should raise the appropriate ``XMPPError`` if the login fails

        :param user: The gateway user whose registration is pending
            Use their ``.bare_jid`` and/or``.registration_form`` attributes
            to get what you need
        :param code: The code they entered, either via "chatbot" message or
            adhoc command
        """
        raise NotImplementedError

    async def get_qr_text(self, user: GatewayUser) -> str:
        """
        Plugins should call this to complete registration with QR codes

        :param user: The not-yet-fully-registered GatewayUser.
            Use its ``.bare_jid`` and/or``.registration_form`` attributes
            to get what you need
        """
        raise NotImplementedError

    async def confirm_qr(
        self, user_bare_jid: str, exception: Optional[Exception] = None
    ):
        """
        Plugins should call this to complete registration with QR codes

        :param user_bare_jid: The not-yet-fully-registered ``GatewayUser`` instance
            Use their ``.bare_jid`` and/or``.registration_form`` attributes
            to get what you need
        :param exception: Optionally, an XMPPError to be raised to **not** confirm
            QR code flashing.
        """
        fut = self.qr_pending_registrations[user_bare_jid]
        if exception is None:
            fut.set_result(True)
        else:
            fut.set_exception(exception)


KICKABLE_ERRORS = [
    "gone",
    "internal-server-error",
    "item-not-found",
    "jid-malformed",
    "recipient-unavailable",
    "redirect",
    "remote-server-not-found",
    "remote-server-timeout",
    "service-unavailable",
    "malformed error",
]


SLIXMPP_PLUGINS = [
    "xep_0030",  # Service discovery
    "xep_0045",  # Multi-User Chat
    "xep_0050",  # Adhoc commands
    "xep_0054",  # VCard-temp (avatar of the MUC room)
    "xep_0055",  # Jabber search
    "xep_0059",  # Result Set Management
    "xep_0066",  # Out of Band Data
    "xep_0077",  # In-band registration
    "xep_0084",  # User Avatar
    "xep_0085",  # Chat state notifications
    "xep_0100",  # Gateway interaction
    "xep_0106",  # JID Escaping
    "xep_0115",  # Entity capabilities
    "xep_0122",  # Data Forms Validation
    "xep_0153",  # vCard-Based Avatars (avatar of the MUC room)
    "xep_0172",  # User nickname
    "xep_0184",  # Message Delivery Receipts
    "xep_0199",  # XMPP Ping
    "xep_0221",  # Data Forms Media Element
    "xep_0280",  # Carbons
    "xep_0292_provider",  # VCard4
    "xep_0308",  # Last message correction
    "xep_0313",  # Message Archive Management
    "xep_0319",  # Last User Interaction in Presence
    "xep_0333",  # Chat markers
    "xep_0334",  # Message Processing Hints
    "xep_0356",  # Privileged Entity
    "xep_0356_old",  # Privileged Entity (old namespace)
    "xep_0363",  # HTTP file upload
    "xep_0385",  # Stateless in-line media sharing
    "xep_0424",  # Message retraction
    "xep_0425",  # Message moderation
    "xep_0444",  # Message reactions
    "xep_0447",  # Stateless File Sharing
    "xep_0461",  # Message replies
]

LOG_STRIP_ELEMENTS = ["data", "binval"]

log = logging.getLogger(__name__)
