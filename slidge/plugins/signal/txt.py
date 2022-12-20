from slidge import *

REGISTRATION_INSTRUCTIONS = (
    "Fill the form to register a new signal account. "
    "If you want to link slidge to an existing signal account, use 'link' (ad-hoc or chat command).\n"
    "You may need to complete a captcha first, cf https://signald.org/articles/captcha/#getting-a-token "
)

REGISTRATION_FIELDS = [
    FormField(var="phone", label="Phone number (ex: +123456789)", required=True),
    FormField(var="name", label="Your name (or nickname)", required=True),
    FormField(var="captcha", label="Captcha token"),
]
