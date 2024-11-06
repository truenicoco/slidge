from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Iterable,
    Optional,
    Sequence,
    Type,
    TypedDict,
    Union,
)

from slixmpp import JID  # type: ignore[attr-defined]
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0004 import Form as SlixForm  # type: ignore[attr-defined]
from slixmpp.plugins.xep_0004 import (
    FormField as SlixFormField,  # type: ignore[attr-defined]
)
from slixmpp.types import JidStr

from ..core import config
from ..util.types import AnyBaseSession, FieldType

NODE_PREFIX = "https://slidge.im/command/core/"

if TYPE_CHECKING:
    from ..core.gateway import BaseGateway
    from ..core.session import BaseSession
    from .categories import CommandCategory


HandlerType = Union[
    Callable[[AnyBaseSession, JID], "CommandResponseType"],
    Callable[[AnyBaseSession, JID], Awaitable["CommandResponseType"]],
]

FormValues = dict[str, Union[str, JID, bool]]


FormHandlerType = Callable[
    [FormValues, AnyBaseSession, JID],
    Awaitable["CommandResponseType"],
]

ConfirmationHandlerType = Callable[
    [Optional[AnyBaseSession], JID], Awaitable["CommandResponseType"]
]


@dataclass
class TableResult:
    """
    Structured data as the result of a command
    """

    fields: Sequence["FormField"]
    """
    The 'columns names' of the table.
    """
    items: Sequence[dict[str, Union[str, JID]]]
    """
    The rows of the table. Each row is a dict where keys are the fields ``var``
    attribute.
    """
    description: str
    """
    A description of the content of the table.
    """

    jids_are_mucs: bool = False

    def get_xml(self) -> SlixForm:
        """
        Get a slixmpp "form" (with <reported> header)to represent the data

        :return: some XML
        """
        form = SlixForm()  # type: ignore[no-untyped-call]
        form["type"] = "result"
        form["title"] = self.description
        for f in self.fields:
            form.add_reported(f.var, label=f.label, type=f.type)  # type: ignore[no-untyped-call]
        for item in self.items:
            form.add_item({k: str(v) for k, v in item.items()})  # type: ignore[no-untyped-call]
        return form


@dataclass
class SearchResult(TableResult):
    """
    Results of the search command (search for contacts via Jabber Search)

    Return type of :meth:`BaseSession.search`.
    """

    description: str = "Contact search results"


@dataclass
class Confirmation:
    """
    A confirmation 'dialog'
    """

    prompt: str
    """
    The text presented to the command triggering user
    """
    handler: ConfirmationHandlerType
    """
    An async function that should return a ResponseType
    """
    success: Optional[str] = None
    """
    Text in case of success, used if handler does not return anything
    """
    handler_args: Iterable[Any] = field(default_factory=list)
    """
    arguments passed to the handler
    """
    handler_kwargs: dict[str, Any] = field(default_factory=dict)
    """
    keyword arguments passed to the handler
    """

    def get_form(self) -> SlixForm:
        """
        Get the slixmpp form

        :return: some xml
        """
        form = SlixForm()  # type: ignore[no-untyped-call]
        form["type"] = "form"
        form["title"] = self.prompt
        form.append(
            FormField(
                "confirm", type="boolean", value="true", label="Confirm"
            ).get_xml()
        )
        return form


@dataclass
class Form:
    """
    A form, to request user input
    """

    title: str
    instructions: str
    fields: Sequence["FormField"]
    handler: FormHandlerType
    handler_args: Iterable[Any] = field(default_factory=list)
    handler_kwargs: dict[str, Any] = field(default_factory=dict)

    def get_values(
        self, slix_form: SlixForm
    ) -> dict[str, Union[list[str], list[JID], str, JID, bool, None]]:
        """
        Parse form submission

        :param slix_form: the xml received as the submission of a form
        :return: A dict where keys=field.var and values are either strings
            or JIDs (if field.type=jid-single)
        """
        str_values: dict[str, str] = slix_form.get_values()  # type: ignore[no-untyped-call]
        values = {}
        for f in self.fields:
            values[f.var] = f.validate(str_values.get(f.var))
        return values

    def get_xml(self) -> SlixForm:
        """
        Get the slixmpp "form"

        :return: some XML
        """
        form = SlixForm()  # type: ignore[no-untyped-call]
        form["type"] = "form"
        form["title"] = self.title
        form["instructions"] = self.instructions
        for fi in self.fields:
            form.append(fi.get_xml())
        return form


class CommandAccess(int, Enum):
    """
    Defines who can access a given Command
    """

    ADMIN_ONLY = 0
    USER = 1
    USER_LOGGED = 2
    USER_NON_LOGGED = 3
    NON_USER = 4
    ANY = 5


class Option(TypedDict):
    """
    Options to be used for ``FormField``s of type ``list-*``
    """

    label: str
    value: str


# TODO: support forms validation XEP-0122
@dataclass
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
    """Description of the field that the user will see"""
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
    options: Optional[list[Option]] = None

    image_url: Optional[str] = None
    """An image associated to this field, eg, a QR code"""

    def __post_init__(self) -> None:
        if self.private:
            self.type = "text-private"

    def __acceptable_options(self) -> list[str]:
        if self.options is None:
            raise RuntimeError
        return [x["value"] for x in self.options]

    def validate(
        self, value: Optional[Union[str, list[str]]]
    ) -> Union[list[str], list[JID], str, JID, bool, None]:
        """
        Raise appropriate XMPPError if a given value is valid for this field

        :param value: The value to test
        :return: The same value OR a JID if ``self.type=jid-single``
        """
        if isinstance(value, list) and not self.type.endswith("multi"):
            raise XMPPError("not-acceptable", "A single value was expected")

        if self.type in ("list-multi", "jid-multi"):
            if not value:
                value = []
            if isinstance(value, list):
                return self.__validate_list_multi(value)
            else:
                raise XMPPError("not-acceptable", "Multiple values was expected")

        assert isinstance(value, (str, bool, JID)) or value is None

        if self.required and value is None:
            raise XMPPError("not-acceptable", f"Missing field: '{self.label}'")

        if value is None:
            return None

        if self.type == "jid-single":
            try:
                return JID(value)
            except ValueError:
                raise XMPPError("not-acceptable", f"Not a valid JID: '{value}'")

        elif self.type == "list-single":
            if value not in self.__acceptable_options():
                raise XMPPError("not-acceptable", f"Not a valid option: '{value}'")

        elif self.type == "boolean":
            return value.lower() in ("1", "true") if isinstance(value, str) else value

        return value

    def __validate_list_multi(self, value: list[str]) -> Union[list[str], list[JID]]:
        for v in value:
            if v not in self.__acceptable_options():
                raise XMPPError("not-acceptable", f"Not a valid option: '{v}'")
        if self.type == "list-multi":
            return value
        return [JID(v) for v in value]

    def get_xml(self) -> SlixFormField:
        """
        Get the field in slixmpp format

        :return: some XML
        """
        f = SlixFormField()
        f["var"] = self.var
        f["label"] = self.label
        f["required"] = self.required
        f["type"] = self.type
        if self.options:
            for o in self.options:
                f.add_option(**o)  # type: ignore[no-untyped-call]
        f["value"] = self.value
        if self.image_url:
            f["media"].add_uri(self.image_url, itype="image/png")
        return f


CommandResponseType = Union[TableResult, Confirmation, Form, str, None]


class Command(ABC):
    """
    Abstract base class to implement gateway commands (chatbot and ad-hoc)
    """

    NAME: str = NotImplemented
    """
    Friendly name of the command, eg: "do something with stuff"
    """
    HELP: str = NotImplemented
    """
    Long description of what the command does
    """
    NODE: str = NotImplemented
    """
    Name of the node used for ad-hoc commands
    """
    CHAT_COMMAND: str = NotImplemented
    """
    Text to send to the gateway to trigger the command via a message
    """

    ACCESS: "CommandAccess" = NotImplemented
    """
    Who can use this command
    """

    CATEGORY: Optional[Union[str, "CommandCategory"]] = None
    """
    If used, the command will be under this top-level category.
    Use the same string for several commands to group them.
    This hierarchy only used for the adhoc interface, not the chat command
    interface.
    """

    subclasses = list[Type["Command"]]()

    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # store subclasses so subclassing is enough for the command to be
        # picked up by slidge
        cls.subclasses.append(cls)

    async def run(
        self, session: Optional["BaseSession[Any, Any]"], ifrom: JID, *args: str
    ) -> CommandResponseType:
        """
        Entry point of the command

        :param session: If triggered by a registered user, its slidge Session
        :param ifrom: JID of the command-triggering entity
        :param args: When triggered via chatbot type message, additional words
            after the CHAT_COMMAND string was passed

        :return: Either a TableResult, a Form, a Confirmation, a text, or None
        """
        raise XMPPError("feature-not-implemented")

    def _get_session(self, jid: JID) -> Optional["BaseSession[Any, Any]"]:
        user = self.xmpp.store.users.get(jid)
        if user is None:
            return None

        return self.xmpp.get_session_from_user(user)

    def __can_use_command(self, jid: JID):
        j = jid.bare
        return self.xmpp.jid_validator.match(j) or j in config.ADMINS

    def raise_if_not_authorized(self, jid: JID) -> Optional["BaseSession[Any, Any]"]:
        """
        Raise an appropriate error is jid is not authorized to use the command

        :param jid: jid of the entity trying to access the command
        :return:session of JID if it exists
        """
        session = self._get_session(jid)
        if not self.__can_use_command(jid):
            raise XMPPError(
                "bad-request", "Your JID is not allowed to use this gateway."
            )

        if self.ACCESS == CommandAccess.ADMIN_ONLY and not is_admin(jid):
            raise XMPPError("not-authorized")
        elif self.ACCESS == CommandAccess.NON_USER and session is not None:
            raise XMPPError(
                "bad-request", "This is only available for non-users. Unregister first."
            )
        elif self.ACCESS == CommandAccess.USER and session is None:
            raise XMPPError(
                "forbidden",
                "This is only available for users that are registered to this gateway",
            )
        elif self.ACCESS == CommandAccess.USER_NON_LOGGED:
            if session is None or session.logged:
                raise XMPPError(
                    "forbidden",
                    (
                        "This is only available for users that are not logged to the"
                        " legacy service"
                    ),
                )
        elif self.ACCESS == CommandAccess.USER_LOGGED:
            if session is None or not session.logged:
                raise XMPPError(
                    "forbidden",
                    (
                        "This is only available when you are logged in to the legacy"
                        " service"
                    ),
                )
        return session


def is_admin(jid: JidStr) -> bool:
    return JID(jid).bare in config.ADMINS
