import aiotdlib.api as tgapi


def get_best_file(content: tgapi.MessageContent):
    if isinstance(content, tgapi.MessagePhoto):
        photo = content.photo
        return max(photo.sizes, key=lambda x: x.width).photo
    elif isinstance(content, tgapi.MessageVideo):
        return content.video.video
    elif isinstance(content, tgapi.MessageAnimation):
        return content.animation.animation
    elif isinstance(content, tgapi.MessageAudio):
        return content.audio.audio
