"""
Slidge can be configured via CLI args, environment variables and/or INI files.
To use env vars, use this convention: ``--home-dir`` becomes ``HOME_DIR``.
"""
import importlib
import logging
import signal
from pathlib import Path

import configargparse

from slidge import BaseGateway
from slidge.core import config
from slidge.core.cache import avatar_cache
from slidge.util.conf import ConfigModule
from slidge.util.db import user_store


class MainConfig(ConfigModule):
    def update_dynamic_defaults(self, args):
        logging.basicConfig(level=args.loglevel)

        if args.home_dir is None:
            args.home_dir = Path("/var/lib/slidge") / str(args.jid)

        if args.user_jid_validator is None:
            args.user_jid_validator = ".*@" + args.server


class SigTermInterrupt(Exception):
    pass


def get_configurator():
    p = configargparse.ArgumentParser(
        default_config_files=["/etc/slidge/conf.d/*.conf"], description=__doc__
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
    configurator = MainConfig(config, p)
    return configurator


def get_parser():
    return get_configurator().parser


def configure():
    configurator = get_configurator()
    args, unknown_argv = configurator.set_conf()

    if not (h := config.HOME_DIR).exists():
        logging.info("Creating directory '%s'", h)
        h.mkdir()

    db_file = config.HOME_DIR / "slidge.db"
    user_store.set_file(db_file, args.secret_key)

    avatar_cache.set_dir(h / "slidge_avatars")

    config.UPLOAD_REQUESTER = config.UPLOAD_REQUESTER or config.JID.bare

    return unknown_argv


def handle_sigterm(_signum, _frame):
    logging.info("Caught SIGTERM")
    raise SigTermInterrupt


def main():
    signal.signal(signal.SIGTERM, handle_sigterm)

    unknown_argv = configure()

    legacy_module = importlib.import_module(config.LEGACY_MODULE)

    if plugin_config_obj := getattr(
        legacy_module, "config", getattr(legacy_module, "Config", None)
    ):
        logging.debug("Found a config object in plugin: %r", plugin_config_obj)
        ConfigModule.ENV_VAR_PREFIX += (
            f"_{config.LEGACY_MODULE.split('.')[-1].upper()}_"
        )
        logging.debug("Env var prefix: %s", ConfigModule.ENV_VAR_PREFIX)
        ConfigModule(plugin_config_obj).set_conf(unknown_argv)
    else:
        if unknown_argv:
            raise RuntimeError("Some arguments have not been recognized", unknown_argv)

    gateway = BaseGateway.get_unique_subclass()()
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
            gateway.shutdown()
            gateway.disconnect()
            gateway.loop.run_until_complete(gateway.disconnected)
        else:
            logging.debug("Gateway is not connected, no need to clean up")
        user_store.close()
        avatar_cache.close()
        logging.info("Successful clean shut down")
    logging.debug("Exiting with code %s", return_code)
    exit(return_code)


if __name__ == "__main__":
    main()
