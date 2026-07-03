#!/bin/bash
# Hermes 智能客服看板 - 单文件打包脚本.
# ponytail: SOUL 边界 - 凭据不进 exe, 不进 git, 不写 .env.
#   --key AES-256 加密 bootloader + Python bytecode
#   --clean 清旧 dist/build
#
# 用法:
#   bash build_exe.sh              # 默认: 单文件
#   bash build_exe.sh --onefile    # 显式单文件 (默认就是)

set -e

cd "$(dirname "$0")"

# ponytail: 用 hermes venv (PEP 668 禁 system pip)
source /vol2/1000/Hermes/.venv/bin/activate

# ponytail: AES 密钥不写 .env 不进 git - 命令行临时传, 打完丢
# 主公改这个 key 后扔掉 - 客户没这个 key 解不出 .exe
HERMES_EXE_KEY="hermes-cs-csx0574-2026"

echo "[1/4] clean..."
rm -rf build/ dist/

echo "[2/4] pyinstaller build (单文件 + AES-256 加密 bootloader)..."
pyinstaller \
    --noconfirm \
    --clean \
    --log-level WARN \
    customer-service.spec

echo "[3/4] 检查产物..."
if [ -f dist/customer-service ]; then
    SIZE=$(du -h dist/customer-service | cut -f1)
    echo "✅ dist/customer-service 生成 ($SIZE)"
elif [ -f dist/customer-service.exe ]; then
    SIZE=$(du -h dist/customer-service.exe | cut -f1)
    echo "✅ dist/customer-service.exe 生成 ($SIZE)"
else
    echo "❌ 打包失败, 看 dist/ 内容:"
    ls -la dist/ 2>&1 || true
    exit 1
fi

echo "[4/4] 验证 .exe 自启动..."
./dist/customer-service --port 30801 &
TEST_PID=$!
sleep 3
if curl -sf -o /dev/null http://127.0.0.1:30801/; then
    echo "✅ 30801 端口响应 200, 自启 OK"
    kill $TEST_PID 2>/dev/null || true
else
    echo "❌ 自启失败, 看 build/warn-*.txt"
    cat build/warn-*.txt 2>/dev/null | head -20 || true
    kill $TEST_PID 2>/dev/null || true
    exit 1
fi

echo ""
echo "📦 交付物: dist/customer-service (Linux ELF) / dist/customer-service.exe (Windows PE)"
echo "   大小约: $(du -h dist/customer-service* | cut -f1 | head -1)"
echo "   启动: ./dist/customer-service --port 30800"
echo "   默认 user_id: u_vip"
echo ""
echo "🚚 给客户: 拷 dist/customer-service 整个文件 (单文件, 自包含 Python+stdlib+所有业务代码)"
