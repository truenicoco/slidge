from pathlib import Path

SIGNALD_SOCKET = Path("/signald/signald.sock")
SIGNALD_SOCKET__DOC = "Path to the signald socket"

PREFER_PROFILE_NAME = False
PREFER_PROFILE_NAME__DOC = (
    "Signal contacts have both a profile name that they choose, and a local contact"
    " name that you set for them. Set this to true to favor the profile name they"
    " choose for their XMPP puppets. Useful if you have wrong contact names possibly"
    " caused by a signald bug."
)
