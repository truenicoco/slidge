from logging import getLogger
from pathlib import Path
from shelve import open

from slidge import BaseGateway, GatewayUser, global_config
from slidge.plugins.whatsapp.generated import whatsapp

from . import config

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
    WELCOME_MESSAGE = WELCOME_MESSAGE
    REGISTRATION_FIELDS = []
    ROSTER_GROUP = "WhatsApp"
    MARK_ALL_MESSAGES = True

    def __init__(self):
        super().__init__()
        Path(config.DB_PATH.parent).mkdir(exist_ok=True)
        self.whatsapp = whatsapp.NewGateway()
        self.whatsapp.SetLogHandler(handle_log)
        self.whatsapp.DBPath = str(config.DB_PATH)
        self.whatsapp.SkipVerifyTLS = config.SKIP_VERIFY_TLS
        self.whatsapp.Name = "Slidge on " + str(global_config.JID)
        self.whatsapp.Init()

    async def unregister(self, user: GatewayUser):
        user_shelf_path = (
            global_config.HOME_DIR / "whatsapp" / (user.bare_jid + ".shelf")
        )
        with open(str(user_shelf_path)) as shelf:
            try:
                device = whatsapp.LinkedDevice(ID=shelf["device_id"])
                self.whatsapp.CleanupSession(device)
            except KeyError:
                pass
            except RuntimeError as err:
                log.error("Failed to clean up WhatsApp session: %s", err)


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
