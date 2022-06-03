import dataclasses
from typing import Literal, Optional, Iterable, Dict


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
class FormField:
    """
    Represents a field of the form that a user will see when registering to the gateway
    via their XMPP client.
    """

    var: str
    """
    Internal name of the field, will be used to retrieve via :py:attr:`slidge.GatewayUser.registration_form`
    """
    label: Optional[str] = None
    """Description of the field that the aspiring user will see"""
    required: bool = False
    """Whether this field is mandatory or not"""
    private: bool = False
    """For sensitive info that should not be displayed on screen while the user types."""
    type: Literal["boolean", "fixed", "text-single", "jid-single"] = "text-single"
    """Type of the field, see `XEP-0004 <https://xmpp.org/extensions/xep-0004.html#protocol-fieldtypes>`_"""
    value: str = ""
    """Pre-filled value. Will be automatically pre-filled if a registered user modifies their subscription"""

    def dict(self):
        return dataclasses.asdict(self)


class BiDict(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inverse = {}
        for key, value in self.items():
            self.inverse[value] = key

    def __setitem__(self, key, value):
        if key in self:
            self.inverse[self[key]].remove(key)
        super().__setitem__(key, value)
        self.inverse[value] = key


@dataclasses.dataclass
class SearchResult:
    fields: Iterable[FormField]
    items: Iterable[Dict[str, str]]
