"""
W7 看板 - stdlib http.server + 单 HTML + Recharts CDN.

ponytail: 不引 flask/fastapi, stdlib http.server 足够.
路由:
  GET /              -> 静态 HTML
  GET /api/curve/<user_id>?channel=wecom
  GET /api/tickets/<user_id>
ceiling: 单进程, 无缓存, 无 CORS (本机/反代后 OK).
add when: 100 并发 -> 切 gunicorn/uvicorn; 跨域 -> 加 CORS 头.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 复用已有组件
sys.path.insert(0, str(Path(__file__).parent))
from message_store import MessageStore
from sentiment import SentimentStore
from ticket import TicketStore

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Hermes 客情看板</title>
<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script crossorigin src="https://unpkg.com/prop-types@15/prop-types.min.js"></script>
<script crossorigin src="https://unpkg.com/recharts@2.8.0/umd/Recharts.js"></script>
<style>
body{font-family:system-ui;max-width:1100px;margin:24px auto;padding:0 16px;background:#0f172a;color:#e2e8f0}
h1{color:#38bdf8}h2{color:#a5f3fc;margin-top:32px}
input{padding:8px;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;width:240px}
button{padding:8px 16px;border-radius:6px;background:#0ea5e9;color:white;border:none;cursor:pointer;margin-left:8px}
.card{background:#1e293b;border-radius:8px;padding:16px;margin:8px 0}
table{width:100%;border-collapse:collapse}td,th{padding:6px 12px;text-align:left;border-bottom:1px solid #334155}
.badge{padding:2px 8px;border-radius:4px;font-size:12px}
.b-positive{background:#16a34a}.b-neutral{background:#64748b}
.b-negative{background:#ea580c}.b-angry{background:#dc2626}
</style>
</head>
<body>
<h1>🎯 Hermes 客情看板</h1>
<div class="card">
  <input id="uid" placeholder="user_id (例: u_vip)" value="u_vip">
  <button onclick="loadAll()">查询</button>
</div>
<h2>情绪曲线</h2>
<div class="card" id="chart" style="height:320px"></div>
<h2>工单</h2>
<div class="card" id="tickets"></div>
<script>
const {LineChart,Line,XAxis,YAxis,CartesianGrid,Tooltip,Legend,ResponsiveContainer}=Recharts;
async function loadAll(){
  const uid=document.getElementById('uid').value;
  const c=await fetch('/api/curve/'+uid).then(r=>r.json());
  const ts=await fetch('/api/tickets/'+uid).then(r=>r.json());
  renderChart(c.points||[]);
  renderTickets(ts.tickets||[]);
}
function renderChart(pts){
  const data=pts.map(p=>({ts:new Date(p.bucket_ts*1000).toLocaleTimeString(),score:p.avg_score,label:p.label}));
  ReactDOM.render(React.createElement(ResponsiveContainer,{width:'100%',height:300},
    React.createElement(LineChart,{data,margin:{top:5,right:20,left:0,bottom:5}},
      React.createElement(CartesianGrid,{strokeDasharray:'3 3',stroke:'#334155'}),
      React.createElement(XAxis,{dataKey:'ts',stroke:'#94a3b8'}),
      React.createElement(YAxis,{domain:[-1,1],stroke:'#94a3b8'}),
      React.createElement(Tooltip,{contentStyle:{background:'#1e293b',border:'none'}}),
      React.createElement(Line,{type:'monotone',dataKey:'score',stroke:'#38bdf8',strokeWidth:2,dot:{r:4}})
    )),document.getElementById('chart'));
}
function renderTickets(ts){
  const html=ts.length===0?'<p>无工单</p>':'<table><tr><th>ID</th><th>渠道</th><th>意图</th><th>状态</th><th>更新</th></tr>'+
    ts.map(t=>`<tr><td>${t.id.slice(0,8)}</td><td>${t.channel}</td><td>${t.intent}</td>
      <td><span class="badge b-${t.label_color||'neutral'}">${t.status}</span></td>
      <td>${new Date(t.updated_at*1000).toLocaleString()}</td></tr>`).join('')+'</table>';
  document.getElementById('tickets').innerHTML=html;
}
loadAll();
</script>
</body></html>
"""


# ponytail: 同主公偏好"30000 以上端口"
DEFAULT_PORT = 30800


class Handler(BaseHTTPRequestHandler):
    msg_store: MessageStore
    sent_store: SentimentStore
    ticket_store: TicketStore

    def log_message(self, fmt: str, *args) -> None:
        # ponytail: 静默默认 access log, 减少噪音
        pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        if path == "/":
            self._send_html(DASHBOARD_HTML)
        elif path.startswith("/api/curve/"):
            uid = path[len("/api/curve/"):]
            ch = qs.get("channel", [None])[0]
            pts = self.sent_store.curve(uid, channel=ch)
            self._send_json({"user_id": uid, "points": [p.__dict__ | {"label": p.label.value} for p in pts]})
        elif path.startswith("/api/tickets/"):
            uid = path[len("/api/tickets/"):]
            tickets = self.ticket_store.list_by_user(uid)
            self._send_json({
                "user_id": uid,
                "tickets": [self._ticket_to_dict(t) for t in tickets],
            })
        else:
            self._send_json({"error": "not found"}, status=404)

    def _ticket_to_dict(self, t) -> dict:
        sent = self.sent_store.curve(t.user_id, channel=t.channel)
        avg = sum(p.avg_score for p in sent) / len(sent) if sent else 0.0
        label_color = "angry" if avg < -0.7 else "negative" if avg < -0.3 else "positive" if avg > 0.3 else "neutral"
        return {
            "id": t.id, "user_id": t.user_id, "channel": t.channel,
            "intent": t.intent, "status": t.status.value,
            "created_at": t.created_at, "updated_at": t.updated_at,
            "label_color": label_color,
        }

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
    Handler.msg_store = MessageStore()
    Handler.sent_store = SentimentStore()
    Handler.ticket_store = TicketStore()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"dashboard: http://{host}:{port}/  (default user_id=u_vip)")
    srv.serve_forever()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()
    run(args.host, args.port)
