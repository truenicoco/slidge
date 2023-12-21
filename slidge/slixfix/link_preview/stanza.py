# Slixmpp: The Slick XMPP Library
# Copyright (C) 2012 Nathanael C. Fritz, Lance J.T. Stout
# This file is part of Slixmpp.
# See the file LICENSE for copying permission.
from typing import Optional, Type

from slixmpp.stanza.message import Message
from slixmpp.xmlstream import ElementBase, register_stanza_plugin


class LinkPreview(ElementBase):
    name = "Description"
    namespace = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    plugin_attrib = "link_preview"
    plugin_multi_attrib = "link_previews"
    interfaces = {"about", "title", "description", "url", "image", "type", "site_name"}

    def _set_og(self, el: ElementBase, value: str) -> None:
        el.xml.text = value
        self.xml.append(el.xml)

    def _get_og(self, el: Type[ElementBase]) -> Optional[str]:
        child = self.xml.find(f"{{{el.namespace}}}{el.name}")
        if child is None:
            return None
        return child.text

    def set_title(self, v: str) -> None:
        self._set_og(Title(), v)

    def get_title(self) -> Optional[str]:
        return self._get_og(Title)

    def set_description(self, v: str) -> None:
        self._set_og(Description(), v)

    def get_description(self) -> Optional[str]:
        return self._get_og(Description)

    def set_url(self, v: str) -> None:
        self._set_og(Url(), v)

    def get_url(self) -> Optional[str]:
        return self._get_og(Url)

    def set_image(self, v: str) -> None:
        self._set_og(Image(), v)

    def get_image(self) -> Optional[str]:
        return self._get_og(Image)

    def set_type(self, v: str) -> None:
        self._set_og(Type_(), v)

    def get_type(self) -> Optional[str]:
        return self._get_og(Type_)

    def set_site_name(self, v: str) -> None:
        self._set_og(SiteName(), v)

    def get_site_name(self) -> Optional[str]:
        return self._get_og(SiteName)

    def get_about(self) -> Optional[str]:
        return self.xml.attrib.get(f"{{{self.namespace}}}about")


class OpenGraphMixin(ElementBase):
    namespace = "https://ogp.me/ns#"


class Title(OpenGraphMixin):
    name = plugin_attrib = "title"


class Description(OpenGraphMixin):
    name = plugin_attrib = "description"


class Url(OpenGraphMixin):
    name = plugin_attrib = "url"


class Image(OpenGraphMixin):
    name = plugin_attrib = "image"


class Type_(OpenGraphMixin):
    name = plugin_attrib = "type"


class SiteName(OpenGraphMixin):
    name = plugin_attrib = "site_name"


def register_plugin():
    for plugin in Title, Description, Url, Image, Type_, SiteName:
        register_stanza_plugin(plugin, Title)
    register_stanza_plugin(Message, LinkPreview, iterable=True)
