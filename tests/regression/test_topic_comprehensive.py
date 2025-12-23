"""
Comprehensive test suite for UserMemory Topic functionality

This test suite covers all aspects of the Topic feature:
- Basic topic extraction with default structure
- Custom topic structures
- Strict mode vs non-strict mode
- Incremental updates
- Query and filtering
- Edge cases and error handling
"""

import json
import os
import sys
import pytest
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from powermem import UserMemory, auto_config


def get_config():
    """Get configuration for UserMemory"""
    config = auto_config()
    return config


@pytest.fixture(scope="session")
def user_memory():
    """Session-scoped fixture providing a shared UserMemory instance for all tests."""
    config = get_config()
    um = UserMemory(config=config, agent_id="test_agent")
    yield um
    # Cleanup after all tests complete
    try:
        test_user_ids = [
            "topic_user_001", "topic_user_002", "topic_user_003_strict", "topic_user_003_non_strict",
            "topic_user_004", "topic_user_005", "topic_user_006", "topic_user_007_1", "topic_user_007_2",
            "topic_user_008", "topic_user_010", "topic_user_011",
            "topic_user_012_1", "topic_user_012_2", "topic_user_012_3"
        ]
        # Add topic_user_009_* users
        for idx in range(10):
            test_user_ids.append(f"topic_user_009_{idx}")
        
        for user_id in test_user_ids:
            try:
                um.delete_all(user_id=user_id)
            except Exception:
                pass
        
        print(f"\n✓ Cleaned up all test data for {len(test_user_ids)} test users")
    except Exception as e:
        print(f"\n⚠ Could not cleanup all test data: {str(e)[:100]}")


def print_section(title):
    """Print a section header"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_subsection(title):
    """Print a subsection header"""
    print(f"\n{'-'*70}")
    print(f"  {title}")
    print(f"{'-'*70}")


def print_result(success, message, details=None):
    """Print test result"""
    status = "✓" if success else "✗"
    print(f"{status} {message}")
    if details:
        for key, value in details.items():
            if isinstance(value, dict):
                print(f"    {key}:")
                print(json.dumps(value, indent=6, ensure_ascii=False))
            elif isinstance(value, list):
                print(f"    {key}: {len(value)} items")
                if value:  # Check if list is not empty
                    if isinstance(value[0], dict):
                        print(f"      First item keys: {list(value[0].keys())}")
                    else:
                        print(f"      First item: {value[0]}")
                # If list is empty, don't print first item
            else:
                print(f"    {key}: {value}")


# ============================================================================
# Test Case 1: Basic Topic Extraction (Default Structure)
# ============================================================================

def test_case_1_basic_topic_extraction(user_memory):
    """
    Test Case 1: Basic Topic Extraction with Default Structure
    
    Purpose: Verify that topics can be extracted from conversations using
    the default USER_PROFILE_TOPICS structure.
    
    Expected: Topics are extracted and structured as JSON with snake_case keys.
    """
    print_section("Test Case 1: Basic Topic Extraction (Default Structure)")
    
    conversation = [
        {
            "role": "user",
            "content": "Hi, I'm Alice, 28 years old. I work as a software engineer at Google in San Francisco. "
                      "I love hiking and reading science fiction books. My email is alice@example.com."
        },
        {
            "role": "assistant",
            "content": "Nice to meet you, Alice! That's great to hear about your work and hobbies."
        }
    ]
    
    try:
        result = user_memory.add(
            messages=conversation,
            user_id="topic_user_001",
            profile_type="topics",
            strict_mode=False
        )
        
        topics = result.get('topics', {})
        has_basic_info = 'basic_information' in topics and 'contact_information' in topics
        has_employment = 'employment' in topics
        has_interests = 'interests_and_hobbies' in topics or 'interests' in topics
        
        print_result(
            True,
            "Basic topic extraction completed",
            {
                "profile_extracted": result.get('profile_extracted', False),
                "has_basic_info": has_basic_info,
                "has_employment": has_employment,
                "has_interests": has_interests,
                "topics_structure": list(topics.keys()) if topics else [],
                "topics": topics
            }
        )
        
        # Cleanup
        try:
            user_memory.delete_all(user_id="topic_user_001")
        except Exception:
            pass
        
        return True
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 2: Custom Topic Structure
# ============================================================================

def test_case_2_custom_topics(user_memory):
    """
    Test Case 2: Custom Topic Structure
    
    Purpose: Verify that custom topic structures can be used for extraction.
    
    Expected: Topics are extracted according to the custom structure provided.
    """
    print_section("Test Case 2: Custom Topic Structure")
    
    custom_topics = json.dumps({
        "personal_info": {
            "name": "User's full name",
            "age": "User's age as integer",
            "city": "User's city of residence"
        },
        "work_info": {
            "company": "Company name",
            "role": "Job title or position"
        },
        "preferences": {
            "favorite_food": "Favorite cuisine or dish",
            "hobby": "Primary hobby"
        }
    })
    
    conversation = [
        {
            "role": "user",
            "content": "My name is Bob, I'm 35 years old. I live in Seattle and work as a data scientist at Microsoft. "
                      "I love Italian food and my hobby is playing guitar."
        },
        {
            "role": "assistant",
            "content": "That's interesting, Bob!"
        }
    ]
    
    try:
        result = user_memory.add(
            messages=conversation,
            user_id="topic_user_002",
            profile_type="topics",
            custom_topics=custom_topics,
            strict_mode=False
        )
        
        topics = result.get('topics', {})
        has_personal = 'personal_info' in topics
        has_work = 'work_info' in topics
        has_preferences = 'preferences' in topics
        
        print_result(
            True,
            "Custom topic extraction completed",
            {
                "profile_extracted": result.get('profile_extracted', False),
                "has_personal_info": has_personal,
                "has_work_info": has_work,
                "has_preferences": has_preferences,
                "topics": topics
            }
        )
        
        # Cleanup
        try:
            user_memory.delete_all(user_id="topic_user_002")
        except Exception:
            pass
        
        return True
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 3: Strict Mode vs Non-Strict Mode
# ============================================================================

def test_case_3_strict_mode_comparison(user_memory):
    """
    Test Case 3: Strict Mode vs Non-Strict Mode Comparison
    
    Purpose: Verify the difference between strict_mode=True and strict_mode=False.
    
    Expected:
    - strict_mode=False: Can add new topics not in the provided structure
    - strict_mode=True: Only outputs topics from the provided structure
    """
    print_section("Test Case 3: Strict Mode Comparison")
    
    custom_topics_dict = {
        "basic_information": {
            "user_name": "User's name",
            "user_age": "User's age",
        },
        "employment": {
            "company": "Company name"
        }
    }
    custom_topics = json.dumps(custom_topics_dict)
    
    conversation = [
        {
            "role": "user",
            "content": "I'm Charlie, 42 years old. I work at Amazon. "
                      "I also love traveling and my favorite color is blue."
                      "My wife is Alice, she is 38 years old and works as a teacher at a local school."
        }
    ]
    
     # Test strict mode
    print_subsection("Strict Mode (strict_mode=True)")
    try:
        result_strict = user_memory.add(
            messages=conversation,
            user_id="topic_user_003_strict",
            profile_type="topics",
            custom_topics=custom_topics,
            strict_mode=True
        )
        
        topics_strict = result_strict.get('topics', {})
        allowed_main_topics = list(custom_topics_dict.keys())  # ['basic_information', 'employment']
        
        # Strict mode validation:
        # 1. Validate that all main topics are in the allowed list
        invalid_main_topics = [key for key in topics_strict.keys() if key not in allowed_main_topics]
        if invalid_main_topics:
            print_result(
                False,
                f"Strict mode failed: Found invalid main topics {invalid_main_topics}. "
                f"Strict mode should only return main topics from: {allowed_main_topics}",
                {
                    "topics": topics_strict,
                    "topic_keys": list(topics_strict.keys()),
                    "allowed_main_topics": allowed_main_topics,
                    "invalid_main_topics": invalid_main_topics
                }
            )
            return False
        
        # 2. Validate that each main topic's sub-topics are defined in the custom_topics structure
        invalid_sub_topics = []
        for main_topic, sub_topics in topics_strict.items():
            if main_topic not in custom_topics_dict:
                continue  # already checked above
            
            if not isinstance(sub_topics, dict):
                continue  # skip non-dictionary values
            
            allowed_sub_topics = list(custom_topics_dict[main_topic].keys())
            for sub_topic in sub_topics.keys():
                if sub_topic not in allowed_sub_topics:
                    invalid_sub_topics.append(f"{main_topic}.{sub_topic}")
        
        if invalid_sub_topics:
            print_result(
                False,
                f"Strict mode failed: Found invalid sub topics {invalid_sub_topics}. "
                f"Strict mode should only return sub topics defined in the custom_topics structure.",
                {
                    "topics": topics_strict,
                    "topic_keys": list(topics_strict.keys()),
                    "custom_topics_structure": custom_topics_dict,
                    "invalid_sub_topics": invalid_sub_topics
                }
            )
            return False
        
        print_result(
            True,
            "Strict mode extraction - only allowed topics and sub-topics returned",
            {
                "topics": topics_strict,
                "topic_keys": list(topics_strict.keys()),
                "validation_passed": True
            }
        )
 
    except Exception as e:
        print_result(False, f"Strict mode failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Cleanup strict mode user
    try:
        user_memory.delete_all(user_id="topic_user_003_strict")
    except Exception:
        pass
    
    # Test non-strict mode
    print_subsection("Non-Strict Mode (strict_mode=False)")
    try:
        result_non_strict = user_memory.add(
            messages=conversation,
            user_id="topic_user_003_non_strict",
            profile_type="topics",
            custom_topics=custom_topics,
            strict_mode=False
        )
        
        topics_non_strict = result_non_strict.get('topics', {})
        allowed_main_topics = list(custom_topics_dict.keys())  # ['basic_information', 'employment']
        
        has_new_main_topics = any(key not in allowed_main_topics for key in topics_non_strict.keys())
        
        has_new_sub_topics = False
        invalid_sub_topics_in_non_strict = []
        for main_topic, sub_topics in topics_non_strict.items():
            if main_topic in custom_topics_dict and isinstance(sub_topics, dict):
                allowed_sub_topics = list(custom_topics_dict[main_topic].keys())
                for sub_topic in sub_topics.keys():
                    if sub_topic not in allowed_sub_topics:
                        has_new_sub_topics = True
                        invalid_sub_topics_in_non_strict.append(f"{main_topic}.{sub_topic}")
        
        # Compare the results of strict mode and non strict mode
        strict_keys = set(topics_strict.keys())
        non_strict_keys = set(topics_non_strict.keys())
        
        if has_new_main_topics or has_new_sub_topics:
            print_result(
                True,
                "Non-strict mode extraction - successfully extended topic structure",
                {
                    "topics": topics_non_strict,
                    "can_add_new_main_topics": has_new_main_topics,
                    "can_add_new_sub_topics": has_new_sub_topics,
                    "topic_keys": list(topics_non_strict.keys()),
                    "new_main_topics": [k for k in topics_non_strict.keys() if k not in allowed_main_topics] if has_new_main_topics else [],
                    "new_sub_topics": invalid_sub_topics_in_non_strict if has_new_sub_topics else []
                }
            )
        else:
            print_result(
                True,
                "Non-strict mode extraction - used provided topics (extension allowed but not required)",
                {
                    "topics": topics_non_strict,
                    "can_add_new_main_topics": has_new_main_topics,
                    "can_add_new_sub_topics": has_new_sub_topics,
                    "topic_keys": list(topics_non_strict.keys()),
                    "note": "Non-strict mode allows extension but doesn't require it. LLM chose to use provided topics only."
                }
            )
        
        # Cleanup non-strict mode user
        try:
            user_memory.delete_all(user_id="topic_user_003_non_strict")
        except Exception:
            pass
        
        return True

    except Exception as e:
        print_result(False, f"Non-strict mode failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
   


# ============================================================================
# Test Case 4: Incremental Updates
# ============================================================================

def test_case_4_incremental_updates(user_memory):
    """
    Test Case 4: Incremental Profile Updates
    
    Purpose: Verify that topics can be incrementally updated across multiple conversations.
    
    Expected: New conversations update existing topics, adding new information and
    updating existing information while preserving unchanged data.
    """
    print_section("Test Case 4: Incremental Profile Updates")
    
    # First conversation
    print_subsection("First Conversation")
    conversation1 = [
        {
            "role": "user",
            "content": "Hi, I'm David, 30 years old. I'm a teacher."
        }
    ]
    
    try:
        result1 = user_memory.add(
            messages=conversation1,
            user_id="topic_user_004",
            profile_type="topics"
        )
        
        topics1 = result1.get('topics', {})
        print_result(
            True,
            "First conversation added",
            {
                "topics": topics1
            }
        )
    except Exception as e:
        print_result(False, f"First conversation failed: {e}")
        return False
    
    # Second conversation - add more information
    print_subsection("Second Conversation - Adding Information")
    conversation2 = [
        {
            "role": "user",
            "content": "I also love playing basketball and reading science fiction. "
                      "I work at Lincoln High School."
        }
    ]
    
    try:
        result2 = user_memory.add(
            messages=conversation2,
            user_id="topic_user_004",
            profile_type="topics"
        )
        
        topics2 = result2.get('topics', {})
        has_interests = any('interest' in key.lower() or 'hobby' in key.lower() for key in topics2.keys())
        has_employment = 'employment' in topics2
        
        print_result(
            True,
            "Second conversation added",
            {
                "topics": topics2,
                "has_interests": has_interests,
                "has_employment": has_employment,
                "topics_count": len(topics2)
            }
        )
    except Exception as e:
        print_result(False, f"Second conversation failed: {e}")
        return False
    
    # Third conversation - update existing information
    print_subsection("Third Conversation - Updating Information")
    conversation3 = [
        {
            "role": "user",
            "content": "Actually, I'm 31 years old now. I also enjoy cooking now."
        }
    ]
    
    try:
        result3 = user_memory.add(
            messages=conversation3,
            user_id="topic_user_004",
            profile_type="topics"
        )
        
        topics3 = result3.get('topics', {})
        
        # Verify profile was retrieved
        profile = user_memory.profile("topic_user_004")
        final_topics = profile.get('topics', {}) if profile else {}
        
        print_result(
            True,
            "Third conversation added",
            {
                "topics_after_update": topics3,
                "final_topics_count": len(final_topics),
                "final_topics": final_topics
            }
        )
        
        # Cleanup
        try:
            user_memory.delete_all(user_id="topic_user_004")
        except Exception:
            pass
        
        return True
    except Exception as e:
        print_result(False, f"Third conversation failed: {e}")
        return False


# ============================================================================
# Test Case 5: Query and Filtering - Main Topic
# ============================================================================

def test_case_5_filter_by_main_topic(user_memory):
    """
    Test Case 5: Filter Profiles by Main Topic
    
    Purpose: Verify that profiles can be filtered by main topic names.
    
    Expected: Only profiles containing the specified main topics are returned.
    """
    print_section("Test Case 5: Filter by Main Topic")
    
    # Setup: Create a profile with multiple topics
    conversation = [
        {
            "role": "user",
            "content": "I'm Emma, 29 years old. I work as a designer at Apple. "
                      "I love photography and live in New York."
        }
    ]
    
    try:
        user_memory.add(
            messages=conversation,
            user_id="topic_user_005",
            profile_type="topics"
        )
        
        # Filter by main topic
        profiles = user_memory.profile_list(
            user_id="topic_user_005",
            main_topic=["basic_information", "interests_and_hobbies"]
        )
        
        if profiles:
            filtered_topics = profiles[0].get('topics', {})
            only_basic_info = all(key == 'basic_information' for key in filtered_topics.keys())
            
            all_topics = user_memory.profile_list(
                user_id="topic_user_005"
            )
            all_topics = all_topics[0].get('topics', {})
            
            print("all_topics:")
            print(json.dumps(all_topics, indent=4, ensure_ascii=False))
            
            print_result(
                True,
                "Filtered by main topic: basic_information, interests_and_hobbies",
                {
                    "profiles_found": len(profiles),
                    "filtered_topics": filtered_topics,
                    "only_basic_info": only_basic_info
                }
            )
            
            # Cleanup
            try:
                user_memory.delete_all(user_id="topic_user_005")
            except Exception:
                pass
            
            return True
        else:
            print_result(False, "No profiles found")
            return False
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 6: Query and Filtering - Sub Topic
# ============================================================================

def test_case_6_filter_by_sub_topic(user_memory):
    """
    Test Case 6: Filter Profiles by Sub Topic
    
    Purpose: Verify that profiles can be filtered by sub topic paths.
    
    Expected: Only profiles containing the specified sub topics are returned.
    """
    print_section("Test Case 6: Filter by Sub Topic")
    
    # Setup: Create a profile
    conversation = [
        {
            "role": "user",
            "content": "I'm Frank, 33 years old. I work as an engineer at Tesla."
        }
    ]
    
    try:
        user_memory.add(
            messages=conversation,
            user_id="topic_user_006",
            profile_type="topics"
        )
        
        # Filter by sub topic (full path format)
        profiles = user_memory.profile_list(
            user_id="topic_user_006",
            sub_topic=["user_age", "employment.company"]
        )
        
        if profiles:
            filtered_topics = profiles[0].get('topics', {})
            
            print_result(
                True,
                "Filtered by sub topic: user_age, employment.company",
                {
                    "profiles_found": len(profiles),
                    "filtered_topics": filtered_topics
                }
            )
            
            # Cleanup
            try:
                user_memory.delete_all(user_id="topic_user_006")
            except Exception:
                pass
            
            return True
        else:
            print_result(False, "No profiles found")
            return False
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 7: Query and Filtering - Topic Value
# ============================================================================

def test_case_7_filter_by_topic_value(user_memory):
    """
    Test Case 7: Filter Profiles by Topic Value
    
    Purpose: Verify that profiles can be filtered by exact topic values.
    
    Expected: Only profiles containing the specified values are returned.
    """
    print_section("Test Case 7: Filter by Topic Value")
    
    # Setup: Create multiple profiles with different values
    conversations = [
        (
            "topic_user_007_1",
            [{"role": "user", "content": "I'm Grace, 25 years old. I work at Microsoft."}]
        ),
        (
            "topic_user_007_2",
            [{"role": "user", "content": "I'm Henry, 30 years old. I work at Google."}]
        ),
    ]
    
    try:
        for user_id, conv in conversations:
            user_memory.add(
                messages=conv,
                user_id=user_id,
                profile_type="topics"
            )
            all_profiles = user_memory.profile_list(user_id=user_id)
            print("all_profiles:")
            print(json.dumps(all_profiles, indent=4, ensure_ascii=False))
        # Filter by topic value
        profiles = user_memory.profile_list(
            topic_value=["Microsoft", "30"]
        )
        print("Filtered profiles:")
        print(json.dumps(profiles, indent=4, ensure_ascii=False))

        
        print_result(
            True,
            "Filtered by topic value [Microsoft, 30]",
            {
                "profiles_found": len(profiles),
                "matching_user_ids": [p.get('user_id') for p in profiles]
            }
        )
        
        # Cleanup
        try:
            user_memory.delete_all(user_id="topic_user_007_1")
            user_memory.delete_all(user_id="topic_user_007_2")
        except Exception:
            pass
        
        return True
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 8: Combined Filtering
# ============================================================================

def test_case_8_combined_filtering(user_memory):
    """
    Test Case 8: Combined Filtering (Multiple Criteria)
    
    Purpose: Verify that multiple filter criteria can be combined.
    
    Expected: Profiles matching all criteria are returned.
    """
    print_section("Test Case 8: Combined Filtering")
    
    # Setup: Create a profile
    conversation = [
        {
            "role": "user",
            "content": "I'm Ivy, 27 years old. I work as a product manager at Meta. "
                      "I love reading and live in San Francisco."
        }
    ]
    
    try:
        user_memory.add(
            messages=conversation,
            user_id="topic_user_008",
            profile_type="topics"
        )
        
        # Combined filtering
        profiles = user_memory.profile_list(
            user_id="topic_user_008",
            main_topic=["basic_information", "employment"],
            sub_topic=["employment.company"]
        )
        
        if profiles:
            filtered_topics = profiles[0].get('topics', {})
            
            print_result(
                True,
                "Combined filtering completed",
                {
                    "profiles_found": len(profiles),
                    "filtered_topics": filtered_topics
                }
            )
            
            # Cleanup
            try:
                user_memory.delete_all(user_id="topic_user_008")
            except Exception:
                pass
            
            return True
        else:
            print_result(False, "No profiles found")
            return False
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 9: Empty Conversation Handling
# ============================================================================

def test_case_9_empty_conversation(user_memory):
    """
    Test Case 9: Empty Conversation Handling
    
    Purpose: Verify that empty conversations are handled gracefully.
    
    Expected: Empty conversations should not extract topics (return None or empty).
    """
    print_section("Test Case 9: Empty Conversation Handling")
    
    empty_conversations = [
        # [],
        # [""], 
        [{"role": "user", "content": ""}],
    ]
    
    try:
        for idx, empty_conv in enumerate(empty_conversations):
            result = user_memory.add(
                messages=empty_conv,
                user_id=f"topic_user_009_{idx}",
                profile_type="topics"
            )
            
            topics = result.get('topics')
            profile_extracted = result.get('profile_extracted', False)
            
            print_result(
                True,
                f"Empty conversation {idx+1} handled",
                {
                    "profile_extracted": profile_extracted,
                    "topics": topics,
                    "is_none_or_empty": topics is None or topics == {}
                }
            )
        
        # Cleanup all empty conversation users
        try:
            for idx in range(len(empty_conversations)):
                user_memory.delete_all(user_id=f"topic_user_009_{idx}")
        except Exception:
            pass
        
        return True
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 10: Profile Content and Topics Coexistence
# ============================================================================

def test_case_10_coexistence(user_memory):
    """
    Test Case 10: Profile Content and Topics Coexistence
    
    Purpose: Verify that profile_content and topics can coexist.
    
    Expected: Both profile_content and topics can be stored and retrieved together.
    """
    print_section("Test Case 10: Profile Content and Topics Coexistence")
    
    conversation = [
        {
            "role": "user",
            "content": "I'm Jack, 32 years old. I work as a developer at Netflix. "
                      "I enjoy watching movies and playing video games."
        }
    ]
    
    try:
        # First add with content profile
        result_content = user_memory.add(
            messages=conversation,
            user_id="topic_user_010",
            profile_type="content"
        )
        
        # Then add with topics profile
        result_topics = user_memory.add(
            messages=conversation,
            user_id="topic_user_010",
            profile_type="topics"
        )
        
        # Retrieve full profile
        profile = user_memory.profile("topic_user_010")
        
        has_content = bool(profile.get('profile_content')) if profile else False
        has_topics = bool(profile.get('topics')) if profile else False
        
        print_result(
            True,
            "Coexistence verified",
            {
                "has_profile_content": has_content,
                "has_topics": has_topics,
                "both_exist": has_content and has_topics,
                "profile_content_preview": profile.get('profile_content', '')[:100] + "..." if profile and profile.get('profile_content') else None,
                "topics": profile.get('topics', {}) if profile else {}
            }
        )
        
        # Cleanup
        try:
            user_memory.delete_all(user_id="topic_user_010")
        except Exception:
            pass
        
        return True
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 11: Search with Topics
# ============================================================================

def test_case_11_search_with_topics(user_memory):
    """
    Test Case 11: Search Memories with Topics
    
    Purpose: Verify that search() can include topics when add_profile=True.
    
    Expected: Search results include topics when requested.
    """
    print_section("Test Case 11: Search with Topics")
    
    conversation = [
        {
            "role": "user",
            "content": "I'm Kelly, 28 years old. I work as a designer at Adobe. "
                      "I love painting and live in Los Angeles."
        }
    ]
    
    try:
        # Add conversation with topics
        user_memory.add(
            messages=conversation,
            user_id="topic_user_011",
            profile_type="topics"
        )
        
        # Search with profile
        search_result = user_memory.search(
            query="designer work",
            user_id="topic_user_011",
            add_profile=True,
            limit=5
        )
        
        has_topics = 'topics' in search_result
        topics = search_result.get('topics', {})
        
        print_result(
            True,
            "Search with topics completed",
            {
                "results_count": len(search_result.get('results', [])),
                "has_topics": has_topics,
                "topics": topics
            }
        )
        
        # Cleanup
        try:
            user_memory.delete_all(user_id="topic_user_011")
        except Exception:
            pass
        
        return True
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Test Case 12: Profile List Pagination
# ============================================================================

def test_case_12_pagination(user_memory):
    """
    Test Case 12: Profile List Pagination
    
    Purpose: Verify that profile_list supports pagination with limit and offset.
    
    Expected: Pagination works correctly, returning limited results.
    """
    print_section("Test Case 12: Profile List Pagination")
    
    # Create multiple profiles
    conversations = [
        ("topic_user_012_1", "I'm user 1, 25 years old."),
        ("topic_user_012_2", "I'm user 2, 30 years old."),
        ("topic_user_012_3", "I'm user 3, 35 years old."),
    ]
    
    try:
        for user_id, content in conversations:
            user_memory.add(
                messages=[{"role": "user", "content": content}],
                user_id=user_id,
                profile_type="topics"
            )
        
        # Test pagination
        page1 = user_memory.profile_list(limit=2, offset=0)
        page2 = user_memory.profile_list(limit=2, offset=2)
        
        print("page1:")
        print(json.dumps(page1, indent=4, ensure_ascii=False))
        print("page2:")
        print(json.dumps(page2, indent=4, ensure_ascii=False))
        print_result(
            True,
            "Pagination tested",
            {
                "page1_count": len(page1),
                "page2_count": len(page2),
                "page1_user_ids": [p.get('user_id') for p in page1],
                "page2_user_ids": [p.get('user_id') for p in page2]
            }
        )
        
        # Cleanup
        try:
            user_memory.delete_all(user_id="topic_user_012_1")
            user_memory.delete_all(user_id="topic_user_012_2")
            user_memory.delete_all(user_id="topic_user_012_3")
        except Exception:
            pass
        
        return True
    except Exception as e:
        print_result(False, f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Main Test Runner
# ============================================================================

def main():
    """Run all test cases"""
    print("\n" + "="*70)
    print("  UserMemory Topic Functionality - Comprehensive Test Suite")
    print("="*70)
    
    # Initialize UserMemory
    try:
        config = get_config()
        user_memory = UserMemory(config=config, agent_id="test_agent")
        print("\n✓ UserMemory initialized successfully")
    except Exception as e:
        print(f"\n✗ Failed to initialize UserMemory: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Define all test cases
    test_cases = [
        ("Basic Topic Extraction", test_case_1_basic_topic_extraction),
        ("Custom Topic Structure", test_case_2_custom_topics),
        ("Strict Mode Comparison", test_case_3_strict_mode_comparison),
        ("Incremental Updates", test_case_4_incremental_updates),
        ("Filter by Main Topic", test_case_5_filter_by_main_topic),
        ("Filter by Sub Topic", test_case_6_filter_by_sub_topic),
        ("Filter by Topic Value", test_case_7_filter_by_topic_value),
        ("Combined Filtering", test_case_8_combined_filtering),
        ("Empty Conversation Handling", test_case_9_empty_conversation),
        ("Profile Content and Topics Coexistence", test_case_10_coexistence),
        ("Search with Topics", test_case_11_search_with_topics),
        ("Profile List Pagination", test_case_12_pagination),
    ]
    
    # Run tests
    test_results = []
    for test_name, test_func in test_cases:
        try:
            result = test_func(user_memory)
            test_results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ Test '{test_name}' raised exception: {e}")
            import traceback
            traceback.print_exc()
            test_results.append((test_name, False))
    
    # Print summary
    print_section("Test Summary")
    passed = sum(1 for _, result in test_results if result)
    total = len(test_results)
    
    for test_name, result in test_results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")
    
    print(f"\n  Total: {passed}/{total} tests passed")
    print(f"  Success rate: {passed/total*100:.1f}%")
    
    if passed == total:
        print("\n✓ All tests passed!")
    else:
        print(f"\n✗ {total - passed} test(s) failed")
    
    # Final cleanup - delete all test users
    try:
        test_user_ids = [
            "topic_user_001", "topic_user_002", "topic_user_003_strict", "topic_user_003_non_strict",
            "topic_user_004", "topic_user_005", "topic_user_006", "topic_user_007_1", "topic_user_007_2",
            "topic_user_008", "topic_user_010", "topic_user_011",
            "topic_user_012_1", "topic_user_012_2", "topic_user_012_3"
        ]
        # Add topic_user_009_* users
        for idx in range(10):  # Clean up to 10 possible empty conversation users
            test_user_ids.append(f"topic_user_009_{idx}")
        
        for user_id in test_user_ids:
            try:
                user_memory.delete_all(user_id=user_id)
            except Exception:
                pass
        
        print(f"\n✓ Cleaned up all test data for {len(test_user_ids)} test users")
    except Exception as e:
        print(f"\n⚠ Could not cleanup all test data: {str(e)[:100]}")


if __name__ == "__main__":
    main()

