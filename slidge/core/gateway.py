"""
This module extends slixmpp.ComponentXMPP to make writing new LegacyClients easier
"""
import asyncio
import logging
import re
import tempfile
from asyncio import Future
from datetime import timedelta
from pathlib import Path
from typing import Any, Generic, Iterable, Optional, Sequence, Type, TypeVar

import qrcode
from slixmpp import JID, ComponentXMPP, Iq, Message
from slixmpp.exceptions import IqError, IqTimeout, XMPPError
from slixmpp.types import MessageTypes

from ..util import ABCSubclassableOnceAtMost, FormField
from ..util.db import GatewayUser, RosterBackend, user_store
from ..util.types import AvatarType
from ..util.xep_0292.vcard4 import VCard4Provider
from ..util.xep_0363 import FileUploadError
from .pubsub import PubSubComponent
from .session import BaseSession, SessionType


class BaseGateway(
    Generic[SessionType], ComponentXMPP, metaclass=ABCSubclassableOnceAtMost
):
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

    REGISTRATION_FIELDS: Iterable[FormField] = [
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
        FormField(var="phone", label="Last name", required=False),
    ]
    """
    Fields used for searching items via the component, through :xep:`0055` (jabber search).
    A common use case is to allow users to search for legacy contacts by something else than
    their usernames, eg their phone number.
    
    Plugins should implement search by overriding :meth:`.BaseSession.search`, effectively
    restricting search to registered users by default.
    """
    SEARCH_TITLE: str = "Search for legacy contacts"
    """
    Title of the search form.
    """
    SEARCH_INSTRUCTIONS: str = ""
    """
    Instructions of the search form.
    """

    _BASE_CHAT_COMMANDS = {
        "find": "_chat_command_search",
        "help": "_chat_command_help",
        "register": "_chat_command_register",
        "contacts": "_chat_command_list_contacts",
    }
    CHAT_COMMANDS: dict[str, str] = {}
    """
    Keys of this dict can be used to trigger a command by a simple chat message to the gateway
    component. Extra words after the key are passed as *args to the handler. Values of the dict
    are strings, and handlers are resolved using ``getattr()`` on the :class:`.BaseGateway`
    instance.
    
    Handlers are coroutines with following signature:
    
    .. code-block::python
    
        async def _chat_command_xxx(*args, msg: Message, session: Optional[Session] = None)
            ...
    
    The original :class:`slixmpp.stanza.Message` is also passed to the handler as the
    msg kwarg. If the command comes from a registered gateway user, its session attribute is also
    passed to the handler.
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

    def __init__(self, args):
        """

        :param args: CLI arguments parsed by :func:`.slidge.__main__.get_parser`
        """
        super().__init__(
            args.jid,
            args.secret,
            args.server,
            args.port,
            plugin_whitelist=SLIXMPP_PLUGINS,
            plugin_config={
                "xep_0077": {
                    "form_fields": None,
                    "form_instructions": self.REGISTRATION_INSTRUCTIONS,
                },
                "xep_0100": {
                    "component_name": self.COMPONENT_NAME,
                    "user_store": user_store,
                    "type": self.COMPONENT_TYPE,
                },
                "xep_0184": {
                    "auto_ack": False,
                    "auto_request": True,
                },
                "xep_0363": {
                    "upload_service": args.upload_service,
                },
            },
        )
        self.loop.set_exception_handler(self.__exception_handler)
        self.has_crashed = False

        self.home_dir = Path(args.home_dir)
        self._jid_validator = re.compile(args.user_jid_validator)
        self._config = args
        self.no_roster_push = args.no_roster_push
        self.upload_requester = args.upload_requester or self.boundjid.bare
        self.ignore_delay_threshold = timedelta(seconds=args.ignore_delay_threshold)

        self._session_cls: Type[SessionType] = BaseSession.get_unique_subclass()
        self._session_cls.xmpp = self

        self._get_session_from_stanza = self._session_cls.from_stanza
        self._get_session_from_user = self._session_cls.from_user
        self.register_plugins()
        self.__register_slixmpp_api()
        self.__register_handlers()
        self._input_futures: dict[str, Future] = {}

        self._chat_commands = {
            k: getattr(self, v)
            for k, v in (self._BASE_CHAT_COMMANDS | self.CHAT_COMMANDS).items()
        }

        self.register_plugin("pubsub", {"component_name": self.COMPONENT_NAME})
        self.pubsub: PubSubComponent = self["pubsub"]
        self.vcard: VCard4Provider = self["xep_0292_provider"]

    def __exception_handler(self, loop: asyncio.AbstractEventLoop, context):
        """
        Called when a task created by loop.create_task() raises an Exception

        :param loop:
        :param context:
        :return:
        """
        log.debug("CONTEXT: %s", context)
        exc = context.get("exception")
        if exc is None:
            log.warning("No exception in this context: %s", context)
        elif isinstance(exc, SystemExit):
            log.debug("SystemExit called in an asyncio task")
        else:
            log.exception("Crash in an asyncio task: %s", context)
            self.has_crashed = True
            loop.stop()

    def exception(self, exception: Exception):
        """
        Called when a task created by slixmpp's internal (eg, on slix events) raises an Exception.

        Stop the event loop and exit on unhandled exception.

        The default :class:slixmpp.basexmpp.BaseXMPP` behaviour is just to
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
            self._make_registration_form,
            "make_registration_form",
        )
        self["xep_0077"].api.register(self._user_validate, "user_validate")
        self["xep_0077"].api.register(self._user_modify, "user_modify")

        self["xep_0055"].api.register(self._search_get_form, "search_get_form")
        self["xep_0055"].api.register(self._search_query, "search_query")

        self.roster.set_backend(RosterBackend)

    def __register_handlers(self):
        self.add_event_handler("session_start", self.__on_session_start)
        self.add_event_handler("disconnected", self.connect)
        self.add_event_handler("gateway_message", self._on_gateway_message_private)
        self.add_event_handler("user_register", self._on_user_register)
        self.add_event_handler("user_unregister", self._on_user_unregister)
        get_session = self._get_session_from_stanza

        # fmt: off
        async def msg(m): await get_session(m).send_from_msg(m)
        async def disp(m): await get_session(m).displayed_from_msg(m)
        async def active(m): await get_session(m).active_from_msg(m)
        async def inactive(m): await get_session(m).inactive_from_msg(m)
        async def composing(m): await get_session(m).composing_from_msg(m)
        async def paused(m): await get_session(m).paused_from_msg(m)
        async def correct(m): await get_session(m).correct_from_msg(m)
        async def react(m): await get_session(m).react_from_msg(m)
        async def retract(m): await get_session(m).retract_from_msg(m)
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

    async def __on_session_start(self, event):
        log.debug("Gateway session start: %s", event)

        # prevents XMPP clients from considering the gateway as an HTTP upload
        disco = self.plugin["xep_0030"]
        await disco.del_feature(
            feature="urn:xmpp:http:upload:0", jid=self.boundjid.bare
        )
        await self.plugin["xep_0115"].update_caps(jid=self.boundjid.bare)

        self.__add_adhoc_commands()
        self.add_adhoc_commands()
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
            session = self._session_cls.from_user(user)
            self.loop.create_task(self._login_wrap(session))
            for c in session.contacts:
                # we need to receive presences directed at the contacts, in order to
                # send pubsub events for their +notify features
                self.send_presence(pfrom=c.jid, pto=user.bare_jid, ptype="probe")

        log.info("Slidge has successfully started")

    @staticmethod
    async def _login_wrap(session: "SessionType"):
        session.send_gateway_status("Logging in…", show="dnd")
        try:
            status = await session.login()
        except Exception as e:
            log.warning(f"Login problem for %s: %r", session.user, e)
            session.send_gateway_status(f"Could not login: {e}", show="busy")
            session.send_gateway_message(
                f"You are not connected to this gateway! "
                f"Maybe this message will tell you why: {e}"
            )
        else:
            log.info(f"Login success for %s", session.user)
            if status is None:
                session.send_gateway_status("Logged in", show="chat")
            else:
                session.send_gateway_status(status, show="chat")

    def re_login(self, session: "SessionType"):
        async def w():
            await session.logout()
            await self._login_wrap(session)

        self.loop.create_task(w())

    def __add_adhoc_commands(self):
        # TODO: this should only be advertised to admins
        # Not a big deal since we need to check if 'from' is an admin in the handler
        # anyway, BUT it would be nice if this simply does not show up in the list
        # of available commands for regular users.
        self["xep_0050"].add_command(
            node="info", name="List registered users", handler=self._handle_info
        )
        self.plugin["xep_0050"].add_command(
            node="search", name="Search for contacts", handler=self._handle_search
        )

    def _handle_info(self, iq: Iq, session: dict[str, Any]):
        """
        List registered users for admins
        """
        if iq.get_from().bare not in self._config.admins:
            raise XMPPError("not-authorized")
        form = self["xep_0004"].make_form("result", "Component info")
        form.add_field(
            ftype="jid-multi",
            label="Users",
            value=[u.bare_jid for u in user_store.get_all()],
        )

        session["payload"] = form
        session["has_next"] = False

        return session

    async def _handle_search(self, iq: Iq, adhoc_session: dict[str, Any]):
        """
        Jabber search, but as an adhoc command (search form)
        """
        user = user_store.get_by_jid(iq.get_from())
        if user is None:
            raise XMPPError(
                "not-authorized", text="Search is only allowed for registered users"
            )

        session = self._get_session_from_stanza(iq)

        reply = await self._search_get_form(None, None, ifrom=iq.get_from(), iq=iq)
        adhoc_session["payload"] = reply["search"]["form"]
        adhoc_session["next"] = self._handle_search2
        adhoc_session["has_next"] = True
        adhoc_session["session"] = session

        return adhoc_session

    async def _handle_search2(self, form, adhoc_session: dict[str, Any]):
        """
        Jabber search, but as an adhoc command (results)
        """

        search_results = await adhoc_session["session"].search(form.get_values())

        form = self.plugin["xep_0004"].make_form("result", "Contact search results")
        for field in search_results.fields:
            form.add_reported(field.var, label=field.label, type=field.type)
        for item in search_results.items:
            form.add_item(item)

        adhoc_session["next"] = None
        adhoc_session["has_next"] = False
        adhoc_session["payload"] = form

        return adhoc_session

    async def _make_registration_form(self, _jid, _node, _ifrom, iq: Iq):
        if not self._jid_validator.match(iq.get_from().bare):
            raise XMPPError(condition="not-allowed")

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

    async def _user_prevalidate(self, ifrom: JID, form_dict: dict[str, Optional[str]]):
        """
        Pre validate a registration form using the content of self.REGISTRATION_FIELDS
        before passing it to the plugin custom validation logic.
        """
        for field in self.REGISTRATION_FIELDS:
            if field.required and not form_dict.get(field.var):
                raise ValueError(f"Missing field: '{field.label}'")

        await self.validate(ifrom, form_dict)

    async def _user_validate(
        self, _gateway_jid, _node, ifrom: JID, form_dict: dict[str, Optional[str]]
    ):
        """
        SliXMPP internal API stuff
        """
        log.debug("User validate: %s", ifrom.bare)
        if not self._jid_validator.match(ifrom.bare):
            raise XMPPError(condition="not-allowed")
        await self._user_prevalidate(ifrom, form_dict)
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
        await self._user_prevalidate(ifrom, form_dict)
        user_store.add(ifrom, form_dict)

    async def _on_user_register(self, iq: Iq):
        session = self._get_session_from_stanza(iq)
        for jid in self._config.admins:
            self.send_message(
                mto=jid,
                mbody=f"{iq.get_from()} has registered",
                mtype="headline",
                mfrom=self.boundjid.bare,
            )
        session.send_gateway_message(self.WELCOME_MESSAGE)
        await session.login()

    async def _on_user_unregister(self, iq: Iq):
        # Mypy: "Type[SessionType?]" has no attribute "kill_by_jid"
        # I don't understand why ^ this question mark...
        kill = self._session_cls.kill_by_jid  # type: ignore
        await kill(iq.get_from())

    async def _search_get_form(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        """
        Prepare the search form using self.SEARCH_FIELDS
        """
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError(text="Search is only allowed for registered users")

        reply = iq.reply()
        form = reply["search"]["form"]
        form["title"] = self.SEARCH_TITLE
        form["instructions"] = self.SEARCH_INSTRUCTIONS
        for field in self.SEARCH_FIELDS:
            form.add_field(**field.dict())
        return reply

    async def _search_query(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        """
        Handles a search request
        """
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError(text="Search is only allowed for registered users")

        result = await self._get_session_from_stanza(iq).search(
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

    async def _chat_command_search(
        self, *args, msg: Message, session: Optional[SessionType] = None
    ):
        if session is None:
            msg.reply("Register to the gateway first!")
            return

        search_form = {}
        diff = len(args) - len(self.SEARCH_FIELDS)

        if diff > 0:
            session.send_gateway_message("Too many parameters!")
            return

        for field, arg in zip(self.SEARCH_FIELDS, args):
            search_form[field.var] = arg

        if diff < 0:
            for field in self.SEARCH_FIELDS[diff:]:
                if not field.required:
                    continue
                search_form[field.var] = await session.input(
                    (field.label or field.var) + "?"
                )

        results = await session.search(search_form)
        if results is None:
            session.send_gateway_message("No results!")
            return

        result_fields = results.fields
        for result in results.items:
            text = ""
            for f in result_fields:
                if f.type == "jid-single":
                    text += f"xmpp:{result[f.var]}\n"
                else:
                    text += f"{f.label}: {result[f.var]}\n"
            session.send_gateway_message(text)

    async def _chat_command_help(
        self, *_args, msg: Message, session: Optional[SessionType]
    ):
        if session is None:
            msg.reply("Register to the gateway first!").send()
        else:
            t = "|".join(
                x
                for x in self._chat_commands.keys()
                if not x not in ("register", "help")
            )
            log.debug("In help: %s", t)
            msg.reply(f"Available commands: {t}").send()

    @staticmethod
    async def _chat_command_list_contacts(
        *_args, msg: Message, session: Optional[SessionType]
    ):
        if session is None:
            msg.reply("Register to the gateway first!").send()
        else:
            contacts = sorted(
                session.contacts, key=lambda c: c.name.casefold() if c.name else ""
            )
            t = "\n".join(f"{c.name}: xmpp:{c.jid.bare}" for c in contacts)
            msg.reply(t).send()

    async def _chat_command_register(
        self, *args, msg: Message, session: Optional[SessionType]
    ):
        if session is not None:
            msg.reply("You are already registered to this gateway").send()
            return

        jid = msg.get_from()

        if not self._jid_validator.match(jid.bare):
            msg.reply("You are not allowed to register to this gateway").send()
            return

        form: dict[str, Optional[str]] = {}
        for field in self.REGISTRATION_FIELDS:
            text = field.label or field.var
            if field.value != "":
                text += f" (default: '{field.value}')"
            if not field.required:
                text += " (optional, reply with '.' to skip)"
            if (options := field.options) is not None:
                for option in options:
                    label = option["label"]
                    value = option["value"]
                    text += f"\n{label}: reply with '{value}'"

            while True:
                ans = await self.input(jid, text + "?")
                if ans == "." and not field.required:
                    form[field.var] = None
                    break
                else:
                    if (options := field.options) is not None:
                        valid_choices = [x["value"] for x in options]
                        if ans not in valid_choices:
                            continue
                    form[field.var] = ans
                    break

        try:
            await self.validate(jid, form)
            await self["xep_0077"].api["user_validate"](None, None, jid, form)
        except (ValueError, XMPPError) as e:
            msg.reply(f"Something went wrong: {e}").send()
        else:
            self.event("user_register", msg)
            msg.reply(f"Success!").send()

    def add_adhoc_commands(self):
        """
        Override this if you want to provide custom adhoc commands (:xep:`0050`)
        for your plugin, using :class:`slixmpp.plugins.xep_0050.XEP_0050`

        Basic example:

        .. code-block:python

            def add_adhoc_commands(self):
                self["xep_0050"].add_command(
                    node="account_info",
                    name="Account Information",
                    handler=self.handle_account_info
                )

            async def handle_account_info(self, iq: Iq, adhoc_session: dict[str, Any]):
                # beware, 'adhoc_session' is not a slidge session!
                user = user_store.get_by_stanza(iq)

                if user is None:
                    raise XMPPError("subscription-required")

                form = self["xep_0004"].make_form("result", "Account info")
                form.add_field(
                    label="Credits",
                    value=await FakeLegacyClient.get_credits(user.registration_form['username']),
                )

                adhoc_session["payload"] = form
                adhoc_session["has_next"] = False

                return session
        """
        pass

    def config(self, argv: list[str]):
        """
        Override this to access CLI args to configure the slidge plugin

        :param argv: CLI args that were not parsed by the slidge main entrypoint parser
        :func:`slidge.__main__.get_parser`
        """
        pass

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        """
        Validate a registration form from a user.

        Since :xep:`0077` is pretty limited in terms of validation, it is OK to validate
        anything that looks good here and continue the legacy auth process via direct messages
        to the user (using :meth:`.BaseGateway.input` for instance)

        :param user_jid: JID of the user that has just registered
        :param registration_form: A dict where keys are the :attr:`.FormField.var` attributes
         of the :attr:`.BaseGateway.REGISTRATION_FIELDS` iterable
        """
        pass

    async def unregister(self, user: GatewayUser):
        """
        Optionally override this if you need to clean additional
        stuff after a user has been removed from the permanent user_store.

        :param user:
        :return:
        """
        pass

    async def _on_gateway_message_private(self, msg: Message):
        """
        Called when an XMPP user (not necessarily registered as a gateway user) sends a direct message to
        the gateway.

        If you override this and still want :meth:`.BaseGateway.input` to work, make sure to include the try/except part.

        :param msg: Message sent by the XMPP user
        """
        try:
            f = self._input_futures.pop(msg.get_from().bare)
        except KeyError:
            text = msg["body"]
            command, *rest = text.split(" ")

            user = user_store.get_by_stanza(msg)
            if user is None:
                session = None
            else:
                session = self._get_session_from_user(user)

            handler = self._chat_commands.get(command)
            if handler is None:
                await self.on_gateway_message(msg, session=session)
            else:
                log.debug("Chat command handler: %s", handler)
                await handler(*rest, msg=msg, session=session)
        else:
            f.set_result(msg["body"])

    @staticmethod
    async def on_gateway_message(msg: Message, session: Optional[SessionType] = None):
        """
        Called when the gateway component receives a direct gateway message.

        Can be used to implement bot like commands, especially in conjunction with
        :meth:`.BaseGateway.input`

        :param msg:
        :param session: If the message comes from a registered gateway user, their :.BaseSession:
        """
        if session is None:
            r = msg.reply(
                body="I got that, but I'm not doing anything with it. I don't even know you!"
            )
        else:
            r = msg.reply(body="What? Type 'help' for the list of available commands.")
        r["type"] = "chat"
        r.send()

    async def input(
        self, jid: JID, text=None, mtype: MessageTypes = "chat", **msg_kwargs
    ) -> str:
        """
        Request arbitrary user input using a simple chat message, and await the result.

        You shouldn't need to call directly bust instead user :meth:`.BaseSession.input`
        to directly target a user.

        NB: When using this, the next message that the user sent to the component will
        not be transmitted to :meth:`.BaseGateway.on_gateway_message`, but rather intercepted.
        Await the coroutine to get its content.

        :param jid: The JID we want input from
        :param text: A prompt to display for the user
        :param mtype: Message type
        :return: The user's reply
        """
        if text is not None:
            self.send_message(
                mto=jid, mbody=text, mtype=mtype, mfrom=self.boundjid.bare, **msg_kwargs
            )
        f = self.loop.create_future()
        self._input_futures[jid.bare] = f
        await f
        return f.result()

    async def send_file(self, filename: str, **msg_kwargs):
        """
        Upload a file using :xep:`0363` and send the link as out of band (:xep:`0066`)
        content in a message.

        :param filename:
        :param msg_kwargs:
        :return:
        """
        msg = self.make_message(**msg_kwargs)
        msg.set_from(self.boundjid.bare)
        try:
            url = await self["xep_0363"].upload_file(
                filename=filename, ifrom=self.upload_requester
            )
        except FileUploadError as e:
            log.warning(
                "Something is wrong with the upload service, see the traceback below"
            )
            log.exception(e)
            msg["body"] = (
                "I tried to send a file, but something went wrong. "
                "Tell your XMPP admin to check slidge logs."
            )
            msg.send()
            return
        msg["oob"]["url"] = url
        msg["body"] = url
        msg.send()

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
            session = self._session_cls.from_jid(user.jid)
            for c in session.contacts:
                c.offline()
            self.loop.create_task(session.logout())
            self.send_presence(ptype="unavailable", pto=user.jid)


GatewayType = TypeVar("GatewayType", bound=BaseGateway)


SLIXMPP_PLUGINS = [
    "xep_0050",  # Adhoc commands
    "xep_0055",  # Jabber search
    "xep_0066",  # Out of Band Data
    "xep_0077",  # In-band registration
    "xep_0084",  # User Avatar
    "xep_0085",  # Chat state notifications
    "xep_0100",  # Gateway interaction
    "xep_0115",  # Entity capabilities
    "xep_0172",  # User nickname
    "xep_0184",  # Message Delivery Receipts
    "xep_0280",  # Carbons
    "xep_0292_provider",  # VCard4
    "xep_0308",  # Last message correction
    "xep_0333",  # Chat markers
    "xep_0334",  # Message Processing Hints
    "xep_0356",  # Privileged Entity
    "xep_0356_old",  # Privileged Entity (old namespace)
    "xep_0363",  # HTTP file upload
    "xep_0424",  # Message retraction
    "xep_0444",  # Message reactions
    "xep_0461",  # Message replies
]
log = logging.getLogger(__name__)
