import logging
from argparse import Namespace

from slixmpp import JID

from slidge import *

from .config import get_parser

REGISTRATION_INSTRUCTIONS = """You can visit https://my.telegram.org/apps to get an API ID and an API HASH

This is the only tested login method, but other methods (password, bot token, 2FA...)
should work too, in theory at least.
"""


class Gateway(BaseGateway):
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

    async def validate(self, user_jid: JID, registration_form: dict[str, str]):
        pass

    async def unregister(self, user):
        pass


log = logging.getLogger(__name__)
