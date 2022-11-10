import logging
from pathlib import Path
from typing import Optional

import pytest
from slixmpp import JID

from slidge import __main__ as main
from slidge.core import config


def test_get_parser(monkeypatch):
    class Config:
        REQUIRED: str
        REQUIRED__DOC = "some doc"
        REQUIRED__SHORT = "r"

        REQUIRED_INT: int
        REQUIRED_INT__DOC = "some doc"

        MULTIPLE: tuple[str, ...] = ()
        MULTIPLE__DOC = "some more doc"

        OPTIONAL: Optional[str] = None
        OPTIONAL__DOC = "not required"

    monkeypatch.setattr(main, "config", Config)
    parser = main.get_parser()
    with pytest.raises(SystemExit) as e:
        parser.parse_known_args([])
    assert e.value.args[0] == 2  # Exit code 2

    args = parser.parse_args(["--required", "some_value", "--required-int", "45"])
    assert args.required == "some_value"
    assert args.required_int == 45

    args = parser.parse_args(["-r", "some_value", "--required-int", "45"])
    assert args.required == "some_value"
    assert args.required_int == 45
    assert args.multiple == tuple()

    args = parser.parse_args(
        ["-r", "some_value", "--required-int", "45", "--multiple", "a", "b"]
    )
    assert args.required == "some_value"
    assert args.required_int == 45
    assert args.multiple == ["a", "b"]
    assert args.optional is None

    args = parser.parse_args(
        [
            "-r",
            "some_value",
            "--required-int",
            "45",
            "--multiple",
            "a",
            "b",
            "--optional",
            "prout",
        ]
    )
    assert args.required == "some_value"
    assert args.required_int == 45
    assert args.multiple == ["a", "b"]
    assert args.optional == "prout"


def test_slidge_conf():
    args, rest = main.get_parser().parse_known_args(
        [
            "-c",
            str(Path(__file__).parent.parent / "confs" / "slidge-example.ini"),
            "--legacy-module=slidge.plugins.dummy",
            "--jid=test.localhost",
            "--some-other",
        ]
    )
    assert args.server == "localhost"
    assert args.admins == ["test@localhost"]
    assert args.secret == "secret"
    assert args.loglevel == logging.DEBUG
    assert len(rest) == 1
    assert rest[0] == "--some-other"


def test_set_conf():
    args = main.get_parser().parse_args(
        [
            "-c",
            str(Path(__file__).parent.parent / "confs" / "slidge-example.ini"),
            "--legacy-module=slidge.plugins.dummy",
            "--jid=test.localhost",
            "--ignore-delay-threshold=200",
        ]
    )
    main.get_configurator().set_conf(args)
    assert config.SERVER == "localhost"
    assert config.ADMINS == ["test@localhost"]
    assert isinstance(config.ADMINS[0], JID)
    assert isinstance(config.JID, JID)
    assert config.SECRET == "secret"
    assert config.IGNORE_DELAY_THRESHOLD.seconds == 200
