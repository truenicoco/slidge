CHATS_TO_FETCH = 20
CHATS_TO_FETCH__DOC = (
    "The number of most recent chats to fetch on startup. "
    "Getting all chats might hit rate limiting and possibly account lock. "
    "Please report if you try with high values and don't hit any problem!"
)

ENABLE_PRESENCES = False
ENABLE_PRESENCES__DOC = (
    "Toggle this to enable subscribing to presences of your contacts. "
    "Off by default because it possibly triggers suspicious activity."
)
