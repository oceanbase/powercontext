"""
Sparse vector search regression tests using pytest.

Core checks:
1. Validate returned results after search.
2. Validate score sorting in descending order.
3. Validate compound-term search quality.
4. Compare performance with/without sparse vector support.
"""
import logging
import os
import sys
import time
from typing import List, Dict, Any

import pytest

# Add project root to Python path
project_root = os.path.join(os.path.dirname(__file__), "..", "..")
project_root = os.path.abspath(project_root)
sys.path.insert(0, project_root)

# Change working directory to project root
os.chdir(project_root)

from powermem import auto_config, Memory

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SEED_MEMORY_TEXTS = (
    "I like to take a morning nap on weekends before going out in the afternoon.",
    "Machine learning is a core branch of artificial intelligence with supervised and unsupervised methods.",
    "Deep learning uses multi-layer neural networks; people often discuss machine learning and deep learning together.",
    "Natural language processing combines linguistics and neural network approaches.",
    "Mobile payment and online education are increasingly common in remote work scenarios.",
)


@pytest.fixture(scope="function")
def memory_with_sparse():
    """Create a Memory instance with sparse vector support enabled."""
    config = auto_config()
    
    if 'vector_store' not in config:
        config['vector_store'] = {}
    if 'config' not in config['vector_store']:
        config['vector_store']['config'] = {}
    config['vector_store']['config']['include_sparse'] = True
    
    memory = Memory(config=config)
    
    for text in SEED_MEMORY_TEXTS:
        memory.add(text, user_id="test_user_sparse")
    
    yield memory


@pytest.fixture(scope="function")
def memory_without_sparse():
    """Create a Memory instance with sparse vector support disabled."""
    config = auto_config()
    
    if 'vector_store' not in config:
        config['vector_store'] = {}
    if 'config' not in config['vector_store']:
        config['vector_store']['config'] = {}
    config['vector_store']['config']['include_sparse'] = False
    
    memory = Memory(config=config)
    
    for text in SEED_MEMORY_TEXTS:
        memory.add(text, user_id="test_user_sparse")
    
    yield memory


@pytest.fixture(scope="session")
def test_queries():
    """Return default test queries for performance testing."""
    return [
        "morning nap",
        "machine learning",
        "deep learning",
        "natural language processing",
        "mobile payment",
    ]


@pytest.fixture(scope="session")
def compound_word_queries():
    """Return compound-term queries for testing."""
    return [
        "morning nap",
        "mobile payment",
        "online education",
        "remote work",
        "machine learning",
        "deep learning",
        "natural language processing",
    ]


class TestSparseSearch:
    """Sparse vector search regression test suite."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fixtures - runs before each test method."""
        config = auto_config()
        
        if 'vector_store' not in config:
            config['vector_store'] = {}
        if 'config' not in config['vector_store']:
            config['vector_store']['config'] = {}
        config['vector_store']['config']['include_sparse'] = True
        
        self.memory = Memory(config=config)
        self.user_id = "test_user_sparse"
        
        # Seed test data
        for text in SEED_MEMORY_TEXTS:
            self.memory.add(text, user_id=self.user_id)
        
        yield
    
    def test_search_results(self):
        """
        Check 1: Validate returned search results.
        
        Verifies:
        - Search returns non-empty results
        - Each result has valid schema (score, content fields)
        """
        print("\n" + "=" * 60)
        print("Check 1: validate returned results")
        print("=" * 60)
        
        # Execute search
        query = "morning nap"
        print(f"Running query: {query}")
        search_response = self.memory.search(query=query, user_id=self.user_id, limit=10)
        
        # Search returns dict: {"results": [...]}
        results = search_response.get("results", [])
        
        # Validate not empty
        assert len(results) > 0, "Search results should not be empty"
        print(f"✓ Search succeeded, returned {len(results)} results")
        
        # Validate result schema
        for i, result in enumerate(results, 1):
            # Validate required fields (result should be dict)
            assert isinstance(result, dict), f"Result {i} should be a dict"
            assert "score" in result, f"Result {i} is missing score"
            assert isinstance(result["score"], (int, float)), f"Result {i} score should be numeric"
            
            # Validate memory/data/content field (varies by version)
            has_content = "memory" in result or "data" in result or "content" in result
            assert has_content, f"Result {i} missing content field (memory/data/content)"
            
            content = result.get("memory") or result.get("data") or result.get("content", "")
            assert content, f"Result {i} content should not be empty"
        
        print(f"✓ All {len(results)} results have valid schema")
        
        # Show first 3 results
        print("\nTop 3 results:")
        for i, result in enumerate(results[:3], 1):
            print(f"  {i}. ID: {result.get('id', 'N/A')}")
            print(f"     Score: {result.get('score', 0):.4f}")
            content = result.get("memory") or result.get("data") or result.get("content", "")
            print(f"     Content: {content[:80]}...")
            print(f"     User ID: {result.get('user_id', 'N/A')}")
        
        print("✓ Check 1 passed")
    
    def test_score_sorting(self):
        """
        Check 2: Validate descending score sorting.
        
        Tests multiple queries to ensure results are sorted by score (high to low).
        """
        print("\n" + "=" * 60)
        print("Check 2: score sorting")
        print("=" * 60)
        
        # Execute multiple queries to validate sorting
        test_queries = [
            "morning nap",
            "machine learning",
            "deep learning",
        ]
        
        for query in test_queries:
            print(f"\nTest query: {query}")
            search_response = self.memory.search(query=query, user_id=self.user_id, limit=10)
            results = search_response.get("results", [])
            
            if len(results) == 0:
                logger.warning(f"  ⚠ Query '{query}' returned no results, skip sorting check")
                continue
            
            if len(results) == 1:
                print("  ✓ Only one result, sorting check skipped")
                continue
            
            # Validate descending sort: score high -> low
            scores = [r.get("score", 0) for r in results]
            is_descending = all(scores[i] >= scores[i+1] for i in range(len(scores)-1))
            
            assert is_descending, (
                f"Scores are not in descending order for query '{query}': {scores}"
            )
            
            print(f"  ✓ Correct descending order: {scores[0]:.4f} >= {scores[1]:.4f} >= ... >= {scores[-1]:.4f}")
            
            # Show first 5 scores
            print("  Top 5 result scores:")
            for i, result in enumerate(results[:5], 1):
                print(f"    {i}. Score: {result.get('score', 0):.4f}")
        
        print("\n✓ Check 2 passed (all query sort orders are correct)")
    
    def test_compound_word_search(self, compound_word_queries: List[str]):
        """
        Check 3: Compound-term search quality.
        
        Tests whether compound terms (multi-word queries) return relevant results.
        """
        print("\n" + "=" * 60)
        print("Check 3: compound-term search")
        print("=" * 60)
        
        all_passed = True
        
        for query in compound_word_queries:
            print(f"\nCompound query: {query}")
            search_response = self.memory.search(query=query, user_id=self.user_id, limit=5)
            results = search_response.get("results", [])
            
            if results:
                print(f"  Found {len(results)} results")
                
                # Check whether top result contains query
                top_result = results[0]
                content = top_result.get("memory") or top_result.get("data") or top_result.get("content", "")
                
                # Case-insensitive check
                query_lower = query.lower()
                content_lower = content.lower()
                
                if query_lower in content_lower:
                    print("  ✓ Top result contains full compound term")
                    print(f"     Content: {content[:80]}...")
                else:
                    # Fallback: partial match for multi-word compounds
                    words = query.split()
                    if len(words) > 1:
                        matched_words = [w for w in words if w.lower() in content_lower]
                        if matched_words:
                            print(f"  ✓ Top result contains partial keywords: {matched_words}")
                            print(f"     Content: {content[:80]}...")
                        else:
                            logger.warning("  ⚠ Top result may be irrelevant")
                            logger.warning(f"     Query: {query}")
                            logger.warning(f"     Content: {content[:80]}...")
                    else:
                        logger.warning("  ⚠ Top result may be irrelevant")
                        logger.warning(f"     Query: {query}")
                        logger.warning(f"     Content: {content[:80]}...")
                
                # Show all result scores and content
                print("  All results:")
                for i, result in enumerate(results, 1):
                    result_content = result.get("memory") or result.get("data") or result.get("content", "")
                    is_relevant = query_lower in result_content.lower()
                    relevance_mark = "✓" if is_relevant else "?"
                    print(f"    {i}. Score: {result.get('score', 0):.4f} {relevance_mark} | Content: {result_content}")
            else:
                logger.warning("  ⚠ No results found")
                all_passed = False
        
        # Note: This is a soft check - we warn but don't fail hard
        if all_passed:
            print("\n✓ Check 3 passed (compound queries are correct)")
        else:
            print("\n⚠ Check 3 partially passed (some compound queries may be inaccurate)")
    
    @pytest.mark.slow
    def test_performance_comparison(
        self,
        memory_without_sparse,
        test_queries: List[str],
        iterations: int = 5,
        limit: int = 10
    ):
        """
        Check 4: Performance comparison between sparse enabled/disabled.
        
        Args:
            memory_without_sparse: Memory instance with sparse disabled (from fixture)
            test_queries: Query list (from fixture)
            iterations: Number of runs per query (for averaging)
            limit: Number of results returned per search
        """
        print("\n" + "=" * 60)
        print("Performance comparison: sparse vs non-sparse")
        print("=" * 60)
        
        user_id = "test_user_sparse"
        
        print(f"Number of test queries: {len(test_queries)}")
        print(f"Iterations per query: {iterations} (average)")
        print(f"Result limit per search: {limit}")
        
        # Verify sparse service status
        has_sparse_1 = hasattr(memory_without_sparse, 'sparse_embedder') and memory_without_sparse.sparse_embedder is not None
        print(f"\n✓ Memory instance 1: sparse disabled - sparse_embedder={'initialized' if has_sparse_1 else 'not initialized'}")
        
        has_sparse_2 = hasattr(self.memory, 'sparse_embedder') and self.memory.sparse_embedder is not None
        if has_sparse_2:
            print("✓ Memory instance 2: sparse enabled - sparse_embedder initialized")
        else:
            logger.warning("⚠ Memory instance 2: sparse enabled but sparse_embedder is not initialized")
            logger.warning("  Performance comparison may be inaccurate")
        
        # Run performance comparison
        results_summary = []
        
        for query in test_queries:
            print(f"\n{'='*60}")
            print(f"Test query: {query}")
            print(f"{'='*60}")
            
            # Measure sparse-disabled path
            times_without = []
            original_level = logging.getLogger().level
            logging.getLogger().setLevel(logging.WARNING)
            
            for i in range(iterations):
                start_time = time.time()
                search_response = memory_without_sparse.search(
                    query=query, 
                    user_id=user_id, 
                    limit=limit
                )
                elapsed = time.time() - start_time
                times_without.append(elapsed)
            
            logging.getLogger().setLevel(original_level)
            
            avg_time_without = sum(times_without) / len(times_without)
            min_time_without = min(times_without)
            max_time_without = max(times_without)
            
            results = search_response.get("results", [])
            
            print("\n[Without sparse]")
            print(f"  Average time: {avg_time_without*1000:.2f} ms")
            print(f"  Min time: {min_time_without*1000:.2f} ms")
            print(f"  Max time: {max_time_without*1000:.2f} ms")
            print(f"  Result count: {len(results)}")
            
            # Measure sparse-enabled path
            times_with = []
            logging.getLogger().setLevel(logging.WARNING)
            
            for i in range(iterations):
                start_time = time.time()
                search_response = self.memory.search(
                    query=query, 
                    user_id=user_id, 
                    limit=limit
                )
                elapsed = time.time() - start_time
                times_with.append(elapsed)
            
            logging.getLogger().setLevel(original_level)
            
            avg_time_with = sum(times_with) / len(times_with)
            min_time_with = min(times_with)
            max_time_with = max(times_with)
            
            results = search_response.get("results", [])
            
            print("\n[With sparse]")
            print(f"  Average time: {avg_time_with*1000:.2f} ms")
            print(f"  Min time: {min_time_with*1000:.2f} ms")
            print(f"  Max time: {max_time_with*1000:.2f} ms")
            print(f"  Result count: {len(results)}")
            
            # Check whether sparse path is likely active
            has_sparse_active = hasattr(self.memory, 'sparse_embedder') and self.memory.sparse_embedder is not None
            if has_sparse_active:
                print("  ✓ sparse_embedder is initialized; sparse search should be active")
            else:
                logger.warning("  ⚠ sparse_embedder is not initialized; likely dense+fulltext only")
            
            # Compute performance delta
            time_diff = avg_time_with - avg_time_without
            time_diff_percent = (time_diff / avg_time_without * 100) if avg_time_without > 0 else 0
            
            print("\n[Performance diff]")
            print(f"  Time delta: {time_diff*1000:+.2f} ms ({time_diff_percent:+.1f}%)")
            
            if time_diff > 0:
                print(f"  ⚠ Enabling sparse increased latency by {time_diff*1000:.2f} ms")
            else:
                print(f"  ✓ Enabling sparse reduced latency by {abs(time_diff)*1000:.2f} ms")
            
            # Save result
            results_summary.append({
                'query': query,
                'without_sparse': {
                    'avg': avg_time_without,
                    'min': min_time_without,
                    'max': max_time_without,
                },
                'with_sparse': {
                    'avg': avg_time_with,
                    'min': min_time_with,
                    'max': max_time_with,
                },
                'diff': time_diff,
                'diff_percent': time_diff_percent,
            })
        
        # Print summary
        print(f"\n{'='*60}")
        print("Performance summary")
        print(f"{'='*60}")
        
        print(f"\n{'Query':<30} {'Without sparse(ms)':<18} {'With sparse(ms)':<16} {'Delta(ms)':<12} {'Delta(%)':<10}")
        print("-" * 80)
        
        total_avg_without = 0
        total_avg_with = 0
        
        for result in results_summary:
            print(
                f"{result['query']:<20} "
                f"{result['without_sparse']['avg']*1000:>12.2f}    "
                f"{result['with_sparse']['avg']*1000:>12.2f}    "
                f"{result['diff']*1000:>+10.2f}    "
                f"{result['diff_percent']:>+8.1f}%"
            )
            total_avg_without += result['without_sparse']['avg']
            total_avg_with += result['with_sparse']['avg']
        
        print("-" * 80)
        overall_avg_without = total_avg_without / len(results_summary)
        overall_avg_with = total_avg_with / len(results_summary)
        overall_diff = overall_avg_with - overall_avg_without
        overall_diff_percent = (overall_diff / overall_avg_without * 100) if overall_avg_without > 0 else 0
        
        print(
            f"{'Average':<30} "
            f"{overall_avg_without*1000:>12.2f}    "
            f"{overall_avg_with*1000:>12.2f}    "
            f"{overall_diff*1000:>+10.2f}    "
            f"{overall_diff_percent:>+8.1f}%"
        )
        
        print("\nOverall conclusion:")
        if overall_diff > 0:
            print(f"  Enabling sparse increased average latency by {overall_diff*1000:.2f} ms ({overall_diff_percent:+.1f}%)")
            print(f"  Impact: {'small' if overall_diff_percent < 30 else 'medium' if overall_diff_percent < 50 else 'large'}")
        else:
            print(f"  Enabling sparse reduced average latency by {abs(overall_diff)*1000:.2f} ms ({overall_diff_percent:+.1f}%)")
            print("  Improvement: sparse path likely parallelized, impact is small")
        
        print("\n✓ Performance comparison completed")


if __name__ == "__main__":
    # Allow running with python test_sparse_search.py for backward compatibility
    import subprocess
    import sys
    
    print("Running tests via pytest...")
    sys.exit(subprocess.run(["pytest", __file__, "-v"] + sys.argv[1:]).returncode)
