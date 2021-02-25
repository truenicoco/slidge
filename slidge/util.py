import xml.dom.minidom


def escape(identifier: str) -> str:
    """
    Escape a string to comply with XEP-0106.

    :param identifier: The string to escape
    :returns: The escaped string
    """
    for k, v in ESCAPE_RULES.items():
        identifier = identifier.replace(k, v)
    return identifier.lower()


ESCAPE_RULES = {
    " ": r"\20",
    '"': r"\22",
    "&": r"\26",
    "'": r"\27",
    "/": r"\2f",
    ":": r"\3a",
    "<": r"\3c",
    ">": r"\3e",
    "@": r"\40",
    "\\": r"\5c",
}


def pprint(stanza):
    print(
        xml.dom.minidom.parseString(str(stanza))
        .toprettyxml()
        .replace('<?xml version="1.0" ?>', "")
    )
