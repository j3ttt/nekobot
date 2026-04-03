from nekobot.bus.queue import MessageBus
from nekobot.channels.telegram import TelegramChannel
from nekobot.config.schema import TelegramConfig


def test_request_kwargs_disable_env_proxy_by_default():
    channel = TelegramChannel(TelegramConfig(token="test-token"), MessageBus())

    req_kwargs = channel._build_request_kwargs()

    assert req_kwargs["httpx_kwargs"]["trust_env"] is False
    assert "proxy" not in req_kwargs


def test_request_kwargs_use_explicit_proxy_when_configured():
    channel = TelegramChannel(
        TelegramConfig(token="test-token", proxy="socks5://127.0.0.1:7890"),
        MessageBus(),
    )

    req_kwargs = channel._build_request_kwargs()

    assert req_kwargs["proxy"] == "socks5://127.0.0.1:7890"
    assert req_kwargs["httpx_kwargs"]["trust_env"] is False
