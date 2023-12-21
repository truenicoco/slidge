import unittest

from slixmpp.test import SlixTest

from slidge.slixfix.link_preview import stanza


class TestLinkPreview(SlixTest):
    def setUp(self):
        stanza.register_plugin()

    def testSetLinkPreview(self):
        msg = self.Message()
        msg["link_preview"]["about"] = "https://the.link.example.com/what-was-linked-to"
        msg["link_preview"]["title"] = "A cool title"
        self.check(
            msg,  # language=xml
            """
            <message xmlns="jabber:client">
              <Description xmlns="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                           about="https://the.link.example.com/what-was-linked-to">
                <title xmlns="https://ogp.me/ns#">A cool title</title>
              </Description>
            </message>
            """,
            use_values=False,
        )
        assert msg["link_preview"]["description"] is None

    def testGetLinkPreview(self):
        # language=xml
        xml_str = """
            <message to="whoever@example.com">
              <body>I wanted to mention https://the.link.example.com/what-was-linked-to</body>
              <rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                               xmlns:og="https://ogp.me/ns#"
                               rdf:about="https://the.link.example.com/what-was-linked-to">
                <og:title>Page Title</og:title>
                <og:description>Page Description</og:description>
                <og:url>Canonical URL</og:url>
                <og:image>https://link.to.example.com/image.png</og:image>
                <og:type>website</og:type>
                <og:site_name>Some Website</og:site_name>
              </rdf:Description>
            </message>
            """
        xml = self.parse_xml(xml_str)
        msg = self.Message(xml)
        assert msg["link_preview"]["title"] == "Page Title"
        assert msg["link_preview"]["description"] == "Page Description"
        assert msg["link_preview"]["url"] == "Canonical URL"
        assert msg["link_preview"]["image"] == "https://link.to.example.com/image.png"
        assert msg["link_preview"]["site_name"] == "Some Website"
        assert msg["link_preview"]["type"] == "website"
        assert (
            msg["link_preview"]["about"]
            == "https://the.link.example.com/what-was-linked-to"
        )

    def testGetLinkPreviews(self):
        # language=xml
        xml_str = """
            <message to="whoever@example.com">
              <body>I wanted to mention https://the.link.example.com/what-was-linked-to</body>
              <rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                               xmlns:og="https://ogp.me/ns#"
                               rdf:about="https://the.link.example.com/what-was-linked-to 1">
                <og:title>Page Title 1</og:title>
                <og:description>Page Description 1</og:description>
                <og:url>Canonical URL 1</og:url>
                <og:image>https://link.to.example.com/image.png 1</og:image>
                <og:type>website 1</og:type>
                <og:site_name>Some Website 1</og:site_name>
              </rdf:Description>
              <rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                               xmlns:og="https://ogp.me/ns#"
                               rdf:about="https://the.link.example.com/what-was-linked-to 2">
                <og:title>Page Title 2</og:title>
                <og:description>Page Description 2</og:description>
                <og:url>Canonical URL 2</og:url>
                <og:image>https://link.to.example.com/image.png 2</og:image>
                <og:type>website 2</og:type>
                <og:site_name>Some Website 2</og:site_name>
              </rdf:Description>
            </message>
            """
        xml = self.parse_xml(xml_str)
        msg = self.Message(xml)
        previews = msg["link_previews"]
        assert len(previews) == 2
        for i, p in enumerate(previews, start=1):
            assert p["title"] == f"Page Title {i}"
            assert p["description"] == f"Page Description {i}"
            assert p["url"] == f"Canonical URL {i}"
            assert p["image"] == f"https://link.to.example.com/image.png {i}"
            assert p["site_name"] == f"Some Website {i}"
            assert p["type"] == f"website {i}"
            assert p["about"] == f"https://the.link.example.com/what-was-linked-to {i}"


suite = unittest.TestLoader().loadTestsFromTestCase(TestLinkPreview)
