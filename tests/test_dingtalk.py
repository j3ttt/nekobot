"""Tests for the DingTalk channel."""

import pytest

from nekobot.bus.queue import MessageBus
from nekobot.channels.dingtalk import DingTalkChannel
from nekobot.config.schema import DingTalkConfig


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_body: dict | None = None) -> None:
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = "{}"

    def json(self) -> dict:
        return self._json_body


class _FakeHttp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, url: str, json=None, headers=None, **kwargs):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()


@pytest.mark.asyncio
async def test_group_message_uses_conversation_id_as_chat_id() -> None:
    """Group messages should use conversation_id as both sender_id and chat_id."""
    config = DingTalkConfig(client_id="app", client_secret="secret", allow_from=["conv123"])
    bus = MessageBus()
    channel = DingTalkChannel(config, bus)

    await channel._on_message(
        "hello",
        sender_id="user1",
        sender_name="Alice",
        conversation_type="2",
        conversation_id="conv123",
    )

    msg = await bus.consume_inbound()
    assert msg.sender_id == "conv123"
    assert msg.chat_id == "conv123"
    assert msg.metadata["conversation_type"] == "2"
    assert msg.metadata["is_group"] is True


@pytest.mark.asyncio
async def test_private_message_uses_sender_id_as_chat_id() -> None:
    """Private messages should use sender_id as chat_id (no prefix)."""
    config = DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"])
    bus = MessageBus()
    channel = DingTalkChannel(config, bus)

    await channel._on_message(
        "hi",
        sender_id="user1",
        sender_name="Alice",
        conversation_type="1",
        conversation_id="conv456",
    )

    msg = await bus.consume_inbound()
    assert msg.sender_id == "user1"
    assert msg.chat_id == "user1"
    assert msg.metadata["is_group"] is False


@pytest.mark.asyncio
async def test_group_send_uses_group_messages_api() -> None:
    """Sending to a group chat_id should use the groupMessages/send endpoint."""
    from nekobot.bus.events import OutboundMessage

    config = DingTalkConfig(client_id="app", client_secret="secret", allow_from=["*"])
    channel = DingTalkChannel(config, MessageBus())
    channel._http = _FakeHttp()

    msg = OutboundMessage(channel="dingtalk", chat_id="conv123", content="hello")
    await channel._send_payload(
        msg=msg,
        headers={"x-acs-dingtalk-access-token": "token"},
        is_group=True,
        msg_key="sampleMarkdown",
        msg_param={"text": "hello", "title": "NekoBot Reply"},
    )

    call = channel._http.calls[0]
    assert call["url"] == "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
    assert call["json"]["openConversationId"] == "conv123"
    assert call["json"]["msgKey"] == "sampleMarkdown"


@pytest.mark.asyncio
async def test_private_send_uses_oto_messages_api() -> None:
    """Sending to a private chat should use the oToMessages/batchSend endpoint."""
    from nekobot.bus.events import OutboundMessage

    config = DingTalkConfig(client_id="app", client_secret="secret", allow_from=["*"])
    channel = DingTalkChannel(config, MessageBus())
    channel._http = _FakeHttp()

    msg = OutboundMessage(channel="dingtalk", chat_id="user1", content="hello")
    await channel._send_payload(
        msg=msg,
        headers={"x-acs-dingtalk-access-token": "token"},
        is_group=False,
        msg_key="sampleMarkdown",
        msg_param={"text": "hello", "title": "NekoBot Reply"},
    )

    call = channel._http.calls[0]
    assert call["url"] == "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
    assert call["json"]["userIds"] == ["user1"]


class TestIsGroupChat:
    """Tests for _is_group_chat static method."""

    def test_string_2_is_group(self) -> None:
        assert DingTalkChannel._is_group_chat("2") is True

    def test_string_1_is_private(self) -> None:
        assert DingTalkChannel._is_group_chat("1") is False

    def test_int_2_is_group(self) -> None:
        assert DingTalkChannel._is_group_chat(2) is True

    def test_int_1_is_private(self) -> None:
        assert DingTalkChannel._is_group_chat(1) is False

    def test_bool_true_is_group(self) -> None:
        assert DingTalkChannel._is_group_chat(True) is True

    def test_bool_false_is_private(self) -> None:
        assert DingTalkChannel._is_group_chat(False) is False

    def test_string_group_is_group(self) -> None:
        assert DingTalkChannel._is_group_chat("group") is True

    def test_string_private_is_private(self) -> None:
        assert DingTalkChannel._is_group_chat("private") is False

    def test_cid_fallback(self) -> None:
        assert DingTalkChannel._is_group_chat("unknown", "cidABC123") is True

    def test_no_cid_fallback_is_false(self) -> None:
        assert DingTalkChannel._is_group_chat("unknown", "notcid") is False


class TestExtractLocalImagePaths:
    """Tests for _extract_local_image_paths."""

    def _make_channel(self) -> DingTalkChannel:
        config = DingTalkConfig(client_id="a", client_secret="b", allow_from=["*"])
        return DingTalkChannel(config, MessageBus())

    def test_absolute_path_extracted(self) -> None:
        channel = self._make_channel()
        paths, cleaned = channel._extract_local_image_paths("![img](/tmp/photo.png)")
        assert len(paths) == 1
        assert paths[0].endswith("/tmp/photo.png")
        assert cleaned == ""

    def test_relative_path_not_extracted(self) -> None:
        channel = self._make_channel()
        paths, cleaned = channel._extract_local_image_paths("![img](relative/photo.png)")
        assert paths == []
        assert "relative/photo.png" in cleaned

    def test_url_not_extracted(self) -> None:
        channel = self._make_channel()
        paths, cleaned = channel._extract_local_image_paths("![img](https://example.com/photo.png)")
        assert paths == []
        assert "https://example.com/photo.png" in cleaned

    def test_mixed_content(self) -> None:
        channel = self._make_channel()
        content = "Hello\n![img](/tmp/a.png)\nWorld\n![img](https://example.com/b.png)"
        paths, cleaned = channel._extract_local_image_paths(content)
        assert len(paths) == 1
        assert paths[0].endswith("/tmp/a.png")
        assert "Hello" in cleaned
        assert "World" in cleaned

    def test_link_with_image_suffix(self) -> None:
        channel = self._make_channel()
        paths, cleaned = channel._extract_local_image_paths("[photo](/tmp/photo.jpg)")
        assert len(paths) == 1
        assert paths[0].endswith("/tmp/photo.jpg")

    def test_link_without_image_suffix_not_extracted(self) -> None:
        channel = self._make_channel()
        paths, cleaned = channel._extract_local_image_paths("[doc](/tmp/file.txt)")
        assert paths == []
