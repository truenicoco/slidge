from slidge import *

REGISTRATION_INSTRUCTIONS = (
    "Fill the form to use your XMPP account as a signal client. "
    "More information at https://slidge.readthedocs.io/en/latest/user/signal.html"
)
REGISTRATION_FIELDS = [
    FormField(var="phone", label="Phone number (ex: +123456789)", required=True),
    FormField(
        var="device",
        type="list-single",
        label="What do you want to do?",
        options=[
            {"label": "Create a new signal account", "value": "primary"},
            {"label": "Link to an existing signal account", "value": "secondary"},
        ],
        required=True,
    ),
    FormField(
        var="name",
        label="Your name (only used if you chose to create a new signal account; "
        "doesn't have to be your real name)",
    ),
    FormField(
        var="device_name",
        label="Device name (only used if you chose to link to an existing signal account)",
        value="slidge",
    ),
]

CAPTCHA_REQUIRED = (
    "Signal requires you to complete a captcha to register a new account. "
    "Please follow the instructions at https://signald.org/articles/captcha/#getting-a-token and "
    "reply to this message with the token (signalcaptcha://XXXXXXXXX)."
)

NAME_REQUIRED = (
    "If you want to register a new signal account, you must enter a name. "
    "Nothing forces you to use your real name."
)

LINK_TIMEOUT = (
    "You took too much time… "
    "Reply to this message with 'link' once you're ready or 'cancel' "
    "to remove your gateway registration"
)

LINK_SUCCESS = (
    "It looks like everything's all set up. You should now send and "
    "receive signal messages via XMPP."
)

REGISTER_SUCCESS = "This XMPP bridge is now your 'primary' signal device. Congrats!"
