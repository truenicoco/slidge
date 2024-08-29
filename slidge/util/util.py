import logging
import mimetypes
import re
import subprocess
import warnings
from abc import ABCMeta
from functools import wraps
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Callable, NamedTuple, Optional, Type, TypeVar

try:
    import emoji
except ImportError:
    EMOJI_LIB_AVAILABLE = False
else:
    EMOJI_LIB_AVAILABLE = True

from .types import Mention, ResourceDict

if TYPE_CHECKING:
    from ..contact.contact import LegacyContact

try:
    import magic
except ImportError as e:
    magic = None  # type:ignore
    logging.warning(
        (
            "Libmagic is not available: %s. "
            "It's OK if you don't use fix-filename-suffix-mime-type."
        ),
        e,
    )


def fix_suffix(path: Path, mime_type: Optional[str], file_name: Optional[str]):
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

    valid_suffix = mimetypes.guess_extension(mime_type.split(";")[0], strict=False)
    if valid_suffix is None:
        log.debug("No valid suffix found")
        return name

    log.debug("Changing suffix of %s to %s", file_name or path.name, valid_suffix)
    return name.with_suffix(valid_suffix)


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


def strip_illegal_chars(s: str):
    return ILLEGAL_XML_CHARS_RE.sub("", s)


# from https://stackoverflow.com/a/64570125/5902284 and Link Mauve
ILLEGAL = [
    (0x00, 0x08),
    (0x0B, 0x0C),
    (0x0E, 0x1F),
    (0x7F, 0x84),
    (0x86, 0x9F),
    (0xFDD0, 0xFDDF),
    (0xFFFE, 0xFFFF),
    (0x1FFFE, 0x1FFFF),
    (0x2FFFE, 0x2FFFF),
    (0x3FFFE, 0x3FFFF),
    (0x4FFFE, 0x4FFFF),
    (0x5FFFE, 0x5FFFF),
    (0x6FFFE, 0x6FFFF),
    (0x7FFFE, 0x7FFFF),
    (0x8FFFE, 0x8FFFF),
    (0x9FFFE, 0x9FFFF),
    (0xAFFFE, 0xAFFFF),
    (0xBFFFE, 0xBFFFF),
    (0xCFFFE, 0xCFFFF),
    (0xDFFFE, 0xDFFFF),
    (0xEFFFE, 0xEFFFF),
    (0xFFFFE, 0xFFFFF),
    (0x10FFFE, 0x10FFFF),
]

ILLEGAL_RANGES = [rf"{chr(low)}-{chr(high)}" for (low, high) in ILLEGAL]
XML_ILLEGAL_CHARACTER_REGEX = "[" + "".join(ILLEGAL_RANGES) + "]"
ILLEGAL_XML_CHARS_RE = re.compile(XML_ILLEGAL_CHARACTER_REGEX)


# from https://stackoverflow.com/a/35804945/5902284
def addLoggingLevel(
    levelName: str = "TRACE", levelNum: int = logging.DEBUG - 5, methodName=None
):
    """
    Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

    To avoid accidental clobberings of existing attributes, this method will
    raise an `AttributeError` if the level name is already an attribute of the
    `logging` module or if the method name is already present

    Example
    -------
    >>> addLoggingLevel('TRACE', logging.DEBUG - 5)
    >>> logging.getLogger(__name__).setLevel("TRACE")
    >>> logging.getLogger(__name__).trace('that worked')
    >>> logging.trace('so did this')
    >>> logging.TRACE
    5

    """
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
        log.debug("{} already defined in logging module".format(levelName))
        return
    if hasattr(logging, methodName):
        log.debug("{} already defined in logging module".format(methodName))
        return
    if hasattr(logging.getLoggerClass(), methodName):
        log.debug("{} already defined in logger class".format(methodName))
        return

    # This method was inspired by the answers to Stack Overflow post
    # http://stackoverflow.com/q/2183233/2988730, especially
    # http://stackoverflow.com/a/13638084/2988730
    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)

    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)


class SlidgeLogger(logging.Logger):
    def trace(self):
        pass


log = logging.getLogger(__name__)


def get_version():
    try:
        git = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode()
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    else:
        return "git-" + git[:10]

    return "NO_VERSION"


def merge_resources(resources: dict[str, ResourceDict]) -> Optional[ResourceDict]:
    if len(resources) == 0:
        return None

    if len(resources) == 1:
        return next(iter(resources.values()))

    by_priority = sorted(resources.values(), key=lambda r: r["priority"], reverse=True)

    if any(r["show"] == "" for r in resources.values()):
        # if a client is "available", we're "available"
        show = ""
    else:
        for r in by_priority:
            if r["show"]:
                show = r["show"]
                break
        else:
            raise RuntimeError()

    # if there are different statuses, we use the highest priority one,
    # but we ignore resources without status, even with high priority
    status = ""
    for r in by_priority:
        if r["status"]:
            status = r["status"]
            break

    return {
        "show": show,  # type:ignore
        "status": status,
        "priority": 0,
    }


def remove_emoji_variation_selector_16(emoji: str):
    # this is required for compatibility with dino, and maybe other future clients?
    return bytes(emoji, encoding="utf-8").replace(b"\xef\xb8\x8f", b"").decode()


def deprecated(name: str, new: Callable):
    # @functools.wraps
    def wrapped(*args, **kwargs):
        warnings.warn(
            f"{name} is deprecated. Use {new.__name__} instead",
            category=DeprecationWarning,
        )
        return new(*args, **kwargs)

    return wrapped


T = TypeVar("T", bound=NamedTuple)


def dict_to_named_tuple(data: dict, cls: Type[T]) -> T:
    return cls(*(data.get(f) for f in cls._fields))  # type:ignore


def replace_mentions(
    text: str,
    mentions: Optional[list[Mention]],
    mapping: Callable[["LegacyContact"], str],
):
    if not mentions:
        return text

    cursor = 0
    pieces = []
    for mention in mentions:
        pieces.extend([text[cursor : mention.start], mapping(mention.contact)])
        cursor = mention.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def with_session(func):
    @wraps(func)
    async def wrapped(self, *args, **kwargs):
        with self.xmpp.store.session():
            return await func(self, *args, **kwargs)

    return wrapped


def timeit(func):
    @wraps(func)
    async def wrapped(self, *args, **kwargs):
        start = time()
        r = await func(self, *args, **kwargs)
        self.log.info("%s took %s ms", func.__name__, round((time() - start) * 1000))
        return r

    return wrapped


def strip_leading_emoji(text: str) -> str:
    if not EMOJI_LIB_AVAILABLE:
        return text
    words = text.split(" ")
    # is_emoji returns False for 🛷️ for obscure reasons,
    # purely_emoji seems better
    if len(words) > 1 and emoji.purely_emoji(words[0]):
        return " ".join(words[1:])
    return text
