import emoji as emoji_lib


def _emoji_name_conversion(x: str):
    return x.replace("_3_", "_three_").replace("thumbsup", "+1")


def emojize(x: str):
    return emoji_lib.emojize(f":{_emoji_name_conversion(x)}:", language="alias")


def demojize(emoji_char: str):
    return _emoji_name_conversion(
        emoji_lib.demojize(emoji_char, delimiters=("", ""), language="alias")
    )
