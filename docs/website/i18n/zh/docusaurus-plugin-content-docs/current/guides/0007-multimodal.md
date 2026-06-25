---
title: 多模态指南
sidebar_label: 多模态指南
---

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 powermem（`pip install powermem`）
- 支持多模态 LLM API

## 配置 {#configuration}

### 创建 .env 文件 {#create-env-file}

1. 复制示例配置文件：
   ```bash
   cp .env.example .env
   ```
2. 编辑 `.env` 文件并配置多模态参数

> **注意：** 多模态功能需要支持视觉的 LLM 模型，例如 `gpt-4o`、`gpt-4-vision-preview` 或 `qwen-vl-plus`。

## 什么是多模态能力？ {#what-is-multimodal-capability}

多模态能力使 PowerMem 能够处理不仅限于文本的内容：
- **图片**：从图片中提取信息并生成文本描述
- **图片 URL**：处理在线图片链接
- **音频**：处理音频文件并将语音转换为文本
- **音频 URL**：处理在线音频链接
- **混合内容**：处理包含文本、图片和音频的复合消息

PowerMem 会自动将图片和音频内容转换为文本描述，并将其存储为记忆，使多媒体内容可被搜索和检索。

## 使用 OpenAI 多模态格式添加图片记忆 {#add-image-memory-using-openai-multimodal-format}

使用标准的 OpenAI 多模态消息格式添加包含图片的记忆：
```python
import os
from powermem import Memory

config = {
    "llm": {
        "provider": "openai",  # 使用 OpenAI 兼容的多模态模型
        "config": {
            "model": "qwen-vl-plus",
            "enable_vision": True,  # 关键：启用视觉处理
            "vision_details": "auto",  # 图片分析精度：auto/low/high
            "api_key": "your-api-key-here",
            "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
    },
    "vector_store": {
        "provider": "oceanbase",
        "config": {
            "collection_name": "simple_test",
            "embedding_model_dims": 1536,
            "host": os.getenv("OCEANBASE_HOST", "localhost"),
            "port": int(os.getenv("OCEANBASE_PORT", "2881")),
            "user": os.getenv("OCEANBASE_USER", "root@sys"),
            "password": os.getenv("OCEANBASE_PASSWORD", ""),
            "db_name": os.getenv("OCEANBASE_DATABASE", "test"),
        }
    },
    "version": "v1.1",
    "embedding": {
        "provider": "qwen",
        "config": {
            "model": "text-embedding-v4",
            "embedding_dims": 1536,
            "api_key": "your-api-key-here",
        }
    },
}

memory = Memory(config=config)

# OpenAI 多模态消息格式
messages_multimodal = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "This is Bob's favorite working state"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://example.com/workspace.jpg",
                    "detail": "auto"  # 可选：auto/low/high
                }
            },
        ]
    }
]

# 添加图片记忆
result = memory.add(
    messages=messages_multimodal,
    user_id="test_user",
    metadata={"type": "workspace_photo", "source": "user_upload"}
)

print(f"✅ Successfully added image memory")
print(f"   Memory ID: {result.get('id')}")
print(f"   Processed content: {result}")
```
## 搜索与图像相关的记忆 {#search-image-related-memories}

搜索之前添加的图像记忆：
```python
from powermem import Memory

memory = Memory(config=config)

# 搜索图片相关记忆
query = "Bob's favorite working state"

print(f"\nQuery: '{query}'")
results = memory.search(query=query, user_id="test_user", limit=3)

if results.get("results"):
    for idx, mem in enumerate(results["results"], 1):
        print(f"  Result {idx}: {mem.get('memory', '')[:100]}...")
        print(f"    Similarity: {mem.get('score', 0):.4f}")
        print(f"    Metadata: {mem.get('metadata', {})}")
else:
    print("  未找到相关记忆")
```
## 使用 OpenAI 多模态格式添加音频记忆 {#add-audio-memory-using-openai-multimodal-format}

使用标准的 OpenAI 多模态消息格式添加包含音频的记忆。**重要提示：音频必须像图像一样以 URL 的形式提供。**

> **注意：** 即使是音频内容，音频处理也需要在 LLM 配置中设置 `enable_vision: True`。
```python
import os
from powermem import Memory

config = {
    "llm": {
        "provider": "openai",  # 使用 OpenAI 兼容的多模态模型
        "config": {
            "model": "qwen-vl-plus",
            "enable_vision": True,  # 必填：音频处理必须设为 True
            "vision_details": "auto",
            "api_key": "your-api-key-here",
            "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
    },
    "audio_llm": {
        "provider": "qwen_asr",
        "config": {
            "model": "qwen3-asr-flash",
            "api_key": "your-api-key-here",
        }
    },
    "vector_store": {
        "provider": "oceanbase",
        "config": {
            "collection_name": "simple_test",
            "embedding_model_dims": 1536,
            "host": os.getenv("OCEANBASE_HOST", "localhost"),
            "port": int(os.getenv("OCEANBASE_PORT", "2881")),
            "user": os.getenv("OCEANBASE_USER", "root@sys"),
            "password": os.getenv("OCEANBASE_PASSWORD", ""),
            "db_name": os.getenv("OCEANBASE_DATABASE", "test"),
        }
    },
    "version": "v1.1",
    "embedding": {
        "provider": "qwen",
        "config": {
            "model": "text-embedding-v4",
            "embedding_dims": 1536,
            "api_key": "your-api-key-here",
        }
    },
}

memory = Memory(config=config)

# 音频 URL（必须是 URL，不能是本地文件路径）
audio_url = "https://example.com/example.wav"

# 包含音频的 OpenAI 多模态消息格式
messages_multimodal = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "This is a voice message from Alice"
            },
            {
                "type": "audio",
                "content": {
                    "audio": audio_url,  # 必须是 URL
                }
            },
        ]
    }
]

# 添加音频记忆
result = memory.add(
    messages=messages_multimodal,
    user_id="test_user",
    metadata={"type": "voice_message", "source": "user_upload"}
)

print(f"✅ Successfully added audio memory")
print(f"   Memory ID: {result.get('id')}")
print(f"   Processed content: {result}")
```
**关键点：**
- 音频必须以 **URL** 的形式提供（不能是本地文件路径）
- 在 LLM 配置中必须设置 `enable_vision: True`（音频处理所必需）
- 音频转录需要配置 `audio_llm`
- 音频内容会自动转换为文本并存储为可搜索的记忆

## 搜索与音频相关的记忆 {#search-audio-related-memories}

搜索之前添加的音频记忆：
```python
from powermem import Memory

memory = Memory(config=config)

# 搜索音频相关记忆
query = "voice message from Alice"

print(f"\nQuery: '{query}'")
results = memory.search(query=query, user_id="test_user", limit=3)

if results.get("results"):
    for idx, mem in enumerate(results["results"], 1):
        print(f"  Result {idx}: {mem.get('memory', '')[:100]}...")
        print(f"    Similarity: {mem.get('score', 0):.4f}")
        print(f"    Metadata: {mem.get('metadata', {})}")
else:
    print("  未找到相关记忆")
```
## 配置选项 {#configuration-options}

### 与视觉相关的配置 {#vision-related-configuration}
```python
config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": "gpt-4o",  # 也可以使用 gpt-4-vision-preview、qwen-vl-plus
            "enable_vision": True,  # 必填：启用视觉处理
            "vision_details": "auto",  # 可选：图片分析精度
            # auto：自动选择（推荐）
            # low：较低精度，速度更快
            # high：更高精度，细节更多但速度更慢
        }
    }
}
```
### 与音频相关的配置 {#audio-related-configuration}
```python
config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": "qwen-vl-plus",
            "enable_vision": True,  # 必填：音频处理必须设为 True
            "api_key": "your-api-key",
            "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
    },
    "audio_llm": {
        "provider": "qwen_asr",  # 音频转写提供商
        "config": {
            "model": "qwen3-asr-flash",  # ASR 模型
            "api_key": "your-api-key",
        }
    }
}
```
**重要说明：**
- `enable_vision: True` 对于图像和音频处理是**必需的**
- 音频转录需要配置 `audio_llm`
- 音频文件必须以**URL**形式提供（不能是本地文件路径）
- 图像文件也必须以**URL**形式提供（不能是本地文件路径）

### 支持的模型 {#supported-models}

- **OpenAI**: `gpt-4o`，`gpt-4-vision-preview`，`gpt-4-turbo`
- **Qwen**: `qwen-vl-plus`，`qwen-vl-max`
- **Audio ASR**: `qwen3-asr-flash`（通过 qwen_asr 提供）
- **其他**: 任何支持 OpenAI vision API 格式的模型
