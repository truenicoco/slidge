import asyncio
import functools
import logging
from functools import partial
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from slixmpp import JID, Iq  # type: ignore[attr-defined]
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0004 import Form as SlixForm  # type: ignore[attr-defined]
from slixmpp.plugins.xep_0030.stanza.items import DiscoItems

from ..core import config
from ..util.util import strip_leading_emoji
from . import Command, CommandResponseType, Confirmation, Form, TableResult
from .base import FormField
from .categories import CommandCategory

if TYPE_CHECKING:
    from ..core.gateway import BaseGateway
    from ..core.session import BaseSession


AdhocSessionType = dict[str, Any]


class AdhocProvider:
    """
    A slixmpp-like plugin to handle adhoc commands, with less boilerplate and
    untyped dict values than slixmpp.
    """

    def __init__(self, xmpp: "BaseGateway") -> None:
        self.xmpp = xmpp
        self._commands = dict[str, Command]()
        self._categories = dict[str, list[Command]]()
        xmpp.plugin["xep_0030"].set_node_handler(
            "get_items",
            jid=xmpp.boundjid,
            node=self.xmpp.plugin["xep_0050"].stanza.Command.namespace,
            handler=self.get_items,
        )

    async def __wrap_initial_handler(
        self, command: Command, iq: Iq, adhoc_session: AdhocSessionType
    ) -> AdhocSessionType:
        ifrom = iq.get_from()
        session = command.raise_if_not_authorized(ifrom)
        result = await self.__wrap_handler(command.run, session, ifrom)
        return await self.__handle_result(session, result, adhoc_session)

    async def __handle_category_list(
        self, category: CommandCategory, iq: Iq, adhoc_session: AdhocSessionType
    ) -> AdhocSessionType:
        try:
            session = self.xmpp.get_session_from_stanza(iq)
        except XMPPError:
            session = None
        commands: dict[str, Command] = {}
        for command in self._categories[category.node]:
            try:
                command.raise_if_not_authorized(iq.get_from())
            except XMPPError:
                continue
            commands[command.NODE] = command
        if len(commands) == 0:
            raise XMPPError(
                "not-authorized", "There is no command you can run in this category"
            )
        return await self.__handle_result(
            session,
            Form(
                category.name,
                "",
                [
                    FormField(
                        var="command",
                        label="Command",
                        type="list-single",
                        options=[
                            {
                                "label": strip_leading_emoji_if_needed(command.NAME),
                                "value": command.NODE,
                            }
                            for command in commands.values()
                        ],
                    )
                ],
                partial(self.__handle_category_choice, commands),
            ),
            adhoc_session,
        )

    async def __handle_category_choice(
        self,
        commands: dict[str, Command],
        form_values: dict[str, str],
        session: "BaseSession[Any, Any]",
        jid: JID,
    ):
        command = commands[form_values["command"]]
        result = await self.__wrap_handler(command.run, session, jid)
        return result

    async def __handle_result(
        self,
        session: Optional["BaseSession[Any, Any]"],
        result: CommandResponseType,
        adhoc_session: AdhocSessionType,
    ) -> AdhocSessionType:
        if isinstance(result, str) or result is None:
            adhoc_session["has_next"] = False
            adhoc_session["next"] = None
            adhoc_session["payload"] = None
            adhoc_session["notes"] = [("info", result or "Success!")]
            return adhoc_session

        if isinstance(result, Form):
            adhoc_session["next"] = partial(self.__wrap_form_handler, session, result)
            adhoc_session["has_next"] = True
            adhoc_session["payload"] = result.get_xml()
            return adhoc_session

        if isinstance(result, Confirmation):
            adhoc_session["next"] = partial(self.__wrap_confirmation, session, result)
            adhoc_session["has_next"] = True
            adhoc_session["payload"] = result.get_form()
            adhoc_session["next"] = partial(self.__wrap_confirmation, session, result)
            return adhoc_session

        if isinstance(result, TableResult):
            adhoc_session["next"] = None
            adhoc_session["has_next"] = False
            adhoc_session["payload"] = result.get_xml()
            return adhoc_session

        raise XMPPError("internal-server-error", text="OOPS!")

    @staticmethod
    async def __wrap_handler(f: Union[Callable, functools.partial], *a, **k):  # type: ignore
        try:
            if asyncio.iscoroutinefunction(f):
                return await f(*a, **k)
            elif hasattr(f, "func") and asyncio.iscoroutinefunction(f.func):
                return await f(*a, **k)
            else:
                return f(*a, **k)
        except Exception as e:
            log.debug("Exception in %s", f, exc_info=e)
            raise XMPPError("internal-server-error", text=str(e))

    async def __wrap_form_handler(
        self,
        session: Optional["BaseSession[Any, Any]"],
        result: Form,
        form: SlixForm,
        adhoc_session: AdhocSessionType,
    ) -> AdhocSessionType:
        form_values = result.get_values(form)
        new_result = await self.__wrap_handler(
            result.handler,
            form_values,
            session,
            adhoc_session["from"],
            *result.handler_args,
            **result.handler_kwargs,
        )

        return await self.__handle_result(session, new_result, adhoc_session)

    async def __wrap_confirmation(
        self,
        session: Optional["BaseSession[Any, Any]"],
        confirmation: Confirmation,
        form: SlixForm,
        adhoc_session: AdhocSessionType,
    ) -> AdhocSessionType:
        if form.get_values().get("confirm"):  # type: ignore[no-untyped-call]
            result = await self.__wrap_handler(
                confirmation.handler,
                session,
                adhoc_session["from"],
                *confirmation.handler_args,
                **confirmation.handler_kwargs,
            )
            if confirmation.success:
                result = confirmation.success
        else:
            result = "You canceled the operation"

        return await self.__handle_result(session, result, adhoc_session)

    def register(self, command: Command, jid: Optional[JID] = None) -> None:
        """
        Register a command as a adhoc command.

        this does not need to be called manually, ``BaseGateway`` takes care of
        that.

        :param command:
        :param jid:
        """
        if jid is None:
            jid = self.xmpp.boundjid
        elif not isinstance(jid, JID):
            jid = JID(jid)

        if (category := command.CATEGORY) is None:
            if command.NODE in self._commands:
                raise RuntimeError(
                    "There is already a command for the node '%s'", command.NODE
                )
            self._commands[command.NODE] = command
            self.xmpp.plugin["xep_0050"].add_command(  # type: ignore[no-untyped-call]
                jid=jid,
                node=command.NODE,
                name=strip_leading_emoji_if_needed(command.NAME),
                handler=partial(self.__wrap_initial_handler, command),
            )
        else:
            if isinstance(category, str):
                category = CommandCategory(category, category)
            node = category.node
            name = category.name
            if node not in self._categories:
                self._categories[node] = list[Command]()
                self.xmpp.plugin["xep_0050"].add_command(  # type: ignore[no-untyped-call]
                    jid=jid,
                    node=node,
                    name=strip_leading_emoji_if_needed(name),
                    handler=partial(self.__handle_category_list, category),
                )
            self._categories[node].append(command)

    async def get_items(self, jid: JID, node: str, iq: Iq) -> DiscoItems:
        """
        Get items for a disco query

        :param jid: who is requesting the disco
        :param node: which command node is requested
        :param iq:  the disco query IQ
        :return: commands accessible to the given JID will be listed
        """
        all_items = self.xmpp.plugin["xep_0030"].static.get_items(jid, node, None, None)
        log.debug("Static items: %r", all_items)
        if not all_items:
            return DiscoItems()

        ifrom = iq.get_from()

        filtered_items = DiscoItems()
        filtered_items["node"] = self.xmpp.plugin["xep_0050"].stanza.Command.namespace
        for item in all_items:
            authorized = True
            if item["node"] in self._categories:
                for command in self._categories[item["node"]]:
                    try:
                        command.raise_if_not_authorized(ifrom)
                    except XMPPError:
                        authorized = False
                    else:
                        authorized = True
                        break
            else:
                try:
                    self._commands[item["node"]].raise_if_not_authorized(ifrom)
                except XMPPError:
                    authorized = False

            if authorized:
                filtered_items.append(item)

        return filtered_items


def strip_leading_emoji_if_needed(text: str) -> str:
    if config.STRIP_LEADING_EMOJI_ADHOC:
        return strip_leading_emoji(text)
    return text


log = logging.getLogger(__name__)
