"""
永久测试 - 看板 HTTP 端点.
ponytail: 用 ThreadingHTTPServer 在 random port 跑, 测 3 个路由.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest

from ticket import TicketStatus
from dashboard import Handler, run
import dashboard as _dash_mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """每个测试 fresh DB + random port, 避免相互污染.
    ponytail: function scope, 启动/关停 server 60ms, 6 个测试 < 2s.
    """
    Handler.msg_store = _dash_mod.MessageStore(tmp_path / "m.db")
    Handler.sent_store = _dash_mod.SentimentStore(tmp_path / "s.db")
    Handler.ticket_store = _dash_mod.TicketStore(tmp_path / "t.db")
    port = _free_port()
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    th.join(timeout=2)


def _get(url: str) -> tuple:
    """发 GET 请求, return (status, body_dict)."""
    from urllib.parse import urlparse
    p = urlparse(url)
    # ponytail: 之前只发 p.path, 丢了 query string. channel= 过滤就不生效.
    full_path = p.path + (f"?{p.query}" if p.query else "")
    c = HTTPConnection(p.hostname, p.port, timeout=5)
    c.request("GET", full_path)
    r = c.getresponse()
    body = r.read().decode("utf-8")
    c.close()
    return r.status, body


def test_root_returns_html(server):
    status, body = _get(server + "/")
    assert status == 200
    assert "<title>Hermes 客情看板</title>" in body
    assert "recharts" in body.lower()


def test_curve_endpoint_empty(server):
    """无数据用户返回空 points."""
    status, body = _get(server + "/api/curve/u_ghost")
    assert status == 200
    data = json.loads(body)
    assert data["user_id"] == "u_ghost"
    assert data["points"] == []


def test_curve_endpoint_with_data(server):
    """先 record 2 个点, 再查."""
    Handler.sent_store.record("wecom", "u_test", "骗子", ts=1000.0)
    Handler.sent_store.record("wecom", "u_test", "谢谢", ts=1010.0)
    status, body = _get(server + "/api/curve/u_test")
    data = json.loads(body)
    assert data["user_id"] == "u_test"
    assert len(data["points"]) == 2
    assert data["points"][0]["avg_score"] == -1.0
    assert data["points"][1]["avg_score"] == 1.0
    assert data["points"][0]["label"] == "angry"
    assert data["points"][1]["label"] == "positive"


def test_tickets_endpoint(server):
    """先 create 1 个工单, 再查."""
    t = Handler.ticket_store.create("u_test2", "feishu", "complaint", history_dump="<ctx>")
    Handler.ticket_store.update_status(t.id, TicketStatus.OPEN)
    status, body = _get(server + "/api/tickets/u_test2")
    data = json.loads(body)
    assert data["user_id"] == "u_test2"
    assert len(data["tickets"]) == 1
    assert data["tickets"][0]["status"] == "open"
    assert data["tickets"][0]["channel"] == "feishu"


def test_404_not_found(server):
    status, body = _get(server + "/api/unknown")
    assert status == 404
    assert "not found" in body


def test_curve_filter_by_channel(server):
    """channel= 过滤生效."""
    Handler.sent_store.record("wecom", "u_ch", "骗子", ts=1000.0)
    Handler.sent_store.record("feishu", "u_ch", "太棒了", ts=1000.0)
    status, body = _get(server + "/api/curve/u_ch?channel=wecom")
    data = json.loads(body)
    assert len(data["points"]) == 1
    assert data["points"][0]["avg_score"] == -1.0
