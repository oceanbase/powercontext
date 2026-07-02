# PowerMem 基准测试 {#powermem-benchmark}

一个全面的 PowerMem 基准测试套件，包括用于管理记忆的 REST API 服务器和基于 LOCOMO 数据集的负载测试工具。

## 概述 {#overview}

PowerMem 基准测试套件由两个主要组件组成：

1. **基准测试服务器** (`benchmark/server/`): 一个基于 FastAPI 的 REST API 服务器，提供以下功能：
   - 记忆存储和管理
   - 语义搜索功能
   - Token 使用量跟踪
   - 支持多种数据库后端（OceanBase, PostgreSQL）

2. **负载测试工具** (`benchmark/locomo/`): 一个全面的基准测试工具，功能包括：
   - 测试记忆添加和搜索性能
   - 使用多种指标评估响应质量
   - 测量延迟和 Token 消耗
   - 使用 LOCOMO 数据集进行真实场景测试

## 快速开始 {#quick-start}

### 1. 启动基准测试服务器 {#1-start-the-benchmark-server}
```bash
# 安装依赖
pip install -e .

# 配置环境（使用项目根目录 .env）
cp .env.example .env
# 编辑项目根目录的 .env 并填写您的设置

# 启动服务器
uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000 --reload
```
### 2. 运行负载测试 {#2-run-load-tests}
```bash
# 安装压测依赖
pip install -r benchmark/locomo/requirements.txt

# 配置环境
cd benchmark/locomo
cp .env.example .env
# 编辑 .env 并填写 API key 和服务器 URL

# 运行测试
bash run.sh results
```
## 基准测试服务器 {#benchmark-server}

### 前置条件 {#prerequisites}

- Python 3.11 或更高版本
- 用于依赖管理的 pip 或 poetry
- LLM 和 Embedding API 密钥（如 OpenAI、Qwen 等——参见根目录下的 `.env.example`）
- 数据库：OceanBase、PostgreSQL 或 SQLite（取决于您的配置）

### 安装 {#installation}

1. **安装依赖**

   在项目根目录运行：
   ```bash
   pip install -e .
   ```
或者安装特定依赖项：
   ```bash
   pip install fastapi uvicorn powermem
   ```
2. **配置环境变量**

   复制项目根目录下的示例环境文件：
   ```bash
   cp .env.example .env
   ```
编辑项目根目录下的 `.env` 文件并进行配置：
- `LLM_API_KEY` 和 `EMBEDDING_API_KEY`（必填）
- `DATABASE_PROVIDER` 和数据库连接设置（OceanBase、PostgreSQL 或 SQLite）
- 其他选项请参考根目录下的 `.env.example`

查看根目录下的 `.env.example` 以获取所有可用的配置选项。

### 配置 {#configuration}

所有配置均通过环境变量完成。服务器会从项目根目录加载 `.env` 文件（与主 PowerMem 应用相同）。

#### 必填环境变量 {#required-environment-variables}

- `LLM_API_KEY`：您的 LLM API 密钥（或设置 `OPENAI_API_KEY` 用于 OpenAI）
- `EMBEDDING_API_KEY`：您的 Embedding API 密钥（或设置 `OPENAI_API_KEY` 用于 OpenAI）

#### 可选环境变量 {#optional-environment-variables}

所有选项与主 PowerMem 应用相同。完整列表请参考根目录下的 `.env.example`。示例：

- `DATABASE_PROVIDER`：`oceanbase`、`postgres` 或 `sqlite`（默认值：`sqlite`）
- `LLM_PROVIDER` / `LLM_MODEL` / `LLM_TEMPERATURE`：LLM 设置
- `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIMS`：Embedding 设置

在基准测试服务器上，**始终启用** Token 计数功能（没有环境变量可以禁用它）。

### 启动服务器 {#starting-the-server}

#### 方法 1：使用 uvicorn（推荐） {#method-1-using-uvicorn-recommended}

从项目根目录运行：
```bash
uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000 --reload
```
`--reload` 标志在开发过程中启用自动重载。

#### 方法 2：生产模式 {#method-2-production-mode}

对于具有多个工作进程的生产环境：
```bash
uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000 --workers 4
```
#### 方法 3：使用 Python 模块 {#method-3-using-python-module}
```bash
python -m uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000
```
#### 方法 4：从服务器目录运行 {#method-4-from-server-directory}

如果您位于 `benchmark/server` 目录：
```bash
cd benchmark/server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
### 访问 API {#accessing-the-api}

一旦服务器启动后，你可以访问以下内容：

- **备用 API 文档 (ReDoc)**: http://localhost:8000/redoc
- **API 根路径**: http://localhost:8000/

### API 端点 {#api-endpoints}

服务器提供以下主要端点：

| 方法   | 端点                        | 描述                     |
|--------|-----------------------------|--------------------------|
| `POST` | `/memories`                 | 创建新的记忆             |
| `GET`  | `/memories`                 | 获取所有记忆（支持过滤） |
| `GET`  | `/memories/{memory_id}`     | 获取特定记忆             |
| `PUT`  | `/memories/{memory_id}`     | 更新记忆                 |
| `DELETE` | `/memories/{memory_id}`   | 删除记忆                 |
| `DELETE` | `/memories`               | 删除所有记忆（支持过滤） |
| `POST` | `/search`                   | 搜索记忆                 |
| `GET`  | `/memories/{memory_id}/history` | 获取记忆历史         |
| `POST` | `/reset`                    | 重置所有记忆             |
| `POST` | `/configure`                | 更新服务器配置           |
| `GET`  | `/token_count`              | 获取 Token 使用统计      |
| `POST` | `/reset_token_count`        | 重置 Token 计数          |

## 压力测试 (LOCOMO) {#load-testing-locomo}

LOCOMO 基准测试工具使用 LOCOMO 数据集对记忆系统进行全面评估。它测试记忆的添加和搜索能力，并衡量性能、质量和资源消耗。

### 压力测试的先决条件 {#prerequisites-for-load-testing}

1. **基准测试服务器必须运行**
   - 首先启动基准测试服务器（参见 [启动服务器](#starting-the-server)）
   - 服务器应可通过配置中指定的 URL 访问

2. **安装 LOCOMO 依赖项**

   在项目根目录运行：
   ```bash
   pip install -r benchmark/locomo/requirements.txt
   ```
或者从 locomo 目录运行：
   ```bash
   cd benchmark/locomo
   pip install -r requirements.txt
   ```
### 配置负载测试的环境变量 {#configuring-environment-variables-for-load-testing}

1. **创建环境配置文件**
   ```bash
   cd benchmark/locomo
   cp .env.example .env
   ```
2. **编辑 `.env` 文件**

   打开 `benchmark/locomo/.env` 并配置以下变量：
   ```bash
   # OpenAI API 配置
   MODEL="qwen3-max"  # 或 "gpt-4o"、"gpt-4" 等
   OPENAI_BASE_URL="https://api.openai.com/v1"  # 或您的 API 端点
   OPENAI_API_KEY="your_api_key_here"  # 您的 OpenAI API key

   # API 配置，必须与正在运行的服务器匹配
   API_BASE_URL="http://localhost:8000"  # benchmark 服务器的 URL
   ```
**重要配置说明：**
- `API_BASE_URL`：必须与您的benchmark服务器运行的URL匹配
  - 默认值：`http://localhost:8000`（如果服务器运行在localhost:8000）
  - 如果服务器运行在其他端口，请相应更新
- `MODEL`：用于生成答案的LLM模型
- `OPENAI_API_KEY`：LLM服务的API密钥
- `OPENAI_BASE_URL`：LLM API的基础URL（可以是OpenAI或兼容服务）

### 运行负载测试 {#running-load-tests}

#### 完整测试套件 {#full-test-suite}

1. **确保benchmark服务器正在运行**

   在一个单独的终端中：
   ```bash
   uvicorn benchmark.server.main:app --host 0.0.0.0 --port 8000
   ```
2. **运行完整测试脚本**

   在 `benchmark/locomo` 目录下：
   ```bash
   cd benchmark/locomo
   bash run.sh [output_folder]
   ```
或者从项目根目录运行：
   ```bash
   cd benchmark/locomo && bash run.sh results
   ```
`output_folder` 参数是可选的（默认为 `results`）。

3. **脚本的功能**

   `run.sh` 脚本执行以下步骤：
   - 重置服务器上的 token 计数
   - 记录初始 token 计数
   - 运行记忆添加实验（`add` 方法）
   - 运行记忆搜索实验（`search` 方法）
   - 记录最终 token 计数
   - 生成评估指标
   - 显示评估结果

#### 运行单独的测试方法 {#running-individual-test-methods}

您也可以手动运行单独的测试方法：

**记忆添加测试：**
```bash
cd benchmark/locomo
python3 run_experiments.py --method add --output_folder results
```
**记忆搜索测试：**
```bash
cd benchmark/locomo
python3 run_experiments.py --method search --output_folder results --top_k 30
```
**可用选项:**
- `--method`: 测试方法 - `add` 或 `search`（默认值: `add`）
- `--chunk_size`: 处理的块大小（默认值: 1000）
- `--top_k`: 检索的记忆数量（默认值: 30）
- `--filter_memories`: 启用记忆过滤
- `--is_graph`: 使用基于图的搜索
- `--num_chunks`: 处理的块数量（默认值: 1）
- `--output_folder`: 结果输出目录（默认值: `results/`）

### 理解测试结果 {#understanding-test-results}

运行测试后，结果存储在输出目录中（默认值: `results/`）：

#### 输出文件 {#output-files}

- **`results.json`**: 每个问题和对话的详细结果
  - 包含所有问答对及检索到的记忆
  - 包括每个操作的时间信息

- **`evaluation_metrics.json`**: 计算的评估指标
  - BLEU 分数
  - F1 分数
  - LLM 评估分数

- **`evaluation.txt`**: 可读的评估摘要
  - 服务器总执行时间
  - 处理的总请求数
  - 平均请求时间
  - P95 延迟（95% 分位数）
  - Token 消耗统计

- **`token1.json`** 和 **`token2.json`**: 测试前后 Token 计数
  - 用于计算总 Token 消耗

### 评估指标 {#evaluation-metrics}

基准测试使用多种指标评估性能：

1. **BLEU 分数**: 测量模型响应与真实答案的相似性
   - 范围: 0.0 到 1.0（越高越好）
   - 基于 n-gram 重叠

2. **F1 分数**: 精确率和召回率的调和平均值
   - 范围: 0.0 到 1.0（越高越好）
   - 衡量答案质量

3. **LLM 分数**: 来自 LLM 评估的二进制分数（0 或 1）
   - 1 = 正确答案，0 = 错误答案
   - 由 LLM 评估决定

4. **Token 消耗**: 用于生成答案的 Token 数量
   - 包括提示和完成的 Token
   - 在测试前后跟踪

5. **延迟指标**:
   - **平均请求时间**: 每个请求的平均时间
   - **P95 延迟**: 95% 分位数请求时间
   - **总执行时间**: 所有操作的总时间

## 故障排除 {#troubleshooting}

### 服务器问题 {#server-issues}

#### "需要设置 OPENAI_API_KEY 环境变量"（或缺少 LLM/embedding 密钥） {#openai_api_key-environment-variable-is-required-or-missing-llmembedding-keys}
- **解决方案**: 在 **项目根目录**（与 PowerMem 相同）创建或编辑 `.env` 文件
- 确保在项目根目录的 `.env` 文件中设置了 `LLM_API_KEY` 和 `EMBEDDING_API_KEY`（或 `OPENAI_API_KEY`）
- 在启动服务器之前，确保已正确配置根目录的 `.env`

#### 数据库连接错误 {#database-connection-errors}
- **解决方案**:
  - 检查 `.env` 文件中的数据库配置
  - 确保数据库服务器正在运行
  - 验证连接凭据（主机、端口、用户、密码）
  - 单独测试数据库连接

#### 端口已被占用 {#port-already-in-use}
- **解决方案**:
  - 更改端口: `uvicorn benchmark.server.main:app --port 8001`
  - 或查找并终止使用该端口的进程:
    ```bash
    lsof -ti:8000 | xargs kill -9
    ```
#### OpenAI API 404 Not Found 错误 {#openai-api-404-not-found-error}
- **解决方案**：
  - 检查你的 `OPENAI_BASE_URL` 是否正确，例如：
    - https://api.openai.com/v1 ✅
    - https://api.openai.com ❌
    - https://api.openai.com/v1/chat/completions ❌
    - https://api.openai.com/v1/embeddings ❌

#### OpenAI API 402 速率限制 {#openai-api-402-rate-limiting}
- **解决方案**：
  - 减少并发：根据需要调整 `methods/add.py` 中的 `max_workers`
  - 增加 ApiKey 数量（多 ApiKey 代理解决方案将在未来上传）

#### 模块未找到错误 {#module-not-found-errors}
- **解决方案**：
  - 安装依赖：`pip install -e .`
  - 确保从项目根目录运行
  - 检查 Python 路径和虚拟环境是否已激活

### 压测问题 {#load-testing-issues}

#### "api_base_url is not set" {#api_base_url-is-not-set}
- **解决方案**：
  - 在 `benchmark/locomo/` 目录下创建 `.env` 文件
  - 确保 `API_BASE_URL` 设置正确
  - 确保 URL 与正在运行的服务器地址匹配

#### 连接被拒绝错误 {#connection-refused-errors}
- **解决方案**：
  - 确保压测服务器正在运行
  - 检查 `.env` 中的 `API_BASE_URL` 是否与服务器 URL 匹配
  - 确保服务器可以从测试位置访问

#### "model is not set" 或 "openai_api_key is not set" {#model-is-not-set-or-openai_api_key-is-not-set}
- **解决方案**：
  - 检查 `benchmark/locomo/.env` 中是否设置了 `MODEL` 和 `OPENAI_API_KEY`
  - 验证 API 密钥是否有效
  - 确保值中没有多余的引号或空格

#### 导入错误 {#import-errors}
- **解决方案**：
  - 安装所有依赖：`pip install -r benchmark/locomo/requirements.txt`
  - 确保从正确的目录运行
  - 检查 Python 版本（需要 3.11+）

#### 性能缓慢 {#slow-performance}
- **解决方案**：
  - 测试使用多线程（默认 32 个线程）
  - 减少并发：根据需要调整 `methods/add.py` 中的 `max_workers`
  - 考虑在资源更多的机器上运行测试
  - 检查服务器性能和数据库连接池大小

#### 数据集未找到 {#dataset-not-found}
- **解决方案**：
  - Nltk 数据未找到：
    ```python
    import nltk
    nltk.download("punkt", quiet=True)
    nltk.download("wordnet", quiet=True)
    ```
  - 未找到 SentenceTransformer 模型：
    ```python
    from sentence_transformers import SentenceTransformer
    # 初始化 SentenceTransformer 模型（会被复用）
    sentence_model = SentenceTransformer("all-MiniLM-L6-v2")
    ```
## 许可证 {#license}

有关详细信息，请参阅主项目的 LICENSE 文件。
