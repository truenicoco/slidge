from slixmpp.stanza import Message
from slixmpp.xmlstream import ElementBase, register_stanza_plugin

NS = "urn:xmpp:reply:0"


class Reply(ElementBase):
    namespace = NS
    name = "reply"
    plugin_attrib = "reply"
    interfaces = {"id", "to"}


class FeatureFallBack(ElementBase):
    # should also be a multi attrib
    namespace = "urn:xmpp:fallback:0"
    name = "fallback"
    plugin_attrib = "feature_fallback"
    interfaces = {"for"}

    def get_stripped_body(self):
        # only works for a single fallback_body attrib
        start = self["fallback_body"]["start"]
        end = self["fallback_body"]["end"]
        body = self.parent()["body"]
        if start <= end < len(body):
            return body[:start] + body[end:]
        else:
            return body

    def get_fallback_body(self):
        # only works for a single fallback_body attrib
        start = self["fallback_body"]["start"]
        end = self["fallback_body"]["end"]
        body = self.parent()["body"]
        if start <= end:
            return body[start:end]
        else:
            return ""

    def add_quoted_fallback(self, fallback: str):
        msg = self.parent()
        quoted = "\n".join("> " + x.strip() for x in fallback.split("\n")) + "\n"
        msg["body"] = quoted + msg["body"]
        msg["feature_fallback"]["for"] = NS
        msg["feature_fallback"]["fallback_body"]["start"] = 0
        msg["feature_fallback"]["fallback_body"]["end"] = len(quoted)


class FallBackBody(ElementBase):
    # According to https://xmpp.org/extensions/inbox/compatibility-fallback.html
    # this should be a multi_attrib *but* since it's a protoXEP, we'll see...
    namespace = FeatureFallBack.namespace
    name = "body"
    plugin_attrib = "fallback_body"
    interfaces = {"start", "end"}

    def set_start(self, v: int):
        self._set_attr("start", str(v))

    def get_start(self):
        try:
            return int(self._get_attr("start"))
        except ValueError:
            return 0

    def set_end(self, v: int):
        self._set_attr("end", str(v))

    def get_end(self):
        try:
            return int(self._get_attr("end"))
        except ValueError:
            return 0


def register_plugins():
    register_stanza_plugin(Message, Reply)
    register_stanza_plugin(Message, FeatureFallBack)
    register_stanza_plugin(FeatureFallBack, FallBackBody)
