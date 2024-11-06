# Handle slidge commands by exchanging chat messages with the gateway components.

# Ad-hoc methods should provide a better UX, but some clients do not support them,
# so this is mostly a fallback.
import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Callable, Literal, Optional, Union, overload
from urllib.parse import quote as url_quote

from slixmpp import JID, CoroutineCallback, Message, StanzaPath
from slixmpp.exceptions import XMPPError
from slixmpp.types import JidStr, MessageTypes

from . import Command, CommandResponseType, Confirmation, Form, TableResult
from .categories import CommandCategory

if TYPE_CHECKING:
    from ..core.gateway import BaseGateway


class ChatCommandProvider:
    UNKNOWN = "Wut? I don't know that command: {}"

    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        self._keywords = list[str]()
        self._commands: dict[str, Command] = {}
        self._input_futures = dict[str, asyncio.Future[str]]()
        self.xmpp.register_handler(
            CoroutineCallback(
                "chat_command_handler",
                StanzaPath(f"message@to={self.xmpp.boundjid.bare}"),
                self._handle_message,  # type: ignore
            )
        )

    def register(self, command: Command):
        """
        Register a command to be used via chat messages with the gateway

        Plugins should not call this, any class subclassing Command should be
        automatically added by slidge core.

        :param command: the new command
        """
        t = command.CHAT_COMMAND
        if t in self._commands:
            raise RuntimeError("There is already a command triggered by '%s'", t)
        self._commands[t] = command

    @overload
    async def input(
        self, jid: JidStr, text: Optional[str], blocking: Literal[False]
    ) -> asyncio.Future[str]: ...

    @overload
    async def input(
        self,
        jid: JidStr,
        text: Optional[str],
        mtype: MessageTypes = ...,
        blocking: Literal[True] = ...,
    ) -> str: ...

    async def input(
        self,
        jid,
        text=None,
        mtype="chat",
        timeout=60,
        blocking=True,
        **msg_kwargs,
    ):
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
        :param timeout:
        :param blocking: If set to False, timeout has no effect and an :class:`asyncio.Future`
            is returned instead of a str
        :return: The user's reply
        """
        jid = JID(jid)
        if text is not None:
            self.xmpp.send_message(
                mto=jid,
                mbody=text,
                mtype=mtype,
                mfrom=self.xmpp.boundjid.bare,
                **msg_kwargs,
            )
        f = asyncio.get_event_loop().create_future()
        self._input_futures[jid.bare] = f
        if not blocking:
            return f
        try:
            await asyncio.wait_for(f, timeout)
        except asyncio.TimeoutError:
            self.xmpp.send_message(
                mto=jid,
                mbody="You took too much time to reply",
                mtype=mtype,
                mfrom=self.xmpp.boundjid.bare,
            )
            del self._input_futures[jid.bare]
            raise XMPPError("remote-server-timeout", "You took too much time to reply")

        return f.result()

    async def _handle_message(self, msg: Message):
        if not msg["body"]:
            return

        if not msg.get_from().node:
            return  # ignore component and server messages

        f = self._input_futures.pop(msg.get_from().bare, None)
        if f is not None:
            f.set_result(msg["body"])
            return

        c = msg["body"]
        first_word, *rest = c.split(" ")
        first_word = first_word.lower()

        if first_word == "help":
            return self._handle_help(msg, *rest)

        mfrom = msg.get_from()

        command = self._commands.get(first_word)
        if command is None:
            return self._not_found(msg, first_word)

        try:
            session = command.raise_if_not_authorized(mfrom)
        except XMPPError as e:
            reply = msg.reply()
            reply["body"] = e.text
            reply.send()
            raise

        result = await self.__wrap_handler(msg, command.run, session, mfrom, *rest)
        self.xmpp.delivery_receipt.ack(msg)
        return await self._handle_result(result, msg, session)

    def __make_uri(self, body: str) -> str:
        return f"xmpp:{self.xmpp.boundjid.bare}?message;body={body}"

    async def _handle_result(self, result: CommandResponseType, msg: Message, session):
        if isinstance(result, str) or result is None:
            reply = msg.reply()
            reply["body"] = result or "End of command."
            reply.send()
            return

        if isinstance(result, Form):
            form_values = {}
            for t in result.title, result.instructions:
                if t:
                    msg.reply(t).send()
            for f in result.fields:
                if f.type == "fixed":
                    msg.reply(f"{f.label or f.var}: {f.value}").send()
                else:
                    if f.type == "list-multi":
                        msg.reply(
                            "Multiple selection allowed, use new lines as a separator, ie, "
                            "one selected item per line. To select no item, reply with a space "
                            "(the punctuation)."
                        ).send()
                    if f.options:
                        for o in f.options:
                            msg.reply(
                                f"{o['label']}: {self.__make_uri(o['value'])}"
                            ).send()
                    if f.value:
                        msg.reply(f"Default: {f.value}").send()
                    if f.type == "boolean":
                        msg.reply("yes: " + self.__make_uri("yes")).send()
                        msg.reply("no: " + self.__make_uri("no")).send()

                    ans = await self.xmpp.input(
                        msg.get_from(), (f.label or f.var) + "? (or 'abort')"
                    )
                    if ans.lower() == "abort":
                        return await self._handle_result(
                            "Command aborted", msg, session
                        )
                    if f.type == "boolean":
                        if ans.lower() == "yes":
                            ans = "true"
                        else:
                            ans = "false"

                    if f.type.endswith("multi"):
                        choices = [] if ans == " " else ans.split("\n")
                        form_values[f.var] = f.validate(choices)
                    else:
                        form_values[f.var] = f.validate(ans)
            result = await self.__wrap_handler(
                msg,
                result.handler,
                form_values,
                session,
                msg.get_from(),
                *result.handler_args,
                **result.handler_kwargs,
            )
            return await self._handle_result(result, msg, session)

        if isinstance(result, Confirmation):
            yes_or_no = await self.input(msg.get_from(), result.prompt)
            if not yes_or_no.lower().startswith("y"):
                reply = msg.reply()
                reply["body"] = "Canceled"
                reply.send()
                return
            result = await self.__wrap_handler(
                msg,
                result.handler,
                session,
                msg.get_from(),
                *result.handler_args,
                **result.handler_kwargs,
            )
            return await self._handle_result(result, msg, session)

        if isinstance(result, TableResult):
            if len(result.items) == 0:
                msg.reply("Empty results").send()
                return

            body = result.description + "\n"
            for item in result.items:
                for f in result.fields:
                    if f.type == "jid-single":
                        j = JID(item[f.var])
                        value = f"xmpp:{percent_encode(j)}"
                        if result.jids_are_mucs:
                            value += "?join"
                    else:
                        value = item[f.var]  # type:ignore
                    body += f"\n{f.label or f.var}: {value}"
            msg.reply(body).send()

    @staticmethod
    async def __wrap_handler(msg, f: Union[Callable, functools.partial], *a, **k):
        try:
            if asyncio.iscoroutinefunction(f):
                return await f(*a, **k)
            elif hasattr(f, "func") and asyncio.iscoroutinefunction(f.func):
                return await f(*a, **k)
            else:
                return f(*a, **k)
        except Exception as e:
            log.debug("Error in %s", f, exc_info=e)
            reply = msg.reply()
            reply["body"] = f"Error: {e}"
            reply.send()

    def _handle_help(self, msg: Message, *rest):
        if len(rest) == 0:
            reply = msg.reply()
            reply["body"] = self._help(msg.get_from())
            reply.send()
        elif len(rest) == 1 and (command := self._commands.get(rest[0])):
            reply = msg.reply()
            reply["body"] = f"{command.CHAT_COMMAND}: {command.NAME}\n{command.HELP}"
            reply.send()
        else:
            self._not_found(msg, str(rest))

    def _help(self, mfrom: JID):
        msg = "Available commands:"
        for c in sorted(
            self._commands.values(),
            key=lambda co: (
                (
                    co.CATEGORY
                    if isinstance(co.CATEGORY, str)
                    else (
                        co.CATEGORY.name
                        if isinstance(co.CATEGORY, CommandCategory)
                        else ""
                    )
                ),
                co.CHAT_COMMAND,
            ),
        ):
            try:
                c.raise_if_not_authorized(mfrom)
            except XMPPError:
                continue
            msg += f"\n{c.CHAT_COMMAND} -- {c.NAME}"
        return msg

    def _not_found(self, msg: Message, word: str):
        e = self.UNKNOWN.format(word)
        msg.reply(e).send()
        raise XMPPError("item-not-found", e)


def percent_encode(jid: JID):
    return f"{url_quote(jid.user)}@{jid.server}"  # type:ignore


log = logging.getLogger(__name__)
