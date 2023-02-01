import dataclasses
import logging
import mimetypes
import re
from abc import ABCMeta
from pathlib import Path
from typing import Collection, Generic, Optional, TypeVar

try:
    import magic
except ImportError as e:
    magic = None  # type:ignore

from .types import FieldType


def fix_suffix(path: Path, mime_type: Optional[str], file_name: Optional[str]):
    if magic is None:
        log.warning("libmagic is not usable: %s", e)

    guessed = magic.from_file(path, mime=True)
    if guessed == mime_type:
        log.debug("Magic and given MIME match")
    else:
        log.debug("Magic (%s) and given MIME (%s) differ", guessed, mime_type)
        mime_type = guessed

    valid_suffix_list = mimetypes.guess_all_extensions(mime_type, strict=False)

    if file_name:
        name = Path(file_name)
    else:
        name = Path(path.name)

    suffix = name.suffix

    if suffix in valid_suffix_list:
        log.debug("Suffix %s is in %s", suffix, valid_suffix_list)
        return name

    valid_suffix = mimetypes.guess_extension(mime_type, strict=False)
    if valid_suffix is None:
        log.debug("No valid suffix found")
        return name

    log.debug("Changing suffix of %s to %s", file_name or path.name, valid_suffix)
    return name.with_suffix(valid_suffix)


@dataclasses.dataclass
class FormField:
    """
    Represents a field of the form that a user will see when registering to the gateway
    via their XMPP client.
    """

    var: str = ""
    """
    Internal name of the field, will be used to retrieve via :py:attr:`slidge.GatewayUser.registration_form`
    """
    label: Optional[str] = None
    """Description of the field that the aspiring user will see"""
    required: bool = False
    """Whether this field is mandatory or not"""
    private: bool = False
    """
    For sensitive info that should not be displayed on screen while the user types.
    Forces field_type to "text-private"
    """
    type: FieldType = "text-single"
    """Type of the field, see `XEP-0004 <https://xmpp.org/extensions/xep-0004.html#protocol-fieldtypes>`_"""
    value: str = ""
    """Pre-filled value. Will be automatically pre-filled if a registered user modifies their subscription"""
    options: Optional[list[dict[str, str]]] = None

    def dict(self):
        return dataclasses.asdict(self)

    def __post_init__(self):
        if self.private:
            self.type = "text-private"


KeyType = TypeVar("KeyType")
ValueType = TypeVar("ValueType")


class BiDict(Generic[KeyType, ValueType], dict[KeyType, ValueType]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inverse: dict[ValueType, KeyType] = {}
        for key, value in self.items():
            self.inverse[value] = key

    def __setitem__(self, key: KeyType, value: ValueType):
        if key in self:
            del self.inverse[self[key]]
        super().__setitem__(key, value)
        self.inverse[value] = key


@dataclasses.dataclass
class SearchResult:
    fields: Collection[FormField]
    items: Collection[dict[str, str]]


class SubclassableOnce(type):
    TEST_MODE = False  # To allow importing everything, including plugins, during tests

    def __init__(cls, name, bases, dct):
        for b in bases:
            if type(b) in (SubclassableOnce, ABCSubclassableOnceAtMost):
                if hasattr(b, "_subclass") and not cls.TEST_MODE:
                    raise RuntimeError(
                        "This class must be subclassed once at most!",
                        cls,
                        name,
                        bases,
                        dct,
                    )
                else:
                    log.debug("Setting %s as subclass for %s", cls, b)
                    b._subclass = cls

        super().__init__(name, bases, dct)

    def get_self_or_unique_subclass(cls):
        try:
            return cls.get_unique_subclass()
        except AttributeError:
            return cls

    def get_unique_subclass(cls):
        r = getattr(cls, "_subclass", None)
        if r is None:
            raise AttributeError("Could not find any subclass", cls)
        return r

    def reset_subclass(cls):
        try:
            log.debug("Resetting subclass of %s", cls)
            delattr(cls, "_subclass")
        except AttributeError:
            log.debug("No subclass were registered for %s", cls)


class ABCSubclassableOnceAtMost(ABCMeta, SubclassableOnce):
    pass


def is_valid_phone_number(phone: Optional[str]):
    if phone is None:
        return False
    match = re.match(r"\+\d.*", phone)
    if match is None:
        return False
    return match[0] == phone


log = logging.getLogger(__name__)
