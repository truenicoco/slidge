import logging
from typing import TYPE_CHECKING, Optional

from slixmpp import Message
from slixmpp.exceptions import XMPPError

if TYPE_CHECKING:
    from slidge import BaseGateway
    from slidge.core.session import SessionType


class ChatCommandProvider:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

    async def _chat_command_search(
        self, *args, msg: Message, session: Optional["SessionType"] = None
    ):
        if session is None:
            msg.reply("Register to the gateway first!")
            return

        search_form = {}
        diff = len(args) - len(self.xmpp.SEARCH_FIELDS)

        if diff > 0:
            session.send_gateway_message("Too many parameters!")
            return

        for field, arg in zip(self.xmpp.SEARCH_FIELDS, args):
            search_form[field.var] = arg

        if diff < 0:
            for field in self.xmpp.SEARCH_FIELDS[diff:]:
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
        self, *_args, msg: Message, session: Optional["SessionType"]
    ):
        if session is None:
            msg.reply("Register to the gateway first!").send()
        else:
            t = "|".join(
                x
                for x in self.xmpp._chat_commands.keys()
                if x not in ("register", "help")
            )
            log.debug("In help: %s", t)
            msg.reply(f"Available commands: {t}").send()

    @staticmethod
    async def _chat_command_list_contacts(
        *_args, msg: Message, session: Optional["SessionType"]
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
        self, *args, msg: Message, session: Optional["SessionType"]
    ):
        if session is not None:
            msg.reply("You are already registered to this gateway").send()
            return

        jid = msg.get_from()

        if not self.xmpp.jid_validator.match(jid.bare):  # type:ignore
            msg.reply("You are not allowed to register to this gateway").send()
            return

        form: dict[str, Optional[str]] = {}
        for field in self.xmpp.REGISTRATION_FIELDS:
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
                ans = await self.xmpp.input(jid, text + "?")
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
            await self.xmpp.validate(jid, form)
            await self.xmpp["xep_0077"].api["user_validate"](None, None, jid, form)
        except (ValueError, XMPPError) as e:
            msg.reply(f"Something went wrong: {e}").send()
        else:
            self.xmpp.event("user_register", msg)
            msg.reply(f"Success!").send()


log = logging.getLogger(__name__)
