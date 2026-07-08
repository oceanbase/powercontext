# Docker 目录说明

本目录包含 PowerMem Server 的 Docker 相关文件，可用于构建镜像、启动 `powermem-server`，或通过 Docker Compose 同时启动 `powermem-server` 与内置 `seekdb`。

英文说明见 [README.md](./README.md)，更完整的英文 Docker 部署指南见 [DOCKER.md](./DOCKER.md)。

## 文件说明

- `Dockerfile`：PowerMem Server 的多阶段 Docker 构建文件。
- `docker-compose.yml`：带 `seekdb` 的 Docker Compose 配置。
- `docker-entrypoint.sh`：容器入口脚本。
- `.dockerignore`：Docker 构建时排除的文件。
- `DOCKER.md`：更完整的 Docker 部署说明。

## 快速开始

所有命令都在项目根目录执行。

### 1. 准备配置文件

```bash
cp .env.example docker/.env
# 编辑 docker/.env，设置使用的 LLM provider、model、API key 和 base URL。
```

### 2. 使用 Docker Compose 启动 PowerMem Server 和 seekdb（推荐）

```bash
# 不设置 seekdb root 密码（默认）
docker compose -f docker/docker-compose.yml up -d --build

# 通过命令行设置 seekdb root 密码
SEEKDB_ROOT_PASSWORD=your_password docker compose -f docker/docker-compose.yml up -d --build

# 或先导出环境变量
export SEEKDB_ROOT_PASSWORD=your_password
docker compose -f docker/docker-compose.yml up -d --build
```

Compose 会通过 `env_file` 读取 `docker/.env`，用于 LLM、embedding、Server 等应用配置；Compose 会有意覆盖 `DATABASE_PROVIDER` 和 `OCEANBASE_*`，让 `powermem-server` 连接内置 `seekdb` 容器。如果需要连接已有数据库服务，请使用单容器部署或自定义 Compose override。

### 3. 验证服务

```bash
curl http://localhost:8848/api/v1/system/health
```

常用入口：

- PowerMem Server API：`http://localhost:8848`
- 健康检查：`http://localhost:8848/api/v1/system/health`
- seekdb MySQL 端口：`localhost:2881`
- seekdb Web Dashboard：`http://localhost:2886`

### 4. 查看日志和停止服务

```bash
docker compose -f docker/docker-compose.yml logs -f powermem-server
docker compose -f docker/docker-compose.yml down
```

`seekdb` 数据会保存在 Docker volume `docker_seekdb_data` 中。若要同时删除数据卷，请谨慎执行：

```bash
docker compose -f docker/docker-compose.yml down -v
```

## 单容器部署

如果 `.env` 已配置外部数据库，或者不希望启动内置 `seekdb`，可以只运行 `powermem-server` 容器。

```bash
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .

docker run -d \
  --name powermem-server \
  -p 8848:8848 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest
```

## seekdb 连接方式

### 默认无密码

```bash
mysql -h localhost -P 2881 -u root
```

### 设置了 `SEEKDB_ROOT_PASSWORD`

```bash
mysql -h localhost -P 2881 -u root -p
# 根据提示输入密码
```

seekdb Web Dashboard：

- 地址：`http://localhost:2886`
- 用户名：`root`
- 密码：与 `SEEKDB_ROOT_PASSWORD` 相同；默认未设置。

## 默认配置

PowerMem Server：

- Host：`0.0.0.0`
- Port：`8848`
- Workers：`4`
- Authentication：默认关闭
- CORS：默认允许所有来源

seekdb：

- 数据库：`powermem`
- CPU：4 核
- 内存：4 GB
- 数据持久化：Docker volume `docker_seekdb_data`
- 密码：由 `SEEKDB_ROOT_PASSWORD` 控制；默认未设置

## 注意事项

- Docker 命令应在项目根目录执行，不要在 `docker/` 目录内执行。
- Docker 构建上下文是项目根目录，因此 Dockerfile 中的路径按项目根目录解析。
- Compose 使用的 `.env` 应复制到 `docker/.env`，并通过 `env_file` 注入容器环境变量。
- 如果修改 `SEEKDB_ROOT_PASSWORD`，建议停止服务后用新密码重新启动。
- macOS 上 Docker 版本高于 4.9.0 时，`seekdb` 可能存在兼容性问题；如遇到问题，可考虑使用较低版本 Docker。
