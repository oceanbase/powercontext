# Scenario 9: User Profile Management

This scenario demonstrates powermem's UserMemory feature - automatic user profile extraction, management, and integration with memory search.

## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)
- LLM provider configured (for profile extraction)
- OceanBase configured as vector store (UserMemory requires OceanBase)

> **Important Note**: `UserMemory` requires OceanBase as the storage backend. If you attempt to use `UserMemory` with a different storage provider, it will raise a `ValueError`. Please configure OceanBase as your vector store provider.

## Understanding User Profile Management

UserMemory extends Memory with user profile capabilities:

- **Automatic profile extraction**: Extracts user-related information from conversations (name, profession, interests, preferences, etc.)
- **Continuous profile updates**: Updates and refines the profile based on new conversations
- **Profile storage**: Stores profiles separately from memories for efficient retrieval
- **Joint search**: Optionally includes profile information when searching memories

## Step 1: Initialize UserMemory

First, let's initialize UserMemory with proper configuration:

```python
# user_profile_example.py
from powermem import UserMemory, auto_config

# Load configuration (auto-loads from .env or uses defaults)
config = auto_config()

# Create UserMemory instance
user_memory = UserMemory(config=config)

print("✓ UserMemory initialized successfully!")
```

**Run this code:**
```bash
python user_profile_example.py
```

**Expected output:**
```
✓ UserMemory initialized successfully!
```

## Step 2: Add Conversation and Extract Profile

Add a conversation and automatically extract user profile information:

```python
# user_profile_example.py
from powermem import UserMemory, auto_config

config = auto_config()
user_memory = UserMemory(config=config)

# Add a conversation
conversation = [
    {"role": "user", "content": "Hi, I'm Alice. I'm a 28-year-old software engineer from San Francisco. I work at a tech startup and love Python programming."},
    {"role": "assistant", "content": "Nice to meet you, Alice! Python is a great language for software engineering."}
]

result = user_memory.add(
    messages=conversation,
    user_id="user_001",
    agent_id="assistant_agent",
    run_id="session_001"
)

print(f"✓ Conversation added successfully")
print(f"  - Profile extracted: {result.get('profile_extracted', False)}")
if result.get('profile_content'):
    print(f"  - Profile content: {result['profile_content']}")
print(f"  - Memory results count: {len(result.get('results', []))}")
```

**Run this code:**
```bash
python user_profile_example.py
```

**Expected output:**
```
✓ Conversation added successfully
  - Profile extracted: True
  - Profile content: Name: Alice. Age: 28. Location: San Francisco. Profession: Software engineer at a tech startup. Interests: Python programming.
  - Memory results count: 1
```

## Step 3: Update Profile with More Conversations

Add more conversations to update and refine the user profile:

```python
# user_profile_example.py
from powermem import UserMemory, auto_config

config = auto_config()
user_memory = UserMemory(config=config)

# First conversation
conversation1 = [
    {"role": "user", "content": "Hi, I'm Alice. I'm a software engineer from San Francisco."},
    {"role": "assistant", "content": "Nice to meet you, Alice!"}
]

result1 = user_memory.add(
    messages=conversation1,
    user_id="user_001",
    agent_id="assistant_agent"
)

print("=== First conversation ===")
print(f"Profile extracted: {result1.get('profile_extracted', False)}")
if result1.get('profile_content'):
    print(f"Profile: {result1['profile_content']}")

# Second conversation - adds more information
conversation2 = [
    {"role": "user", "content": "I also enjoy reading science fiction novels and playing tennis on weekends."},
    {"role": "assistant", "content": "That sounds like great hobbies!"}
]

result2 = user_memory.add(
    messages=conversation2,
    user_id="user_001",
    agent_id="assistant_agent"
)

print("\n=== Second conversation ===")
print(f"Profile updated: {result2.get('profile_extracted', False)}")
if result2.get('profile_content'):
    print(f"Updated profile: {result2['profile_content']}")
```

**Run this code:**
```bash
python user_profile_example.py
```

**Expected output:**
```
=== First conversation ===
Profile extracted: True
Profile: Name: Alice. Profession: Software engineer. Location: San Francisco.

=== Second conversation ===
Profile updated: True
Updated profile: Name: Alice. Profession: Software engineer. Location: San Francisco. Hobbies: Reading science fiction novels, playing tennis.
```

## Step 4: Get User Profile

Retrieve the user profile directly:

```python
# user_profile_example.py
from powermem import UserMemory, auto_config

config = auto_config()
user_memory = UserMemory(config=config)

# Add some conversations first
conversation = [
    {"role": "user", "content": "I'm Bob, a data scientist. I love machine learning and hiking."},
    {"role": "assistant", "content": "Great to meet you, Bob!"}
]

user_memory.add(
    messages=conversation,
    user_id="user_002",
    agent_id="assistant_agent"
)

# Get the user profile
profile = user_memory.profile(
    user_id="user_002"
)

if profile:
    print("✓ Profile retrieved successfully")
    print(f"  - Profile ID: {profile.get('id')}")
    print(f"  - User ID: {profile.get('user_id')}")
    if profile.get('profile_content'):
        print(f"  - Profile content: {profile.get('profile_content', '')}")
    if profile.get('topics'):
        print(f"  - Topics: {profile.get('topics')}")
    print(f"  - Created at: {profile.get('created_at')}")
    print(f"  - Updated at: {profile.get('updated_at')}")
else:
    print("✗ No profile found")
```

**Run this code:**
```bash
python user_profile_example.py
```

**Expected output:**
```
✓ Profile retrieved successfully
  - Profile ID: 1234567890123456789
  - User ID: user_002
  - Agent ID: assistant_agent
  - Run ID: 
  - Profile content: Name: Bob. Profession: Data scientist. Interests: Machine learning, hiking.
  - Created at: 2024-01-15T10:30:00
  - Updated at: 2024-01-15T10:30:00
```

## Step 5: Search Memories Without Profile

Search memories without including the profile:

```python
# user_profile_example.py
from powermem import UserMemory, auto_config

config = auto_config()
user_memory = UserMemory(config=config)

user_id = "user_003"

# Add some conversations
user_memory.add(
    messages=[
        {"role": "user", "content": "I'm Charlie, a product manager. I work on mobile apps."},
        {"role": "assistant", "content": "Interesting!"}
    ],
    user_id=user_id,
    agent_id="assistant_agent"
)

user_memory.add(
    messages=[
        {"role": "user", "content": "I prefer agile development methodology."},
        {"role": "assistant", "content": "That's a popular approach."}
    ],
    user_id=user_id,
    agent_id="assistant_agent"
)

# Search without profile
results = user_memory.search(
    query="work and preferences",
    user_id=user_id,
    agent_id="assistant_agent",
    limit=5,
    add_profile=False  # Don't include profile
)

print(f"✓ Search completed")
print(f"  - Results count: {len(results.get('results', []))}")
print(f"  - Profile included: {'profile_content' in results}")

for i, result in enumerate(results.get('results', []), 1):
    print(f"  {i}. {result.get('memory', '')} (score: {result.get('score', 0):.2f})")
```

**Run this code:**
```bash
python user_profile_example.py
```

**Expected output:**
```
✓ Search completed
  - Results count: 2
  - Profile included: False
  1. Works on mobile apps as a product manager (score: 0.85)
  2. Prefers agile development methodology (score: 0.78)
```

## Step 6: Search Memories With Profile

Search memories and include the user profile in results:

```python
# user_profile_example.py
from powermem import UserMemory, auto_config

config = auto_config()
user_memory = UserMemory(config=config)

user_id = "user_004"

# Add conversations
user_memory.add(
    messages=[
        {"role": "user", "content": "I'm Diana, a UX designer. I love creating beautiful interfaces."},
        {"role": "assistant", "content": "That's wonderful!"}
    ],
    user_id=user_id,
    agent_id="assistant_agent"
)

# Search with profile
results = user_memory.search(
    query="user background and interests",
    user_id=user_id,
    agent_id="assistant_agent",
    limit=5,
    add_profile=True  # Include profile
)

print(f"✓ Search with profile completed")
print(f"  - Results count: {len(results.get('results', []))}")
print(f"  - Profile included: {'profile_content' in results}")

if 'profile_content' in results:
    print(f"\n  User Profile:")
    print(f"  {results['profile_content']}")

print(f"\n  Search Results:")
for i, result in enumerate(results.get('results', []), 1):
    print(f"  {i}. {result.get('memory', '')} (score: {result.get('score', 0):.2f})")
```

**Run this code:**
```bash
python user_profile_example.py
```

**Expected output:**
```
✓ Search with profile completed
  - Results count: 1
  - Profile included: True

  User Profile:
  Name: Diana. Profession: UX designer. Interests: Creating beautiful interfaces.

  Search Results:
  1. Works as a UX designer, loves creating beautiful interfaces (score: 0.92)
```

## Step 7: Delete User Profile

Delete a user profile:

```python
# user_profile_example.py
from powermem import UserMemory, auto_config

config = auto_config()
user_memory = UserMemory(config=config, agent_id="assistant_agent")

user_id = "user_007"

# Add conversation and create profile
user_memory.add(
    messages=[
        {"role": "user", "content": "I'm Grace, a teacher."},
        {"role": "assistant", "content": "Nice to meet you!"}
    ],
    user_id=user_id
)

# Get profile to confirm it exists
profile = user_memory.profile(user_id=user_id)
if profile:
    profile_id = profile.get('id')
    print(f"✓ Profile exists with ID: {profile_id}")
    
    # Delete the profile
    deleted = user_memory.delete_profile(
        user_id=user_id
    )
    
    if deleted:
        print(f"✓ Profile deleted successfully")
    else:
        print(f"✗ Failed to delete profile")
    
    # Verify deletion
    profile_after = user_memory.profile(user_id=user_id)
    if not profile_after:
        print("✓ Profile confirmed deleted")
    else:
        print("✗ Profile still exists")
```

**Run this code:**
```bash
python user_profile_example.py
```

**Expected output:**
```
✓ Profile exists with ID: 1234567890123456789
✓ Profile deleted successfully
✓ Profile confirmed deleted
```

## Complete Example

Here's a complete example combining all the features:

```python
# complete_user_profile_example.py
from powermem import UserMemory, auto_config

def main():
    # Load configuration
    config = auto_config()
    
    # Initialize UserMemory
    user_memory = UserMemory(config=config, agent_id="demo_agent")
    user_id = "demo_user"
    
    print("=" * 60)
    print("UserMemory Profile Management Example")
    print("=" * 60)
    
    # Step 1: Add initial conversation
    print("\n1. Adding initial conversation...")
    conversation1 = [
        {"role": "user", "content": "Hi, I'm Alex, a 32-year-old data scientist from New York. I specialize in machine learning and love reading tech blogs."},
        {"role": "assistant", "content": "Nice to meet you, Alex! That's fascinating."}
    ]
    
    result1 = user_memory.add(
        messages=conversation1,
        user_id=user_id,
        run_id="session_001"
    )
    
    print(f"   ✓ Profile extracted: {result1.get('profile_extracted', False)}")
    if result1.get('profile_content'):
        print(f"   Profile: {result1['profile_content']}")
    
    # Step 2: Update profile with more information
    print("\n2. Updating profile with more conversations...")
    conversation2 = [
        {"role": "user", "content": "I also enjoy hiking and photography on weekends."},
        {"role": "assistant", "content": "Those are great hobbies!"}
    ]
    
    result2 = user_memory.add(
        messages=conversation2,
        user_id=user_id,
        run_id="session_002"
    )
    
    print(f"   ✓ Profile updated: {result2.get('profile_extracted', False)}")
    
    # Step 3: Get full profile
    print("\n3. Retrieving full profile...")
    profile = user_memory.profile(
        user_id=user_id
    )
    
    if profile:
        print(f"   ✓ Profile ID: {profile.get('id')}")
        print(f"   Profile content: {profile.get('profile_content', '')}")
        print(f"   Last updated: {profile.get('updated_at')}")
    
    # Step 4: Search with profile
    print("\n4. Searching memories with profile...")
    search_results = user_memory.search(
        query="user interests and hobbies",
        user_id=user_id,
        limit=5,
        add_profile=True
    )
    
    if 'profile_content' in search_results:
        print(f"   User Profile: {search_results['profile_content']}")
    
    print(f"\n   Found {len(search_results.get('results', []))} memories:")
    for i, result in enumerate(search_results.get('results', []), 1):
        print(f"   {i}. {result.get('memory', '')} (score: {result.get('score', 0):.2f})")
    
    # Step 5: Cleanup (optional)
    print("\n5. Cleaning up...")
    # Note: In production, you might want to keep the profiles
    # deleted = user_memory.delete_profile(user_id=user_id)
    # if deleted:
    #     print("   ✓ Profile deleted")
    
    print("\n" + "=" * 60)
    print("Example completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
```

**Run this code:**
```bash
python complete_user_profile_example.py
```

## Best Practices

1. **Always provide `user_id`**: The `add()` method requires `user_id`; ensure each user has a unique identifier

2. **Use `agent_id` to distinguish agents**: In multi-agent scenarios, use `agent_id` to separate profiles and memories across agents

3. **Use `run_id` appropriately**: Use `run_id` to distinguish sessions or runs for more precise profile management

4. **Check profiles regularly**: Use `profile()` to periodically check and ensure profile information remains accurate

5. **Include profile when needed**: When additional context is useful, set `add_profile=True` to include the user profile in search results

6. **Handle empty profiles**: If `profile()` returns `{}`, no profile has been extracted yet; call `add()` with conversation data first

7. **Profile updates**: Profiles are automatically updated when you add new conversations with the same `user_id`, `agent_id`, and `run_id` combination

## Related Documents

- [UserMemory Guide](../guides/0010-user_memory.md) - Detailed guide on UserMemory features
- [Getting Started](scenario_1_basic_usage.md) - Learn the basics of PowerMem
- [Multi-Agent Guide](scenario_3_multi_agent.md) - Using multiple agents with Memory
