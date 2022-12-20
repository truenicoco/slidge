import asyncio
import logging
import typing
from datetime import datetime

import aiotdlib.api as tgapi
from slixmpp import JID, Iq
from slixmpp.exceptions import XMPPError

from slidge import *
from slidge.core.adhoc import RegistrationType

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


class Gateway(BaseGateway["Session"]):
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
        # FIXME: this effectively removes user data from disk, but crashes slidge.
        await session.tg.start()
        await session.tg.api.log_out()

    def add_adhoc_commands(self):
        self.adhoc.add_command(
            node="get_sessions",
            name="List active sessions",
            handler=self.adhoc_active_sessions1,
            only_users=True,
        )

    async def adhoc_active_sessions1(
        self, iq: Iq, adhoc_session: dict[str, typing.Any]
    ):
        user = user_store.get_by_stanza(iq)
        if user is None:
            raise XMPPError("subscription-required")
        session = self.session_cls.from_stanza(iq)

        form = self["xep_0004"].make_form("form", "Active telegram sessions")
        tg_sessions = (await session.tg.api.get_active_sessions()).sessions
        form.add_field(
            "tg_session_id",
            ftype="list-single",
            label="Sessions",
            options=[{"label": f"{s.country}", "value": s.id} for s in tg_sessions],
        )

        adhoc_session["payload"] = form
        adhoc_session["next"] = self.adhoc_active_sessions2
        adhoc_session["has_next"] = True
        adhoc_session["tg_sessions"] = {s.id: s for s in tg_sessions}
        adhoc_session["slidge_session"] = session

        return adhoc_session

    async def adhoc_active_sessions2(self, form, adhoc_session: dict[str, typing.Any]):
        tg_session_id = int(form.get_values()["tg_session_id"])

        form = self["xep_0004"].make_form("form", "Telegram session info")
        tg_session: tgapi.Session = adhoc_session["tg_sessions"][str(tg_session_id)]
        for x in fmt_tg_session(tg_session):
            form.add_field(
                ftype="fixed",
                value=x,
            )
        if tg_session.is_current:
            adhoc_session["has_next"] = False
        else:
            form.add_field(
                "terminate", ftype="boolean", label="Terminate session", value="0"
            )
            form.add_field("tg_session_id", ftype="hidden", value=tg_session.id)
            adhoc_session["has_next"] = True
            adhoc_session["next"] = self.adhoc_active_sessions3

        adhoc_session["payload"] = form

        return adhoc_session

    async def adhoc_active_sessions3(self, form, adhoc_session: dict[str, typing.Any]):
        form_values = form.get_values()
        terminate = bool(int(form_values["terminate"]))

        if terminate:
            session: Session = adhoc_session["slidge_session"]
            await session.tg.api.terminate_session(int(form_values["tg_session_id"]))
            info = "Session terminated."
        else:
            info = "Session not terminated."

        adhoc_session["notes"] = [("info", info)]
        adhoc_session["has_next"] = False

        return adhoc_session


def fmt_tg_session(s: tgapi.Session):
    return [
        f"Country: {s.country}",
        f"Region: {s.region}",
        f"Ip: {s.ip}",
        f"App: {s.application_name}",
        f"Device: {s.device_model}",
        f"Platform: {s.platform}",
        f"Since: {datetime.fromtimestamp(s.log_in_date).isoformat()}",
        f"Last seen: {datetime.fromtimestamp(s.last_active_date).isoformat()}",
    ]


log = logging.getLogger(__name__)
