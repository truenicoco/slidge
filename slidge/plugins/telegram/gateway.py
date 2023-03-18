import asyncio
import logging
import shutil
import typing
from datetime import datetime

import aiotdlib.api as tgapi
from slixmpp import JID

from slidge import *
from slidge.core.command import Command, CommandAccess, Confirmation, Form, TableResult
from slidge.core.command.register import RegistrationType

from ...util import is_valid_phone_number
from . import config
from .client import CredentialsValidation

if typing.TYPE_CHECKING:
    from .session import Session

REGISTRATION_INSTRUCTIONS = (
    "You need to create a telegram account in an official telegram client.\n\n"
    "Then you can enter your phone number here, and you will receive a confirmation code "
    "in the official telegram client. "
    "You can uninstall the telegram client after this if you want."
)


class SessionCommandMixin:
    INSTRUCTIONS: str = NotImplemented

    async def run(self, session, ifrom: JID, *args):
        assert session is not None
        tg_sessions = (await session.tg.api.get_active_sessions()).sessions
        if args:
            return await self.step2(
                {"tg-session": args[0]}, session, ifrom, tg_sessions
            )
        return Form(
            title="Telegram sessions",
            instructions=self.INSTRUCTIONS,
            fields=[
                FormField(
                    "tg-session",
                    type="list-single",
                    label="Session",
                    options=[
                        {"label": f"{i}: {s.country} ({s.region})", "value": str(i)}
                        for i, s in enumerate(tg_sessions)
                    ],
                )
            ],
            handler=self.step2,
            handler_args=[tg_sessions],
        )

    async def step2(
        self, form_values, _session, _ifrom, tg_sessions: list[tgapi.Session]
    ):
        raise NotImplementedError


class ListSessions(SessionCommandMixin, Command):
    NAME = "List telegram sessions"
    NODE = CHAT_COMMAND = "tg-sessions"
    ACCESS = CommandAccess.USER_LOGGED
    INSTRUCTIONS = "Pick a session for more details"

    async def step2(
        self, form_values, _session, _ifrom, tg_sessions: list[tgapi.Session]
    ):
        i = int(form_values["tg-session"])
        tg_session = tg_sessions[i]
        items = [
            {"name": n.removesuffix("_"), "value": str(getattr(tg_session, n))}
            for n in [
                "is_current",
                "type_",
                "application_name",
                "ip",
                "country",
                "region",
            ]
        ]
        items.extend(
            {
                "name": n,
                "value": fmt_timestamp(getattr(tg_session, n, 0)),
            }
            for n in ["log_in_date", "last_active_date"]
        )
        return TableResult(
            description=f"Details of telegram session #{i}",
            fields=[FormField("name"), FormField("value")],
            items=items,
        )


class TerminateSession(SessionCommandMixin, Command):
    NAME = "Terminate a telegram session"
    NODE = CHAT_COMMAND = "terminate-tg-session"
    ACCESS = CommandAccess.USER_LOGGED
    INSTRUCTIONS = "Pick a session to terminate it"

    async def step2(
        self, form_values, session, _ifrom, tg_sessions: list[tgapi.Session]
    ):
        assert session is not None
        i = int(form_values["tg-session"])
        tg_session = tg_sessions[i]
        return Confirmation(
            prompt=f"Are you sure you want to terminate session #{i} "
            f"(last active on {fmt_timestamp(tg_session.last_active_date)})",
            success="The session has been terminated",
            handler=self.finish,
            handler_args=[i],
        )

    @staticmethod
    async def finish(session: "Session", _ifrom, session_i: int):
        await session.tg.api.terminate_session(session_i)
        return "Session has been terminated"


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = REGISTRATION_INSTRUCTIONS
    REGISTRATION_FIELDS = [FormField(var="phone", label="Phone number", required=True)]
    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE
    ROSTER_GROUP = "Telegram"
    COMPONENT_NAME = "Telegram (slidge)"
    COMPONENT_TYPE = "telegram"
    COMPONENT_AVATAR = "https://web.telegram.org/img/logo_share.png"

    SEARCH_FIELDS = [
        FormField(var="phone", label="Phone number", required=True),
        FormField(var="first", label="First name", required=True),
        FormField(var="last", label="Last name", required=False),
    ]

    GROUPS = True

    def __init__(self):
        super().__init__()
        if config.TDLIB_PATH is None:
            config.TDLIB_PATH = global_config.HOME_DIR / "tdlib"
        self._pending_registrations = dict[
            str, tuple[asyncio.Task[CredentialsValidation], CredentialsValidation]
        ]()
        if not config.API_ID:
            self.REGISTRATION_FIELDS.extend(
                [
                    FormField(
                        var="info",
                        type="fixed",
                        label="Get API id and hash on https://my.telegram.org/apps",
                    ),
                    FormField(var="api_id", label="API ID", required=True),
                    FormField(var="api_hash", label="API Hash", required=True),
                ]
            )
        log.debug("CONFIG %s", vars(config))

    async def validate(
        self, user_jid: JID, registration_form: dict[str, typing.Optional[str]]
    ):
        phone = registration_form.get("phone")
        if not is_valid_phone_number(phone):
            raise ValueError("Not a valid phone number")
        for u in user_store.get_all():
            if u.registration_form.get("phone") == phone:
                raise XMPPError(
                    "not-allowed",
                    text="Someone is already using this phone number on this server.",
                )
        tg_client = CredentialsValidation(registration_form)  # type: ignore
        auth_task = self.loop.create_task(tg_client.start())
        self._pending_registrations[user_jid.bare] = auth_task, tg_client

    async def validate_two_factor_code(self, user: GatewayUser, code: str):
        auth_task, tg_client = self._pending_registrations.pop(user.bare_jid)
        tg_client.code_future.set_result(code)
        try:
            await asyncio.wait_for(auth_task, config.REGISTRATION_AUTH_CODE_TIMEOUT)
        except asyncio.TimeoutError:
            raise XMPPError(
                "not-authorized",
                text="Something went wrong when trying to authenticate you on the "
                "telegram network. Please retry and/or contact your slidge admin.",
            )
        await tg_client.stop()

    async def unregister(self, user: GatewayUser):
        session = self.session_cls.from_user(user)
        session.logged = False
        workdir = session.tg.settings.files_directory.absolute()
        await session.tg.api.log_out()
        shutil.rmtree(workdir)


def fmt_timestamp(t: int):
    return datetime.fromtimestamp(t).isoformat(timespec="minutes")


log = logging.getLogger(__name__)
