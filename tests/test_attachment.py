import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import ANY, MagicMock, patch

import pytest
from conftest import AvatarFixtureMixin
from test_shakespeare import Base as Shakespeare

from slidge.core import config
from slidge.util.types import LegacyAttachment


@pytest.fixture(scope="function")
def attachment(request):
    class MockResponse:
        status = 200

    class MockAioHTTP:
        @asynccontextmanager
        async def head(*a, **k):
            yield MockResponse

    with (
        patch(
            "slixmpp.plugins.xep_0363.http_upload.XEP_0363.upload_file",
            return_value="http://url",
        ) as http_upload,
        patch("aiohttp.ClientSession", return_value=MockAioHTTP) as client_session,
        patch("slidge.core.mixins.attachment.uuid4", return_value="uuid"),
    ):
        request.cls.head = client_session.head = MockAioHTTP.head
        request.cls.http_upload = http_upload
        yield


@pytest.mark.usefixtures("avatar")
@pytest.mark.usefixtures("attachment")
class Base(Shakespeare, AvatarFixtureMixin):
    http_upload: MagicMock

    def _assert_body(self, text="body", i=None):
        if i:
            self.send(  # language=XML
                f"""
            <message type="chat"
                     from="juliet@aim.shakespeare.lit/slidge"
                     to="romeo@montague.lit"
                     id="{i}">
              <body>{text}</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <store xmlns="urn:xmpp:hints" />
            </message>
            """,
                use_values=False,
            )
        else:
            self.send(  # language=XML
                f"""
            <message type="chat"
                     from="juliet@aim.shakespeare.lit/slidge"
                     to="romeo@montague.lit">
              <body>{text}</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <store xmlns="urn:xmpp:hints" />
            </message>
            """,
                use_values=False,
            )

    def _assert_file(self, url="http://url"):
        when = (
            datetime.fromtimestamp(self.avatar_path.stat().st_mtime)
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.send(  # language=XML
            f"""
            <message type="chat"
                     from="juliet@aim.shakespeare.lit/slidge"
                     to="romeo@montague.lit">
              <reference xmlns="urn:xmpp:reference:0"
                         type="data">
                <media-sharing xmlns="urn:xmpp:sims:1">
                  <sources>
                    <reference xmlns="urn:xmpp:reference:0"
                               uri="{url}"
                               type="data" />
                  </sources>
                  <file xmlns="urn:xmpp:jingle:apps:file-transfer:5">
                    <name>5x5.png</name>
                    <size>547</size>
                    <date>{when}</date>
                    <hash xmlns="urn:xmpp:hashes:2"
                          algo="sha-256">NdpqDQuHlshve2c0iU25l2KI4cjpoyzaTk3a/CdbjPQ=</hash>
                  </file>
                </media-sharing>
              </reference>
              <file-sharing xmlns="urn:xmpp:sfs:0"
                            disposition="inline">
                <sources>
                  <url-data xmlns="http://jabber.org/protocol/url-data"
                            target="{url}" />
                </sources>
                <file xmlns="urn:xmpp:file:metadata:0">
                  <name>5x5.png</name>
                  <size>547</size>
                  <date>{when}</date>
                  <hash xmlns="urn:xmpp:hashes:2"
                        algo="sha-256">NdpqDQuHlshve2c0iU25l2KI4cjpoyzaTk3a/CdbjPQ=</hash>
                </file>
              </file-sharing>
              <x xmlns="jabber:x:oob">
                <url>{url}</url>
              </x>
              <body>{url}</body>
            </message>
            """,
            use_values=False,  # ?
        )


class TestBodyOnly(Base):
    def test_no_file_no_body(self):
        self.run_coro(self.juliet.send_files([]))
        assert self.next_sent() is None

    def test_just_body(self):
        self.run_coro(self.juliet.send_files([], body="body"))
        self._assert_body()
        self.run_coro(self.juliet.send_files([], body="body", body_first=True))
        self._assert_body()
        self.run_coro(self.juliet.send_files([], body="body", legacy_msg_id=12))
        self._assert_body(i=12)


class TestAttachmentUpload(Base):
    def __test_basic(self, attachment: LegacyAttachment, upload_kwargs: dict):
        """
        Basic test that file is uploaded.
        """
        self.run_coro(self.juliet.send_files([attachment]))
        self.http_upload.assert_called_with(**upload_kwargs)
        self._assert_file()

    def __test_reuse(self, attachment: LegacyAttachment, upload_kwargs: dict):
        """
        Basic test the no new file is uploaded when the same attachment is used
        twice.
        """
        self.run_coro(self.juliet.send_files([attachment]))
        self.http_upload.assert_called_with(**upload_kwargs)
        self._assert_file()
        self.http_upload.reset_mock()
        self.run_coro(self.juliet.send_files([attachment]))
        self.http_upload.assert_not_called()
        self._assert_file()

    def test_path(self):
        self.__test_basic(
            LegacyAttachment(path=self.avatar_path),
            dict(
                filename=self.avatar_path, content_type=None, ifrom=self.xmpp.boundjid
            ),
        )

    def test_path_and_id(self):
        self.__test_reuse(
            LegacyAttachment(path=self.avatar_path, legacy_file_id=1235),
            dict(
                filename=self.avatar_path, content_type=None, ifrom=self.xmpp.boundjid
            ),
        )

    def test_bytes(self):
        with patch("pathlib.Path.stat", return_value=os.stat(self.avatar_path)):
            self.__test_basic(
                LegacyAttachment(data=self.avatar_path.read_bytes(), name="5x5.png"),
                dict(filename=ANY, content_type=None, ifrom=self.xmpp.boundjid),
            )

    def test_bytes_and_id(self):
        with patch("pathlib.Path.stat", return_value=os.stat(self.avatar_path)):
            self.__test_reuse(
                LegacyAttachment(
                    data=self.avatar_path.read_bytes(),
                    legacy_file_id=123,
                    name="5x5.png",
                ),
                dict(filename=ANY, content_type=None, ifrom=self.xmpp.boundjid),
            )


class TestAttachmentNoUpload(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        config.NO_UPLOAD_URL_PREFIX = "https://url"

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        config.NO_UPLOAD_PATH = None
        config.NO_UPLOAD_URL_PREFIX = None

    def setUp(self):
        super().setUp()
        config.NO_UPLOAD_PATH = tempfile.TemporaryDirectory().name

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(config.NO_UPLOAD_PATH)

    def __test_basic(self, attachment: LegacyAttachment, url: str):
        """
        Basic test that file is copied.
        """
        self.run_coro(self.juliet.send_files([attachment]))
        self._assert_file(url=url)

    def __test_reuse(self, attachment: LegacyAttachment, url: str):
        """
        Basic test the no new file is copied when the same attachment is used
        twice.
        """
        self.run_coro(self.juliet.send_files([attachment]))
        self._assert_file(url=url)
        self.run_coro(self.juliet.send_files([attachment]))
        self._assert_file(url=url)

    def test_path(self):
        self.__test_basic(
            LegacyAttachment(path=self.avatar_path), "https://url/uuid/uuid/5x5.png"
        )

    def test_path_and_id(self):
        self.__test_reuse(
            LegacyAttachment(path=self.avatar_path, legacy_file_id=1234),
            "https://url/1234/uuid/5x5.png",
        )
