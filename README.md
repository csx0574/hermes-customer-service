# Hermes 智能客服 + 客情分析

> 基于开源 Hermes Agent 的私有化智能客服 + 客情分析系统。MIT 协议，可私有化部署。

## ✨ 核心特性

- **全渠道接入** — 企业微信 + 飞书原生支持
- **持久记忆** — 跨会话记住用户偏好、历史工单、情绪
- **意图识别** — 5 类意图 (咨询/投诉/售后/退款/表扬) + LLM 兜底
- **工单 + 转人工** — OpenAPI 标准格式，无缝接力不丢上下文
- **情绪曲线** — 5 秒聚合，热力图 + 异常告警
- **客情看板** — Web 端单页，Recharts 实时渲染

## 🏗 架构

```
┌─────────────────┐
│  企业微信 / 飞书  │ (渠道接入, W1)
└────────┬────────┘
         │ ChannelMessage
         ▼
┌─────────────────┐
│ ChannelGateway  │ (多渠道路由, W1)
└────────┬────────┘
         │
         ├─→ MessageStore ──→ SQLite (持久记忆, W3)
         │
         ├─→ IntentClassifier ──→ 正则 + LLM (W4)
         │
         ├─→ TicketStore ──→ SQLite (工单, W5)
         │
         ├─→ SentimentStore ──→ SQLite (5秒聚合, W6)
         │
         └─→ AlertDetector ──→ Mailer (异常告警, W8)
                                │
                                ▼
                          AlertLog (SQLite)
```

## 📦 组件清单

| 组件 | 文件 | 行数 | 状态 |
|---|---|---|---|
| 渠道网关 | `channel_gateway.py` | ~150 | ✅ Mock (W1) |
| 持久记忆 | `message_store.py` | ~140 | ✅ (W3) |
| 意图识别 | `intent_classifier.py` | ~200 | ✅ 规则 + LLM (W4) |
| 工单 | `ticket.py` | ~180 | ✅ (W5) |
| 情绪曲线 | `sentiment.py` | ~200 | ✅ (W6) |
| 看板 | `dashboard.py` | ~170 | ✅ (W7) |
| 告警 | `alert.py` | ~250 | ✅ (W8) |
| LLM 接入 | `llm_fallback.py` | ~120 | ✅ DeepSeek/智谱 (W10) |

## 🚀 快速开始

```bash
# 1. 安装依赖 (只需 hermes venv, 无新依赖)
cd /vol2/1000/Hermes
source .venv/bin/activate

# 2. 运行测试 (76 个测试)
python -m pytest apps/customer-service/tests/ -v

# 3. 启动看板 (端口 30800)
python apps/customer-service/tests/dashboard.py --port 30800

# 4. 浏览器访问
open http://localhost:30800/

# 5. 跑端到端 demo (5 件套串联)
python apps/customer-service/tests/e2e_demo_v3.py
```

## 🔑 环境变量 (LLM)

```bash
# DeepSeek (推荐, 1 元/百万 token)
export DEEPSEEK_API_KEY="sk-***"
# 替换 e2e_demo_v2.py 里的 mock_llm 为:
# from llm_fallback import make_llm_fallback
# mock_llm = make_llm_fallback("deepseek")

# 智谱 (备选)
export ZHIPU_API_KEY="***"
mock_llm = make_llm_fallback("zhipu")
```

## 🔌 真实渠道接入 (待主公拿凭据)

详见 `~/my-vault/05-资源/channel-credentials.md`

| 平台 | 凭据数 | 申请时长 | 状态 |
|---|---|---|---|
| 企业微信 | 5 项 | 1-3 工作日 | ⏳ 等主公 |
| 飞书 | 4 项 | 1-2 工作日 | ⏳ 等主公 |

## 📊 API 端点 (dashboard.py)

| 路由 | 方法 | 用途 |
|---|---|---|
| `/` | GET | 看板首页 (Recharts 单页) |
| `/api/curve/<user_id>` | GET | 情绪曲线 (支持 `?channel=wecom`) |
| `/api/tickets/<user_id>` | GET | 用户工单列表 |

## 🧪 测试

```bash
# 全量测试 (76 个, 10s)
python -m pytest apps/customer-service/tests/

# 单组件测试
python -m pytest apps/customer-service/tests/test_intent_classifier.py
python -m pytest apps/customer-service/tests/test_message_store.py

# 自检 (单文件)
python apps/customer-service/tests/sentiment.py
python apps/customer-service/tests/alert.py
```

## 📈 进度 (W1-W11)

| 周 | 交付 | 状态 |
|---|---|---|
| W1 | 渠道网关骨架 | ✅ |
| W2 | 跨会话记忆 | ✅ |
| W3 | MessageStore 持久化 | ✅ |
| W4 | 意图识别 + 转人工 | ✅ |
| W5 | 工单 + 状态机 | ✅ |
| W6 | 情绪曲线 + 5秒聚合 | ✅ |
| W7 | Web 看板 | ✅ |
| W8 | 异常告警 + 邮件 | ✅ |
| W9 | 3 份种子客户联系模板 | ✅ |
| W10 | LLM 接入 (DeepSeek/智谱) | ✅ |
| W10b | 渠道凭据清单 | ✅ |
| W11 | 销售材料 (单页+话术+合同) | ✅ |
| W12 | 启动销售 | ⏳ 等主公 |

## 🛡️ 数据安全 (A 轨私有化)

- 传输加密: TLS 1.3
- 存储加密: PostgreSQL TDE
- 私有化部署: 客户机房 / VPC
- 审计日志: 所有 LLM 调用 + 工单操作留痕
- 模型选择: 客户可选 (DeepSeek / 智谱 / Qwen / 私有化 LLM)
- 数据隔离: 多租户 RLS
- 认证: OAuth 2.0 + 企微/飞书原生身份
- 合规: 支持等保 2.0 三级 / ISO 27001 / SOC 2

## 💰 商业模式

| 轨道 | 客单价 | 适合 |
|---|---|---|
| A 私有化 | ¥20-50 万一次性 + ¥3-8 万/年升级 | 金融/医疗/政企 |
| B SaaS | ¥500-8000/月/坐席 | 零售/教育/本地 |
| C 客情增值 | ¥2000/月/账号 | A/B 客户都加 |

**单坐席月成本 ¥90 → 售价 ¥2000 → 毛利 95.5%**

## 📞 联系方式

[主公名] | hermes.csx0574.top

---

## 📚 相关文档

- `~/my-vault/04-项目/PRD/hermes-customer-service-v2.md` — 完整 PRD
- `~/my-vault/06-输出/seed-outreach/` — 3 份种子客户联系模板
- `~/my-vault/06-输出/sales-materials/` — 销售材料
- `~/my-vault/05-资源/channel-credentials.md` — 渠道凭据清单

## 📝 License

MIT — 客户可永久使用、修改、再分发
