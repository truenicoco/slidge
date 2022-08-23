import logging
import typing
from argparse import Namespace
from datetime import datetime

import aiotdlib.api as tgapi
from slixmpp import JID, Iq
from slixmpp.exceptions import XMPPError

from slidge import *

from .config import get_parser

if typing.TYPE_CHECKING:
    from .session import Session

REGISTRATION_INSTRUCTIONS = """You can visit https://my.telegram.org/apps to get an API ID and an API HASH

This is the only tested login method, but other methods (password, bot token, 2FA...)
should work too, in theory at least.
"""


class Gateway(BaseGateway["Session"]):
    REGISTRATION_INSTRUCTIONS = REGISTRATION_INSTRUCTIONS
    REGISTRATION_FIELDS = [
        FormField(var="phone", label="Phone number", required=True),
        FormField(var="api_id", label="API ID", required=False),
        FormField(var="api_hash", label="API hash", required=False),
        FormField(var="", value="The fields below have not been tested", type="fixed"),
        FormField(var="bot_token", label="Bot token", required=False),
        FormField(var="first", label="First name", required=False),
        FormField(var="last", label="Last name", required=False),
    ]
    ROSTER_GROUP = "Telegram"
    COMPONENT_NAME = "Telegram (slidge)"
    COMPONENT_TYPE = "telegram"
    COMPONENT_AVATAR = "https://web.telegram.org/img/logo_share.png"

    SEARCH_FIELDS = [
        FormField(var="phone", label="Phone number", required=True),
    ]

    args: Namespace

    def config(self, argv: list[str]):
        Gateway.args = args = get_parser().parse_args(argv)
        if args.tdlib_path is None:
            args.tdlib_path = self.home_dir / "tdlib"

    async def validate(
        self, user_jid: JID, registration_form: dict[str, typing.Optional[str]]
    ):
        pass

    async def unregister(self, user):
        pass

    def add_adhoc_commands(self):
        self["xep_0050"].add_command(
            node="get_sessions",
            name="List active sessions",
            handler=self.adhoc_active_sessions1,
        )

    async def adhoc_active_sessions1(
        self, iq: Iq, adhoc_session: dict[str, typing.Any]
    ):
        user = user_store.get_by_stanza(iq)
        if user is None:
            raise XMPPError("subscription-required")
        session = self._session_cls.from_stanza(iq)

        form = self["xep_0004"].make_form("form", "Active telegram sessions")
        tg_sessions = (await session.list_sessions()).sessions
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
            await session.terminate_session(int(form_values["tg_session_id"]))
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
