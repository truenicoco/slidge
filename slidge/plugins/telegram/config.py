import string
from argparse import ArgumentParser
import random


def get_parser():
    parser = ArgumentParser()
    parser.add_argument("--tdlib-path", help="Defaults to ${SLIDGE_HOME_DIR}/tdlib")
    parser.add_argument(
        "--tdlib-key",
        default="".join(
            string.ascii_letters[random.randrange(0, len(string.ascii_letters))]
            for _ in range(30)
        ),
        help="Key used to encrypt tdlib persistent DB. Random string by default.",
    )
    return parser
