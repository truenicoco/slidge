from argparse import ArgumentParser


def get_parser():
    parser = ArgumentParser()
    parser.add_argument("--tdlib-path", help="Defaults to ${SLIDGE_HOME_DIR}/tdlib")
    parser.add_argument(
        "--tdlib-key",
        default="NOT_SECURE",
        help="Key used to encrypt tdlib persistent DB",
    )
    return parser
