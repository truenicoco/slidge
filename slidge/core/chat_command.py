import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from slixmpp import Message
from slixmpp.exceptions import XMPPError

from ..util.db import GatewayUser
from ..util.types import SessionType
from . import config
from .adhoc import RegistrationType, TwoFactorNotRequired

if TYPE_CHECKING:
    from .gateway import BaseGateway


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

        msg.reply(self.xmpp.REGISTRATION_INSTRUCTIONS).send()
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

        user = GatewayUser(bare_jid=jid.bare, registration_form=form)

        try:
            two_fa_needed = True
            try:
                await self.xmpp.user_prevalidate(jid, form)
            except TwoFactorNotRequired:
                if self.xmpp.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE:
                    two_fa_needed = False
                else:
                    raise

            if self.xmpp.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE:
                if two_fa_needed:
                    code = await self.xmpp.input(
                        jid,
                        self.xmpp.REGISTRATION_2FA_TITLE
                        + "\n"
                        + self.xmpp.REGISTRATION_2FA_INSTRUCTIONS,
                    )
                    await self.xmpp.validate_two_factor_code(user, code)

            elif self.xmpp.REGISTRATION_TYPE == RegistrationType.QRCODE:
                fut = self.xmpp.loop.create_future()
                self.xmpp.qr_pending_registrations[user.bare_jid] = fut  # type:ignore
                qr_url = await self.xmpp.get_qr_text(user)
                self.xmpp.send_message(
                    mto=jid, mbody=qr_url, mfrom=self.xmpp.boundjid.bare
                )
                await self.xmpp.send_qr(qr_url, mto=jid)
                try:
                    await asyncio.wait_for(fut, config.QR_TIMEOUT)
                except asyncio.TimeoutError:
                    msg.reply(f"You did not flash the QR code in time!").send()
                    return

        except (ValueError, XMPPError) as e:
            msg.reply(f"Something went wrong: {e}").send()

        else:
            user.commit()
            self.xmpp.event("user_register", msg)
            msg.reply(f"Success!").send()

    async def _chat_command_unregister(
        self, *args, msg: Message, session: Optional["SessionType"]
    ):
        ifrom = msg.get_from()
        await self.xmpp.plugin["xep_0077"].api["user_remove"](None, None, ifrom)
        await self.xmpp.session_cls.kill_by_jid(ifrom)

    @staticmethod
    async def _chat_command_list_groups(
        *_args, msg: Message, session: Optional["SessionType"]
    ):
        if session is None:
            msg.reply("Register to the gateway first!").send()
        else:
            groups = sorted(
                session.bookmarks,
                key=lambda m: m.DISCO_NAME.casefold() if m.DISCO_NAME else "",
            )
            if groups:
                t = "\n".join(f"{m.DISCO_NAME}: xmpp:{m.jid}?join" for m in groups)
                msg.reply(t).send()
            else:
                msg.reply("No groups!").send()


log = logging.getLogger(__name__)
