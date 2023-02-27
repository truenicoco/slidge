import asyncio
import functools
import logging
from functools import partial
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from slixmpp import JID, Iq
from slixmpp.plugins.xep_0004 import Form as SlixForm

from ..util.error import XMPPError
from ..util.xep_0030.stanza.items import DiscoItems
from .command import Command, CommandResponseType, Confirmation, Form, TableResult

if TYPE_CHECKING:
    from .gateway import BaseGateway
    from .session import BaseSession


class AdhocProvider:
    """
    A slixmpp-like plugin to handle adhoc commands, with less boilerplate and
    untyped dict values than slixmpp.
    """

    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        self._commands = dict[str, Command]()
        xmpp.plugin["xep_0030"].set_node_handler(
            "get_items",
            jid=xmpp.boundjid,
            node=self.xmpp.plugin["xep_0050"].stanza.Command.namespace,
            handler=self.get_items,
        )

    async def __wrap_initial_handler(
        self, command: Command, iq: Iq, adhoc_session: dict[str, Any]
    ):
        ifrom = iq.get_from()
        session = command.raise_if_not_authorized(ifrom)
        result = await self.__wrap_handler(command.run, session, ifrom)
        return await self.__handle_result(session, result, adhoc_session)

    async def __handle_result(
        self,
        session: Optional["BaseSession"],
        result: CommandResponseType,
        adhoc_session: dict[str, Any],
    ):
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
    async def __wrap_handler(f: Union[Callable, functools.partial], *a, **k):
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
        session: Optional["BaseSession"],
        result: Form,
        form: SlixForm,
        adhoc_session: dict[str, Any],
    ):
        form_values = result.get_values(form)
        result = await self.__wrap_handler(
            result.handler,
            form_values,
            session,
            adhoc_session["from"],
            *result.handler_args,
            **result.handler_kwargs
        )

        return await self.__handle_result(session, result, adhoc_session)

    async def __wrap_confirmation(
        self,
        session: Optional["BaseSession"],
        confirmation: Confirmation,
        form: SlixForm,
        adhoc_session: dict[str, Any],
    ):
        if form.get_values().get("confirm"):
            result = await self.__wrap_handler(
                confirmation.handler,
                session,
                adhoc_session["from"],
                *confirmation.handler_args,
                **confirmation.handler_kwargs
            )
            if confirmation.success:
                result = confirmation.success
        else:
            result = "You canceled the operation"

        return await self.__handle_result(session, result, adhoc_session)

    def register(self, command: Command, jid: Optional[JID] = None):
        """
        Register a command as a adhoc command.

        this does not need to be called manually, ``BaseGateway`` takes care of
        that.

        :param command:
        :param jid:
        """
        if command.NODE in self._commands:
            raise RuntimeError(
                "There is already a command for the node '%s'", command.NODE
            )
        self._commands[command.NODE] = command
        if jid is None:
            jid = self.xmpp.boundjid
        elif not isinstance(jid, JID):
            jid = JID(jid)

        self.xmpp.plugin["xep_0050"].add_command(
            jid=jid,
            node=command.NODE,
            name=command.NAME,
            handler=partial(self.__wrap_initial_handler, command),
        )

    async def get_items(self, jid: JID, node: str, iq: Iq):
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
            return all_items

        ifrom = iq.get_from()

        filtered_items = DiscoItems()
        filtered_items["node"] = self.xmpp.plugin["xep_0050"].stanza.Command.namespace
        for item in all_items:
            try:
                self._commands[item["node"]].raise_if_not_authorized(ifrom)
            except XMPPError:
                continue

            filtered_items.append(item)

        return filtered_items


log = logging.getLogger(__name__)
