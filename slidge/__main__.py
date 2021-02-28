"""
"""

import asyncio
import time
import logging
import importlib
from argparse import ArgumentParser
from configparser import ConfigParser

from slidge.plugins import xep_0100


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("config")
    parser.add_argument(
        "--quiet",
        "-q",
        help="Override log config settings and set loglevel to warning",
        action="store_true",
        default=False,
    )
    args = parser.parse_args()

    config_path = args.config
    config = ConfigParser()
    config.read(config_path)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else config["logging"]["level"].upper(),
        format="%(asctime)s:%(levelname)s:%(name)s:%(message)s",
    )

    log = logging.getLogger(__name__)

    legacy_module = importlib.import_module(config["legacy"].get("module"))
    component_class = getattr(legacy_module, "Gateway")
    client_class = getattr(legacy_module, "Client")

    gateway = component_class(config, client_class)
    gateway.connect()

    try:  # TODO: handle reconnection
        gateway.process()
    except (KeyboardInterrupt, Exception) as e:
        log.info(f"The gateway stopped because of {e}, trying to cleanly shut down")
        asyncio.get_event_loop().run_until_complete(gateway.shutdown())
        gateway.disconnect()
        gateway.process(forever=False)
