#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export COPYFILE_DISABLE=1

PYTHON_BIN="${PYTHON_BIN:-python3}"
PACKAGE_BASENAME="${PACKAGE_BASENAME:-jien-cross-border-fingerprint-browser-macos}"
STAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE_ROOT="$ROOT_DIR/release"
PACKAGE_NAME="${PACKAGE_BASENAME}-${STAMP}"
STAGE="$RELEASE_ROOT/$PACKAGE_NAME"
ZIP_PATH="$RELEASE_ROOT/${PACKAGE_NAME}.zip"

echo "==> Verifying Python sources"
"$PYTHON_BIN" -m py_compile app/main.py app/camoufox_core.py app/camoufox_fleet_io.py app/runner.py app/ecommerce.py app/proxy_check.py app/store.py app/config.py

echo "==> Building frontend"
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm install && npm run build)
elif [ ! -f "frontend/dist/index.html" ]; then
  echo "npm is not available and frontend/dist/index.html is missing" >&2
  exit 1
else
  echo "npm is not available; using existing frontend/dist"
fi

echo "==> Creating clean release directory"
rm -rf "$STAGE"
mkdir -p "$STAGE"
mkdir -p "$STAGE/data/profiles" "$STAGE/data/screenshots" "$STAGE/data/exports" "$STAGE/logs"

rsync -a \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='._*' \
  app "$STAGE/"

mkdir -p "$STAGE/frontend"
rsync -a \
  --exclude='node_modules/' \
  --exclude='._*' \
  frontend/index.html \
  frontend/package.json \
  frontend/package-lock.json \
  frontend/tsconfig.json \
  frontend/vite.config.ts \
  frontend/public \
  frontend/src \
  frontend/dist \
  "$STAGE/frontend/"

rsync -a --exclude='._*' requirements.txt config.yaml run.sh README.md "$STAGE/"
rsync -a --exclude='._*' scripts "$STAGE/"

"$PYTHON_BIN" - <<'PY' "$STAGE/data/state.json"
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(
    json.dumps(
        {
            "version": 1,
            "profiles": [],
            "events": [],
            "settings": {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "packaged": True,
                "note": "Clean release state. Real browser profiles and proxy credentials are intentionally not included.",
            },
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
PY

cat > "$STAGE/install.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m camoufox fetch

echo "Install complete. Start with ./start.sh, then open http://127.0.0.1:8138/"
SH

cat > "$STAGE/start.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-1}"
exec ./run.sh
SH

cat > "$STAGE/start.command" <<'SH'
#!/usr/bin/env bash
cd "$(dirname "$0")"
./start.sh
SH

cat > "$STAGE/README_PACKAGE.md" <<'MD'
# 极恩跨境指纹浏览器发布包

这是一个干净发布包，包含后端、已构建前端、配置文件和启动脚本。

## 不包含的内容

- `.venv`
- `frontend/node_modules`
- 真实 `data/state.json`
- Camoufox 浏览器持久目录、cookies、登录状态
- 代理账号密码
- 日志与截图

这些内容默认排除，避免把本机账号环境和敏感数据一起打包。

## 首次安装

```bash
./install.sh
```

安装完成后启动：

```bash
./start.sh
```

或者在 macOS 里双击 `start.command`。

打开：

```text
http://127.0.0.1:8138/
```

## 迁移账号

进入系统后可在页面顶部的“数据迁移”区域导入其他 Camoufox 项目目录，例如：

```text
/Volumes/Rtl9210/camoufox-fleet-local
```

页面里的“导出数据”会生成 zip 数据包。该数据包可能包含代理信息、cookies 和登录状态，请不要分享给别人。
MD

chmod +x "$STAGE/run.sh" "$STAGE/install.sh" "$STAGE/start.sh" "$STAGE/start.command"
find "$STAGE" -name '._*' -delete 2>/dev/null || true

echo "==> Writing manifest"
"$PYTHON_BIN" - <<'PY' "$STAGE" "$PACKAGE_NAME"
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

stage = Path(sys.argv[1])
name = sys.argv[2]
files = []
for path in sorted(stage.rglob("*")):
    if path.is_file():
        files.append(str(path.relative_to(stage)))

(stage / "manifest.json").write_text(
    json.dumps(
        {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entrypoint": "start.sh",
            "url": "http://127.0.0.1:8138/",
            "sensitive_data_included": False,
            "file_count": len(files),
            "files": files,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
PY

echo "==> Creating zip"
find "$STAGE" -name '._*' -delete 2>/dev/null || true
rm -f "$ZIP_PATH"
(cd "$RELEASE_ROOT" && /usr/bin/zip -qry "${PACKAGE_NAME}.zip" "$PACKAGE_NAME")

echo "==> Writing checksum"
shasum -a 256 "$ZIP_PATH" > "${ZIP_PATH}.sha256"

echo
echo "Release directory: $STAGE"
echo "Zip package:       $ZIP_PATH"
echo "Checksum:          ${ZIP_PATH}.sha256"
