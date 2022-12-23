import asyncio
import shelve
from logging import getLogger
from pathlib import Path
from typing import Coroutine

from slixmpp import JID

from slidge import BaseGateway, FormField, GatewayUser, global_config
from slidge.plugins.whatsapp.generated import whatsapp

from ...core.adhoc import RegistrationType
from .config import Config
from .util import make_sync

REGISTRATION_INSTRUCTIONS = (
    "Continue and scan the resulting QR codes on your main device to complete registration. "
    "More information at https://slidge.readthedocs.io/en/latest/user/plugins/whatsapp.html"
)

WELCOME_MESSAGE = (
    "Thank you for registering! Please scan the following QR code on your main device to complete "
    "registration, or type 'help' to list other available commands."
)


class Gateway(BaseGateway):
    COMPONENT_NAME = "WhatsApp (slidge)"
    COMPONENT_TYPE = "whatsapp"
    COMPONENT_AVATAR = "https://www.whatsapp.com/apple-touch-icon.png"
    REGISTRATION_INSTRUCTIONS = REGISTRATION_INSTRUCTIONS
    REGISTRATION_TYPE = RegistrationType.QRCODE
    WELCOME_MESSAGE = WELCOME_MESSAGE
    REGISTRATION_FIELDS = []
    ROSTER_GROUP = "WhatsApp"
    SEARCH_FIELDS = [FormField(var="phone", label="Phone", required=True)]

    def __init__(self):
        super().__init__()
        Path(Config.DB_PATH.parent).mkdir(exist_ok=True)
        self.whatsapp = whatsapp.NewGateway()
        self.whatsapp.SetLogHandler(handle_log)
        self.whatsapp.DBPath = str(Config.DB_PATH)
        self.whatsapp.SkipVerifyTLS = Config.SKIP_VERIFY_TLS
        self.whatsapp.Name = "Slidge on " + str(global_config.JID)
        self.whatsapp.Init()
        self._pending_qrs = dict[str, asyncio.Future[str]]()
        self._sessions = dict[str, whatsapp.Session]()
        self._event_handlers = dict[str, Coroutine]()

    async def validate(self, user_jid: JID, registration_form):
        log.debug("device")
        device = whatsapp.LinkedDevice()
        log.debug("session")
        w = self._sessions[user_jid.bare] = whatsapp.Session(device)
        self._pending_qrs[user_jid.bare] = self.loop.create_future()

        async def handle_event(event, ptr):
            log.debug("EVENT: %s, %s", event, ptr)
            data = whatsapp.EventPayload(handle=ptr)
            if event == whatsapp.EventQRCode:
                self._pending_qrs[user_jid.bare].set_result(data.QRCode)
            elif event == whatsapp.EventPairSuccess:
                self.user_shelf_path = ()
                with shelve.open(
                    str(
                        global_config.HOME_DIR / "whatsapp" / (user_jid.bare + ".shelf")
                    )
                ) as shelf:
                    shelf["device_id"] = data.PairDeviceID
                await self.confirm_qr(user_jid.bare)

        log.debug("make sync")
        self._event_handlers[user_jid.bare] = make_sync(handle_event, self.loop)
        log.debug("handler")
        # FIXME:
        # panic: interface conversion: interface {} is nil, not *whatsapp.Session
        #
        # goroutine 17 [running, locked to thread]:
        # main.whatsapp_Session_SetEventHandler(0xc0000280c0?, 0x7fa33ddf9d30, 0x0)
        #   /venv/lib/python3.9/site-packages/slidge/plugins/whatsapp/generated/whatsapp.go:1897 +0x2c5
        w.SetEventHandler(self._event_handlers[user_jid.bare])
        log.debug("done")

    async def get_qr_text(self, user: GatewayUser):
        return await self._pending_qrs.pop(user.bare_jid)

    async def unregister(self, user: GatewayUser):
        self.whatsapp.DestroySession(
            whatsapp.LinkedDevice(ID=user.registration_form.get("device_id", ""))
        )


def handle_log(level, msg: str):
    """
    Log given message of specified level in system-wide logger.
    """
    if level == whatsapp.LevelError:
        log.error(msg)
    elif level == whatsapp.LevelWarning:
        log.warning(msg)
    elif level == whatsapp.LevelDebug:
        log.debug(msg)
    else:
        log.info(msg)


log = getLogger(__name__)
