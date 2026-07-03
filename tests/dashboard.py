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
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = {
    theme: {
      extend: {
        colors: {
          // ponytail: Plausible-inspired palette (dark, navy, cyan accent)
          ink:    { 950: '#0a0e1a', 900: '#0f172a', 800: '#1e293b', 700: '#334155' },
          slate2: { 400: '#94a3b8', 500: '#64748b' },
          accent: { 400: '#22d3ee', 500: '#06b6d4', 600: '#0891b2' },
          pos:    '#10b981',
          neg:    '#f97316',
          angry:  '#ef4444',
        },
        fontFamily: {
          sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
          mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
        },
      },
    },
  };
</script>
<style>
/* ponytail: 显式居中 + 全宽, 避免 inline 1100px max-width 跟 Tailwind .max-w-6xl 嵌套导致窄条 */
body{font-family:system-ui;padding:0;background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column;align-items:stretch;min-height:100vh}
h1{color:#38bdf8}h2{color:#a5f3fc;margin-top:32px}
input{padding:8px;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;width:240px}
button{padding:8px 16px;border-radius:6px;background:#0ea5e9;color:white;border:none;cursor:pointer;margin-left:8px}
.card{background:#1e293b;border-radius:8px;padding:16px;margin:8px 0}
table{width:100%;border-collapse:collapse}td,th{padding:6px 12px;text-align:left;border-bottom:1px solid #334155}
.badge{padding:2px 8px;border-radius:4px;font-size:12px}
.b-positive{background:#16a34a}.b-neutral{background:#64748b}
.b-negative{background:#ea580c}.b-angry{background:#dc2626}
/* ponytail: gradient backdrop blur for hero, system font smoothing */
.glass { background: linear-gradient(135deg, rgba(34,211,238,0.04) 0%, rgba(15,23,42,0.6) 100%); backdrop-filter: blur(8px); }
.smooth { -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
</style>
</head>
<body class="bg-ink-950 text-slate-200 font-sans smooth min-h-screen">
<div class="w-full max-w-[1600px] mx-auto px-6 py-8">
  <!-- Header -->
  <header class="flex items-center justify-between mb-8">
    <div class="flex items-center gap-3">
      <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-accent-400 to-accent-600 flex items-center justify-center text-ink-950 text-xl font-bold shadow-lg shadow-accent-500/20">H</div>
      <div>
        <h1 class="text-2xl font-semibold text-white tracking-tight">Hermes 客情看板</h1>
        <p class="text-xs text-slate2-400 mt-0.5">智能客服 · 情绪洞察 · 实时工单</p>
      </div>
    </div>
    <div class="flex items-center gap-2 text-xs text-slate2-400">
      <span class="w-2 h-2 rounded-full bg-pos animate-pulse"></span>
      <span class="font-mono" id="status">在线</span>
    </div>
  </header>

  <!-- Search bar -->
  <div class="glass rounded-2xl border border-ink-700/50 p-5 mb-6 flex items-center gap-3">
    <svg class="w-5 h-5 text-slate2-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z"/></svg>
    <input id="uid" placeholder="输入 user_id 查询 (例: u_vip, u_demo)" value="u_demo"
      class="flex-1 bg-transparent border-0 outline-none text-white placeholder-slate2-500 font-mono text-sm" />
    <button onclick="loadAll()"
      class="px-5 py-2 rounded-lg bg-accent-500 hover:bg-accent-400 text-ink-950 text-sm font-semibold transition shadow-lg shadow-accent-500/20">查询</button>
  </div>

  <!-- Stat cards row -->
  <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6" id="stats"></div>

  <!-- Chart -->
  <div class="rounded-2xl border border-ink-700/50 bg-ink-900/40 p-6 mb-6">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-sm font-semibold text-slate2-400 uppercase tracking-wider">情绪曲线</h2>
      <span class="text-xs text-slate2-500 font-mono" id="chart-range"></span>
    </div>
    <div id="chart" class="h-72"></div>
  </div>

  <!-- Tickets -->
  <div class="rounded-2xl border border-ink-700/50 bg-ink-900/40 p-6">
    <h2 class="text-sm font-semibold text-slate2-400 uppercase tracking-wider mb-4">工单</h2>
    <div id="tickets"></div>
  </div>
</div>
<script>
const {LineChart,Line,XAxis,YAxis,CartesianGrid,Tooltip,Legend,ResponsiveContainer,ReferenceLine,Area,AreaChart,ComposedChart}=Recharts;
const LABEL_COLOR = {positive:'#10b981',neutral:'#64748b',negative:'#f97316',angry:'#ef4444'};
async function loadAll(){
  const uid=document.getElementById('uid').value;
  document.getElementById('status').textContent='加载中...';
  try{
    const c=await fetch('/api/curve/'+uid).then(r=>r.json());
    const ts=await fetch('/api/tickets/'+uid).then(r=>r.json());
    renderStats(c.points||[], ts.tickets||[]);
    renderChart(c.points||[]);
    renderTickets(ts.tickets||[]);
    document.getElementById('status').textContent='在线';
  }catch(e){
    document.getElementById('status').textContent='错误: '+e.message;
  }
}
function renderStats(pts, ts){
  const total=pts.reduce((a,p)=>a+p.count,0);
  const avg=pts.length?(pts.reduce((a,p)=>a+p.avg_score,0)/pts.length).toFixed(2):'—';
  const ang=pts.filter(p=>p.label==='angry').length;
  const open=ts.filter(t=>t.status==='open'||t.status==='pending').length;
  const cards=[
    {label:'情绪均值',   value:avg,         sub: avg>0?'正面':'负面',  color: avg>=0?'text-pos':'text-neg'},
    {label:'总消息数',   value:total,       sub:'最近曲线'},
    {label:'angry 桶', value:ang,        sub: ang?'需关注':'一切正常', color: ang?'text-angry':'text-slate2-400'},
  ];
  document.getElementById('stats').innerHTML=cards.map(c=>`
    <div class="rounded-2xl border border-ink-700/50 bg-ink-900/40 p-5">
      <div class="text-xs text-slate2-400 uppercase tracking-wider mb-2">${c.label}</div>
      <div class="text-3xl font-semibold ${c.color||'text-white'} font-mono">${c.value}</div>
      <div class="text-xs text-slate2-500 mt-1">${c.sub}</div>
    </div>`).join('');
}
function renderChart(pts){
  const data=pts.map(p=>({ts:new Date(p.bucket_ts*1000).toLocaleTimeString(),score:p.avg_score,label:p.label}));
  document.getElementById('chart-range').textContent=data.length?`${data.length} 个桶`:'无数据';
  if(!data.length){document.getElementById('chart').innerHTML='<div class="flex items-center justify-center h-full text-slate2-500 text-sm">暂无数据</div>';return;}
  ReactDOM.render(React.createElement(ResponsiveContainer,{width:'100%',height:'100%'},
    React.createElement(ComposedChart,{data,margin:{top:10,right:20,left:0,bottom:0}},
      React.createElement('defs',null,
        React.createElement('linearGradient',{id:'g',x1:0,y1:0,x2:0,y2:1},
          React.createElement('stop',{offset:'0%',stopColor:'#22d3ee',stopOpacity:0.4}),
          React.createElement('stop',{offset:'100%',stopColor:'#22d3ee',stopOpacity:0}))),
      React.createElement(CartesianGrid,{strokeDasharray:'3 3',stroke:'#334155',vertical:false}),
      React.createElement(XAxis,{dataKey:'ts',stroke:'#64748b',fontSize:11,tickLine:false}),
      React.createElement(YAxis,{domain:[-1,1],stroke:'#64748b',fontSize:11,tickLine:false,axisLine:false}),
      React.createElement(ReferenceLine,{y:0,stroke:'#334155'}),
      React.createElement(Tooltip,{contentStyle:{background:'#1e293b',border:'1px solid #334155',borderRadius:'8px',fontSize:'12px'},labelStyle:{color:'#94a3b8'}}),
      React.createElement(Area,{type:'monotone',dataKey:'score',stroke:'#22d3ee',strokeWidth:2,fill:'url(#g)',dot:{r:3,fill:'#22d3ee',strokeWidth:0}}))),document.getElementById('chart'));
}
function renderTickets(ts){
  if(!ts.length){document.getElementById('tickets').innerHTML='<div class="text-center py-12 text-slate2-500 text-sm">暂无工单</div>';return;}
  const colorMap={positive:'bg-pos/10 text-pos border-pos/30',neutral:'bg-slate2-500/10 text-slate2-400 border-slate2-500/30',negative:'bg-neg/10 text-neg border-neg/30',angry:'bg-angry/10 text-angry border-angry/30'};
  const statusColor={open:'bg-accent-500/10 text-accent-400 border-accent-500/30',pending:'bg-neg/10 text-neg border-neg/30',closed:'bg-slate2-500/10 text-slate2-400 border-slate2-500/30',new:'bg-pos/10 text-pos border-pos/30'};
  document.getElementById('tickets').innerHTML=`
    <table class="w-full text-sm">
      <thead><tr class="text-xs text-slate2-400 uppercase tracking-wider border-b border-ink-700/50">
        <th class="text-left py-2 font-medium">ID</th><th class="text-left font-medium">渠道</th>
        <th class="text-left font-medium">意图</th><th class="text-left font-medium">状态</th>
        <th class="text-left font-medium">更新</th>
      </tr></thead>
      <tbody>${ts.map(t=>`<tr class="border-b border-ink-700/30 hover:bg-ink-800/30 transition">
        <td class="py-3 font-mono text-xs text-slate2-400">${t.id.slice(0,8)}</td>
        <td><span class="px-2 py-0.5 rounded text-xs ${colorMap[t.label_color]||colorMap.neutral} border">${t.channel}</span></td>
        <td class="text-slate2-400">${t.intent}</td>
        <td><span class="px-2 py-0.5 rounded text-xs ${statusColor[t.status]||statusColor.closed} border">${t.status}</span></td>
        <td class="text-slate2-500 text-xs">${new Date(t.updated_at*1000).toLocaleString()}</td>
      </tr>`).join('')}</tbody></table>`;
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
