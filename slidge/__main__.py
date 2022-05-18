"""
Slidge can be configured via CLI args, environment variables and/or INI files.
To use env vars, use this convention: ``--server`` becomes ``SLIDGE_SERVER``.
"""
import importlib
import logging

import configargparse

from .db import user_store


# noinspection PyUnresolvedReferences
def get_parser():
    p = configargparse.ArgParser(
        default_config_files=["/etc/slidge/conf.d/*.conf"], description=__doc__
    )
    p.add(
        "--legacy-module",
        help="Importable python module containing (at least) Gateway and LegacyClient",
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
        "--db",
        default="/var/lib/slidge/slidge.db",
        env_var="SLIDGE_DB",
        help="Shelve file used to store persistent user data.",
    )
    return p


def main():
    args, argv = get_parser().parse_known_args()
    logging.basicConfig(level=logging.DEBUG)

    user_store.set_file(args.db)

    module = importlib.import_module(args.legacy_module)

    gateway = module.Gateway(args.jid, args.secret, args.server, args.port)
    client = module.LegacyClient(gateway)
    if len(argv) != 0:
        client.config(argv)
    gateway.connect()

    try:  # TODO: handle reconnection
        gateway.process()
    except (KeyboardInterrupt, Exception) as e:
        gateway.disconnect()


if __name__ == "__main__":
    main()
