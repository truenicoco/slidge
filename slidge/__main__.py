"""
Slidge can be configured via CLI args, environment variables and/or INI files.
To use env vars, use this convention: ``--home-dir`` becomes ``HOME_DIR``.
"""
import importlib
import logging
from pathlib import Path

import configargparse

from slidge import BaseGateway
from .db import user_store


# noinspection PyUnresolvedReferences
def get_parser():
    p = configargparse.ArgParser(
        default_config_files=["/etc/slidge/conf.d/*.conf"], description=__doc__
    )
    p.add(
        "-c",
        "--config",
        help="Path to a INI config file.",
        env_var="SLIDGE_CONFIG",
        is_config_file=True,
    )
    p.add(
        "--legacy-module",
        help="Importable python module containing (at least) "
        "a BaseGateway and a LegacySession subclass",
        env_var="SLIDGE_LEGACY_MODULE",
    )
    p.add(
        "-s",
        "--server",
        env_var="SLIDGE_SERVER",
        required=True,
        help="The XMPP server's host name.",
    )
    p.add(
        "-p",
        "--port",
        default="5347",
        env_var="SLIDGE_PORT",
        help="The XMPP server's port for incoming component connections",
    )
    p.add(
        "--secret",
        required=True,
        env_var="SLIDGE_SECRET",
        help="The gateway component's secret (required to connect to the XMPP server)",
    )
    p.add(
        "-j",
        "--jid",
        required=True,
        env_var="SLIDGE_JID",
        help="The gateway component's JID",
    )
    p.add(
        "--upload-service",
        env_var="SLIDGE_UPLOAD",
        help="JID of an HTTP upload service the gateway can use. "
        "This is optional, as it should be automatically determined via service discovery",
    )
    p.add(
        "--home-dir",
        env_var="SLIDGE_HOME_DIR",
        help="Shelve file used to store persistent user data. "
        "Defaults to /var/lib/slidge/${SLIDGE_JID}",
    )
    p.add(
        "--admins",
        env_var="SLIDGE_ADMINS",
        nargs="*",
        help="JIDs of the gateway admins",
    )
    p.add(
        "--user-jid-validator",
        env_var="SLIDGE_RESTRICT",
        help="Regular expression to restrict user that can register to the gateway by JID. "
        "Defaults to .*@${SLIDGE_SERVER}, forbids the gateway to JIDs "
        "not using the same XMPP server as the gateway",
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

    return p


def main():
    args, argv = get_parser().parse_known_args()
    logging.basicConfig(level=args.loglevel)

    if args.home_dir is None:
        args.home_dir = Path("/var/lib/slidge") / args.jid
        if not args.home_dir.exists():
            logging.info("Making directory '%s'", args.home_dir)
            args.home_dir.mkdir()

    if args.user_jid_validator is None:
        args.user_jid_validator = ".*@" + args.server

    db_file = Path(args.home_dir) / "slidge.db"
    user_store.set_file(db_file)

    importlib.import_module(args.legacy_module)
    gateway = BaseGateway.get_unique_subclass()(args)
    gateway.config(argv)
    gateway.connect()

    try:  # TODO: handle reconnection
        gateway.process()
    except KeyboardInterrupt:
        logging.debug("Received SIGINT")
    except Exception as e:
        logging.exception(e)
    finally:
        gateway.shutdown()
        gateway.disconnect()
        gateway.process(forever=False)
        logging.info("Successful clean shut down")


if __name__ == "__main__":
    main()
