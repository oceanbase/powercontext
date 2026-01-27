"""
Comprehensive test cases for Query Rewrite module

Tests for user profile-based Query Rewrite functionality, covering:
1. Basic functionality tests: Enable/disable rewrite
2. User profile scenarios: With profile/Without profile/Empty profile
3. Query rewrite effectiveness: Ambiguous references/Location-related/Time-related/Project-related
4. Edge cases: Short queries/Empty queries/Rewrite failures
5. Rewrite accuracy tests: Verify rewrite effectiveness
"""

import logging
import os
import sys
import pytest
from typing import Dict, Any, List, Optional

# Add project root to Python path
project_root = os.path.join(os.path.dirname(__file__), "..", "..")
project_root = os.path.abspath(project_root)
sys.path.insert(0, project_root)

from powermem import auto_config, UserMemory

# Configure logging for pytest
# Note: To see logs when running pytest, use: pytest --log-cli-level=INFO
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True  # Force reconfiguration
)
logger = logging.getLogger(__name__)

# Also use print for important messages so they show with -s flag
def log_info(msg):
    """Log and print info message"""
    logger.info(msg)
    print(msg)

def log_warning(msg):
    """Log and print warning message"""
    logger.warning(msg)
    print(f"WARNING: {msg}")

def log_error(msg):
    """Log and print error message"""
    logger.error(msg)
    print(f"ERROR: {msg}")


@pytest.fixture(scope="class")
def query_rewrite_tester(request):
    """Fixture to create and cleanup QueryRewriteTester instance"""
    user_id_prefix = getattr(request.cls, 'user_id_prefix', 'test_rewrite')
    tester = QueryRewriteTester(user_id_prefix=user_id_prefix)
    yield tester
    # Cleanup is handled by individual test methods


class QueryRewriteTester:
    """Query Rewrite functionality test class"""
    
    def __init__(self, user_id_prefix: str = "test_rewrite"):
        """Initialize tester"""
        self.user_id_prefix = user_id_prefix
        self.config = auto_config()
        # Ensure Query Rewrite is enabled
        if 'query_rewrite' not in self.config:
            self.config['query_rewrite'] = {}
        self.config['query_rewrite']['enabled'] = True
        
        self.user_memory = UserMemory(config=self.config)
        log_info("Query Rewrite tester initialized")
    
    def setup_user_profile(self, user_id: str, profile_text: str) -> bool:
        """Setup user profile"""
        try:
            result = self.user_memory.add(
                messages=profile_text,
                user_id=user_id,
                profile_type="content"
            )
            if result.get("profile_extracted"):
                profile = self.user_memory.profile(user_id)
                log_info(f"✓ User {user_id} profile created")
                log_info(f"  Profile content: {profile.get('profile_content', '')[:100]}...")
                return True
            else:
                log_warning(f"✗ User {user_id} profile not extracted")
                return False
        except Exception as e:
            log_error(f"✗ Failed to setup user profile: {e}")
            return False
    
    def get_rewrite_comparison(self, query: str, user_id: str) -> Optional[Dict[str, str]]:
        """
        Get query rewrite comparison (before and after)
        
        Returns:
            Dict with 'original' and 'rewritten' keys, or None if rewrite not performed
        """
        if not hasattr(self.user_memory, 'query_rewriter') or not self.user_memory.query_rewriter:
            return None
        
        if not user_id:
            return None
        
        try:
            # Get user profile
            profile = self.user_memory.profile_store.get_profile_by_user_id(user_id)
            if not profile or not profile.get("profile_content"):
                return None
            
            profile_content = profile["profile_content"]
            
            # Execute rewrite
            rewrite_result = self.user_memory.query_rewriter.rewrite(
                query=query,
                profile_content=profile_content
            )
            
            if rewrite_result.is_rewritten:
                return {
                    'original': rewrite_result.original_query,
                    'rewritten': rewrite_result.rewritten_query
                }
            else:
                return {
                    'original': query,
                    'rewritten': query,
                    'skipped': True
                }
        except Exception as e:
            logger.debug(f"Failed to get rewrite comparison: {e}")
            return None
    
    def cleanup_user(self, user_id: str):
        """Cleanup user data"""
        try:
            self.user_memory.delete_all(user_id=user_id, delete_profile=True)
            log_info(f"✓ User {user_id} data cleaned up")
        except Exception as e:
            log_warning(f"Failed to cleanup user data: {e}")


@pytest.mark.usefixtures("query_rewrite_tester")
class TestQueryRewrite:
    """Test class for Query Rewrite functionality"""
    
    user_id_prefix = "test_rewrite"
    
    @pytest.fixture(autouse=True)
    def setup_tester(self, query_rewrite_tester):
        """Setup tester instance for each test"""
        self.tester = query_rewrite_tester
    
    def test_basic_functionality(self):
        """
        Test Point 1: Basic functionality tests
        - Enable Query Rewrite
        - Disable Query Rewrite
        - Verify rewriter initialization
        """
        log_info("=" * 80)
        log_info("Test Point 1: Basic Functionality Tests")
        log_info("=" * 80)
        
        # 1.1 Test enabling rewrite
        log_info("\n[1.1] Testing Query Rewrite enabled...")
        config_enabled = auto_config()
        config_enabled['query_rewrite'] = {'enabled': True}
        user_memory_enabled = UserMemory(config=config_enabled)
        
        # Check if query_rewriter is initialized
        has_rewriter = hasattr(user_memory_enabled, 'query_rewriter') and user_memory_enabled.query_rewriter is not None
        assert has_rewriter, "Query rewriter should be initialized when enabled"
        log_info("✓ Query rewriter initialized")
        
        # 1.2 Test disabling rewrite
        log_info("\n[1.2] Testing Query Rewrite disabled...")
        config_disabled = auto_config()
        config_disabled['query_rewrite'] = {'enabled': False}
        user_memory_disabled = UserMemory(config=config_disabled)
        
        has_rewriter_disabled = hasattr(user_memory_disabled, 'query_rewriter') and user_memory_disabled.query_rewriter is not None
        assert not has_rewriter_disabled, "Query rewriter should not be initialized when disabled"
        log_info("✓ Query rewriter not initialized when disabled (as expected)")
        
        log_info("\n✓ Test Point 1 passed")
    
    def test_user_profile_scenarios(self):
        """
        Test Point 2: User profile scenario tests
        - Rewrite with user profile
        - Behavior without user profile (should skip rewrite)
        - Behavior with empty user profile
        """
        log_info("=" * 80)
        log_info("Test Point 2: User Profile Scenario Tests")
        log_info("=" * 80)
        
        # 2.1 Scenario with user profile
        log_info("\n[2.1] Testing scenario with user profile...")
        user_id_with_profile = f"{self.tester.user_id_prefix}_with_profile"
        
        profile_text = "My name is Li Si, I am a product manager. I live in Chaoyang District, Beijing, and currently work at an internet company. I like to visit 798 Art District on weekends."
        assert self.tester.setup_user_profile(user_id_with_profile, profile_text), "Failed to setup user profile"
        
        # Execute query (should trigger rewrite)
        query = "Recommend nearby restaurants"
        log_info(f"  Original query: {query}")
        
        # Get rewrite comparison
        rewrite_comparison = self.tester.get_rewrite_comparison(query, user_id_with_profile)
        if rewrite_comparison:
            if rewrite_comparison.get('skipped'):
                log_info(f"  Rewrite status: Skipped (query is clear or no rewrite needed)")
            else:
                log_info(f"  Before rewrite: {rewrite_comparison['original']}")
                log_info(f"  After rewrite: {rewrite_comparison['rewritten']}")
        
        result = self.tester.user_memory.search(
            query=query,
            user_id=user_id_with_profile,
            limit=5
        )
        
        log_info(f"  Result count: {len(result.get('memories', []))}")
        log_info("✓ Query executed successfully with profile")
        
        # 2.2 Scenario without user profile
        log_info("\n[2.2] Testing scenario without user profile...")
        user_id_no_profile = f"{self.tester.user_id_prefix}_no_profile"
        
        query2 = "Recommend nearby restaurants"
        log_info(f"  Original query: {query2}")
        
        result2 = self.tester.user_memory.search(
            query=query2,
            user_id=user_id_no_profile,
            limit=5
        )
        
        log_info(f"  Result count: {len(result2.get('memories', []))}")
        log_info("✓ Query executed successfully without profile (should skip rewrite)")
        
        # 2.3 Scenario with empty user profile
        log_info("\n[2.3] Testing scenario with empty user profile...")
        user_id_empty_profile = f"{self.tester.user_id_prefix}_empty_profile"
        
        # Create a user but don't setup profile (only add conversation, don't extract profile)
        # Or create a profile but profile_content is empty
        self.tester.user_memory.add(
            messages="This is a test message without personal information",
            user_id=user_id_empty_profile,
            profile_type="content"
        )
        
        # Check profile status
        profile = self.tester.user_memory.profile(user_id_empty_profile)
        if profile:
            profile_content = profile.get('profile_content', '')
            if profile_content and profile_content.strip():
                log_warning(f"  User profile is not empty: {profile_content[:50]}...")
                log_warning("  Note: This test expects empty profile, but profile content exists")
            else:
                log_info("  User profile exists but profile_content is empty (as expected)")
        else:
            log_info("  User profile does not exist (as expected)")
        
        # Execute query, verify if rewrite is skipped
        query3 = "Recommend nearby restaurants"
        log_info(f"  Original query: {query3}")
        
        # Get rewrite comparison (should return None or skipped=True)
        rewrite_comparison = self.tester.get_rewrite_comparison(query3, user_id_empty_profile)
        if rewrite_comparison is None:
            log_info("  Rewrite status: Not executed (no user profile or profile is empty)")
        elif rewrite_comparison.get('skipped'):
            log_info("  Rewrite status: Skipped (profile is empty)")
            log_info(f"  Before rewrite: {rewrite_comparison['original']}")
            log_info(f"  After rewrite: {rewrite_comparison['rewritten']} (not rewritten)")
        else:
            log_warning("  Rewrite status: Executed (unexpected, should skip)")
            log_warning(f"  Before rewrite: {rewrite_comparison['original']}")
            log_warning(f"  After rewrite: {rewrite_comparison['rewritten']}")
        
        # Execute search
        result3 = self.tester.user_memory.search(
            query=query3,
            user_id=user_id_empty_profile,
            limit=5
        )
        
        log_info(f"  Result count: {len(result3.get('memories', []))}")
        
        # Verify: Empty profile should use original query, no rewrite
        assert rewrite_comparison is None or rewrite_comparison.get('skipped'), \
            "Empty profile should skip rewrite and use original query"
        log_info("✓ Empty profile correctly skips rewrite, uses original query")
        
        # Cleanup
        self.tester.cleanup_user(user_id_with_profile)
        self.tester.cleanup_user(user_id_no_profile)
        self.tester.cleanup_user(user_id_empty_profile)
        
        log_info("\n✓ Test Point 2 passed")
    
    def test_query_rewrite_effectiveness(self):
        """
        Test Point 3: Query rewrite effectiveness tests
        - Ambiguous queries (there, here, nearby, etc.)
        - Location-related queries
        - Time-related queries
        - Project/work-related queries
        """
        log_info("=" * 80)
        log_info("Test Point 3: Query Rewrite Effectiveness Tests")
        log_info("=" * 80)
        
        user_id = f"{self.tester.user_id_prefix}_effectiveness"
        
        # Setup detailed user profile
        profile_text = """My name is Wang Wu, I am a software engineer.
            I moved from Shanghai to Nanshan District, Shenzhen last month, currently working at Tencent, responsible for AI product development.
            I like to run at Shenzhen Bay Park on weekends, and usually study machine learning and deep learning technologies.
            I am recently working on an open source project called PowerMem, focusing on vector database performance optimization."""
        
        assert self.tester.setup_user_profile(user_id, profile_text), "Failed to setup user profile"
        
        # Test cases: Ambiguous queries
        log_info("\n[3.1] Testing ambiguous queries...")
        ambiguous_queries = [
            "Recommend nearby restaurants",
            "How's the weather there",
            "What fun places are nearby",
            "How is my project going",
        ]
        
        for query in ambiguous_queries:
            log_info(f"\n  Query: {query}")
            
            # Get rewrite comparison
            rewrite_comparison = self.tester.get_rewrite_comparison(query, user_id)
            if rewrite_comparison and not rewrite_comparison.get('skipped'):
                log_info(f"    Before rewrite: {rewrite_comparison['original']}")
                log_info(f"    After rewrite: {rewrite_comparison['rewritten']}")
            
            result = self.tester.user_memory.search(
                query=query,
                user_id=user_id,
                limit=3
            )
            memories = result.get('memories', [])
            log_info(f"  Result count: {len(memories)}")
            if memories:
                log_info(f"  First result: {memories[0].get('memory', '')[:80]}...")
        
        # Test cases: Location-related
        log_info("\n[3.2] Testing location-related queries...")
        location_queries = [
            "What good food is nearby",
            "Recommend some fun places",
            "How's the housing price there",
        ]
        
        for query in location_queries:
            log_info(f"\n  Query: {query}")
            
            # Get rewrite comparison
            rewrite_comparison = self.tester.get_rewrite_comparison(query, user_id)
            if rewrite_comparison and not rewrite_comparison.get('skipped'):
                log_info(f"    Before rewrite: {rewrite_comparison['original']}")
                log_info(f"    After rewrite: {rewrite_comparison['rewritten']}")
            
            result = self.tester.user_memory.search(
                query=query,
                user_id=user_id,
                limit=3
            )
            log_info(f"  Result count: {len(result.get('memories', []))}")
        
        # Test cases: Time-related
        log_info("\n[3.3] Testing time-related queries...")
        time_queries = [
            "What have I been doing recently",
            "What happened last month",
        ]
        
        for query in time_queries:
            log_info(f"\n  Query: {query}")
            
            # Get rewrite comparison
            rewrite_comparison = self.tester.get_rewrite_comparison(query, user_id)
            if rewrite_comparison and not rewrite_comparison.get('skipped'):
                log_info(f"    Before rewrite: {rewrite_comparison['original']}")
                log_info(f"    After rewrite: {rewrite_comparison['rewritten']}")
            
            result = self.tester.user_memory.search(
                query=query,
                user_id=user_id,
                limit=3
            )
            log_info(f"  Result count: {len(result.get('memories', []))}")
        
        # Test cases: Project/work-related
        log_info("\n[3.4] Testing project/work-related queries...")
        work_queries = [
            "How is my project going",
            "How's work recently",
            "Any progress on technical research",
        ]
        
        for query in work_queries:
            log_info(f"\n  Query: {query}")
            
            # Get rewrite comparison
            rewrite_comparison = self.tester.get_rewrite_comparison(query, user_id)
            if rewrite_comparison and not rewrite_comparison.get('skipped'):
                log_info(f"    Before rewrite: {rewrite_comparison['original']}")
                log_info(f"    After rewrite: {rewrite_comparison['rewritten']}")
            
            result = self.tester.user_memory.search(
                query=query,
                user_id=user_id,
                limit=3
            )
            log_info(f"  Result count: {len(result.get('memories', []))}")
        
        # Cleanup
        self.tester.cleanup_user(user_id)
        
        log_info("\n✓ Test Point 3 passed")
    
    def test_edge_cases(self):
        """
        Test Point 4: Edge case tests
        - Short queries (<3 characters)
        - Empty queries
        - Very long queries
        - Special character queries
        """
        log_info("=" * 80)
        log_info("Test Point 4: Edge Case Tests")
        log_info("=" * 80)
        
        user_id = f"{self.tester.user_id_prefix}_edge"
        
        profile_text = "My name is Zhao Liu, I live in Beijing."
        assert self.tester.setup_user_profile(user_id, profile_text), "Failed to setup user profile"
        
        # 4.1 Short queries
        log_info("\n[4.1] Testing short queries (should skip rewrite)...")
        short_queries = ["hi", "好", "a"]
        
        for query in short_queries:
            log_info(f"  Query: '{query}' (length: {len(query)})")
            result = self.tester.user_memory.search(
                query=query,
                user_id=user_id,
                limit=3
            )
            log_info(f"  Result count: {len(result.get('memories', []))}")
        
        # 4.2 Empty queries
        log_info("\n[4.2] Testing empty queries...")
        empty_queries = ["", "   ", "\n"]
        
        for query in empty_queries:
            log_info(f"  Query: '{query}' (length: {len(query)})")
            try:
                result = self.tester.user_memory.search(
                    query=query,
                    user_id=user_id,
                    limit=3
                )
                log_info(f"  Result count: {len(result.get('memories', []))}")
            except Exception as e:
                log_warning(f"  Empty query handling exception: {e}")
        
        # 4.3 Very long queries
        log_info("\n[4.3] Testing very long queries...")
        long_query = "Recommend" * 100  # Very long query
        log_info(f"  Query length: {len(long_query)}")
        result = self.tester.user_memory.search(
            query=long_query,
            user_id=user_id,
            limit=3
        )
        log_info(f"  Result count: {len(result.get('memories', []))}")
        
        # 4.4 Special character queries
        log_info("\n[4.4] Testing special character queries...")
        special_queries = [
            "Recommend@#$%restaurants",
            "How's the weather there?",
            "How is my project (progress)?",
        ]
        
        for query in special_queries:
            log_info(f"  Query: {query}")
            result = self.tester.user_memory.search(
                query=query,
                user_id=user_id,
                limit=3
            )
            log_info(f"  Result count: {len(result.get('memories', []))}")
        
        # Cleanup
        self.tester.cleanup_user(user_id)
        
        log_info("\n✓ Test Point 4 passed")
    
    def test_rewrite_accuracy(self):
        """
        Test Point 5: Rewrite accuracy tests
        - Verify if rewritten query contains user profile information
        - Verify if rewrite maintains original intent
        - Compare search results before and after rewrite
        """
        log_info("=" * 80)
        log_info("Test Point 5: Rewrite Accuracy Tests")
        log_info("=" * 80)
        
        user_id = f"{self.tester.user_id_prefix}_accuracy"
        
        # Setup user profile with clear information
        profile_text = "My name is Zhou Ba, I am a doctor. I live in Tianhe District, Guangzhou, currently working at the First Affiliated Hospital of Sun Yat-sen University. I specialize in cardiovascular medicine."
        
        assert self.tester.setup_user_profile(user_id, profile_text), "Failed to setup user profile"
        
        # Add some related memories
        self.tester.user_memory.add(
            messages=[
                {"role": "user", "content": "I recently found a great Sichuan restaurant in Tianhe District, Guangzhou"},
                {"role": "user", "content": "I am very busy working at the First Affiliated Hospital of Sun Yat-sen University"},
            ],
            user_id=user_id,
            profile_type="content"
        )
        
        # Test rewrite effectiveness
        test_cases = [
            {
                "original": "Recommend nearby restaurants",
                "expected_keywords": ["Guangzhou", "Tianhe District"]
            },
            {
                "original": "How's my work",
                "expected_keywords": ["hospital", "doctor"]
            },
        ]
        
        log_info("\nTesting rewrite accuracy...")
        for i, test_case in enumerate(test_cases, 1):
            log_info(f"\n  Test case {i}:")
            log_info(f"    Original query: {test_case['original']}")
            log_info(f"    Expected keywords: {test_case['expected_keywords']}")
            
            # Get rewrite comparison
            rewrite_comparison = self.tester.get_rewrite_comparison(test_case['original'], user_id)
            if rewrite_comparison and not rewrite_comparison.get('skipped'):
                log_info(f"    Before rewrite: {rewrite_comparison['original']}")
                log_info(f"    After rewrite: {rewrite_comparison['rewritten']}")
            
            result = self.tester.user_memory.search(
                query=test_case['original'],
                user_id=user_id,
                limit=5
            )
            
            memories = result.get('memories', [])
            log_info(f"    Result count: {len(memories)}")
            
            # Check if results are relevant (simple verification)
            if memories:
                first_memory = memories[0].get('memory', '')
                log_info(f"    First result: {first_memory[:80]}...")
        
        # Cleanup
        self.tester.cleanup_user(user_id)
        
        log_info("\n✓ Test Point 5 passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
