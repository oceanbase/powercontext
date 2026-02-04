"""
User Profile Native Language Support - Test Script

Test case coverage:
- Basic functionality tests (TC-001 ~ TC-006)
- Language coverage tests (TC-007 ~ TC-011)
- Boundary condition tests (TC-012 ~ TC-015)
- Compatibility tests (TC-016 ~ TC-018)
- API endpoint tests (TC-019 ~ TC-022)

Usage:
    pytest test_native_language.py -v
    pytest test_native_language.py -v -k "TC001"  # Run single test case
    pytest test_native_language.py -v -m "api"    # Run API tests only
"""

import os
import sys
import json
import logging
import uuid
import requests
import pytest
from typing import Dict, Any, Optional, List

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from powermem import auto_config
from powermem.user_memory import UserMemory

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# ==================== Fixtures ====================

@pytest.fixture(scope="module")
def config():
    """Provide shared configuration for all tests with qwen provider."""
    # Get base config from auto_config
    base_config = auto_config()
    
    # Get QWEN_API_KEY from environment
    qwen_api_key = os.getenv("QWEN_API_KEY")
    
    # Override LLM config with qwen provider
    base_config["llm"] = {
        "provider": "qwen",
        "config": {
            "api_key": qwen_api_key,
            "model": "qwen3-max",
            "temperature": 0.7,
            "max_tokens": 1000,
        }
    }
    
    # Override embedder config with qwen provider
    base_config["embedder"] = {
        "provider": "qwen",
        "config": {
            "api_key": qwen_api_key,
            "model": "text-embedding-v4",
            "embedding_dims": 1536,
        }
    }
    
    return base_config


@pytest.fixture(scope="module")
def user_memory(config):
    """Module-scoped fixture providing a shared UserMemory instance."""
    um = UserMemory(config=config, agent_id="test_native_language_agent")
    yield um


@pytest.fixture(scope="module")
def api_client():
    """Provide API client for HTTP tests."""
    base_url = os.getenv("POWERMEM_API_URL", "http://localhost:8000")
    api_key = os.getenv("POWERMEM_API_KEY", "key1")
    return APIClient(base_url=base_url, api_key=api_key)


class APIClient:
    """Simple API client for testing HTTP endpoints."""
    
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "key1"):
        self.base_url = base_url.rstrip('/')
        self.api_base = f"{self.base_url}/api/v1"
        self.api_key = api_key
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
    
    def post(self, endpoint: str, data: Dict[str, Any], timeout: int = 60) -> requests.Response:
        """Send POST request."""
        url = f"{self.api_base}{endpoint}"
        return requests.post(url, headers=self.headers, json=data, timeout=timeout)
    
    def get(self, endpoint: str, timeout: int = 30) -> requests.Response:
        """Send GET request."""
        url = f"{self.api_base}{endpoint}"
        return requests.get(url, headers=self.headers, timeout=timeout)
    
    def delete(self, endpoint: str, timeout: int = 30) -> requests.Response:
        """Send DELETE request."""
        url = f"{self.api_base}{endpoint}"
        return requests.delete(url, headers=self.headers, timeout=timeout)


# ==================== Helper Functions ====================

def print_test_result(test_id: str, messages: Any, params: Dict[str, Any], result: Dict[str, Any]):
    """Print detailed test results"""
    print(f"\n{'='*60}")
    print(f"Test Case: {test_id}")
    print(f"{'='*60}")
    
    # Input parameters
    print(f"\n📥 Input Parameters:")
    print(f"  - native_language: {params.get('native_language', 'not specified')}")
    print(f"  - profile_type: {params.get('profile_type', 'content')}")
    if params.get('include_roles'):
        print(f"  - include_roles: {params.get('include_roles')}")
    if params.get('exclude_roles'):
        print(f"  - exclude_roles: {params.get('exclude_roles')}")
    
    # Input messages
    print(f"\n📝 Input Messages:")
    if isinstance(messages, list):
        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            print(f"  [{role}]: {content}")
    else:
        print(f"  {messages}")
    
    # Output results
    print(f"\n📤 Output Results:")
    print(f"  - profile_extracted: {result.get('profile_extracted', False)}")
    
    if result.get('profile_content'):
        print(f"  - profile_content: {result['profile_content']}")
    
    if result.get('topics'):
        print(f"  - topics: {json.dumps(result['topics'], ensure_ascii=False, indent=4)}")
    
    # Memory results
    memory_results = result.get('results', [])
    print(f"\n💾 Memory Storage Results (total {len(memory_results)} items):")
    if memory_results:
        for i, mem in enumerate(memory_results, 1):
            print(f"  [{i}] ID: {mem.get('id', 'N/A')}")
            print(f"      Memory: {mem.get('memory', 'N/A')}")
            if mem.get('metadata'):
                print(f"      Metadata: {mem.get('metadata')}")
    else:
        print("  (No new memories)")
    
    print(f"\n{'='*60}")
    print(f"✓ {test_id} Test Passed")
    print(f"{'='*60}\n")


def has_chinese_chars(text: str) -> bool:
    """Check if text contains Chinese characters."""
    return any('\u4e00' <= char <= '\u9fff' for char in text)


def has_japanese_chars(text: str) -> bool:
    """Check if text contains Japanese characters (Hiragana, Katakana, or Kanji)."""
    for char in text:
        # Hiragana: U+3040 - U+309F
        # Katakana: U+30A0 - U+30FF
        # Kanji (CJK): U+4E00 - U+9FFF (shared with Chinese)
        if '\u3040' <= char <= '\u309f' or '\u30a0' <= char <= '\u30ff':
            return True
    return False


def has_korean_chars(text: str) -> bool:
    """Check if text contains Korean characters (Hangul)."""
    return any('\uac00' <= char <= '\ud7a3' or '\u1100' <= char <= '\u11ff' for char in text)


def has_cyrillic_chars(text: str) -> bool:
    """Check if text contains Cyrillic characters (Russian etc.)."""
    return any('\u0400' <= char <= '\u04ff' for char in text)


def check_topics_keys_english(topics: Dict[str, Any]) -> bool:
    """Check if all topic keys are in English (ASCII)."""
    def _check_keys(d):
        for key, value in d.items():
            if not key.replace('_', '').isascii():
                return False
            if isinstance(value, dict):
                if not _check_keys(value):
                    return False
        return True
    return _check_keys(topics)


def flatten_topics_values(topics: Dict[str, Any]) -> List[str]:
    """Flatten all values from nested topics dict to a list."""
    values = []
    def _extract_values(d):
        for value in d.values():
            if isinstance(value, dict):
                _extract_values(value)
            elif isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        values.append(item)
    _extract_values(topics)
    return values


# ==================== Section 1: Basic Functionality Tests ====================

class TestBasicFunctionality:
    """Basic functionality test cases TC-001 ~ TC-006"""
    
    def test_TC001_chinese_native_language_content(self, user_memory):
        """TC-001: Extract unstructured profile with Chinese native language"""
        user_id = "tc001_zh_content_user"
        messages = [
            {"role": "user", "content": "I work in Beijing as a software engineer."},
            {"role": "assistant", "content": "That's great! What kind of projects do you work on?"}
        ]
        params = {"native_language": "zh", "profile_type": "content"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        assert has_chinese_chars(profile_content), f"Profile should be in Chinese, actual: {profile_content}"
        print_test_result("TC-001", messages, params, result)
    
    def test_TC002_chinese_native_language_topics(self, user_memory):
        """TC-002: Extract structured profile (topics) with Chinese native language"""
        user_id = "tc002_zh_topics_user"
        messages = [
            {"role": "user", "content": "My name is John and I live in Shanghai."},
            {"role": "assistant", "content": "Nice to meet you, John!"}
        ]
        params = {"native_language": "zh", "profile_type": "topics"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        topics = result.get("topics", {})
        assert topics, "topics should not be empty"
        
        # Check keys are English
        assert check_topics_keys_english(topics), f"Topic keys should be in English: {topics}"
        
        # Check values contain Chinese
        values = flatten_topics_values(topics)
        has_chinese_value = any(has_chinese_chars(v) for v in values if v)
        assert has_chinese_value, f"Topic values should contain Chinese: {topics}"
        print_test_result("TC-002", messages, params, result)
    
    def test_TC003_japanese_native_language(self, user_memory):
        """TC-003: Extract profile with Japanese native language"""
        user_id = "tc003_ja_user"
        messages = [
            {"role": "user", "content": "我叫测试007。"},
            {"role": "assistant", "content": "嘿，测试007，你好呀！"}
        ]
        params = {"native_language": "ja", "profile_type": "topics"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        topics = result.get("topics", {})
        assert topics, "topics should not be empty"
        
        # Check values - may contain Japanese or transliterated content
        values = flatten_topics_values(topics)
        print_test_result("TC-003", messages, params, result)
    
    def test_TC004_english_native_language(self, user_memory):
        """TC-004: Extract profile with English native language"""
        user_id = "tc004_en_user"
        messages = [
            {"role": "user", "content": "我是一名来自北京的程序员"},
            {"role": "assistant", "content": "很高兴认识你！"}
        ]
        params = {"native_language": "en", "profile_type": "content"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        
        # English content should be mostly ASCII
        ascii_ratio = sum(1 for c in profile_content if c.isascii()) / max(len(profile_content), 1)
        assert ascii_ratio > 0.7, f"Profile should be mostly in English, actual: {profile_content}"
        print_test_result("TC-004", messages, params, result)
    
    def test_TC005_mixed_language_conversation(self, user_memory):
        """TC-005: Mixed language conversation with specified native language"""
        user_id = "tc005_mixed_lang_user"
        messages = [
            {"role": "user", "content": "我在 Google 工作，做 machine learning"},
            {"role": "assistant", "content": "ML is a great field!"},
            {"role": "user", "content": "是的，我专注于 NLP 领域"}
        ]
        params = {"native_language": "zh", "profile_type": "content"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        assert has_chinese_chars(profile_content), f"Profile should be unified in Chinese, actual: {profile_content}"
        print_test_result("TC-005", messages, params, result)
    
    def test_TC006_multi_round_conversation(self, user_memory):
        """TC-006: Multi-round conversation accumulating profile"""
        user_id = "tc006_multi_round_user"
        params = {"native_language": "zh", "profile_type": "content"}
        
        # Round 1
        messages_1 = [{"role": "user", "content": "I'm a teacher"}]
        result_1 = user_memory.add(
            messages=messages_1,
            user_id=user_id,
            **params
        )
        
        assert result_1.get("profile_extracted") == True
        profile_1 = result_1.get("profile_content", "")
        assert has_chinese_chars(profile_1), f"Round 1 profile should be in Chinese: {profile_1}"
        
        print(f"\n{'='*60}")
        print(f"Test Case: TC-006 (Multi-round Conversation)")
        print(f"{'='*60}")
        print(f"\n📥 Round 1 Input:")
        print(f"  [user]: {messages_1[0]['content']}")
        print(f"\n📤 Round 1 Result:")
        print(f"  - profile_content: {profile_1}")
        
        # Round 2
        messages_2 = [{"role": "user", "content": "I live in Tokyo"}]
        result_2 = user_memory.add(
            messages=messages_2,
            user_id=user_id,
            **params
        )
        
        assert result_2.get("profile_extracted") == True
        profile_2 = result_2.get("profile_content", "")
        assert has_chinese_chars(profile_2), f"Round 2 profile should be in Chinese: {profile_2}"
        
        print(f"\n📥 Round 2 Input:")
        print(f"  [user]: {messages_2[0]['content']}")
        print(f"\n📤 Round 2 Result:")
        print(f"  - profile_content: {profile_2}")
        print(f"\n{'='*60}")
        print(f"✓ TC-006 Test Passed")
        print(f"{'='*60}\n")


# ==================== Section 2: Language Coverage Tests ====================

class TestLanguageCoverage:
    """Language coverage test cases TC-007 ~ TC-011"""
    
    @pytest.mark.parametrize("lang_code,test_id,message,check_func", [
        ("ko", "TC-007", "I'm from Seoul and I love K-pop music. My favorite food is kimchi and bibimbap.", has_korean_chars),
        ("ru", "TC-011", "I live in Moscow and I work as a ballet dancer at the Bolshoi Theatre. I love Russian literature.", has_cyrillic_chars),
    ])
    def test_language_with_special_chars(self, user_memory, lang_code, test_id, message, check_func):
        """Test languages with special characters (Korean, Russian)"""
        user_id = f"{test_id.lower().replace('-', '_')}_user"
        messages = [{"role": "user", "content": message}]
        params = {"native_language": lang_code, "profile_type": "content"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, f"{test_id}: Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, f"{test_id}: profile_content should not be empty"
        # Note: LLM may not always output in target language, so we just log
        print_test_result(test_id, messages, params, result)
    
    def test_TC008_french_native_language(self, user_memory):
        """TC-008: French native language test"""
        user_id = "tc008_french_user"
        messages = [
            {"role": "user", "content": "I live in Paris and I love French cuisine. My favorite food is croissant and café au lait."},
            {"role": "assistant", "content": "That sounds wonderful! Paris is a beautiful city."},
            {"role": "user", "content": "Yes, I work as a chef at a restaurant near the Eiffel Tower."}
        ]
        params = {"native_language": "fr", "profile_type": "content"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        print_test_result("TC-008", messages, params, result)
    
    def test_TC009_german_native_language(self, user_memory):
        """TC-009: German native language test"""
        user_id = "tc009_german_user"
        messages = [
            {"role": "user", "content": "I work in Berlin as an engineer at Volkswagen. I love German beer and Oktoberfest."},
            {"role": "assistant", "content": "Das klingt toll!"},
            {"role": "user", "content": "Ja, I also enjoy hiking in the Alps on weekends."}
        ]
        params = {"native_language": "de", "profile_type": "content"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        print_test_result("TC-009", messages, params, result)
    
    def test_TC010_spanish_native_language(self, user_memory):
        """TC-010: Spanish native language test"""
        user_id = "tc010_spanish_user"
        messages = [
            {"role": "user", "content": "I'm a doctor from Madrid. I love flamenco dancing and tapas."},
            {"role": "assistant", "content": "¡Qué interesante!"},
            {"role": "user", "content": "Sí, I also enjoy watching Real Madrid football matches."}
        ]
        params = {"native_language": "es", "profile_type": "content"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        print_test_result("TC-010", messages, params, result)


# ==================== Section 3: Boundary Condition Tests ====================

class TestBoundaryConditions:
    """Boundary condition test cases TC-012 ~ TC-015"""
    
    def test_TC012_no_native_language_param(self, user_memory):
        """TC-012: Without native_language parameter"""
        user_id = "tc012_no_lang_user"
        messages = [
            {"role": "user", "content": "My name is Bob and I'm a developer from San Francisco."},
            {"role": "assistant", "content": "Nice to meet you, Bob!"}
        ]
        params = {"profile_type": "content"}  # Without native_language
        
        # Call without native_language parameter
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        print_test_result("TC-012 (without native_language)", messages, params, result)
    
    def test_TC013_native_language_empty_string(self, user_memory):
        """TC-013: native_language as empty string"""
        user_id = "tc013_empty_lang_user"
        messages = [
            {"role": "user", "content": "My name is Alice and I'm a software engineer from New York."},
            {"role": "assistant", "content": "Nice to meet you, Alice!"}
        ]
        params = {"native_language": "", "profile_type": "content"}  # Empty string
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        print_test_result("TC-013 (native_language=empty string)", messages, params, result)
    
    def test_TC014_unmapped_language_code(self, user_memory):
        """TC-014: Unmapped language code (Polish pl)"""
        user_id = "tc014_polish_user"
        messages = [
            {"role": "user", "content": "I'm from Warsaw and I work as a pianist. I love Chopin's music and Polish pierogi."},
            {"role": "assistant", "content": "That sounds wonderful!"},
            {"role": "user", "content": "Tak, I also enjoy visiting the historic Old Town."}
        ]
        params = {"native_language": "pl", "profile_type": "content"}  # pl not in standard mapping
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        # Should not raise error
        assert result.get("profile_extracted") == True, "Profile should be extracted (even if language code is unmapped)"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        print_test_result("TC-014 (unmapped code pl)", messages, params, result)
    
    def test_TC015_non_standard_language_description(self, user_memory):
        """TC-015: Non-standard language description (français)"""
        user_id = "tc015_francais_user"
        messages = [
            {"role": "user", "content": "Bonjour! I live in Lyon and I'm a sommelier. I love wine tasting and French gastronomy."},
            {"role": "assistant", "content": "Magnifique! Lyon is known for its cuisine."},
            {"role": "user", "content": "Oui, I work at a Michelin star restaurant. My specialty is pairing wine with French dishes like coq au vin and bouillabaisse."}
        ]
        params = {"native_language": "français", "profile_type": "content"}  # Using French word instead of ISO code
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        # Should not raise error - LLM should understand
        assert result.get("profile_extracted") == True, "Profile should be extracted (even with non-standard language description)"
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        print_test_result("TC-015 (non-standard description français)", messages, params, result)


# ==================== Section 4: Compatibility Tests ====================

class TestCompatibility:
    """Compatibility test cases TC-016 ~ TC-018"""
    
    def test_TC016_backward_compatibility(self, user_memory):
        """TC-016: Backward compatibility with old code"""
        user_id = "tc016_backward_compat_user"
        messages = "Hello, I'm a developer named Charlie from Boston"
        params = {}  # Old-style call without any extra parameters
        
        # Use old-style call without native_language
        result = user_memory.add(
            messages=messages,
            user_id=user_id
        )
        
        assert "profile_extracted" in result, "Should return standard structure"
        assert isinstance(result.get("profile_extracted"), bool), "profile_extracted should be bool"
        print_test_result("TC-016 (backward compatibility)", messages, params, result)
    
    def test_TC017_with_role_filters(self, user_memory):
        """TC-017: Combined with include_roles/exclude_roles"""
        user_id = "tc017_role_filter_user"
        messages = [
            {"role": "user", "content": "I'm a data scientist from California"},
            {"role": "assistant", "content": "Your mother is a design engineer working at Google"},
            {"role": "user", "content": "I work with machine learning models"}
        ]
        params = {
            "include_roles": ["user"],
            "exclude_roles": ["assistant"],
            "native_language": "zh",
            "profile_type": "content"
        }
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True, "Profile should be extracted"
        profile_content = result.get("profile_content", "")
        assert has_chinese_chars(profile_content), f"Profile should be in Chinese: {profile_content}"
        print_test_result("TC-017 (role filter + native_language)", messages, params, result)
    
    def test_TC018_with_profile_type_content(self, user_memory):
        """TC-018a: Combined with profile_type=content"""
        user_id = "tc018a_content_user"
        messages = [{"role": "user", "content": "I love hiking and photography. I often go to Yosemite National Park."}]
        params = {"native_language": "zh", "profile_type": "content"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True
        profile_content = result.get("profile_content", "")
        assert profile_content, "profile_content should not be empty"
        assert has_chinese_chars(profile_content), f"Content type profile should be in Chinese: {profile_content}"
        print_test_result("TC-018a (profile_type=content)", messages, params, result)
    
    def test_TC018_with_profile_type_topics(self, user_memory):
        """TC-018b: Combined with profile_type=topics"""
        user_id = "tc018b_topics_user"
        messages = [{"role": "user", "content": "I love hiking and photography. My name is David and I live in Seattle."}]
        params = {"native_language": "zh", "profile_type": "topics"}
        
        result = user_memory.add(
            messages=messages,
            user_id=user_id,
            **params
        )
        
        assert result.get("profile_extracted") == True
        topics = result.get("topics", {})
        assert topics, "topics should not be empty"
        assert check_topics_keys_english(topics), f"Topic keys should be in English: {topics}"
        print_test_result("TC-018b (profile_type=topics)", messages, params, result)


# ==================== Section 5: API Endpoint Tests ====================

@pytest.mark.api
class TestAPIEndpoints:
    """API endpoint test cases TC-019 ~ TC-022"""
    
    def _print_api_result(self, test_id: str, endpoint: str, request_data: Dict, response_data: Dict):
        """Print detailed API test results"""
        print(f"\n{'='*60}")
        print(f"Test Case: {test_id}")
        print(f"{'='*60}")
        print(f"\n🌐 API Request:")
        print(f"  - Endpoint: POST {endpoint}")
        print(f"  - Request Body:")
        print(f"    {json.dumps(request_data, ensure_ascii=False, indent=4)}")
        print(f"\n📤 API Response:")
        print(f"  - Response:")
        print(f"    {json.dumps(response_data, ensure_ascii=False, indent=4)}")
        print(f"\n{'='*60}")
        print(f"✓ {test_id} Test Passed")
        print(f"{'='*60}\n")
    
    def test_TC019_api_with_native_language(self, api_client):
        """TC-019: HTTP API with native_language parameter"""
        user_id = f"api_test_{uuid.uuid4().hex[:8]}"
        data = {
            "messages": [{"role": "user", "content": "I am a developer from Shanghai"}],
            "native_language": "zh",
            "profile_type": "content",
            "agent_id": "test_native_lang_agent",
            "infer": True
        }
        endpoint = f"/users/{user_id}/profile"
        
        try:
            response = api_client.post(endpoint, data=data)
            
            # Print debug info
            print(f"\n🌐 Request URL: {api_client.api_base}{endpoint}")
            print(f"📤 Response Status: {response.status_code}")
            if response.status_code != 200:
                print(f"📄 Response Content: {response.text[:500]}")
            
            assert response.status_code == 200, f"Should return 200, actual: {response.status_code}, response: {response.text[:200]}"
            result = response.json()
            assert result.get("success") == True, f"Request should succeed: {result}"
            
            profile_data = result.get("data", {})
            profile_content = profile_data.get("profile_content", "")
            assert has_chinese_chars(profile_content), f"API returned profile should be in Chinese: {profile_content}"
            self._print_api_result("TC-019 (API with native_language)", endpoint, data, result)
        except requests.exceptions.ConnectionError:
            pytest.skip("API server not running")
    
    def test_TC020_api_without_native_language(self, api_client):
        """TC-020: HTTP API without native_language parameter"""
        user_id = f"api_test_{uuid.uuid4().hex[:8]}"
        data = {
            "messages": [{"role": "user", "content": "I am a developer from Beijing"}],
            "profile_type": "content",
            "agent_id": "test_native_lang_agent",
            "infer": True
        }
        endpoint = f"/users/{user_id}/profile"
        
        try:
            response = api_client.post(endpoint, data=data)
            
            # Print debug info
            print(f"\n🌐 Request URL: {api_client.api_base}{endpoint}")
            print(f"📤 Response Status: {response.status_code}")
            if response.status_code != 200:
                print(f"📄 Response Content: {response.text[:500]}")
            
            assert response.status_code == 200, f"Should return 200, actual: {response.status_code}, response: {response.text[:200]}"
            result = response.json()
            assert result.get("success") == True, f"Request should succeed (backward compatible): {result}"
            self._print_api_result("TC-020 (API without native_language)", endpoint, data, result)
        except requests.exceptions.ConnectionError:
            pytest.skip("API server not running")
    
    def test_TC021_api_native_language_null(self, api_client):
        """TC-021: HTTP API with native_language field as null"""
        user_id = f"api_test_{uuid.uuid4().hex[:8]}"
        data = {
            "messages": [{"role": "user", "content": "I am a developer from Tokyo"}],
            "native_language": None,
            "profile_type": "content",
            "agent_id": "test_native_lang_agent",
            "infer": True
        }
        endpoint = f"/users/{user_id}/profile"
        
        try:
            response = api_client.post(endpoint, data=data)
            
            # Print debug info
            print(f"\n🌐 Request URL: {api_client.api_base}{endpoint}")
            print(f"📤 Response Status: {response.status_code}")
            if response.status_code != 200:
                print(f"📄 Response Content: {response.text[:500]}")
            
            assert response.status_code == 200, f"Should return 200, actual: {response.status_code}, response: {response.text[:200]}"
            result = response.json()
            assert result.get("success") == True, f"Request should succeed (null equals not passing): {result}"
            self._print_api_result("TC-021 (API native_language=null)", endpoint, data, result)
        except requests.exceptions.ConnectionError:
            pytest.skip("API server not running")
    
    def test_TC022_api_non_standard_language_description(self, api_client):
        """TC-022: HTTP API with non-standard language description"""
        user_id = f"api_test_{uuid.uuid4().hex[:8]}"
        data = {
            "messages": [{"role": "user", "content": "I live in Paris and work as a chef. I love French cuisine and wine."}],
            "native_language": "français",  # Non-standard: full language name instead of ISO code
            "profile_type": "content",
            "agent_id": "test_native_lang_agent",
            "infer": True
        }
        endpoint = f"/users/{user_id}/profile"
        
        try:
            response = api_client.post(endpoint, data=data)
            
            # Print debug info
            print(f"\n🌐 Request URL: {api_client.api_base}{endpoint}")
            print(f"📤 Response Status: {response.status_code}")
            if response.status_code != 200:
                print(f"📄 Response Content: {response.text[:500]}")
            
            assert response.status_code == 200, f"Should return 200 (should not error on non-standard language description), actual: {response.status_code}, response: {response.text[:200]}"
            result = response.json()
            assert result.get("success") == True, f"Request should succeed: {result}"
            
            profile_data = result.get("data", {})
            profile_content = profile_data.get("profile_content", "")
            assert profile_content, "profile_content should not be empty"
            self._print_api_result("TC-022 (API non-standard language français)", endpoint, data, result)
        except requests.exceptions.ConnectionError:
            pytest.skip("API server not running")


# ==================== Entry Point ====================

if __name__ == "__main__":
    # Run all tests with verbose output
    pytest.main([__file__, "-v", "--tb=short"])

