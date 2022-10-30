from slidge import global_config


class Config:
    """
    Config contains plugin-specific configuration for WhatsApp, and is loaded automatically by the
    core configuration framework.
    """

    DB_PATH = global_config.HOME_DIR / "whatsapp" / "whatsapp.db"
    DB_PATH__DOC = "The path to the database used for the WhatsApp plugin."

    ALWAYS_SYNC_ROSTER = True
    ALWAYS_SYNC_ROSTER__DOC = (
        "Whether or not to perform a full sync of the WhatsApp roster on startup."
    )

    SKIP_VERIFY_TLS = False
    SKIP_VERIFY_TLS__DOC = "Whether or not HTTPS connections made by this plugin should verify TLS certificates."
