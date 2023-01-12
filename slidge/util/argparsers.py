"""
A helper to get the standard argparse.ArgumentParser instances from our custom
config system, for plugins, so they can be used to generate the docs.
"""

import importlib
from pathlib import Path

from slidge.core import config
from slidge.util.conf import ConfigModule
from slidge.util.test import reset_subclasses  # type:ignore

# required for plugins that use it in their config obj
config.HOME_DIR = Path("/var/lib/slidge/${SLIDGE_JID}/")


def _parser(plugin: str):
    config = importlib.import_module(f"slidge.plugins.{plugin}")
    try:
        parser = ConfigModule(config.config).parser
    except AttributeError:
        parser = ConfigModule(config.Config).parser
    reset_subclasses()
    return parser


def signal():
    return _parser("signal")


def telegram():
    return _parser("telegram")


def facebook():
    return _parser("facebook")


def discord():
    return _parser("discord")


def whatsapp():
    # create empty python modules in case the whatsapp gopy files have not
    # been generated, (eg, on readthedocs)
    generated_dir = Path("..") / "slidge" / "plugins" / "whatsapp" / "generated"
    if not generated_dir.exists():
        generated_dir.mkdir()
        for f in "whatsapp", "go":
            path = (generated_dir / f).with_suffix(".py")
            path.write_text("def __getattr__(name): return")
    return _parser("whatsapp")
