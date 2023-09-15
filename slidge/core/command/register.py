"""
The register commands, either by chat or adhoc command

Note that single step forms via jabber:iq:register are handled in ``xep_0077``
"""

import asyncio
import functools
import tempfile
from datetime import datetime
from enum import Enum
from typing import Any

import qrcode
from slixmpp import JID, Iq
from slixmpp.exceptions import XMPPError

from ...util.db import GatewayUser
from .. import config
from .base import Command, CommandAccess, Form, FormField, FormValues


class RegistrationType(int, Enum):
    """
    The type of registration for a gateway
    """

    SINGLE_STEP_FORM = 0
    """
    Blabla
    """
    QRCODE = 10
    TWO_FACTOR_CODE = 20


class TwoFactorNotRequired(Exception):
    """
    Should be raised by two-factor code validation function in case the
    code is not required after all.
    """

    pass


class Register(Command):
    NAME = "Register to the gateway"
    HELP = "Link your JID to this gateway"
    NODE = "jabber:iq:register"
    CHAT_COMMAND = "register"
    ACCESS = CommandAccess.NON_USER

    SUCCESS_MESSAGE = "Success, welcome!"

    def _finalize(self, user: GatewayUser):
        user.commit()
        self.xmpp.event("user_register", Iq(sfrom=user.jid))
        return self.SUCCESS_MESSAGE

    async def run(self, _session, _ifrom, *_):
        return Form(
            title=f"Registration to '{self.xmpp.COMPONENT_NAME}'",
            instructions=self.xmpp.REGISTRATION_INSTRUCTIONS,
            fields=self.xmpp.REGISTRATION_FIELDS,
            handler=self.register,
        )

    async def register(self, form_values: dict[str, Any], _session, ifrom: JID):
        two_fa_needed = True
        try:
            await self.xmpp.user_prevalidate(ifrom, form_values)
        except ValueError as e:
            raise XMPPError("bad-request", str(e))
        except TwoFactorNotRequired:
            if self.xmpp.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE:
                two_fa_needed = False
            else:
                raise

        user = GatewayUser(
            bare_jid=ifrom.bare,
            registration_form=form_values,
            registration_date=datetime.now(),
        )

        if self.xmpp.REGISTRATION_TYPE == RegistrationType.SINGLE_STEP_FORM or (
            self.xmpp.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE
            and not two_fa_needed
        ):
            return self._finalize(user)

        if self.xmpp.REGISTRATION_TYPE == RegistrationType.TWO_FACTOR_CODE:
            return Form(
                title=self.xmpp.REGISTRATION_2FA_TITLE,
                instructions=self.xmpp.REGISTRATION_2FA_INSTRUCTIONS,
                fields=[FormField("code", label="Code", required=True)],
                handler=functools.partial(self.two_fa, user=user),
            )

        elif self.xmpp.REGISTRATION_TYPE == RegistrationType.QRCODE:
            self.xmpp.qr_pending_registrations[  # type:ignore
                user.bare_jid
            ] = self.xmpp.loop.create_future()
            qr_text = await self.xmpp.get_qr_text(user)
            qr = qrcode.make(qr_text)
            with tempfile.NamedTemporaryFile(
                suffix=".png", delete=config.NO_UPLOAD_METHOD != "move"
            ) as f:
                qr.save(f.name)
                img_url, _ = await self.xmpp.send_file(f.name, mto=ifrom)
            if img_url is None:
                raise XMPPError(
                    "internal-server-error", "Slidge cannot send attachments"
                )
            self.xmpp.send_text(qr_text, mto=ifrom)
            return Form(
                title="Flash this",
                instructions="Flash this QR in the appropriate place",
                fields=[
                    FormField(
                        "qr_img",
                        type="fixed",
                        value=qr_text,
                        image_url=img_url,
                    ),
                    FormField(
                        "qr_text",
                        type="fixed",
                        value=qr_text,
                        label="Text encoded in the QR code",
                    ),
                    FormField(
                        "qr_img_url",
                        type="fixed",
                        value=img_url,
                        label="URL of the QR code image",
                    ),
                ],
                handler=functools.partial(self.qr, user=user),
            )

    async def two_fa(
        self, form_values: FormValues, _session, _ifrom, user: GatewayUser
    ):
        assert isinstance(form_values["code"], str)
        await self.xmpp.validate_two_factor_code(user, form_values["code"])
        return self._finalize(user)

    async def qr(self, _form_values: FormValues, _session, _ifrom, user: GatewayUser):
        try:
            await asyncio.wait_for(
                self.xmpp.qr_pending_registrations[user.bare_jid],  # type:ignore
                config.QR_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise XMPPError(
                "remote-server-timeout",
                (
                    "It does not seem that the QR code was correctly used, "
                    "or you took too much time"
                ),
            )
        return self._finalize(user)
