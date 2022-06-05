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
        "--legacy-module",
        help="Importable python module containing (at least) "
        "a BaseGateway and a LegacySession subclass",
        env_var="SLIDGE_LEGACY_MODULE",
    )
    p.add("-c", "--configuration", help="Path to a INI file", env_var="SLIDGE_CONFIG")
    p.add(
        "-s",
        "--server",
        env_var="SLIDGE_SERVER",
        default="localhost",
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
        default="secret",
        env_var="SLIDGE_SECRET",
        help="The gateway component's secret (required to connect to the XMPP server)",
    )
    p.add(
        "-j",
        "--jid",
        default="slidge.localhost",
        env_var="SLIDGE_JID",
        help="The gateway component's JID",
    )
    p.add(
        "--upload-service",
        env_var="SLIDGE_UPLOAD",
        help="JID of an HTTP upload service the gateway can use. "
        "Defaults to 'upload.${SLIDGE_SERVER}'",
    )
    p.add(
        "--home-dir",
        env_var="SLIDGE_HOME_DIR",
        help="Shelve file used to store persistent user data. "
        "Defaults to /var/lib/slidge/${SLIDGE_JID}",
    )
    p.add_argument(
        "-q",
        "--quiet",
        help="loglevel=WARNING",
        action="store_const",
        dest="loglevel",
        const=logging.WARNING,
        default=logging.INFO,
    )
    p.add_argument(
        "-d",
        "--debug",
        help="loglevel=DEBUG",
        action="store_const",
        dest="loglevel",
        const=logging.DEBUG,
    )

    return p


def main():
    args, argv = get_parser().parse_known_args()
    logging.basicConfig(level=args.loglevel)

    if args.upload_service is None:
        args.upload_service = "upload." + args.server

    if args.home_dir is None:
        args.home_dir = Path("/var/lib/slidge") / args.jid
        if not args.home_dir.exists():
            logging.info("Making directory '%s'", args.home_dir)
            args.home_dir.mkdir()

    db_file = Path(args.home_dir) / "slidge.db"
    user_store.set_file(db_file)

    module = importlib.import_module(args.legacy_module)
    gateway: BaseGateway = module.Gateway(args)
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
