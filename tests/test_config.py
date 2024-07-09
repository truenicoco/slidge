import logging
from pathlib import Path
from typing import Optional

import pytest
from slixmpp import JID

from slidge import main
from slidge.core import config
from slidge.util.conf import ConfigModule


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

        SOME_BOOL = False
        SOME_BOOL__DOC = "a bool"

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
            "--some-bool",
        ]
    )
    assert args.required == "some_value"
    assert args.required_int == 45
    assert args.multiple == ["a", "b"]
    assert args.optional == "prout"
    assert args.some_bool


def test_bool(tmpdir, tmp_path):
    class Config:
        SOME_BOOL = False
        SOME_BOOL__DOC = "a bool"

        TRUE = True
        TRUE__DOC = "true by default"

    configurator = ConfigModule(Config)

    configurator.set_conf([])
    assert not Config.SOME_BOOL
    assert Config.TRUE

    configurator.set_conf(["--some-bool"])
    assert Config.SOME_BOOL
    assert Config.TRUE

    configurator.set_conf(["--true"])
    assert not Config.SOME_BOOL
    assert Config.TRUE

    configurator.set_conf(["--true=false"])
    assert not Config.SOME_BOOL
    assert not Config.TRUE

    configurator.set_conf(["--true=true"])
    assert not Config.SOME_BOOL
    assert Config.TRUE

    configurator.set_conf(["--some-bool=true"])
    assert Config.SOME_BOOL
    assert Config.TRUE

    configurator.set_conf(["--some-bool=false"])
    assert not Config.SOME_BOOL
    assert Config.TRUE

    # for the plugin-specific conf files, we use the rest
    configurator.parser.add_argument("-c", is_config_file=True)

    class Config2:
        SOME_OTHER_BOOL = False
        SOME_OTHER_BOOL__DOC = "a bool"

        TRUE2 = True
        TRUE2__DOC = "true by default"

    configurator2 = ConfigModule(Config2)
    conf_file = tmpdir / "conf.conf"

    # false
    conf_file.write_text("some-other-bool=false", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert rest
    configurator2.set_conf(rest)
    assert not Config2.SOME_OTHER_BOOL
    assert Config2.TRUE2

    # true
    conf_file.write_text("some-other-bool=true", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert rest
    configurator2.set_conf(rest)
    assert Config2.SOME_OTHER_BOOL
    assert Config2.TRUE2

    # true
    conf_file.write_text("", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert not rest
    configurator2.set_conf(rest)
    assert not Config2.SOME_OTHER_BOOL
    assert Config2.TRUE2

    conf_file.write_text("true2=true", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert rest
    configurator2.set_conf(rest)
    assert not Config2.SOME_OTHER_BOOL
    assert Config2.TRUE2

    conf_file.write_text("true2=false", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert rest
    configurator2.set_conf(rest)
    assert not Config2.SOME_OTHER_BOOL
    assert not Config2.TRUE2


def test_true_by_default_file(tmpdir, tmp_path):
    conf_file = tmpdir / "conf.conf"

    class Config:
        TRUE = True
        TRUE__DOC = "true by default"

    configurator = ConfigModule(Config)
    configurator.parser.add_argument("-c", is_config_file=True)

    conf_file.write_text("", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert not rest
    assert Config.TRUE

    # TODO: fix these cases!
    # conf_file.write_text("true=true", "utf-8")
    # args, rest = configurator.set_conf(["-c", str(conf_file)])
    # assert not rest
    # assert Config.TRUE

    # conf_file.write_text("true=false", "utf-8")
    # args, rest = configurator.set_conf(["-c", str(conf_file)])
    # assert not rest
    # assert not Config.TRUE


def test_false_by_default_file(tmpdir, tmp_path):
    conf_file = tmpdir / "conf.conf"

    class Config:
        FALSE = False
        FALSE__DOC = "true by default"

    configurator = ConfigModule(Config)
    configurator.parser.add_argument("-c", is_config_file=True)

    conf_file.write_text("", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert not rest
    assert not Config.FALSE

    conf_file.write_text("false=true", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert not rest
    assert Config.FALSE

    conf_file.write_text("false=false", "utf-8")
    args, rest = configurator.set_conf(["-c", str(conf_file)])
    assert not rest
    assert not Config.FALSE


def test_slidge_conf():
    args, rest = main.get_parser().parse_known_args(
        [
            "-c",
            str(Path(__file__).parent.parent / "dev" / "confs" / "slidge-example.ini"),
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


def test_set_conf(monkeypatch):
    monkeypatch.setenv("SLIDGE_USER_JID_VALIDATOR", "cloup")
    argv = [
        "-c",
        str(Path(__file__).parent.parent / "dev" / "confs" / "slidge-example.ini"),
        "--legacy-module=slidge.plugins.dummy",
        "--jid=test.localhost",
        "--ignore-delay-threshold=200",
    ]
    main.get_configurator().set_conf(argv)
    assert config.SERVER == "localhost"
    assert config.ADMINS == ["test@localhost"]
    assert isinstance(config.ADMINS[0], JID)
    assert isinstance(config.JID, JID)
    assert config.SECRET == "secret"
    assert config.IGNORE_DELAY_THRESHOLD.seconds == 200
    assert config.USER_JID_VALIDATOR == "cloup"


def test_rest(tmp_path):
    class Config1:
        PROUT = "caca"
        PROUT__DOC = "?"

    class Config2:
        PROUT2: Optional[str] = None
        PROUT2__DOC = "?"

    configurator = ConfigModule(Config1)
    configurator.parser.add_argument("-c", is_config_file=True)
    conf_path = tmp_path / "test.conf"
    conf_path.write_text("prout2=something")
    args, rest = configurator.set_conf(["-c", str(conf_path)])

    configurator2 = ConfigModule(Config2)
    configurator2.set_conf(rest)

    assert Config1.PROUT == "caca"
    assert Config2.PROUT2 == "something"
