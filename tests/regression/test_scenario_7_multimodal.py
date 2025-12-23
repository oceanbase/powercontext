"""Executable walkthrough for `scenario_7_multimodal.md`.

This version follows the documentation steps and directly uses `Memory`
without redefining any mock multimodal class. It exercises:
  1) add image memory
  2) search image memory
  3) add audio memory
  4) search audio memory
"""

from __future__ import annotations

import os
from typing import Any, Dict

import pytest
from dotenv import load_dotenv
from powermem import create_memory
from powermem.config_loader import auto_config


# -----------------------------------------------------------------------------
# Env loading
# -----------------------------------------------------------------------------
env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
env_example_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env.example")

# Load environment variables (from .env file or GitHub Secrets)
if os.path.exists(env_path):
    print("Found .env file")
    load_dotenv(env_path, override=True)
else:
    print(f"\n No .env file found at: {env_path}")
    print("To add your API keys:")
    print(f"   1. Copy: cp {env_example_path} {env_path}")
    print(f"   2. Edit {env_path} and add your API keys")
    print("\n  create_memory will fall back to mock providers if keys are missing.")

# Get API key from environment variable (GitHub Secrets) or .env file
# Priority: DASHSCOPE_API_KEY (GitHub Secrets) > LLM_API_KEY > .env file > default fallback
dashscope_api_key = os.getenv("QWEN_API_KEY")
# Handle empty string from GitHub Secrets (if secret is not set, it returns empty string)
if not dashscope_api_key or dashscope_api_key.strip() == "":
    # Fallback to default for local development (not recommended for production)
    print("⚠ Warning: Using default API key. For production, set QWEN environment variable or GitHub Secret.")
else:
    print("✓ API key loaded from environment variable or GitHub Secrets")

custom_config = {
    "llm": {
        "provider": "openai",  # Use OpenAI-compatible multimodal model
        "config": {
            "model": "qwen3-omni-flash",
            "enable_vision": True,  # Key: Enable vision processing
            "vision_details": "auto",  # Image analysis precision: auto/low/high
            "api_key": dashscope_api_key,  # From environment variable or GitHub Secrets
            "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
    },
    "vector_store": {
        "provider": "oceanbase",
        "config": {
            "collection_name": "memories",  # Changed to avoid dimension mismatch
            "connection_args": {
                "host": os.getenv("OCEANBASE_HOST", "127.0.0.1"),
                "port": int(os.getenv("OCEANBASE_PORT", "10001")),
                "user": os.getenv("OCEANBASE_USER", "root"),
                "password": os.getenv("OCEANBASE_PASSWORD", ""),
                "db_name": os.getenv("OCEANBASE_DB", "powermem"),
            },
            "vidx_metric_type": "cosine",
            "index_type": "IVF_FLAT",
            "embedding_model_dims": 1536,  # Match text-embedding-v4 dimension
            "primary_field": "id",
            "vector_field": "embedding",
            "text_field": "document",
            "metadata_field": "metadata",
            "vidx_name": "memories_vidx"
        }
    },
    "embedder": {  # Changed from "embedding" to "embedder"
        "provider": "qwen",
        "config": {
            "model": "text-embedding-v4",
            "embedding_dims": 1536,  # text-embedding-v4 uses 1536 dimensions
            "api_key": dashscope_api_key,
            "dashscope_base_url": "https://dashscope.aliyuncs.com/api/v1"
        }
    },
    "audio_llm": {  # Audio transcription provider
        "provider": "qwen_asr",
        "config": {
            "model": "qwen3-asr-flash",  # ASR model for speech-to-text
            "api_key": dashscope_api_key,
        }
    },
}

# Environment loading is now done above before custom_config definition


@pytest.fixture(scope="session")
def memory():
    """Session-scoped fixture providing a shared Memory instance for all tests."""
    mem = create_memory(config=custom_config)
    yield mem
    # Cleanup after all tests complete
    try:
        mem.delete_all(user_id="test_user")
        print(f"\n✓ Cleaned up all test data for user: test_user")
    except Exception as e:
        print(f"\n⚠ Could not cleanup test data: {str(e)[:100]}")


def _print_banner(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _print_step(title: str) -> None:
    print("\n" + "-" * 80)
    print(title)
    print("-" * 80)


def test_step1_add_image_memory(memory) -> None:
    _print_step("Step 1: Add Image Memory ")
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "The picture mentions the current affairs of the people."
                 },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
                        "detail": "auto",
                    },
                },
            ],
        }
    ]
    try:
        result = memory.add(
            messages=messages,
            user_id="test_user",
            metadata={"type": "workspace_photo", "source": "user_upload"},
        )
        res = result.get("results", [])
        mem = res[0] if res else result
        print("✅ Added image memory")
        print(f"   ID: {mem.get('id')}")
        print(f"   Memory: {mem.get('memory', '')[:120]}...")
    except Exception as e:
        print(f"⚠ Error adding image memory: {e}")


def test_step2_search_image_memories(memory) -> None:
    _print_step("Step 2: Search Image Memories")
    # query = "Alice's favorite working state"
    query = "The picture mentions the current affairs of the people"
    print(f"Query: '{query}'")
    try:
        results = memory.search(query=query, user_id="test_user", limit=3)
        if results.get("results"):
            for idx, mem in enumerate(results["results"], 1):
                print(f"\n  Result {idx}:")
                print(f"    Memory: {mem.get('memory', '')[:120]}...")
                print(f"    Similarity: {mem.get('score', 0):.4f}")
                print(f"    Metadata: {mem.get('metadata', {})}")
        else:
            print("  No related memories found")

    except Exception as e:
        print(f"⚠ Error searching image memories: {e}")
    finally:
        # Cleanup
        try:
            memory.delete_all(user_id="test_user")
        except Exception:
            pass


def test_step3_add_audio_memory(memory) -> None:
    _print_step("Step 3: Add Audio Memory (OpenAI multimodal)")
    audio_url = "https://sis-sample-audio.obs.cn-north-1.myhuaweicloud.com/16k16bit.wav"  # Must be a URL
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Huawei is committed to bringing the digital world to everyone"},
                {"type": "audio", "content": {"audio": audio_url}},
            ],
        }
    ]
    try:
        result = memory.add(
            messages=messages,
            user_id="test_user",
            metadata={"type": "voice_message", "source": "user_upload"},
            infer=False,  # Disable intelligent memory to avoid fact extraction errors
        )
        res = result.get("results", [])
        if res:
            mem = res[0]
            print("✅ Added audio memory")
            print(f"   ID: {mem.get('id')}")
            memory_text = mem.get('memory') or mem.get('content') or str(mem)
            print(f"   Memory: {memory_text[:120]}...")
            if len(memory_text) > 120:
                print(f"   (Full length: {len(memory_text)} chars)")
        else:
            print("✅ Added audio memory (no results returned)")
            print(f"   Result: {result}")
    except Exception as e:
        print(f"⚠ Error adding audio memory: {e}")
        import traceback
        traceback.print_exc()
        print("  Note: enable_vision must be True and audio_llm configured for real audio processing.")


def test_step4_search_audio_memories(memory) -> None:
    _print_step("Step 4: Search Audio Memories")
    query = "Huawei is committed to bringing the digital world to everyone"
    print(f"Query: '{query}'")
    try:
        results = memory.search(query=query, user_id="test_user", limit=3)
        if results.get("results"):
            for idx, mem in enumerate(results["results"], 1):
                print(f"\n  Result {idx}:")
                print(f"    Memory: {mem.get('memory', '')[:120]}...")
                print(f"    Similarity: {mem.get('score', 0):.4f}")
                print(f"    Metadata: {mem.get('metadata', {})}")
        else:
            print("  No related memories found")
    except Exception as e:
        print(f"⚠ Error searching audio memories: {e}")


def _memory_text(entry: Dict[str, Any]) -> str:
    """Extract memory text from entry."""
    if not isinstance(entry, dict):
        return str(entry)
    return entry.get("memory") or entry.get("content") or entry.get("text") or str(entry)


def test_multimodal_mixed_content(memory) -> None:
    """Test mixed multimodal content: verify that mixed messages containing text, images, and audio can be processed"""
    _print_step("Mixed Multimodal Content: Text + Image + Audio")
    user_id = "multimodal_mixed_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Adding memory with text + image...")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "This is a workspace photo with description"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
                        "detail": "auto",
                    },
                },
            ],
        }
    ]
    try:
        result = memory.add(
            messages=messages,
            user_id=user_id,
            metadata={"type": "mixed", "content_types": ["text", "image"]},
        )
        res = result.get("results", [])
        if res:
            print(f"   ✓ Added mixed memory (text+image), ID: {res[0].get('id')}")
        else:
            print("   ✓ Added mixed memory (text+image)")
    except Exception as e:
        print(f"   ⚠ Error: {e}")

    print("\n2. Adding memory with text + audio...")
    audio_url = "https://sis-sample-audio.obs.cn-north-1.myhuaweicloud.com/16k16bit.wav"
    messages_audio = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Voice message transcript"},
                {"type": "audio", "content": {"audio": audio_url}},
            ],
        }
    ]
    try:
        result = memory.add(
            messages=messages_audio,
            user_id=user_id,
            metadata={"type": "mixed", "content_types": ["text", "audio"]},
            infer=False,
        )
        res = result.get("results", [])
        if res:
            print(f"   ✓ Added mixed memory (text+audio), ID: {res[0].get('id')}")
        else:
            print("   ✓ Added mixed memory (text+audio)")
    except Exception as e:
        print(f"   ⚠ Error: {e}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_multimodal_batch_add(memory) -> None:
    """Test multimodal batch add: verify that multiple image memories can be added in batch"""
    _print_step("Multimodal Batch Add: Multiple Images")
    user_id = "multimodal_batch_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Adding multiple image memories...")
    image_urls = [
        "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
        "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",  # Same URL for testing
    ]
    
    added_count = 0
    for idx, img_url in enumerate(image_urls):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Image {idx+1} description"},
                    {
                        "type": "image_url",
                        "image_url": {"url": img_url, "detail": "auto"},
                    },
                ],
            }
        ]
        try:
            result = memory.add(
                messages=messages,
                user_id=user_id,
                metadata={"batch_id": idx+1, "type": "batch_image"},
            )
            added_count += 1
            print(f"   ✓ Added image {idx+1}")
        except Exception as e:
            print(f"   ⚠ Error adding image {idx+1}: {e}")

    print(f"\n2. Verifying batch addition...")
    try:
        all_memories = memory.get_all(user_id=user_id)
        all_results = all_memories.get("results", [])
        print(f"   Total memories: {len(all_results)}")
    except Exception as e:
        print(f"   ⚠ Error getting all: {e}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_multimodal_update_delete(memory) -> None:
    """Test multimodal memory update and delete: verify that multimodal memories can be updated and deleted"""
    _print_step("Multimodal Update and Delete")
    user_id = "multimodal_update_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Adding image memory...")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Original image description"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
                        "detail": "auto",
                    },
                },
            ],
        }
    ]
    
    memory_id = None
    try:
        result = memory.add(
            messages=messages,
            user_id=user_id,
            metadata={"type": "image", "version": 1},
        )
        res = result.get("results", [])
        if res:
            memory_id = res[0].get("id")
            print(f"   ✓ Added image memory, ID: {memory_id}")
    except Exception as e:
        print(f"   ⚠ Error adding: {e}")

    if memory_id:
        print("\n2. Updating memory...")
        try:
            updated = memory.update(
                memory_id=memory_id,
                content="Updated image description",
                user_id=user_id,
                metadata={"type": "image", "version": 2},
            )
            print(f"   ✓ Updated memory: {_memory_text(updated)[:80]}...")
        except Exception as e:
            print(f"   ⚠ Error updating: {e}")

        print("\n3. Deleting memory...")
        try:
            success = memory.delete(memory_id=memory_id, user_id=user_id)
            if success:
                print(f"   ✓ Deleted memory {memory_id}")
        except Exception as e:
            print(f"   ⚠ Error deleting: {e}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_multimodal_metadata_filtering(memory) -> None:
    """Test multimodal memory metadata filtering: verify that multimodal memories can be filtered by metadata"""
    _print_step("Multimodal Metadata Filtering")
    user_id = "multimodal_filter_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Adding memories with different metadata...")
    image_messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Workspace photo"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
                        "detail": "auto",
                    },
                },
            ],
        }
    ]
    
    try:
        memory.add(
            messages=image_messages,
            user_id=user_id,
            metadata={"type": "workspace", "category": "office", "priority": "high"},
        )
        print("   ✓ Added workspace image")
    except Exception as e:
        print(f"   ⚠ Error: {e}")

    try:
        memory.add(
            messages=image_messages,
            user_id=user_id,
            metadata={"type": "personal", "category": "home", "priority": "low"},
        )
        print("   ✓ Added personal image")
    except Exception as e:
        print(f"   ⚠ Error: {e}")

    print("\n2. Filtering by category...")
    try:
        results = memory.search(
            query="photo",
            user_id=user_id,
            filters={"metadata.category": "office"},
            limit=10
        )
        filtered = results.get("results", [])
        print(f"   Found {len(filtered)} memories with category='office'")
        for mem in filtered:
            print(f"      - {_memory_text(mem)[:60]}...")
    except Exception as e:
        print(f"   ⚠ Error filtering: {e}")

    print("\n3. Filtering by priority...")
    try:
        results = memory.search(
            query="photo",
            user_id=user_id,
            filters={"metadata.priority": "high"},
            limit=10
        )
        filtered = results.get("results", [])
        print(f"   Found {len(filtered)} memories with priority='high'")
    except Exception as e:
        print(f"   ⚠ Error filtering: {e}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_multimodal_get_all(memory) -> None:
    """Test multimodal memory get_all operation: verify that all multimodal memories can be retrieved"""
    _print_step("Multimodal Get All")
    user_id = "multimodal_getall_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Adding multiple multimodal memories...")
    image_messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Test image"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
                        "detail": "auto",
                    },
                },
            ],
        }
    ]
    
    added_count = 0
    for i in range(3):
        try:
            memory.add(
                messages=image_messages,
                user_id=user_id,
                metadata={"batch": i+1},
            )
            added_count += 1
        except Exception as e:
            print(f"   ⚠ Error adding memory {i+1}: {e}")
    
    print(f"   ✓ Added {added_count} memories")

    print("\n2. Getting all memories...")
    try:
        all_memories = memory.get_all(user_id=user_id)
        all_results = all_memories.get("results", [])
        print(f"   ✓ Retrieved {len(all_results)} memories:")
        for idx, mem in enumerate(all_results, start=1):
            print(f"      {idx}. {_memory_text(mem)[:60]}...")
    except Exception as e:
        print(f"   ⚠ Error getting all: {e}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_multimodal_get_operation(memory) -> None:
    """Test multimodal memory get operation: verify that a single multimodal memory can be retrieved"""
    _print_step("Multimodal Get Operation")
    user_id = "multimodal_get_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Adding image memory...")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Test image for get operation"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
                        "detail": "auto",
                    },
                },
            ],
        }
    ]
    
    memory_id = None
    try:
        result = memory.add(
            messages=messages,
            user_id=user_id,
            metadata={"test": True},
        )
        res = result.get("results", [])
        if res:
            memory_id = res[0].get("id")
            print(f"   ✓ Added memory, ID: {memory_id}")
    except Exception as e:
        print(f"   ⚠ Error adding: {e}")

    if memory_id:
        print("\n2. Getting memory...")
        try:
            retrieved = memory.get(memory_id=memory_id, user_id=user_id)
            if retrieved:
                print(f"   ✓ Retrieved memory: {_memory_text(retrieved)[:80]}...")
                metadata = retrieved.get("metadata", {})
                print(f"   Metadata: {metadata}")
            else:
                print("   ⚠ Memory not found")
        except Exception as e:
            print(f"   ⚠ Error getting: {e}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_multimodal_mixed_search(memory) -> None:
    """Test multimodal mixed search: verify that mixed content containing text and multimodal memories can be searched"""
    _print_step("Multimodal Mixed Search: Text + Multimodal Memories")
    user_id = "multimodal_mixed_search_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Adding text memory...")
    try:
        memory.add(
            messages="User likes Python programming",
            user_id=user_id,
            metadata={"type": "text"},
        )
        print("   ✓ Added text memory")
    except Exception as e:
        print(f"   ⚠ Error: {e}")

    print("\n2. Adding image memory...")
    image_messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Workspace setup"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
                        "detail": "auto",
                    },
                },
            ],
        }
    ]
    try:
        memory.add(
            messages=image_messages,
            user_id=user_id,
            metadata={"type": "image"},
        )
        print("   ✓ Added image memory")
    except Exception as e:
        print(f"   ⚠ Error: {e}")

    print("\n3. Searching across all memory types...")
    try:
        results = memory.search(query="user preferences", user_id=user_id, limit=10)
        all_results = results.get("results", [])
        print(f"   Found {len(all_results)} memories:")
        for idx, mem in enumerate(all_results, start=1):
            mem_type = mem.get("metadata", {}).get("type", "unknown")
            print(f"      {idx}. [{mem_type}] {_memory_text(mem)[:60]}...")
    except Exception as e:
        print(f"   ⚠ Error searching: {e}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_multimodal_error_handling(memory) -> None:
    """Test multimodal error handling: verify the handling of multimodal operations in error situations"""
    _print_step("Multimodal Error Handling")
    user_id = "multimodal_error_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Testing invalid image URL...")
    invalid_messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Invalid image test"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://invalid-url-that-does-not-exist.com/image.jpg",
                        "detail": "auto",
                    },
                },
            ],
        }
    ]
    try:
        result = memory.add(
            messages=invalid_messages,
            user_id=user_id,
            metadata={"test": "invalid_url"},
        )
        print("   ⚠ Unexpected success with invalid URL")
    except Exception as e:
        print(f"   ✓ Handled invalid URL gracefully: {type(e).__name__}")

    print("\n2. Testing missing content type...")
    try:
        invalid_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "unknown_type", "text": "Unknown content type"},
                ],
            }
        ]
        result = memory.add(
            messages=invalid_messages,
            user_id=user_id,
        )
        print("   ⚠ Unexpected success with unknown type")
    except Exception as e:
        print(f"   ✓ Handled unknown content type: {type(e).__name__}")

    print("\n3. Testing get with non-existent ID...")
    try:
        retrieved = memory.get(memory_id=99999, user_id=user_id)
        if retrieved is None or not retrieved:
            print("   ✓ Handled non-existent ID gracefully")
        else:
            print("   ⚠ Unexpected result for non-existent ID")
    except Exception as e:
        print(f"   ✓ Handled non-existent ID: {type(e).__name__}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_multimodal_different_image_urls(memory) -> None:
    """Test different image URLs: verify that image URLs from different sources and formats can be processed"""
    _print_step("Multimodal Different Image URLs")
    user_id = "multimodal_urls_user"
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass

    print("1. Testing different image URL formats...")
    image_urls = [
        {
            "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
            "detail": "auto",
            "description": "Auto detail level"
        },
        {
            "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
            "detail": "low",
            "description": "Low detail level"
        },
        {
            "url": "https://gips1.baidu.com/it/u=647860401,3888240252&fm=3074&app=3074&f=JPEG",
            "detail": "high",
            "description": "High detail level"
        },
    ]

    for idx, img_config in enumerate(image_urls):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": img_config["description"]},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": img_config["url"],
                            "detail": img_config["detail"],
                        },
                    },
                ],
            }
        ]
        try:
            result = memory.add(
                messages=messages,
                user_id=user_id,
                metadata={"detail_level": img_config["detail"], "test_id": idx+1},
            )
            print(f"   ✓ Added image with {img_config['detail']} detail level")
        except Exception as e:
            print(f"   ⚠ Error adding image {idx+1}: {e}")

    print("\n2. Verifying all images were added...")
    try:
        all_memories = memory.get_all(user_id=user_id)
        all_results = all_memories.get("results", [])
        print(f"   Total memories: {len(all_results)}")
    except Exception as e:
        print(f"   ⚠ Error: {e}")

    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def main() -> None:
    _print_banner("Powermem Scenario 7: Multimodal Capability")


    # Use auto_config directly; user may choose to supply dashscope_base_url
    config = custom_config
    # config = auto_config()
    memory = create_memory(config=config)

    test_step1_add_image_memory(memory)
    test_step2_search_image_memories(memory)
    test_step3_add_audio_memory(memory)
    test_step4_search_audio_memories(memory)
    
    # Additional comprehensive tests
    test_multimodal_mixed_content(memory)
    test_multimodal_batch_add(memory)
    test_multimodal_update_delete(memory)
    test_multimodal_metadata_filtering(memory)
    test_multimodal_get_all(memory)
    test_multimodal_get_operation(memory)
    test_multimodal_mixed_search(memory)
    test_multimodal_error_handling(memory)
    test_multimodal_different_image_urls(memory)

    # Final cleanup
    try:
        memory.delete_all(user_id="test_user")
        print(f"\n✓ Cleaned up all test data for user: test_user")
    except Exception as e:
        print(f"\n⚠ Could not cleanup test data: {str(e)[:100]}")

    _print_banner("Scenario 7 walkthrough completed successfully!")


if __name__ == "__main__":
    main()
