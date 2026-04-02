# websocket_project

这是一个基于 Django 和 Channels 的 WebSocket 聊天项目。

## 技术栈

- Django
- Channels
- Daphne
- Redis（生产建议）
- Firebase Cloud Messaging / FCM（移动端推送）
- SQLite / PostgreSQL

## 本地启动

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

或者直接一键启动：

```bash
./start_mac.sh
```

启动后访问：

```text
http://127.0.0.1:8000/chat/login/
```

## 可选启动方式

如果你想按 ASGI 方式运行，也可以执行：

```bash
daphne -b 0.0.0.0 -p 8000 websocket_project.asgi:application
```

`start_mac.sh` 只用于本地测试，它会自动：

- 创建或复用虚拟环境
- 安装/更新 `requirements.txt` 依赖
- 首次启动时拉起一个本地 Web 配置页，让你选择 SQLite 或 PostgreSQL，并把选择保存到本地 `.runtime-db.env`
- 自动执行数据库迁移
- 启动 Django 开发服务器

首次选择后，后续再执行 `./start_mac.sh` 不会重复弹出这个页面。

如果你想重新选择数据库，可以执行：

```bash
./scripts/service.sh db-reset
```

然后再次启动项目即可。

## PostgreSQL 配置

如果首次启动时选择 PostgreSQL，脚本会提示你输入这些信息：

- 数据库名
- 用户名
- 密码
- 主机
- 端口
- SSL mode

这些配置会写入本地 `.runtime-db.env`，不会提交到 Git。

你也可以提前用环境变量指定，然后首次启动时直接落盘保存：

```bash
DB_BACKEND=postgres \
DB_NAME=websocket_chat \
DB_USER=postgres \
DB_PASSWORD=your_password \
DB_HOST=127.0.0.1 \
DB_PORT=5432 \
./start_mac.sh
```

## SQLite 迁移到 PostgreSQL

如果你已经在 SQLite 里积累了用户、群聊、私聊、资料、表情等数据，可以直接用下面这条脚本迁移到 PostgreSQL：

```bash
./.venv/bin/python scripts/migrate_sqlite_to_postgres.py \
  --source-sqlite db.sqlite3 \
  --db-name websocket_chat \
  --db-user postgres \
  --db-password your_password \
  --db-host 127.0.0.1 \
  --db-port 5432
```

默认行为：

- 从 SQLite 导出项目数据
- 在 PostgreSQL 上执行迁移建表
- 检查目标 PostgreSQL 是否为空
- 将数据导入 PostgreSQL

如果目标 PostgreSQL 已经有旧数据，并且你确认可以清空它：

```bash
./.venv/bin/python scripts/migrate_sqlite_to_postgres.py \
  --source-sqlite db.sqlite3 \
  --db-name websocket_chat \
  --db-user postgres \
  --db-password your_password \
  --db-host 127.0.0.1 \
  --db-port 5432 \
  --reset-target
```

脚本位置：

```text
scripts/migrate_sqlite_to_postgres.py
```

## 打包 / 恢复头像与媒体文件

数据库迁移只会迁移表里的路径，不会自动带走这些实际文件：

- 用户头像
- 群头像
- 聊天图片
- 聊天文件
- 收藏图片表情
- 站点图标等 `media/` 下的文件

所以在从 SQLite 切到 PostgreSQL，或者从一台机器迁到另一台机器时，建议把 `media/` 一起打包。

打包命令：

```bash
./.venv/bin/python scripts/media_bundle.py pack
```

默认会生成类似：

```text
backups/media_bundle_20260325_230000.tar.gz
```

恢复命令：

```bash
./.venv/bin/python scripts/media_bundle.py restore \
  --archive backups/media_bundle_20260325_230000.tar.gz \
  --replace
```

脚本位置：

```text
scripts/media_bundle.py
```

`start.sh` 仍然保留，等价于执行 `./start_mac.sh`。

## Linux 持续运行部署

Linux 统一使用 `start_linux.sh`。

首次执行下面这条即可完成完整服务部署并持续运行：

```bash
chmod +x start_linux.sh scripts/service.sh
./start_linux.sh
```

如果你希望服务以指定用户运行，例如 `www-data`：

```bash
APP_USER=www-data APP_GROUP=www-data ./start_linux.sh
```

常用命令：

```bash
./start_linux.sh start
./start_linux.sh stop
./start_linux.sh restart
./start_linux.sh status
./start_linux.sh enable
./start_linux.sh disable
./start_linux.sh logs
./start_linux.sh config
./start_linux.sh uninstall
./start_linux.sh serve
```

可选环境变量：

```bash
SERVICE_NAME=websocket-chat
APP_USER=www-data
APP_GROUP=www-data
VENV_PATH=.venv
BIND_HOST=0.0.0.0
PORT=8000
APP_MODULE=websocket_project.asgi:application
MIGRATE_ON_START=1
GEOCODE_PROVIDER=auto
GEOCODE_TIMEOUT=8
AMAP_WEB_API_KEY=你的高德Web服务Key
REDIS_URL=redis://127.0.0.1:6379/1
MOBILE_PUSH_NOTIFICATIONS_ENABLED=1
PUSH_NOTIFY_ONLINE_USERS=0
FIREBASE_CREDENTIALS_FILE=/path/to/firebase-service-account.json
FIREBASE_PROJECT_ID=your-firebase-project-id
```

地理位置反解说明：

- 现在支持通过环境变量切换反向地理编码提供方。
- 默认 `GEOCODE_PROVIDER=auto`，中国大陆坐标且配置了 `AMAP_WEB_API_KEY` 时，会优先走高德，再回退到其他公共接口。
- 国内服务器如果访问国外接口不稳定，建议配置 `AMAP_WEB_API_KEY`。

`start_linux.sh` 默认会执行完整部署流程，脚本会自动：

- 创建虚拟环境
- 在依赖变化时自动安装 `requirements.txt` 依赖
- 启动前自动执行数据库迁移
- 生成 `/etc/systemd/system/websocket-chat.service`
- 生成 `/etc/default/websocket-chat`
- 执行 `systemctl enable` 并启动服务

## Android App / 推送基础

项目现在已经预留了移动端推送后端基础，可配合 Android APK 使用：

- 设备注册接口：`/chat/mobile/devices/register/`
- 设备注销接口：`/chat/mobile/devices/unregister/`
- 消息创建后会自动触发移动端推送逻辑
- 推送默认使用 Firebase Cloud Messaging（FCM）
- 如果未配置 `FIREBASE_CREDENTIALS_FILE`，聊天功能仍正常，推送会自动跳过

建议的 Android 接入方式：

1. App 登录后，把设备 FCM token POST 到 `/chat/mobile/devices/register/`
2. 退出登录或关闭通知时，调用 `/chat/mobile/devices/unregister/`
3. 点击通知后，按消息类型跳转到对应私聊或群聊页面

当前后端推送判定默认策略：

- 用户当前在线时默认不重复发推送
- 可通过 `PUSH_NOTIFY_ONLINE_USERS=1` 改为在线也推送

## Capacitor 多端封装

仓库新增了一个 `capacitor-app/` 目录，用于把当前 Django 聊天站点封装成 iOS / Android App。

推荐流程：

```bash
cd capacitor-app
npm install
npm run sync:prod
npm run add:android
npm run add:ios
```

说明：

- 默认正式域名已设置为 `https://chat.6143443.xyz/chat/login/`
- `CAP_SERVER_URL` 仍可指向其他 Django 登录页或聊天入口页
- Android 模拟器本地联调通常使用 `http://10.0.2.2:8000/chat/login/`
- 可直接运行 `npm run sync:local-android`
- Django 页面会自动注入 `static/mobile-bridge.js`
- 当页面运行在 Capacitor 原生容器内时，会尝试申请通知权限并回传 push token 给 Django
- Android 详细构建说明见 `capacitor-app/android/README.md`

更多说明见：

```text
capacitor-app/README.md
```

## 当前依赖

- Django
- channels
- channels_redis
- daphne
- firebase-admin
- requests
- Pillow
- psycopg

## 说明

- 默认数据库是本地 `db.sqlite3`，也支持切换到 PostgreSQL
- WebSocket 路由为 `/ws/chat/<room_name>/`
- 未配置 `REDIS_URL` 时，Channels 默认使用内存通道层，适合本地开发
- 配置 `REDIS_URL` 后会自动切换为 Redis channel layer，适合生产部署
