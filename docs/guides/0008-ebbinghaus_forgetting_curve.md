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

> **Note:** When you call `Memory()`, powermem will automatically load configuration from the `.env` file.

### Using JSON/Dictionary Configuration

Instead of using `.env` files, you can also pass configuration directly as a Python dictionary (JSON-like format). This is useful when:
- You want to load configuration from a JSON file
- You need to programmatically generate configuration
- You're embedding configuration in your application code

Here's an example using a dictionary configuration:

```python
from powermem import Memory

# Define configuration as a dictionary (JSON-like format)
config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus',
            'temperature': 0.7
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'text-embedding-v4'
        }
    },
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'connection_args': {
                'host': '127.0.0.1',
                'port': 2881,
                'user': 'root@sys',
                'password': 'password',
                'db_name': 'powermem'
            },
            'embedding_model_dims': 1536,
            'index_type': 'IVF_FLAT',
            'vidx_metric_type': 'cosine'
        }
    }
}

# Create memory instance with dictionary config
memory = Memory(config=config)

print("✓ Memory initialized with JSON config!")
```

## What is the Ebbinghaus Forgetting Curve?

The Ebbinghaus Forgetting Curve, discovered by German psychologist Hermann Ebbinghaus in the 1880s, describes how information is lost over time when there is no attempt to retain it. This fundamental principle of cognitive psychology has profound implications for memory systems in AI applications.

### Key Insights

The curve reveals that memory retention follows a predictable exponential decay pattern:

- **Immediate retention**: 100% immediately after learning
- **20 minutes**: ~58% retention
- **1 hour**: ~44% retention
- **9 hours**: ~36% retention
- **1 day**: ~33% retention
- **2 days**: ~28% retention
- **6 days**: ~25% retention
- **31 days**: ~21% retention

### Why This Matters for AI Memory Systems

In traditional memory systems, all memories are treated equally regardless of when they were created. However, the forgetting curve suggests that:

1. **Recency matters**: Recently created or accessed memories are more likely to be relevant
2. **Time-based weighting**: Older memories should have lower priority in search results
3. **Spaced repetition**: Memories should be reviewed at optimal intervals to maintain retention
4. **Automatic decay**: Without intervention, memory relevance naturally decreases over time

### Use Cases

Implementing forgetting curve principles in powermem enables:

- **Intelligent search ranking**: Prioritize recent memories that are more likely to be relevant
- **Memory freshness tracking**: Identify stale memories that may need updating
- **Spaced repetition systems**: Build learning and review systems that optimize retention
- **Context-aware retrieval**: Weight search results based on both semantic similarity and temporal relevance

## Guide Overview

This guide will walk you through:

1. **Understanding the formula**: Learn how to mathematically model the forgetting curve
2. **Tracking memory timestamps**: Store and retrieve creation/access times
3. **Calculating retention scores**: Compute how "fresh" each memory is
4. **Weighted search**: Combine semantic similarity with temporal relevance
5. **Visualization**: Create charts to understand memory decay patterns
6. **Spaced repetition**: Implement review scheduling based on retention thresholds

## Understanding the Forgetting Curve Formula

### Mathematical Foundation

The forgetting curve can be mathematically modeled using an exponential decay function. The core principle is that memory retention decreases exponentially over time, following the formula:

**R(t) = e^(-λt)**

Where:
- **R(t)** is the retention rate at time t
- **λ** (lambda) is the decay constant
- **t** is the time elapsed since learning

### Implementation Approach

In our implementation, we use a simplified model based on Ebbinghaus's empirical observations:
- We calibrate the decay constant to match the 44% retention rate after 1 hour
- We set a minimum retention threshold (20%) to prevent memories from becoming completely irrelevant
- The formula ensures smooth decay that matches observed human memory patterns

### Why This Formula Works

The exponential decay model captures several important characteristics:

1. **Rapid initial decay**: Most forgetting happens in the first few hours
2. **Gradual long-term decay**: After the initial drop, retention stabilizes
3. **Predictable patterns**: The curve allows us to calculate retention at any point in time
4. **Practical thresholds**: Minimum retention ensures old memories aren't completely discarded

### Customization Options

You can adjust the formula parameters to match your specific use case:

- **Decay rate**: Faster or slower forgetting depending on memory importance
- **Minimum retention**: How much weight to give to very old memories
- **Base retention**: Calibrate to different time points (e.g., 1 hour, 1 day)

Here's the implementation:

```python
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

## Add Memories with Timestamps

### Why Track Timestamps?

To apply the forgetting curve, we need to know when each memory was created or last accessed. This temporal information allows us to:

- Calculate how much time has elapsed since memory creation
- Determine the current retention score for each memory
- Prioritize memories based on recency
- Schedule reviews for spaced repetition

### Implementation Strategy

powermem's `metadata` field is perfect for storing timestamp information. We'll:

1. Store the creation time as an ISO-formatted string in metadata
2. Include additional context (category, subject) for better organization
3. Use the timestamp to calculate retention scores later

### Best Practices

When adding timestamps to memories:

- **Use ISO format**: Store timestamps as ISO 8601 strings for easy parsing (`datetime.isoformat()`)
- **Store creation time**: Always record when the memory was first created
- **Track access time**: Optionally update `last_accessed` when memories are retrieved
- **Include context**: Add metadata like category, tags, or importance for richer filtering

### Example Implementation

Let's add memories and track their creation time:

```python
from powermem import Memory, auto_config
from datetime import datetime, timedelta

memory = Memory(config=auto_config())
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

## Calculate Retention Scores for Memories

### What Are Retention Scores?

A retention score quantifies how "fresh" or relevant a memory is based on the forgetting curve. It's a value between 0 and 1 where:

- **1.0**: Memory was just created (100% retention)
- **0.44**: Memory is 1 hour old (44% retention, per Ebbinghaus)
- **0.2**: Memory is very old (minimum threshold)

### How to Use Retention Scores

Retention scores enable several powerful capabilities:

1. **Memory freshness analysis**: Identify which memories are stale and may need updating
2. **Search result ranking**: Boost recent memories in search results
3. **Review scheduling**: Determine when memories should be reviewed
4. **Memory cleanup**: Identify memories that have decayed below a useful threshold

### Implementation Workflow

The process involves:

1. **Retrieve memories**: Get all memories for a user
2. **Extract timestamps**: Parse creation times from metadata
3. **Calculate elapsed time**: Compute hours/days since creation
4. **Apply formula**: Use the forgetting curve formula to get retention
5. **Display results**: Show memory age and retention in a readable format

### Interpreting Results

When analyzing retention scores:

- **High retention (`>0.7`)**: Very recent memories, likely highly relevant
- **Medium retention (0.3-0.7)**: Moderately old, may need review soon
- **Low retention (`<0.3`)**: Old memories, consider updating or reviewing

Now let's retrieve memories and calculate their retention scores:

```python
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

memory = Memory(config=auto_config())
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

## Apply Time-Based Weighting to Search Results

### The Problem with Pure Semantic Search

Traditional semantic search ranks results purely by similarity to the query. However, this ignores an important factor: **temporal relevance**. A memory that perfectly matches your query but was created 6 months ago might be less useful than a slightly less similar memory created yesterday.

### The Solution: Combined Scoring

By combining semantic similarity with retention scores, we create a more intelligent ranking system:

**Combined Score = Semantic Similarity × Retention Score**

This approach ensures that:
- Highly relevant AND recent memories rank highest
- Very old memories are deprioritized even if semantically similar
- Recent memories get a boost even if similarity is moderate

### When to Use Weighted Search

Time-based weighting is particularly useful for:

- **User preferences**: Recent preferences are more likely to be current
- **Learning systems**: Recent lessons are more relevant than old ones
- **Conversational AI**: Recent context is more important than historical context
- **Dynamic information**: When information changes over time (prices, availability, etc.)

### Implementation Strategy

The weighted search function:

1. **Performs standard search**: Gets top candidates by similarity
2. **Calculates retention**: Determines how fresh each result is
3. **Combines scores**: Multiplies similarity by retention
4. **Re-ranks results**: Sorts by combined score instead of similarity alone
5. **Returns top results**: Provides the most relevant AND recent memories

### Tuning the Weighting

You can adjust the weighting strategy:

- **Pure similarity**: Use only semantic similarity (retention = 1.0 for all)
- **Balanced**: Multiply similarity × retention (current approach)
- **Recency boost**: Add a bonus for recent memories: `similarity + (retention × 0.2)`
- **Threshold filtering**: Filter out memories below a retention threshold

Let's enhance search results by applying retention-based weighting:

```python
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
memory = Memory(config=auto_config())
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

## Visualize the Forgetting Curve

### Why Visualize?

Visualizing the forgetting curve helps you:

- **Understand the pattern**: See how retention decays over time
- **Identify key intervals**: Recognize when most forgetting occurs
- **Plan review schedules**: Determine optimal review timing
- **Communicate concepts**: Share insights with team members or stakeholders

### Key Visualization Elements

A good forgetting curve visualization should include:

1. **Smooth curve**: Show the continuous decay pattern
2. **Key milestones**: Mark important time points (20 min, 1 hour, 1 day, etc.)
3. **Retention percentages**: Display exact retention values at each milestone
4. **Clear labels**: Make time and retention axes easy to read
5. **Professional styling**: Use colors and formatting that enhance readability

### Interpreting the Visualization

When viewing the curve:

- **Steep initial drop**: Notice how retention drops rapidly in the first few hours
- **Stabilization**: See how the curve flattens after the first day
- **Minimum threshold**: Observe where the curve levels off (20% in our model)
- **Review windows**: Identify optimal times for spaced repetition reviews

### Customization Options

You can customize the visualization:

- **Time range**: Extend beyond 31 days for longer-term analysis
- **Decay parameters**: Adjust to show different forgetting rates
- **Multiple curves**: Compare different decay models side-by-side
- **Interactive plots**: Use libraries like Plotly for interactive exploration

Let's create a visualization of the forgetting curve:

```python
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

## Implement Spaced Repetition System

### What is Spaced Repetition?

Spaced repetition is a learning technique that involves reviewing information at increasing intervals. The principle is simple: review memories just before you're about to forget them, which strengthens retention and extends the forgetting curve.

### How It Works with the Forgetting Curve

The forgetting curve tells us when retention drops below useful thresholds. Spaced repetition uses this information to:

1. **Detect low retention**: Identify memories that have decayed significantly
2. **Schedule reviews**: Suggest optimal times to review each memory
3. **Reset the curve**: After review, the memory's retention resets, extending its useful life
4. **Optimize intervals**: Use increasingly longer intervals between reviews

### Review Schedule Strategy

Based on Ebbinghaus's research, optimal review intervals are:

- **20 minutes**: First review (catches early forgetting)
- **1 hour**: Second review (reinforces initial learning)
- **9 hours**: Third review (before significant overnight decay)
- **1 day**: Fourth review (daily reinforcement)
- **2-6 days**: Subsequent reviews (extending retention)
- **2-4 weeks**: Long-term reviews (maintaining knowledge)

### Implementation Components

A spaced repetition system needs:

1. **Retention calculation**: Determine current retention for each memory
2. **Review schedule**: Define optimal review intervals
3. **Review detection**: Identify memories that need review now
4. **Next review prediction**: Calculate when each memory should be reviewed next
5. **Review tracking**: Update `last_reviewed` timestamp after review

### Best Practices

When implementing spaced repetition:

- **Set thresholds**: Define minimum retention before review is needed (e.g., 50%)
- **Track reviews**: Update metadata with `last_reviewed` timestamp after each review
- **Adaptive intervals**: Adjust intervals based on review success/failure
- **Batch reviews**: Group memories by review time for efficient processing
- **User preferences**: Allow users to mark memories as "mastered" to skip reviews

### Use Cases

Spaced repetition is ideal for:

- **Learning systems**: Language learning, skill acquisition, knowledge retention
- **Training programs**: Employee onboarding, certification preparation
- **Personal knowledge bases**: Maintaining familiarity with stored information
- **AI tutoring systems**: Optimizing when to revisit concepts with students

Based on the forgetting curve, we can implement a spaced repetition system that suggests when to review memories:

```python
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
memory = Memory(config=auto_config())
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

## Best Practices and Tips

### When to Use Forgetting Curve Features

The forgetting curve approach is most valuable when:

- **Information changes over time**: User preferences, product availability, prices
- **Recency matters**: Recent context is more relevant than historical context
- **Learning systems**: Building educational or training applications
- **Large memory stores**: When you have many memories and need intelligent prioritization

### Performance Considerations

- **Metadata storage**: Storing timestamps adds minimal overhead
- **Calculation cost**: Retention calculations are fast (simple math operations)
- **Search optimization**: Weighted search may require fetching more candidates initially
- **Indexing**: Consider indexing metadata fields for faster timestamp queries

### Common Pitfalls

1. **Missing timestamps**: Always store `created_at` when adding memories
2. **Timezone issues**: Use UTC or consistent timezone for all timestamps
3. **Over-weighting recency**: Don't completely ignore old memories that might still be relevant
4. **Fixed thresholds**: Adjust retention thresholds based on your use case

### Advanced Techniques

- **Adaptive decay**: Adjust decay rates based on memory importance or type
- **Access-based retention**: Reset retention when memories are accessed (not just reviewed)
- **Category-specific curves**: Use different decay rates for different memory categories
- **Machine learning**: Train models to predict optimal review times based on user behavior