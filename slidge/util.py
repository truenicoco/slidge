import dataclasses
from typing import Literal, Optional


def get_unique_subclass(cls):
    classes = cls.__subclasses__()
    if len(classes) == 0:
        return cls
    elif len(classes) == 1:
        return classes[0]
    elif len(classes) > 1:
        raise RuntimeError(
            "This class should only be subclassed once by plugin!", cls, classes
        )


@dataclasses.dataclass
class RegistrationField:
    """
    Represents a field of the form that a user will see when registering to the gateway
    via their XMPP client.
    """

    name: str
    """
    Internal name of the field, will be used to retrieve via :py:attr:`slidge.GatewayUser.registration_form`
    """
    label: Optional[str] = None
    """Description of the field that the aspiring user will see"""
    required: bool = True
    """Whether this field is mandatory or not"""
    private: bool = False
    """For sensitive info that should not be displayed on screen while the user types."""
    type: Literal["boolean", "fixed", "text-single"] = "text-single"
    """Type of the field, see `XEP-0004 <https://xmpp.org/extensions/xep-0004.html#protocol-fieldtypes>`_"""
    value: str = ""
    """Pre-filled value. Will be automatically pre-filled if a registered user modifies their subscription"""
