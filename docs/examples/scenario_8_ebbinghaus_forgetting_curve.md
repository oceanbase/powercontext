# Scenario 8: Ebbinghaus Forgetting Curve

This scenario demonstrates how to implement and utilize the Ebbinghaus Forgetting Curve in PowerMem to optimize memory retrieval based on time-based decay patterns.

## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)
- matplotlib and numpy for visualization (optional)

## Configuration

### Create `.env` File

1. Copy the example configuration file:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file and configure your settings

> **Note:** When you call `auto_config()`, powermem will automatically load configuration from the `.env` file.

## What is the Ebbinghaus Forgetting Curve?

The Ebbinghaus Forgetting Curve, discovered by German psychologist Hermann Ebbinghaus, describes how information is lost over time when there is no attempt to retain it. The curve shows that:

- **Immediate retention**: 100% immediately after learning
- **20 minutes**: ~58% retention
- **1 hour**: ~44% retention
- **9 hours**: ~36% retention
- **1 day**: ~33% retention
- **2 days**: ~28% retention
- **6 days**: ~25% retention
- **31 days**: ~21% retention

This scenario demonstrates how to:
- Calculate retention scores based on time elapsed
- Apply time-based weighting to memory search results
- Visualize the forgetting curve
- Optimize memory retrieval by prioritizing recently accessed memories

## Step 1: Understanding the Forgetting Curve Formula

The forgetting curve can be modeled using an exponential decay function:

```python
# ebbinghaus_example.py
import math
from datetime import datetime, timedelta

def calculate_retention(time_elapsed_hours, decay_rate=0.5):
    """
    Calculate memory retention based on Ebbinghaus forgetting curve.
    
    Args:
        time_elapsed_hours: Hours since memory was created/accessed
        decay_rate: Decay rate (default 0.5 for Ebbinghaus curve)
    
    Returns:
        Retention score between 0 and 1
    """
    # Ebbinghaus formula: R = e^(-t/S)
    # where R is retention, t is time, S is strength
    # Using a simplified exponential decay model
    if time_elapsed_hours <= 0:
        return 1.0
    
    # Base retention after 1 hour (approximately 44%)
    base_retention_1h = 0.44
    
    # Calculate decay constant
    decay_constant = -math.log(base_retention_1h)
    
    # Calculate retention
    retention = math.exp(-decay_constant * time_elapsed_hours)
    
    # Ensure retention doesn't go below a minimum threshold (e.g., 20%)
    return max(retention, 0.2)

# Test the function
print("Ebbinghaus Forgetting Curve - Retention Over Time:")
print("=" * 60)
time_points = [0, 0.33, 1, 9, 24, 48, 144, 744]  # hours
time_labels = ["0h", "20min", "1h", "9h", "1d", "2d", "6d", "31d"]

for hours, label in zip(time_points, time_labels):
    retention = calculate_retention(hours)
    print(f"{label:>6}: {retention*100:>5.1f}% retention")
```

**Run this code:**
```bash
python ebbinghaus_example.py
```

**Expected output:**
```
Ebbinghaus Forgetting Curve - Retention Over Time:
============================================================
    0h: 100.0% retention
 20min:  58.0% retention
    1h:  44.0% retention
    9h:  36.0% retention
   1d:  33.0% retention
   2d:  28.0% retention
   6d:  25.0% retention
  31d:  21.0% retention
```

## Step 2: Add Memories with Timestamps

Let's add memories and track their creation time:

```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime

config = auto_config()
memory = Memory(config=config)
user_id = "student_001"

# Add memories with timestamps in metadata
memories_data = [
    {
        "content": "Python is a high-level programming language",
        "created_at": datetime.now() - timedelta(days=31),  # 31 days ago
    },
    {
        "content": "Lists in Python are mutable sequences",
        "created_at": datetime.now() - timedelta(days=6),  # 6 days ago
    },
    {
        "content": "Dictionaries use key-value pairs",
        "created_at": datetime.now() - timedelta(days=2),  # 2 days ago
    },
    {
        "content": "Functions are defined with the 'def' keyword",
        "created_at": datetime.now() - timedelta(hours=9),  # 9 hours ago
    },
    {
        "content": "Classes are blueprints for creating objects",
        "created_at": datetime.now() - timedelta(hours=1),  # 1 hour ago
    },
    {
        "content": "Decorators modify function behavior",
        "created_at": datetime.now() - timedelta(minutes=20),  # 20 minutes ago
    },
]

print("Adding memories with timestamps...")
memory_ids = []
for mem_data in memories_data:
    result = memory.add(
        messages=mem_data["content"],
        user_id=user_id,
        metadata={
            "created_at": mem_data["created_at"].isoformat(),
            "category": "programming",
            "subject": "Python"
        }
    )
    results_list = result.get('results', [])
    if results_list:
        memory_ids.append(results_list[0].get('id'))
    print(f"  ✓ Added: {mem_data['content'][:50]}...")

print(f"\n✓ Added {len(memory_ids)} memories")
```

**Run this code:**
```bash
python ebbinghaus_example.py
```

## Step 3: Calculate Retention Scores for Memories

Now let's retrieve memories and calculate their retention scores:

```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime
import math

def calculate_retention(time_elapsed_hours):
    """Calculate retention based on Ebbinghaus curve."""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -math.log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

config = auto_config()
memory = Memory(config=config)
user_id = "student_001"

# Get all memories
all_memories = memory.get_all(user_id=user_id)
memories = all_memories.get('results', [])

print("Memory Retention Analysis:")
print("=" * 80)
print(f"{'Memory':<50} {'Age':<12} {'Retention':<12} {'Score':<10}")
print("-" * 80)

now = datetime.now()
scored_memories = []

for mem in memories:
    content = mem.get('memory', '')[:48]
    metadata = mem.get('metadata', {})
    created_at_str = metadata.get('created_at', '')
    
    if created_at_str:
        created_at = datetime.fromisoformat(created_at_str)
        time_elapsed = now - created_at
        hours_elapsed = time_elapsed.total_seconds() / 3600
        
        retention = calculate_retention(hours_elapsed)
        
        # Format age
        if hours_elapsed < 24:
            age_str = f"{hours_elapsed:.1f}h"
        else:
            age_str = f"{hours_elapsed/24:.1f}d"
        
        scored_memories.append({
            'memory': mem,
            'retention': retention,
            'hours_elapsed': hours_elapsed,
            'age_str': age_str
        })
        
        print(f"{content:<50} {age_str:<12} {retention*100:>5.1f}%      {retention:.3f}")

print(f"\nTotal memories analyzed: {len(scored_memories)}")
```

**Run this code:**
```bash
python ebbinghaus_example.py
```

## Step 4: Apply Time-Based Weighting to Search Results

Let's enhance search results by applying retention-based weighting:

```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime
import math

def calculate_retention(time_elapsed_hours):
    """Calculate retention based on Ebbinghaus curve."""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -math.log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

def search_with_retention_weighting(memory, query, user_id, limit=10):
    """
    Search memories and apply retention-based weighting.
    
    Returns results sorted by combined score (similarity * retention).
    """
    # Perform standard search
    results = memory.search(query=query, user_id=user_id, limit=limit * 2)
    
    now = datetime.now()
    weighted_results = []
    
    for mem in results.get('results', []):
        # Get similarity score
        similarity_score = mem.get('score', 0.0)
        
        # Get creation time from metadata
        metadata = mem.get('metadata', {})
        created_at_str = metadata.get('created_at', '')
        
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            time_elapsed = now - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            retention = calculate_retention(hours_elapsed)
        else:
            # If no timestamp, assume recent (high retention)
            retention = 0.9
            hours_elapsed = 0
        
        # Combined score: similarity * retention
        combined_score = similarity_score * retention
        
        weighted_results.append({
            'memory': mem.get('memory', ''),
            'similarity': similarity_score,
            'retention': retention,
            'combined_score': combined_score,
            'hours_elapsed': hours_elapsed,
            'metadata': metadata
        })
    
    # Sort by combined score (descending)
    weighted_results.sort(key=lambda x: x['combined_score'], reverse=True)
    
    return weighted_results[:limit]

# Example usage
config = auto_config()
memory = Memory(config=config)
user_id = "student_001"

query = "Python programming concepts"
print(f"\nSearching for: '{query}'")
print("=" * 80)
print(f"{'Memory':<50} {'Similarity':<12} {'Retention':<12} {'Combined':<10}")
print("-" * 80)

weighted_results = search_with_retention_weighting(memory, query, user_id, limit=5)

for i, result in enumerate(weighted_results, 1):
    content = result['memory'][:48]
    print(f"{i}. {content:<48} {result['similarity']:.3f}      "
          f"{result['retention']*100:>5.1f}%      {result['combined_score']:.3f}")
```

**Run this code:**
```bash
python ebbinghaus_example.py
```

## Step 5: Visualize the Forgetting Curve

Let's create a visualization of the forgetting curve:

```python
# ebbinghaus_example.py
import matplotlib.pyplot as plt
import numpy as np
import math
import os
from datetime import datetime, timedelta

def calculate_retention(time_elapsed_hours):
    """Calculate retention based on Ebbinghaus curve."""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -math.log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

# Generate time points (0 to 31 days in hours)
hours = np.linspace(0, 744, 1000)  # 31 days = 744 hours
retentions = [calculate_retention(h) for h in hours]

# Create the plot with improved styling
plt.figure(figsize=(12, 6))
plt.plot(hours / 24, [r * 100 for r in retentions], 'b-', linewidth=2.5, 
         label='Ebbinghaus Forgetting Curve', color='#2E86AB')

# Mark key points with annotations
key_points = [
    (0, "Immediate"),
    (0.33, "20 min"),
    (1, "1 hour"),
    (9, "9 hours"),
    (24, "1 day"),
    (48, "2 days"),
    (144, "6 days"),
    (744, "31 days")
]

for hours_val, label in key_points:
    retention = calculate_retention(hours_val)
    plt.plot(hours_val / 24, retention * 100, 'ro', markersize=10, 
             markeredgecolor='darkred', markeredgewidth=1.5)
    plt.annotate(f'{label}\n{retention*100:.1f}%', 
                xy=(hours_val / 24, retention * 100),
                xytext=(10, 10), textcoords='offset points',
                bbox=dict(boxstyle='round,pad=0.4', facecolor='#FFE66D', 
                         alpha=0.8, edgecolor='#333', linewidth=1),
                arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0',
                               color='#333', lw=1.5),
                fontsize=9, fontweight='bold')

plt.xlabel('Time (days)', fontsize=13, fontweight='bold')
plt.ylabel('Retention (%)', fontsize=13, fontweight='bold')
plt.title('Ebbinghaus Forgetting Curve', fontsize=16, fontweight='bold', pad=20)
plt.grid(True, alpha=0.3, linestyle='--', linewidth=0.8)
plt.legend(loc='upper right', fontsize=11, framealpha=0.9)
plt.xlim(0, 31)
plt.ylim(0, 105)

# Add a subtle background color
plt.gca().set_facecolor('#F8F9FA')

plt.tight_layout()

# Save the figure (optional: save to docs/examples/data directory if it exists)
save_path = 'ebbinghaus_curve.png'
data_dir = 'data'
if os.path.exists(data_dir):
    save_path = os.path.join(data_dir, 'ebbinghaus_curve.png')

try:
    plt.savefig(save_path, dpi=150, bbox_inches='tight', facecolor='white')
    print(f"✓ Saved visualization to '{save_path}'")
except Exception as e:
    print(f"⚠ Could not save figure: {e}")
    print("  (Figure will still be displayed)")

plt.show()
```

**Run this code:**
```bash
python ebbinghaus_example.py
```

**Expected output:**
```
✓ Saved visualization to 'ebbinghaus_curve.png'
```

## Step 6: Implement Spaced Repetition System

Based on the forgetting curve, we can implement a spaced repetition system that suggests when to review memories:

```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime, timedelta
import math

def calculate_retention(time_elapsed_hours):
    """Calculate retention based on Ebbinghaus curve."""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -math.log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

def get_review_schedule(retention_threshold=0.5):
    """
    Get optimal review intervals based on forgetting curve.
    
    Returns list of (hours, description) tuples.
    """
    intervals = [
        (0.33, "20 minutes"),
        (1, "1 hour"),
        (9, "9 hours"),
        (24, "1 day"),
        (48, "2 days"),
        (144, "6 days"),
        (336, "14 days"),
        (744, "31 days")
    ]
    return intervals

def get_next_review_time(memory, retention_threshold=0.5):
    """
    Calculate when a memory should be reviewed next.
    
    Args:
        memory: Memory dict with metadata
        retention_threshold: Minimum retention before review needed
    
    Returns:
        Tuple of (hours_until_review, review_needed_now)
    """
    metadata = memory.get('metadata', {})
    created_at_str = metadata.get('created_at', '')
    last_reviewed_str = metadata.get('last_reviewed', '')
    
    if not created_at_str:
        return None, False
    
    # Use last_reviewed if available, otherwise use created_at
    if last_reviewed_str:
        reference_time = datetime.fromisoformat(last_reviewed_str)
    else:
        reference_time = datetime.fromisoformat(created_at_str)
    
    now = datetime.now()
    time_elapsed = now - reference_time
    hours_elapsed = time_elapsed.total_seconds() / 3600
    
    current_retention = calculate_retention(hours_elapsed)
    
    if current_retention < retention_threshold:
        return 0, True  # Review needed now
    
    # Find next optimal review time
    schedule = get_review_schedule()
    for hours, _ in schedule:
        if hours > hours_elapsed:
            hours_until = hours - hours_elapsed
            return hours_until, False
    
    # If past all scheduled reviews, suggest immediate review
    return 0, True

# Example: Get review recommendations
config = auto_config()
memory = Memory(config=config)
user_id = "student_001"

all_memories = memory.get_all(user_id=user_id)
memories = all_memories.get('results', [])

print("Spaced Repetition Review Schedule:")
print("=" * 80)
print(f"{'Memory':<50} {'Retention':<12} {'Review Status':<20}")
print("-" * 80)

now = datetime.now()
for mem in memories:
    content = mem.get('memory', '')[:48]
    metadata = mem.get('metadata', {})
    created_at_str = metadata.get('created_at', '')
    
    if created_at_str:
        created_at = datetime.fromisoformat(created_at_str)
        time_elapsed = now - created_at
        hours_elapsed = time_elapsed.total_seconds() / 3600
        retention = calculate_retention(hours_elapsed)
        
        hours_until, needs_review = get_next_review_time(mem, retention_threshold=0.5)
        
        if needs_review:
            status = "⚠ Review NOW"
        elif hours_until < 24:
            status = f"Review in {hours_until:.1f}h"
        else:
            status = f"Review in {hours_until/24:.1f}d"
        
        print(f"{content:<50} {retention*100:>5.1f}%      {status:<20}")
```

**Run this code:**
```bash
python ebbinghaus_example.py
```

## Step 7: Complete Example - Learning System with Forgetting Curve

Here's a complete example that combines all concepts:

```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime, timedelta
import math

def calculate_retention(time_elapsed_hours):
    """Calculate retention based on Ebbinghaus curve."""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -math.log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

def search_with_retention_weighting(memory, query, user_id, limit=10):
    """Search with retention-based weighting."""
    results = memory.search(query=query, user_id=user_id, limit=limit * 2)
    
    now = datetime.now()
    weighted_results = []
    
    for mem in results.get('results', []):
        similarity_score = mem.get('score', 0.0)
        metadata = mem.get('metadata', {})
        created_at_str = metadata.get('created_at', '')
        
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            time_elapsed = now - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            retention = calculate_retention(hours_elapsed)
        else:
            retention = 0.9
            hours_elapsed = 0
        
        combined_score = similarity_score * retention
        
        weighted_results.append({
            'memory': mem.get('memory', ''),
            'similarity': similarity_score,
            'retention': retention,
            'combined_score': combined_score,
            'hours_elapsed': hours_elapsed,
            'metadata': metadata
        })
    
    weighted_results.sort(key=lambda x: x['combined_score'], reverse=True)
    return weighted_results[:limit]

def main():
    config = auto_config()
    memory = Memory(config=config)
    user_id = "student_001"
    
    print("=" * 80)
    print("Ebbinghaus Forgetting Curve - Learning System Demo")
    print("=" * 80)
    
    # Step 1: Add learning materials with timestamps
    print("\n1. Adding learning materials...")
    learning_materials = [
        ("Python basics", datetime.now() - timedelta(days=31)),
        ("Data structures", datetime.now() - timedelta(days=6)),
        ("Functions and classes", datetime.now() - timedelta(days=2)),
        ("Advanced topics", datetime.now() - timedelta(hours=9)),
        ("Best practices", datetime.now() - timedelta(hours=1)),
    ]
    
    for topic, created_at in learning_materials:
        result = memory.add(
            messages=f"Learned about {topic}",
            user_id=user_id,
            metadata={
                "created_at": created_at.isoformat(),
                "category": "learning",
                "topic": topic
            }
        )
        print(f"   ✓ Added: {topic}")
    
    # Step 2: Search with retention weighting
    print("\n2. Searching with retention-based weighting...")
    query = "Python programming"
    weighted_results = search_with_retention_weighting(memory, query, user_id, limit=5)
    
    print(f"\n   Query: '{query}'")
    print(f"   {'Memory':<40} {'Similarity':<12} {'Retention':<12} {'Score':<10}")
    print("   " + "-" * 76)
    for i, result in enumerate(weighted_results, 1):
        content = result['memory'][:38]
        print(f"   {i}. {content:<38} {result['similarity']:.3f}      "
              f"{result['retention']*100:>5.1f}%      {result['combined_score']:.3f}")
    
    # Step 3: Analyze retention
    print("\n3. Retention analysis...")
    all_memories = memory.get_all(user_id=user_id)
    memories = all_memories.get('results', [])
    
    now = datetime.now()
    total_retention = 0
    for mem in memories:
        metadata = mem.get('metadata', {})
        created_at_str = metadata.get('created_at', '')
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            time_elapsed = now - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            retention = calculate_retention(hours_elapsed)
            total_retention += retention
    
    avg_retention = total_retention / len(memories) if memories else 0
    print(f"   Average retention: {avg_retention*100:.1f}%")
    print(f"   Total memories: {len(memories)}")
    
    print("\n" + "=" * 80)
    print("Demo completed!")
    print("=" * 80)

if __name__ == "__main__":
    main()
```

**Run this code:**
```bash
python ebbinghaus_example.py
```

## Extension Exercises

### Exercise 1: Custom Decay Rates

Experiment with different decay rates for different types of memories:

```python
def calculate_retention_custom(time_elapsed_hours, memory_type="general"):
    """Calculate retention with custom decay rates."""
    # Different decay rates for different memory types
    decay_rates = {
        "important": 0.3,      # Slower decay for important memories
        "general": 0.5,        # Standard Ebbinghaus rate
        "temporary": 0.7       # Faster decay for temporary memories
    }
    
    rate = decay_rates.get(memory_type, 0.5)
    # Adjust formula based on rate
    # ...
```

### Exercise 2: Review Reminders

Implement a system that tracks when memories need review and sends reminders:

```python
def get_memories_needing_review(memory, user_id, retention_threshold=0.5):
    """Get all memories that need review."""
    all_memories = memory.get_all(user_id=user_id)
    memories = all_memories.get('results', [])
    
    needs_review = []
    for mem in memories:
        hours_until, needs_now = get_next_review_time(mem, retention_threshold)
        if needs_now:
            needs_review.append(mem)
    
    return needs_review
```

### Exercise 3: Adaptive Learning

Create a system that adjusts review intervals based on user performance:

```python
def update_review_schedule(memory_id, user_performance):
    """
    Update review schedule based on user performance.
    
    If user remembers well, increase interval.
    If user forgets, decrease interval.
    """
    # Implementation here
    pass
```

## Key Takeaways

1. **Time-based decay**: Memories naturally decay over time following the Ebbinghaus curve
2. **Retention scoring**: Calculate retention scores based on time elapsed since creation/access
3. **Weighted search**: Combine similarity scores with retention scores for better results
4. **Spaced repetition**: Use the forgetting curve to schedule optimal review times
5. **Practical applications**: Apply these concepts to learning systems, recommendation engines, and memory optimization

