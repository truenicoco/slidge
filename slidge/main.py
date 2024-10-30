"""
Slidge can be configured via CLI args, environment variables and/or INI files.

To use env vars, use this convention: ``--home-dir`` becomes ``HOME_DIR``.

Everything in ``/etc/slidge/conf.d/*`` is automatically used.
To use a plugin-specific INI file, put it in another dir,
and launch slidge with ``-c /path/to/plugin-specific.conf``.
Use the long version of the CLI arg without the double dash prefix inside this
INI file, eg ``debug=true``.

An example configuration file is available at
https://git.sr.ht/~nicoco/slidge/tree/master/item/dev/confs/slidge-example.ini
"""

import asyncio
import importlib
import inspect
import logging
import os
import re
import signal
from pathlib import Path

import configargparse

from slidge import BaseGateway
from slidge.__version__ import __version__
from slidge.core import config
from slidge.core.pubsub import PepAvatar, PepNick
from slidge.db import SlidgeStore
from slidge.db.avatar import avatar_cache
from slidge.db.meta import get_engine
from slidge.migration import migrate
from slidge.util.conf import ConfigModule


class MainConfig(ConfigModule):
    def update_dynamic_defaults(self, args):
        # force=True is needed in case we call a logger before this is reached,
        # or basicConfig has no effect
        logging.basicConfig(
            level=args.loglevel,
            filename=args.log_file,
            force=True,
            format=args.log_format,
        )

        if args.home_dir is None:
            args.home_dir = Path("/var/lib/slidge") / str(args.jid)

        if args.user_jid_validator is None:
            args.user_jid_validator = ".*@" + re.escape(args.server)

        if args.db_url is None:
            args.db_url = f"sqlite:///{args.home_dir}/slidge.sqlite"


class SigTermInterrupt(Exception):
    pass


def get_configurator():
    p = configargparse.ArgumentParser(
        default_config_files=os.getenv(
            "SLIDGE_CONF_DIR", "/etc/slidge/conf.d/*.conf"
        ).split(":"),
        description=__doc__,
    )
    p.add_argument(
        "-c",
        "--config",
        help="Path to a INI config file.",
        env_var="SLIDGE_CONFIG",
        is_config_file=True,
    )
    p.add_argument(
        "-q",
        "--quiet",
        help="loglevel=WARNING",
        action="store_const",
        dest="loglevel",
        const=logging.WARNING,
        default=logging.INFO,
        env_var="SLIDGE_QUIET",
    )
    p.add_argument(
        "-d",
        "--debug",
        help="loglevel=DEBUG",
        action="store_const",
        dest="loglevel",
        const=logging.DEBUG,
        env_var="SLIDGE_DEBUG",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    configurator = MainConfig(config, p)
    return configurator


def get_parser():
    return get_configurator().parser


def configure():
    configurator = get_configurator()
    args, unknown_argv = configurator.set_conf()

    if not (h := config.HOME_DIR).exists():
        logging.info("Creating directory '%s'", h)
        os.makedirs(h)

    config.UPLOAD_REQUESTER = config.UPLOAD_REQUESTER or config.JID.bare

    return unknown_argv


def handle_sigterm(_signum, _frame):
    logging.info("Caught SIGTERM")
    raise SigTermInterrupt


def main():
    signal.signal(signal.SIGTERM, handle_sigterm)

    unknown_argv = configure()
    logging.info("Starting slidge version %s", __version__)

    legacy_module = importlib.import_module(config.LEGACY_MODULE)
    logging.debug("Legacy module: %s", dir(legacy_module))
    logging.info(
        "Starting legacy module: '%s' version %s",
        config.LEGACY_MODULE,
        getattr(legacy_module, "__version__", "No version"),
    )

    if plugin_config_obj := getattr(
        legacy_module, "config", getattr(legacy_module, "Config", None)
    ):
        # If the legacy module has default parameters that depend on dynamic defaults
        # of the slidge main config, it needs to be refreshed at this point, because
        # now the dynamic defaults are set.
        if inspect.ismodule(plugin_config_obj):
            importlib.reload(plugin_config_obj)
        logging.debug("Found a config object in plugin: %r", plugin_config_obj)
        ConfigModule.ENV_VAR_PREFIX += (
            f"_{config.LEGACY_MODULE.split('.')[-1].upper()}_"
        )
        logging.debug("Env var prefix: %s", ConfigModule.ENV_VAR_PREFIX)
        ConfigModule(plugin_config_obj).set_conf(unknown_argv)
    else:
        if unknown_argv:
            raise RuntimeError("Some arguments have not been recognized", unknown_argv)

    migrate()

    store = SlidgeStore(get_engine(config.DB_URL))
    BaseGateway.store = store
    gateway: BaseGateway = BaseGateway.get_unique_subclass()()
    avatar_cache.store = gateway.store.avatars
    avatar_cache.set_dir(config.HOME_DIR / "slidge_avatars_v3")

    PepAvatar.store = gateway.store
    PepNick.contact_store = gateway.store.contacts

    gateway.connect()

    return_code = 0
    try:
        gateway.loop.run_forever()
    except KeyboardInterrupt:
        logging.debug("Received SIGINT")
    except SigTermInterrupt:
        logging.debug("Received SIGTERM")
    except SystemExit as e:
        return_code = e.code  # type: ignore
        logging.debug("Exit called")
    except Exception as e:
        return_code = 2
        logging.exception("Exception in __main__")
        logging.exception(e)
    finally:
        if gateway.has_crashed:
            if return_code != 0:
                logging.warning("Return code has been set twice. Please report this.")
            return_code = 3
        if gateway.is_connected():
            logging.debug("Gateway is connected, cleaning up")
            gateway.loop.run_until_complete(asyncio.gather(*gateway.shutdown()))
            gateway.disconnect()
            gateway.loop.run_until_complete(gateway.disconnected)
        else:
            logging.debug("Gateway is not connected, no need to clean up")
        avatar_cache.close()
        gateway.loop.run_until_complete(gateway.http.close())
        logging.info("Successful clean shut down")
    logging.debug("Exiting with code %s", return_code)
    exit(return_code)
