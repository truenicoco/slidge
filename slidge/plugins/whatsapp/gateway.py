from pathlib import Path
from logging import getLogger

from slidge import BaseGateway, GatewayUser, global_config, user_store
from slidge.plugins.whatsapp.generated import whatsapp
from .config import Config


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
    COMPONENT_AVATAR = "https://www.whatsapp.com/apple-touch-icon..png"
    REGISTRATION_INSTRUCTIONS = REGISTRATION_INSTRUCTIONS
    WELCOME_MESSAGE = WELCOME_MESSAGE
    REGISTRATION_FIELDS = []
    ROSTER_GROUP = "WhatsApp"

    def __init__(self):
        super().__init__()
        self.use_origin_id = True
        Path(Config.DB_PATH.parent).mkdir(exist_ok=True)
        self.whatsapp = whatsapp.NewGateway()
        self.whatsapp.SetLogHandler(handle_log)
        self.whatsapp.DBPath = str(Config.DB_PATH)
        self.whatsapp.SkipVerifyTLS = Config.SKIP_VERIFY_TLS
        self.whatsapp.Name = "Slidge on " + str(global_config.JID)
        self.whatsapp.Init()

    def shutdown(self):
        for user in user_store.get_all():
            session = self.session_cls.from_jid(user.jid)
            for c in session.contacts:
                c.offline()
            self.loop.create_task(session.disconnect())
            self.send_presence(ptype="unavailable", pto=user.jid)

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
