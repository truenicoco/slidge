"""
A helper to get the standard argparse.ArgumentParser instances from our custom
config system, for plugins, so they can be used to generate the docs.
"""

import importlib
from pathlib import Path

from slidge.util.conf import ConfigModule
from slidge.util.test import reset_subclasses
from slidge.core import config

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
    return _parser("whatsapp")
