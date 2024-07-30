"""
The gateway
"""

from typing import Optional

from slixmpp import JID

from slidge import BaseGateway, FormField, GatewayUser
from slidge.command.register import RegistrationType

from .legacy_client import SuperDuperClient
from .util import ASSETS_DIR


class Gateway(BaseGateway):
    """
    This is instantiated once by the slidge entrypoint.

    By customizing the class attributes, we customize the registration process,
    and display name of the component.
    """

    COMPONENT_NAME = "The great legacy network (slidge)"
    COMPONENT_AVATAR = ASSETS_DIR / "slidge-color.png"
    COMPONENT_TYPE = "whatsapp"
    REGISTRATION_INSTRUCTIONS = (
        "Register to this fake service by using 'slidger' as username, and any "
        "password you want. Then you will need to enter '666' as the 2FA code."
    )
    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE
    REGISTRATION_FIELDS = [
        FormField(var="username", label="User name", required=True),
        FormField(var="password", label="Password", required=True, private=True),
    ]
    GROUPS = True
    MARK_ALL_MESSAGES = True

    LEGACY_CONTACT_ID_TYPE = int

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        """
        This function receives the values of the form defined in
        :attr:`REGISTRATION_FIELDS`. Here, since we set
        :attr:`REGISTRATION_TYPE` to "2FA", if this method does not raise any
        exception, the wannabe user will be prompted for their 2FA code.

        :param user_jid:
        :param registration_form:
        :return:
        """
        await SuperDuperClient.send_2fa(
            registration_form["username"],
            registration_form["password"],
        )

    async def validate_two_factor_code(self, user: GatewayUser, code: str):
        """
        This function receives the 2FA code entered by the aspiring user.

        It should raise something if the 2FA does not permit logging in to the
        legacy service.

        :param user:
        :param code:
        """
        await SuperDuperClient.validate_2fa(
            user.legacy_module_data["username"],
            user.legacy_module_data["password"],
            code,
        )
