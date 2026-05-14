# MediaCrawler Monitor

MediaCrawler Monitor 是一个本地创作者账号监控面板，用来集中管理抖音、小红书、B 站账号的数据采集、快照保存、数据看板和 CSV 导出。

项目适合先在局域网服务器上部署运行：服务器打开浏览器扫码登录，服务器保存数据，其他设备通过浏览器查看监控面板。等采集流程稳定后，再迁移到云服务器。

## 重要提醒

- 采集前请关闭 VPN、代理、网络加速器。
- 推荐使用中国大陆本地网络直连目标平台。
- 开启 VPN 或代理时，抖音、小红书、B 站更容易出现登录过期、安全校验、412 拒绝访问、作品数量不完整等问题。
- 不建议把本地服务直接暴露到公网。
- 请合理控制采集频率，遵守目标平台规则和项目许可。

## 功能概览

- 添加和管理抖音、小红书、B 站创作者账号。
- 按平台或按单个账号启动采集任务。
- 实时查看采集日志和运行状态。
- 自动保存账号快照，包括粉丝数、获赞数、作品数等指标。
- 在数据看板查看账号指标和近期作品。
- 展开账号下的全部作品数据。
- 导出账号 CSV，包含账号快照和作品指标。
- 采集单个作品，并按需采集和导出评论内容。

## 当前支持

| 平台 | 账号采集 | 单作品采集 | 快照保存 | 全部作品展开 | CSV 导出 |
| --- | --- | --- | --- | --- | --- |
| 抖音 | 支持 | 支持 | 支持 | 支持 | 支持 |
| 小红书 | 支持 | 支持 | 支持 | 支持 | 支持 |
| B 站 | 支持 | 支持 | 支持 | 支持 | 支持 |

## 目录说明

```text
api/
  main.py                  FastAPI 服务入口
  static/monitor.html      监控面板页面
  routers/monitor.py       账号、看板、快照、作品、导出接口
  services/crawler_manager.py 采集进程管理和日志推送
  services/monitor_sync.py 采集数据到监控快照的同步逻辑
database/
  models.py                采集表与监控表模型
  db_session.py            数据库连接和建表逻辑
media_platform/            平台采集适配
store/                     数据入库实现
config/                    本地运行配置
tests/                     关键逻辑测试
```

## 一、本地部署前准备

推荐环境：

- Windows 10 / Windows 11
- Python 3.11
- uv
- PostgreSQL 14 或更高版本（推荐）
- SQLite（轻量本地模式，可选）
- Git
- Chrome / Edge 浏览器

建议先确认命令可用：

```powershell
python --version
uv --version
git --version
psql --version
```

如果没有安装 `uv`，可以先安装 uv，再回到项目目录继续执行后续步骤。

## 二、获取项目代码

```powershell
git clone https://github.com/Yym11345/MediaCrawler-main5-monitor.git
cd MediaCrawler-main5-monitor
```

如果已经下载过项目，进入项目目录后更新代码：

```powershell
git pull
```

## 三、安装 Python 依赖

在项目根目录执行：

```powershell
uv sync
```

安装 Playwright 浏览器依赖：

```powershell
uv run playwright install chromium
```

如果依赖安装失败，先检查网络是否稳定。注意不要通过 VPN 或代理安装后再直接用于采集，采集时仍然需要关闭 VPN/代理。

## 四、配置数据库

项目默认使用 PostgreSQL。轻量本地试用时，也可以通过 `MONITOR_DB_TYPE=sqlite` 切换到 SQLite。

建议：

- 长期使用、数据量较大、后期准备上云：使用 PostgreSQL。
- 本地测试、演示、少量账号：可以使用 SQLite。

### 方案 A：PostgreSQL 模式（推荐）

#### 1. 创建数据库

使用 pgAdmin 或 psql 创建数据库，默认数据库名建议使用：

```sql
CREATE DATABASE media_crawler WITH ENCODING 'UTF8';
```

默认配置如下：

```text
host: localhost
port: 5432
user: postgres
password: 123456
database: media_crawler
```

#### 2. 配置环境变量

复制环境变量模板：

```powershell
copy .env.example .env
```

打开 `.env`，按服务器上的 PostgreSQL 信息修改：

```env
POSTGRES_DB_HOST=localhost
POSTGRES_DB_PORT=5432
POSTGRES_DB_USER=postgres
POSTGRES_DB_PWD=123456
POSTGRES_DB_NAME=media_crawler
MONITOR_DB_TYPE=postgres
SAVE_LOGIN_STATE=true
BROWSER_PROFILE_ROOT=browser_data
```

如果你的 PostgreSQL 密码不是 `123456`，必须改成自己的密码。

#### 3. 初始化数据表

在项目根目录执行：

```powershell
uv run python main.py --init_db postgres
```

启动 API 服务时也会自动检查并创建监控表，但首次部署时建议先执行一次初始化命令。

### 方案 B：SQLite 模式（轻量本地可选）

如果只是本地测试或少量账号使用，可以不安装 PostgreSQL，直接使用 SQLite。

在 `.env` 中设置：

```env
MONITOR_DB_TYPE=sqlite
```

初始化 SQLite 数据表：

```powershell
uv run python main.py --init_db sqlite
```

SQLite 数据文件默认保存在：

```text
database/sqlite_tables.db
```

注意：

- SQLite 和 PostgreSQL 是两套独立数据，不会自动互相同步。
- 从 PostgreSQL 切换到 SQLite 后，页面只会读取 SQLite 中的数据。
- 从 SQLite 切回 PostgreSQL 后，页面只会读取 PostgreSQL 中的数据。
- 修改 `MONITOR_DB_TYPE` 后需要重启 API 服务。
- 如果后期迁移到云端，仍建议使用 PostgreSQL。

### 浏览器登录状态保存

项目默认开启 Playwright persistent profile，用来保存扫码登录后的 Cookie 和浏览器上下文。每个平台使用独立目录，互不影响：

```text
browser_data/dy_user_data_dir
browser_data/xhs_user_data_dir
browser_data/bili_user_data_dir
```

相关 `.env` 配置：

```env
SAVE_LOGIN_STATE=true
BROWSER_PROFILE_ROOT=browser_data
```

如果某个平台一直提示登录过期、账号切换不干净，先停止采集进程和 API 服务，再删除对应平台目录，例如只重置抖音：

```powershell
Remove-Item -Recurse -Force browser_data\dy_user_data_dir
```

`browser_data` 保存服务器登录态，不会提交到 GitHub，也不建议复制给别人。

## 五、启动本地网站

在项目根目录执行：

```powershell
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8080
```

启动成功后打开：

```text
http://<服务器局域网IP>:8080/monitor
```

API 文档地址：

```text
http://<服务器局域网IP>:8080/docs
```

如果 8080 端口被占用，可以换一个端口：

```powershell
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8090
```

然后访问：

```text
http://<服务器局域网IP>:8090/monitor
```

## 六、如何采集账号数据

1. 打开 `http://<服务器局域网IP>:8080/monitor`。
2. 确认 VPN、代理、网络加速器已经关闭。
3. 进入“账号管理”。
4. 选择平台：抖音、小红书或 B 站。
5. 填入创作者主页 URL 或平台用户 ID。
6. 可填写备注名称，方便识别账号。
7. 点击“添加”。
8. 在账号列表中点击该账号右侧的“采集”。
9. 系统会打开浏览器，按页面提示扫码登录或完成平台校验。
10. 不要手动关闭采集浏览器，等待日志显示采集完成、保存快照。
11. 进入“数据看板”查看粉丝、获赞、作品和近期作品数据。

说明：

- 点击单个账号行内的“采集”，只采集该账号。
- 点击页面顶部“立即采集”，会采集当前平台下已启用的账号。
- 账号监控采集默认不采集评论内容，速度更快，也更稳定。
- 作品上限建议先设置为 50 到 300，稳定后再提高。

## 七、如何采集单个作品数据

单个作品采集适合需要评论内容时使用。

1. 进入“数据看板”。
2. 找到“单个作品”区域。
3. 选择平台。
4. 输入作品 URL 或作品 ID。
5. 设置评论上限。
6. 如果需要二级评论，勾选“二级评论”。
7. 点击“采集单个作品”。
8. 采集完成后点击“导出单个作品”。

说明：

- 单个作品导出会保留评论内容。
- 监控账号 CSV 和作品“导出指标”不会导出评论内容。
- 评论数量越多，采集越慢，也越容易触发平台校验。

## 八、数据导出说明

### 账号 CSV

在“数据看板”中点击“导出账号 CSV”。

账号 CSV 包含：

- 账号快照数据
- 作品 ID
- 标题
- 点赞量
- 评论量
- 分享量
- 发布时间
- 作品链接

账号 CSV 不包含评论内容。

### 单个作品 CSV

在“单个作品”区域点击“导出单个作品”。

单个作品 CSV 包含：

- 作品基础指标
- 评论内容
- 评论者
- 评论点赞数
- 评论时间

## 九、日常运行建议

- 每次只运行一个采集任务。
- 不要频繁切换平台账号。
- 不要长时间高频采集同一个平台。
- 账号监控优先采集作品指标，评论内容只在单个作品中按需采集。
- 定期备份 PostgreSQL 数据库或 SQLite 数据文件。
- 局域网使用时建议监听 `0.0.0.0`，只在内网和受信设备中访问。

## 十、数据库备份和恢复

### PostgreSQL 备份和恢复

备份数据库：

```powershell
pg_dump -U postgres -h localhost -p 5432 -d media_crawler -f media_crawler_backup.sql
```

恢复数据库：

```powershell
psql -U postgres -h localhost -p 5432 -d media_crawler -f media_crawler_backup.sql
```

### SQLite 备份和恢复

SQLite 模式下，直接复制数据库文件即可：

```text
database/sqlite_tables.db
```

建议先停止 API 服务和采集进程，再复制这个文件，避免写入过程中备份不完整。

迁移到云服务器时，通常只需要：

1. 在云服务器安装 Python、uv、PostgreSQL 和浏览器。
2. 从 GitHub 拉取项目代码。
3. 导入本地备份的 PostgreSQL 数据。
4. 配置 `.env`。
5. 重新扫码登录平台账号。

## 十一、常见问题

### 1. 页面打不开

确认 API 是否启动成功：

```powershell
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8080
```

确认访问地址是：

```text
http://<服务器局域网IP>:8080/monitor
```

### 2. 数据库连接失败

检查：

- 如果使用 PostgreSQL，检查 PostgreSQL 是否正在运行。
- 如果使用 PostgreSQL，检查 `.env` 中的账号、密码、端口、数据库名是否正确。
- 如果使用 PostgreSQL，检查数据库 `media_crawler` 是否已经创建。
- 如果使用 PostgreSQL，确认执行过 `uv run python main.py --init_db postgres`。
- 如果使用 SQLite，确认 `.env` 中设置了 `MONITOR_DB_TYPE=sqlite`。
- 如果使用 SQLite，确认执行过 `uv run python main.py --init_db sqlite`。

### 3. 扫码后一直等待

检查：

- 手机端是否已经确认登录。
- 打开的浏览器里是否还有滑块、验证码或手机确认。
- 是否开启了 VPN、代理或网络加速器。
- 可以关闭采集任务后重新扫码。

### 4. 出现登录过期、412、安全校验

建议：

- 关闭 VPN、代理、网络加速器。
- 降低作品上限。
- 重新扫码登录。
- 等待一段时间后再采集。
- 尽量使用稳定的本地网络。

### 5. 作品数量不完整

可能原因：

- 平台分页接口限制。
- 账号内容较多，单次采集触发风控。
- 登录态过期。
- 网络不稳定。

建议先把作品上限设置为 50 到 300，分批确认稳定性。

### 6. 粉丝数和主页显示不完全一致

平台接口返回值和页面展示值可能存在延迟或四舍五入口径差异。重新采集账号后会生成新的快照，历史快照不会自动改写。

## 十二、开发检查

运行测试：

```powershell
uv run python -m pytest tests -q
```

检查前端脚本语法：

```powershell
node -e "const fs=require('fs'); const html=fs.readFileSync('api/static/monitor.html','utf8'); const m=html.match(/<script>([\s\S]*)<\/script>/); if(!m) throw new Error('script not found'); new Function(m[1]); console.log('monitor script syntax ok');"
```

## 运行说明

本项目用于个人学习、研究和本地数据监控。请合理控制采集频率，遵守目标平台规则，不要用于大规模采集或任何违反平台条款的行为。

## 许可

项目包含受非商业学习许可约束的代码，完整许可文本见 [LICENSE](LICENSE)，补充说明见 [NOTICE](NOTICE)。
