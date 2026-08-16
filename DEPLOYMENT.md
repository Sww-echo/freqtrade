# Freqtrade / Frequi 部署与更新指南

本文说明如何发布前端 `Sww-echo/frequi`、后端 `Sww-echo/freqtrade`，以及如何在 2C2G 云服务器上更新服务。

## 1. 工作方式

云服务器不负责构建 Docker 镜像，只负责从 GitHub Container Registry（GHCR）拉取镜像并启动容器。

| 项目 | GitHub 发布分支 | GHCR 镜像 |
| --- | --- | --- |
| 前端 Frequi | `main` | `ghcr.io/sww-echo/frequi:latest` |
| 后端 Freqtrade | `develop` | `ghcr.io/sww-echo/freqtrade:latest` |

合并到对应分支后，GitHub Actions 会自动构建并推送镜像。只有镜像发布工作流显示成功后，才在服务器执行更新。

## 2. 第一次部署

如果 GHCR 镜像是私有的，在服务器登录一次。需要使用具备 `read:packages` 权限的 GitHub Personal Access Token；不要使用 Codecov Token。

```bash
echo "$GHCR_PAT" | docker login ghcr.io -u Sww-echo --password-stdin
```

进入前端和后端各自的 Compose 目录，执行：

```bash
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
```

## 3. 日常更新流程

### 3.1 只修改代码

1. 本地修改代码并提交。
2. 创建或更新 PR。
3. 等待检查通过。
4. 前端 PR 合并到 `main`；后端 PR 合并到 `develop`。
5. 等待 `Publish ... Image` 工作流成功。
6. SSH 登录服务器，分别执行：

```bash
cd /path/to/frequi
docker compose pull
docker compose up -d --remove-orphans

cd /path/to/freqtrade
docker compose pull
docker compose up -d --remove-orphans
```

### 3.2 修改 Compose、环境变量或端口

除了拉取镜像，还要把新的 `docker-compose.yml` 或配置文件同步到服务器：

```bash
git pull --ff-only
docker compose pull
docker compose up -d --remove-orphans
```

如果 `.env`、API 密钥、域名或端口发生变化，需要先手动更新服务器上的配置，再执行 `docker compose up -d`。

## 4. 更新后检查

```bash
docker compose ps
docker compose logs --tail=100
```

确认容器状态为 `Up`，前端页面可以访问，后端 API 和交易服务日志没有持续报错。

## 5. 回滚

发布工作流同时推送了 commit SHA 标签。例如：

```text
ghcr.io/sww-echo/freqtrade:<commit-sha>
ghcr.io/sww-echo/frequi:<commit-sha>
```

需要回滚时，将服务器 Compose 文件中的 `:latest` 改为目标 SHA，然后执行：

```bash
docker compose pull
docker compose up -d --remove-orphans
```

## 6. 数据安全注意事项

- 不要执行 `docker compose down -v`，这可能删除数据库和用户数据卷。
- 后端当前 Compose 使用 `tradesv3.dryrun.sqlite`，请确认这是预期的 dry-run 数据库。
- 更新镜像只会替换容器，不应删除绑定挂载的数据目录。
- 不要把 GitHub、GHCR 或 Codecov Token 提交到仓库或发送到聊天中；已经暴露的 Token 应立即撤销并重新生成。

## 7. 常见问题

### `docker compose pull` 无权限

确认服务器已经登录 GHCR，并且 Token 具备 `read:packages` 权限。如果仓库或镜像是私有的，Token 还需要能够访问对应的私有资源。

### 拉取后服务仍是旧版本

确认 GitHub Actions 的镜像发布任务已经成功，然后执行：

```bash
docker compose pull
docker compose up -d --force-recreate
```

### GitHub Actions 失败

优先查看对应的 `Publish ... Image` 工作流。前端和后端的测试、文档、Codecov 等检查与镜像发布是不同任务；需要先区分是镜像发布失败，还是仓库其他检查失败。

## 8. 最简更新口诀

```text
代码合并到正确分支
→ Actions 构建 GHCR 镜像
→ 服务器 docker compose pull
→ 服务器 docker compose up -d
```
