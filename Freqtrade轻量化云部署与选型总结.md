# Freqtrade 轻量化部署与本次选型总结

> 文档日期：2026-08-09（Asia/Shanghai）
> Fork：<https://github.com/Sww-echo/freqtrade>
> 本地克隆：`/Users/leeee/project/freqtrade`
> 当前分支：`develop`
> 当前提交：`89d469fe638eaf116d45a8f92598aeed4d9f6dde`

## 1. 最终选型

本次讨论后的结论是：

> **Freqtrade 作为唯一的策略、回测、Dry-run、实盘执行和风控核心；TradingView 只用于看盘与 Pine Script 策略原型，不直接产生实盘订单。**

选择 Freqtrade 的原因：

- 使用 Python 编写策略，比 Pine Script 更容易接入 pandas、NumPy、TA-Lib、统计模型和自定义数据。
- 自带历史数据下载、回测、Dry-run、实盘、SQLite、REST API、Telegram 和 FreqUI。
- 已经处理订单状态、重复执行、重启恢复、交易所精度、限频和持仓对账；不需要自己重新实现一个 Webhook 交易系统。
- 单机最小架构只需一个 Freqtrade 进程和一个 `user_data` 持久化目录。

TradingView 保留以下职责：

- 观察行情和画图。
- 快速编写 Pine Script 验证想法。
- 对成熟策略进行视觉对照。

不要让 TradingView 和 Freqtrade 同时向同一个实盘账户产生交易信号，否则两个系统的仓位状态可能不一致。

## 2. 开源项目的能力边界

Freqtrade 开源项目提供完整的交易闭环，但“项目支持”不等于当前轻量实例已经启用了全部可选组件。

核心能力包括：

- Python 自定义策略。
- 现货和受支持交易所的合约交易。
- 历史行情下载、回测和结果分析。
- Dry-run 与实盘。
- 止损、ROI、移动止损、仓位控制和保护规则。
- SQLite 持久化和 REST API。
- Telegram 管理。
- Hyperopt 和 FreqAI。

需要注意：

- FreqUI 是官方独立开源项目，但官方 Docker 镜像和源码 Dockerfile 都会安装它。
- Hyperopt、FreqAI、绘图和 Jupyter 等能力可能需要对应镜像或额外依赖。
- FreqUI 3.1.1 的官方构建没有中文语言资源，可使用浏览器翻译；这不影响策略和数据。
- 支持某交易所不代表云服务器所在地区一定能访问，也不代表账户在当地可以合法使用。

## 3. 本地轻量版是如何实现的

本地验证没有裁剪 Freqtrade 源码，而是安装标准核心，只启用必要模块：

```text
一个 Freqtrade 进程
├── Worker 调度
├── CCXT 交易所连接
├── 一个 Python 策略
├── Dry-run 模拟订单
├── SQLite
├── FastAPI / Uvicorn
└── FreqUI
```

没有使用：

- Redis、Kafka、Celery。
- PostgreSQL。
- 独立 Node.js 前端服务。
- 多账户或多机器人。
- FreqAI、Jupyter、GPU。
- 真实交易密钥、合约和杠杆。

本地验证结果：

- Freqtrade：2026.7。
- FreqUI：3.1.1。
- Python：3.12。
- 模式：Gate 现货 Dry-run。
- 虚拟余额：1,000 USDT。
- 交易对：BTC/USDT、ETH/USDT。
- 数据：最近 30 天，5m 各 8,742 根，1h 各 728 根。
- 示例 EMA 策略回测：335 笔，收益率 -7.10%，最大回撤约 7.13%。

负收益是合理的验证结果：示例策略只用于证明“数据 → 指标 → 信号 → 回测 → FreqUI”链路正常，不能直接用于实盘。

本地曾尝试的数据源：

- Binance：当前本地网络返回 HTTP 451 地区限制，未绕过限制。
- OKX：普通公共请求可达，但 Python 异步连接不稳定。
- Kraken：可访问，但 Freqtrade 要求从逐笔成交重建历史 K 线，初始下载较慢。
- Gate：公共行情和 Freqtrade K 线下载成功，因此用于演示。

云服务器出口 IP 与本地不同，部署时必须重新做只读可用性检查，并遵守交易所和所在司法辖区的规则。

## 4. 云服务器轻量架构

第一阶段推荐：

```text
浏览器
  │
  │ SSH 隧道
  ▼
云服务器 127.0.0.1:8080
  │
  └── Docker Compose
      └── Freqtrade 单容器
          ├── 策略
          ├── Dry-run
          ├── SQLite
          ├── REST API
          └── FreqUI
```

建议最低配置：

- Linux 云服务器，建议 Ubuntu 22.04/24.04 LTS 或同类发行版。
- 2 vCPU、2 GB RAM；如果编译 fork 镜像，建议临时使用更多内存或 swap。
- 至少 10 GB 可用磁盘，虽然 Freqtrade 官方最低需求更低，但镜像、日志和历史数据需要余量。
- Docker Engine 和 Docker Compose plugin。
- 系统启用 NTP 时间同步。
- 防火墙只开放 SSH；第一阶段不要向公网开放 8080。

## 5. 先选清楚：官方镜像还是 fork 源码

### 方案 A：官方 stable 镜像，推荐首次部署

仓库自带的 `docker-compose.yml` 默认使用：

```yaml
image: freqtradeorg/freqtrade:stable
```

优点：

- 最快、最稳定、无需在服务器编译。
- 容器体积和部署故障面更小。
- 适合先验证交易所网络、配置、策略、回测和 Dry-run。

重要边界：**这种方式虽然在 fork 仓库目录中运行 Docker Compose，但实际执行的是官方 stable 镜像，不会包含你以后对 fork 核心源码的修改。** 自定义策略和配置仍会从宿主机 `user_data` 挂载进入容器。

### 方案 B：从 fork 源码构建

只有修改了 Freqtrade 核心代码时才需要。把 `docker-compose.yml` 中的镜像和构建部分调整为：

```yaml
services:
  freqtrade:
    image: sww-echo-freqtrade:local
    build:
      context: .
      dockerfile: ./Dockerfile
```

然后运行：

```bash
docker compose build --pull
docker compose up -d
```

当前 fork 默认分支是 `develop`，可能包含破坏性变化。用于真实资金前应固定经过验证的提交，而不是无条件跟随最新 `develop`。

## 6. 云服务器部署步骤

以下步骤默认使用方案 A，先部署官方 stable 镜像和 Dry-run。

### 6.1 克隆 fork

```bash
git clone https://github.com/Sww-echo/freqtrade.git
cd freqtrade
git rev-parse HEAD
```

记录输出的提交号，便于后续复现和回滚。

### 6.2 检查 Docker

请按 Docker 官方文档为服务器发行版安装 Docker Engine 和 Compose plugin，然后验证：

```bash
docker --version
docker compose version
```

不要通过来源不明的安装脚本部署 Docker。

### 6.3 拉取镜像并创建用户目录

```bash
docker compose pull
docker compose run --rm freqtrade create-userdir --userdir user_data
docker compose run --rm freqtrade new-config --config user_data/config.json
```

交互配置第一阶段建议：

- `dry_run: true`
- 现货模式。
- 只选择一个当前云服务器可访问、账户所在地允许使用的交易所。
- `stake_currency: USDT`
- `stake_amount: 50`
- `max_open_trades: 2`
- `dry_run_wallet: 1000`
- 只允许 BTC/USDT、ETH/USDT。
- 启用 API/FreqUI。
- Telegram 暂时关闭。

### 6.4 Docker 中的 API 监听配置

容器内部必须监听 `0.0.0.0`，宿主机再由 Compose 限制为 `127.0.0.1`：

```json
"api_server": {
  "enabled": true,
  "listen_ip_address": "0.0.0.0",
  "listen_port": 8080,
  "verbosity": "error",
  "enable_openapi": false,
  "jwt_secret_key": "替换为至少32字符的随机值",
  "ws_token": "替换为另一段随机值",
  "CORS_origins": [],
  "username": "替换用户名",
  "password": "替换强密码"
}
```

可在服务器生成随机值：

```bash
openssl rand -hex 32
```

仓库的 Compose 端口映射应保持：

```yaml
ports:
  - "127.0.0.1:8080:8080"
```

不要为了远程访问把它改成 `8080:8080`。

### 6.5 数据库和启动状态

首次 Dry-run 建议把 Compose 命令中的数据库改为：

```text
sqlite:////freqtrade/user_data/tradesv3.dryrun.sqlite
```

配置中建议使用：

```json
"initial_state": "stopped",
"dry_run": true,
"force_entry_enable": false
```

容器启动后先检查状态，再通过 FreqUI 明确启动机器人。

### 6.6 自定义策略

策略文件放在：

```text
user_data/strategies/MyStrategy.py
```

同时把 `docker-compose.yml` 中的：

```text
--strategy SampleStrategy
```

改成：

```text
--strategy MyStrategy
```

策略类名必须与命令中的名字一致。不要直接实盘运行仓库的 SampleStrategy 或未经验证的第三方策略。

### 6.7 下载少量数据

以下以 Gate 为演示，云服务器应先确认交易所可达且允许使用：

```bash
docker compose run --rm freqtrade download-data \
  --config /freqtrade/user_data/config.json \
  --exchange gate \
  --pairs BTC/USDT ETH/USDT \
  --days 30 \
  --timeframes 5m 1h
```

### 6.8 回测

```bash
docker compose run --rm freqtrade backtesting \
  --config /freqtrade/user_data/config.json \
  --strategy MyStrategy \
  --timeframe 5m
```

至少检查：

- 手续费、滑点和资金费率是否被正确考虑。
- 最大回撤、交易次数、收益集中度和连续亏损。
- 样本外、walk-forward 和跨币种表现。
- lookahead analysis 与 recursive analysis。

### 6.9 启动 Dry-run

```bash
docker compose up -d
docker compose ps
docker compose logs -f freqtrade
```

日志文件也会保存在：

```text
user_data/logs/freqtrade.log
```

### 6.10 通过 SSH 隧道访问 FreqUI

在自己的电脑执行：

```bash
ssh -L 8080:127.0.0.1:8080 <服务器用户>@<服务器IP>
```

保持 SSH 会话打开，然后浏览器访问：

```text
http://127.0.0.1:8080
```

这样 FreqUI 不需要直接暴露到公网，也不需要第一阶段增加 Nginx、域名和 HTTPS。

## 7. 轻量版当前不部署的组件

- PostgreSQL、Redis、消息队列。
- FreqAI、GPU、Jupyter。
- 多机器人和多账户控制台。
- 合约、杠杆和做空。
- Telegram。
- 公网 FreqUI。
- TradingView Webhook 执行器。
- 自动跟随 `develop` 更新。

这些功能不是被删除，只是暂不启用。需求出现后再增加，避免首次部署同时引入过多故障点。

## 8. 安全与实盘门槛

切换实盘前必须完成：

1. Dry-run 覆盖数周并经历不同波动状态。
2. 使用独立交易所子账户。
3. API 只开放读取和交易权限，永久关闭提现权限。
4. 如果交易所支持，设置云服务器固定出口 IP 白名单。
5. 更换 FreqUI 用户名、密码、JWT secret 和 WebSocket token。
6. 验证断线、重启、订单撤销、部分成交和仓位对账。
7. 配置单笔风险、最大持仓、日亏损上限和最大回撤熔断。
8. 合约策略额外验证 mark price、资金费率、维持保证金、强平和 ADL。
9. 备份 `user_data`，但不要把 API 密钥和带密钥的配置提交到 Git。
10. 检查服务器时钟、磁盘、内存、日志轮转和安全更新。

## 9. 目录与备份

云服务器需要持久化的主要内容：

```text
user_data/
├── config.json
├── strategies/
├── data/
├── backtest_results/
├── logs/
└── tradesv3.dryrun.sqlite
```

建议：

- 策略单独纳入私有版本控制或安全备份。
- `config.json` 含密钥时不得提交。
- 定期备份 SQLite、策略和配置。
- 备份数据库前暂停机器人，或使用 SQLite 的一致性备份方式。

本仓库 `.gitignore` 已忽略 `config*.json`、`*.sqlite`、`.env` 和大部分 `user_data`，但提交前仍必须执行：

```bash
git status --short
git diff --cached
```

## 10. 更新与回滚

### 使用官方镜像

```bash
docker compose pull
docker compose up -d
```

更新前先阅读 Changelog、备份 `user_data`，并记录旧镜像标签。真实资金环境建议固定明确版本，不要长期使用会自动漂移的标签。

### 使用 fork 源码构建

```bash
git status --short
git pull --ff-only
docker compose build --pull
docker compose up -d
```

必须在 Dry-run 环境验证新提交后再更新实盘实例。回滚时切回已记录的提交并重新构建。

## 11. 推荐实施顺序

1. 云服务器安装 Docker 和 Compose。
2. 克隆 fork，但先使用官方 stable 镜像。
3. 创建 `user_data` 和 Dry-run 配置。
4. 只开放两个现货交易对。
5. 下载少量数据并完成回测。
6. 通过 SSH 隧道查看 FreqUI。
7. Dry-run 运行数周。
8. 编写并验证自己的策略。
9. 只有需要修改核心时，才切换到 fork 源码构建。
10. 完成安全清单后，使用独立子账户和极小资金逐步进入实盘。

## 12. 结论

轻量化不等于删除功能，而是：

> **保留标准 Freqtrade 核心和官方 FreqUI，只运行单容器、单交易所、少量交易对、SQLite 和 Dry-run；高级组件在确有需求时再启用。**

这种方式能最大限度复用 Freqtrade 已验证的订单、持仓、恢复和交易所适配逻辑，同时让首次云部署保持简单、可检查、可回滚。
