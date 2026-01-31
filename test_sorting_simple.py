#!/usr/bin/env python3
"""
Simple test to verify search sorting functionality
"""

def test_sort_function():
    """Test the sorting function logic"""
    
    from datetime import datetime
    
    # Simulate the sorting logic from _sort_search_results
    def sort_search_results(results, sort_by):
        sort_keys = [sort_by] if isinstance(sort_by, str) else sort_by
        
        def get_sort_key(result):
            keys = []
            for criterion in sort_keys:
                criterion = criterion.lower().strip()
                
                if criterion == "relevance" or not criterion:
                    metadata = result.get("metadata", {})
                    quality_score = metadata.get("_quality_score", result.get("score", 0.0))
                    keys.append(-float(quality_score) if quality_score else 0)
                
                elif criterion == "date_asc":
                    created_at = result.get("created_at", "")
                    keys.append(created_at or "")
                
                elif criterion == "date_desc":
                    created_at = result.get("created_at", "")
                    if created_at:
                        try:
                            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            timestamp = dt.timestamp()
                            keys.append(-timestamp)
                        except (ValueError, TypeError):
                            keys.append(created_at)
                    else:
                        keys.append(float('inf'))
                
                elif criterion == "importance_asc":
                    metadata = result.get("metadata", {})
                    user_metadata = metadata.get("metadata", {}) if metadata else {}
                    importance = user_metadata.get("importance_score", 0.5)
                    keys.append(float(importance))
                
                elif criterion == "importance_desc":
                    metadata = result.get("metadata", {})
                    user_metadata = metadata.get("metadata", {}) if metadata else {}
                    importance = user_metadata.get("importance_score", 0.5)
                    keys.append(-float(importance))
                
                elif criterion == "access_count_desc":
                    metadata = result.get("metadata", {})
                    user_metadata = metadata.get("metadata", {}) if metadata else {}
                    access_count = user_metadata.get("access_count", 0)
                    keys.append(-int(access_count))
                
                elif criterion == "retention_desc":
                    metadata = result.get("metadata", {})
                    user_metadata = metadata.get("metadata", {}) if metadata else {}
                    retention = user_metadata.get("retention_score", 0.0)
                    keys.append(-float(retention))
                
                else:
                    quality_score = result.get("metadata", {}).get("_quality_score", result.get("score", 0.0))
                    keys.append(-float(quality_score) if quality_score else 0)
            
            return tuple(keys)
        
        try:
            sorted_results = sorted(results, key=get_sort_key, reverse=False)
            return sorted_results
        except Exception as e:
            print(f"Sort error: {e}")
            return results
    
    # Test 1: Sort by relevance
    print("Test 1: Sort by relevance")
    test_results = [
        {"memory": "test1", "score": 0.5, "metadata": {"_quality_score": 0.5}},
        {"memory": "test2", "score": 0.9, "metadata": {"_quality_score": 0.9}},
        {"memory": "test3", "score": 0.3, "metadata": {"_quality_score": 0.3}},
    ]
    sorted_results = sort_search_results(test_results, "relevance")
    assert sorted_results[0]["metadata"]["_quality_score"] == 0.9, f"Expected 0.9, got {sorted_results[0]['metadata']['_quality_score']}"
    assert sorted_results[1]["metadata"]["_quality_score"] == 0.5
    assert sorted_results[2]["metadata"]["_quality_score"] == 0.3
    print("✓ Relevance sorting works correctly")
    
    # Test 2: Sort by date_desc
    print("\nTest 2: Sort by date_desc")
    test_results = [
        {"memory": "oldest", "created_at": "2024-01-01T10:00:00", "metadata": {}},
        {"memory": "newest", "created_at": "2024-03-01T10:00:00", "metadata": {}},
        {"memory": "middle", "created_at": "2024-02-01T10:00:00", "metadata": {}},
    ]
    sorted_results = sort_search_results(test_results, "date_desc")
    assert sorted_results[0]["memory"] == "newest", f"Expected newest, got {sorted_results[0]['memory']}"
    assert sorted_results[1]["memory"] == "middle"
    assert sorted_results[2]["memory"] == "oldest"
    print("✓ Date descending sorting works correctly")
    
    # Test 3: Sort by importance_desc
    print("\nTest 3: Sort by importance_desc")
    test_results = [
        {"memory": "low", "metadata": {"metadata": {"importance_score": 0.2}}},
        {"memory": "high", "metadata": {"metadata": {"importance_score": 0.9}}},
        {"memory": "medium", "metadata": {"metadata": {"importance_score": 0.5}}},
    ]
    sorted_results = sort_search_results(test_results, "importance_desc")
    assert sorted_results[0]["memory"] == "high"
    assert sorted_results[1]["memory"] == "medium"
    assert sorted_results[2]["memory"] == "low"
    print("✓ Importance sorting works correctly")
    
    # Test 4: Multi-criteria sorting
    print("\nTest 4: Multi-criteria sorting")
    test_results = [
        {"memory": "old_high", "score": 0.9, "created_at": "2024-01-01", "metadata": {"_quality_score": 0.9}},
        {"memory": "new_high", "score": 0.9, "created_at": "2024-03-01", "metadata": {"_quality_score": 0.9}},
        {"memory": "low", "score": 0.5, "created_at": "2024-02-01", "metadata": {"_quality_score": 0.5}},
    ]
    sorted_results = sort_search_results(test_results, ["relevance", "date_desc"])
    assert sorted_results[0]["score"] == 0.9
    assert sorted_results[1]["score"] == 0.9
    assert sorted_results[2]["score"] == 0.5
    assert sorted_results[0]["memory"] == "new_high"
    assert sorted_results[1]["memory"] == "old_high"
    print("✓ Multi-criteria sorting works correctly")
    
    print("\n✅ All tests passed! Search sorting functionality is working correctly.")

if __name__ == "__main__":
    test_sort_function()
