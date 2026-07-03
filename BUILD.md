# 打包与部署 (BUILD.md)

## 🏗️ 加密 .exe 单文件打包

### 打包命令
```bash
cd /vol2/1000/Hermes/apps/customer-service
bash build_exe.sh
```

### 产物
- `dist/customer-service` (Linux ELF, 9.1MB, AES-256 加密 bootloader)
- 拷这一个文件给客户, 自包含 Python 3.11 + stdlib + 8 业务模块

### 启动方式
```bash
./dist/customer-service --port 30800          # 默认 0.0.0.0:30800
./dist/customer-service --port 30800 --host 127.0.0.1
```

### AES 密钥管理
- 密钥写在 `build_exe.sh` 第 25 行 `HERMES_EXE_KEY`
- **主公换 key 后扔掉 build_exe.sh** (客户没这个 key 解不开)
- **SOUL 边界**: 凭据 (.env / 真 webhook) **不进 .exe**, .exe 只装业务代码

---

## 🔐 真凭据接入 (B 任务)

### 1) 主公填 .env (不进 git 不进 .exe)
```bash
# /vol2/1000/Hermes/apps/customer-service/.env
# ponytail: 已在 .gitignore, 不会进 git, 不会进 .exe

# 企微 (主公 5 项)
WECOM_CORP_ID=
WECOM_AGENT_ID=
WECOM_SECRET=
WECOM_CALLBACK_URL=
WECOM_TOKEN=

# 飞书 (主公 4 项)
LARK_APP_ID=
LARK_APP_SECRET=
LARK_VERIFICATION_TOKEN=
LARK_ENCRYPT_KEY=

# LLM (主公选 DeepSeek 或 GLM)
LLM_PROVIDER=deepseek          # deepseek | zhipu
DEEPSEEK_API_KEY=
GLM_API_KEY=
```

### 2) 跑接入代码
```bash
cd /vol2/1000/Hermes/apps/customer-service/tests
python3 channel_gateway.py --test   # 验证凭据可读
python3 e2e_demo_v3.py              # 端到端跑通
```

### 3) SOUL 三重防御
1. `.env` 已在 `.gitignore` (二次防御: 仓内 grep 也搜不到)
2. `.env` 不进 `datas=` (PyInstaller spec 排除)
3. `.env` 不进 `hiddenimports=` (运行时从 `~/.hermes/customer_service/` 读)

---

## 📊 当前状态
- ✅ W1-W11 全 8 组件 + 76 测试 + 5 销售材料
- ✅ Dashboard 暗色 Tailwind 风格 + 全宽 1600px
- ✅ 加密单文件 .exe (9.1MB, AES-256 bootloader)
- ⏳ 真凭据接入 (B 任务, 等主公填 .env)

## 🚚 部署给客户
```bash
# 1. 拷 .exe
scp dist/customer-service user@client-host:/opt/hermes/

# 2. 客户跑 (主公在客服群说: "把 webhook 配到企微后台, 发个测试消息, 看板出 1 个桶就 OK")
ssh user@client-host
/opt/hermes/customer-service --port 30800 &

# 3. 客户内网浏览器打开
http://<client-host>:30800/
```

> **主公交付清单**:
> - `dist/customer-service` (9.1MB, 单文件)
> - `BUILD.md` (这份)
> - `README.md` (5.4KB, 业务介绍)
