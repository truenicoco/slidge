from .config import get_parser

try:
    from .gateway import Gateway, Session, Roster, Contact
except ImportError:
    pass
