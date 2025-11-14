## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)
- Basic understanding of powermem (see [Getting Started Guide](docs/guides/0001-getting_started.md))

## What is Multi-Agent Memory?

Multi-agent memory allows you to create isolated memory spaces for different AI agents while enabling collaboration and information sharing when needed. This is essential for applications where multiple specialized agents work together, such as:

- **Customer support systems**: Different agents handle different aspects (support, sales, technical)
- **Development teams**: Multiple developers or AI agents working on the same project
- **Enterprise applications**: Different departments or teams with separate but interconnected memories
- **Complex workflows**: Agents that need to maintain their own context while accessing shared information

### Key Concepts

**Memory Isolation**: Each agent has its own memory space, preventing one agent's memories from interfering with another's.

**Cross-Agent Collaboration**: Agents can search across all memories when needed, enabling information sharing and collaboration.

**Agent Identification**: Each memory is tagged with an `agent_id`, allowing you to track which agent created or owns each memory.

**Flexible Search**: You can search within a single agent's memories or across all agents' memories, depending on your needs.

## Configuration

Before using multi-agent memory, you need to configure powermem. Powermem can automatically load configuration from a `.env` file in your project directory. This is the recommended way to configure powermem for your use case.

### Creating a `.env` File

1. Copy the example configuration file:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file and configure your settings:
   - **LLM Provider**: Choose your language model provider (Qwen, OpenAI, Anthropic, etc.)
   - **Embedding Provider**: Select how text will be converted to vectors
   - **Vector Store**: Choose your database (SQLite for development, OceanBase for production)

> **Note:** When you call `auto_config()`, powermem will automatically:
> - Look for a `.env` file in the current directory
> - Load configuration from environment variables
> - Use sensible defaults if no configuration is found

For more configuration options, see the full example in `.env.example` or refer to the [Configuration Guide](docs/guides/0003-configuration.md).

### Using JSON/Dictionary Configuration

Instead of using `.env` files, you can also pass configuration directly as a Python dictionary (JSON-like format). This is useful when:
- You want to load configuration from a JSON file
- You need to programmatically generate configuration
- You're embedding configuration in your application code
- You need different configurations for different agents

Here's an example using a dictionary configuration for multi-agent scenarios:

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

# Create multiple agents with the same configuration
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

print("✓ Created agents with JSON config!")
```

> **Note:** When using dictionary/JSON configuration for multi-agent scenarios:
> - All agents typically share the same configuration (database, LLM, embeddings)
> - Each agent is distinguished by its unique `agent_id` parameter
> - The same configuration can be reused for all agents to ensure consistency
> - Make sure to include all required fields (`llm`, `embedder`, `vector_store`) with their respective `provider` and `config` sections

## Understanding Multi-Agent Memory Architecture

Multi-agent memory in powermem is built on several key principles:

### Memory Isolation

Each agent operates in its own isolated memory space. When you create a `Memory` instance with an `agent_id`, all memories added through that instance are automatically tagged with that agent's ID. This ensures:

- **Privacy**: Agents can't accidentally access each other's private memories
- **Organization**: Memories are clearly organized by agent
- **Security**: Sensitive information can be kept within specific agent contexts

### Agent Identification

The `agent_id` parameter is crucial for multi-agent scenarios:

- **Unique identifiers**: Use descriptive, unique names for each agent (e.g., `"support_agent"`, `"sales_agent"`)
- **Consistent usage**: Always use the same `agent_id` for the same agent throughout your application
- **Naming conventions**: Use clear, consistent naming (e.g., `"agent_type_role"` format)

### Cross-Agent Search

While memories are isolated by default, powermem allows you to search across all agents when needed:

- **Collaboration**: Agents can access shared information when appropriate
- **Context building**: Build comprehensive context from multiple agent perspectives
- **Flexible access**: Control whether searches are agent-specific or cross-agent

### Memory Scopes

Memory scopes provide additional control over memory visibility and organization:

- **AGENT scope**: Memories specific to an agent
- **USER scope**: Memories specific to a user
- **GROUP scope**: Memories shared within a group
- **Custom scopes**: Define your own scope types for your use case

## Creating Multiple Agents

To use multi-agent memory, you need to create separate `Memory` instances for each agent. Each instance should have a unique `agent_id` that identifies the agent.

### Understanding Agent Creation

When you create a `Memory` instance with an `agent_id`:

1. **Instance creation**: The `Memory` instance is created with the specified `agent_id`
2. **Automatic tagging**: All memories added through this instance are automatically tagged with this `agent_id`
3. **Isolation**: Memories are isolated to this agent by default
4. **Shared configuration**: All agents can share the same configuration (database, LLM, embeddings)

### Best Practices for Agent Creation

- **Use descriptive names**: Choose clear, descriptive `agent_id` values (e.g., `"support_agent"` not `"agent1"`)
- **Consistent naming**: Establish a naming convention and stick to it
- **Reuse instances**: Create agent instances once and reuse them throughout your application
- **Configuration sharing**: Use the same configuration for all agents to ensure consistency

### Creating Agent Instances

Here's how to create memory instances for different agents:

```python
from powermem import Memory, auto_config

config = auto_config()

# Create memory instances for different agents
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

print("✓ Created memory instances for:")
print("  - Support Agent")
print("  - Sales Agent")
print("  - Technical Agent")
```

## Adding Agent-Specific Memories

Each agent maintains its own memory space. When an agent adds a memory, it's automatically associated with that agent's `agent_id`. This ensures proper isolation and organization.

### How Agent-Specific Memories Work

When you call `add()` on a `Memory` instance with an `agent_id`:

1. **Automatic tagging**: The memory is automatically tagged with the instance's `agent_id`
2. **Isolation**: The memory is stored in the agent's isolated space
3. **Search behavior**: By default, searches will only find memories from that agent
4. **Metadata**: The `agent_id` is stored as metadata, allowing for flexible querying

### When to Use Agent-Specific Memories

- **Specialized knowledge**: Each agent has domain-specific information
- **Privacy requirements**: Information that should only be accessible to specific agents
- **Organization**: Keeping different types of information separated
- **Context management**: Maintaining separate contexts for different agent roles

### Best Practices

- **Consistent user_id**: Use the same `user_id` for the same user across all agents
- **Meaningful metadata**: Add metadata to help organize and filter memories
- **Clear content**: Write memory content that's clear and self-contained
- **Regular updates**: Keep agent memories up-to-date as information changes

Let's see how each agent adds memories to their own space:

```python
from powermem import Memory, auto_config

config = auto_config()
customer_id = "customer_12345"

# Create agents
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# Support agent adds memory
support_agent.add(
    "Customer prefers email support over phone calls",
    user_id=customer_id,
    metadata={"category": "communication_preference"}
)

# Sales agent adds memory
sales_agent.add(
    "Customer interested in AI-powered features and automation",
    user_id=customer_id,
    metadata={"category": "product_interest"}
)

# Technical agent adds memory
tech_agent.add(
    "Customer uses Python and PostgreSQL in their tech stack",
    user_id=customer_id,
    metadata={"category": "technical_info"}
)

print("✓ Added memories for each agent")
```

## Agent-Specific Search

By default, when you search using a `Memory` instance with an `agent_id`, the search is scoped to that agent's memories only. This provides isolation and ensures agents only see relevant information from their own context.

### Understanding Agent-Specific Search

When you call `search()` on a `Memory` instance:

1. **Default behavior**: If the instance has an `agent_id`, searches are automatically filtered to that agent
2. **Explicit filtering**: You can also explicitly specify `agent_id` in the search parameters
3. **Isolation**: Results only include memories from the specified agent
4. **Performance**: Agent-specific searches are typically faster as they search a smaller subset of memories

### When to Use Agent-Specific Search

- **Context retrieval**: Getting relevant information for a specific agent's context
- **Privacy**: Ensuring agents only see their own memories
- **Performance**: Faster searches when you only need one agent's memories
- **Focused results**: Getting results specific to an agent's domain

### Search Parameters

The `search()` method supports several parameters for agent-specific searches:

- **`query`**: The search query (required)
- **`user_id`**: Filter by user (optional, but recommended)
- **`agent_id`**: Explicitly filter by agent (optional, defaults to instance's agent_id)
- **`limit`**: Maximum number of results (default: 10)
- **`filters`**: Additional metadata filters (optional)

### Best Practices

- **Always specify user_id**: For multi-user applications, always include `user_id` in searches
- **Use semantic queries**: Write natural language queries that describe what you're looking for
- **Limit results**: Use appropriate `limit` values to avoid overwhelming results
- **Combine with metadata**: Use metadata filters for more precise searches

Here's how each agent searches their own memories:

```python
from powermem import Memory, auto_config

config = auto_config()
customer_id = "customer_12345"

# Create agents
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")

# Add memories
support_agent.add(
    "Customer prefers email support",
    user_id=customer_id
)
sales_agent.add(
    "Customer interested in AI features",
    user_id=customer_id
)

# Support agent searches their memories
print("Support Agent Search:")
support_results = support_agent.search(
    query="customer preferences",
    user_id=customer_id,
    agent_id="support_agent"
)
for result in support_results.get('results', []):
    print(f"  - {result['memory']}")

# Sales agent searches their memories
print("\nSales Agent Search:")
sales_results = sales_agent.search(
    query="customer interests",
    user_id=customer_id,
    agent_id="sales_agent"
)
for result in sales_results.get('results', []):
    print(f"  - {result['memory']}")
```

## Cross-Agent Search

While agent-specific searches provide isolation, sometimes you need to search across all agents' memories. This enables collaboration and allows agents to access shared information.

### Understanding Cross-Agent Search

Cross-agent search allows you to:

1. **Access all memories**: Search across memories from all agents
2. **Build comprehensive context**: Gather information from multiple agent perspectives
3. **Enable collaboration**: Allow agents to see what other agents know
4. **Flexible querying**: Choose when to search all agents vs. a specific agent

### How Cross-Agent Search Works

To perform a cross-agent search:

1. **Omit agent_id**: Don't specify `agent_id` in the search parameters, or set it to `None`
2. **Search all agents**: The search will query memories from all agents
3. **Result tagging**: Results include the `agent_id` so you know which agent created each memory
4. **Filtering**: You can still filter by `user_id` and other metadata

### When to Use Cross-Agent Search

- **Comprehensive context**: Building complete context from multiple agent perspectives
- **Collaboration**: When agents need to know what other agents have learned
- **Analytics**: Analyzing information across all agents
- **Shared knowledge**: Accessing memories that should be visible to all agents

### Best Practices

- **Use sparingly**: Only use cross-agent search when you truly need information from all agents
- **Check agent_id in results**: Always check the `agent_id` in results to understand the source
- **Combine with filters**: Use `user_id` and metadata filters to narrow results
- **Performance consideration**: Cross-agent searches may be slower as they search more memories

### Use Cases

- **Customer support**: Support agent needs to see what sales and technical agents know about a customer
- **Project management**: Developer needs to see what QA and other developers have documented
- **Analytics**: Analyzing patterns across all agents' memories
- **Knowledge sharing**: Sharing important information across agent boundaries

Here's how to search across all agents:

```python
from powermem import Memory, auto_config

config = auto_config()
customer_id = "customer_12345"

# Create agents
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# Add memories for each agent
support_agent.add("Customer prefers email support", user_id=customer_id)
sales_agent.add("Customer interested in AI features", user_id=customer_id)
tech_agent.add("Customer uses Python and PostgreSQL", user_id=customer_id)

# Cross-agent search (no agent_id filter)
print("Cross-Agent Search:")
all_results = support_agent.search(
    query="customer information",
    user_id=customer_id
    # No agent_id - searches all agents
)

print(f"Found {len(all_results.get('results', []))} memories across all agents:")
for result in all_results.get('results', []):
    agent_id = result.get('agent_id', 'Unknown')
    print(f"  [{agent_id}] {result['memory']}")
```

## Project Collaboration with Multiple Agents

In real-world scenarios, multiple agents often work together on the same project or task. Powermem's multi-agent memory system supports this through `run_id` and metadata, allowing you to organize memories by project while maintaining agent isolation.

### Understanding Project Collaboration

When multiple agents work on the same project:

1. **Shared context**: Use `run_id` to group memories by project or task
2. **Agent isolation**: Each agent still maintains its own memory space
3. **Project-wide search**: Search across all agents' memories for a specific project
4. **Metadata organization**: Use metadata to categorize memories by project aspects

### Using `run_id` for Project Organization

The `run_id` parameter allows you to:

- **Group memories**: Associate memories with a specific project, task, or conversation
- **Project-wide queries**: Search all memories for a specific project across all agents
- **Context management**: Maintain context for long-running projects or conversations
- **Organization**: Organize memories by project lifecycle or workflow stage

### Best Practices for Project Collaboration

- **Consistent run_id**: Use the same `run_id` for all memories related to the same project
- **Descriptive IDs**: Use clear, descriptive `run_id` values (e.g., `"project_ai_platform"`, `"customer_onboarding_12345"`)
- **Metadata enrichment**: Add metadata to categorize memories (e.g., `scope`, `module`, `issue_type`)
- **Regular updates**: Keep project memories updated as work progresses

### Use Cases

- **Software development**: Multiple developers and QA working on the same project
- **Customer onboarding**: Multiple agents (sales, support, technical) working with the same customer
- **Research projects**: Multiple researchers collaborating on the same research topic
- **Content creation**: Multiple agents working on different aspects of the same content

Here's an example of multiple agents working on the same project:

```python
from powermem import Memory, auto_config

config = auto_config()
project_id = "project_ai_platform"

# Create developer agents
alice_dev = Memory(config=config, agent_id="alice_dev")
bob_dev = Memory(config=config, agent_id="bob_dev")
charlie_qa = Memory(config=config, agent_id="charlie_qa")

# Alice adds development memory
alice_dev.add(
    "Implemented user authentication module with JWT tokens",
    user_id="alice",
    run_id=project_id,
    metadata={"scope": "development", "module": "authentication"}
)

# Bob adds development memory
bob_dev.add(
    "Created database schema for user profiles",
    user_id="bob",
    run_id=project_id,
    metadata={"scope": "development", "module": "database"}
)

# Charlie adds QA memory
charlie_qa.add(
    "Found critical bug in user registration flow",
    user_id="charlie",
    run_id=project_id,
    metadata={"scope": "testing", "issue_type": "bug"}
)

# Search project-wide memories
print("Project Status Search:")
project_results = alice_dev.search(
    query="project status and progress",
    run_id=project_id
)

for result in project_results.get('results', []):
    agent_id = result.get('agent_id', 'Unknown')
    scope = result.get('metadata', {}).get('scope', 'Unknown')
    print(f"  [{agent_id}] [{scope}] {result['memory']}")
```

## Memory Scopes

Memory scopes provide an additional layer of control over memory visibility and organization. While `agent_id` isolates memories by agent, scopes allow you to control visibility at a more granular level.

### Understanding Memory Scopes

Memory scopes are metadata values that control:

- **Visibility**: Who can see and access the memory
- **Organization**: How memories are categorized and grouped
- **Access control**: Fine-grained control over memory access
- **Context**: Additional context about the memory's purpose

### Common Scope Types

Powermem supports several scope types, but you can also define custom scopes:

- **AGENT**: Memory is specific to an agent (most restrictive)
- **USER**: Memory is specific to a user
- **GROUP**: Memory is shared within a group
- **GLOBAL**: Memory is accessible to all (least restrictive)
- **Custom**: Define your own scope types for your use case

### How to Use Memory Scopes

1. **Add scope in metadata**: Include `scope` in the `metadata` parameter when adding memories
2. **Filter by scope**: Use metadata filters in search to filter by scope
3. **Combine with agent_id**: Use both `agent_id` and scope for fine-grained control
4. **Flexible querying**: Query memories by scope, agent, user, or combinations

### Best Practices

- **Consistent scope values**: Use consistent scope values across your application
- **Document scope meanings**: Document what each scope type means in your application
- **Combine with agent_id**: Use scopes together with `agent_id` for maximum flexibility
- **Filter appropriately**: Use scope filters to narrow search results

### Use Cases

- **Access control**: Control which agents or users can see specific memories
- **Organization**: Organize memories by visibility level
- **Privacy**: Implement privacy controls through scope management
- **Workflow management**: Use scopes to manage different workflow stages

Here's how to use memory scopes to control visibility:

```python
from powermem import Memory, auto_config

config = auto_config()

# Create agent
agent = Memory(config=config, agent_id="demo_agent")

# Add memories with different scopes
agent.add(
    "Agent-specific memory",
    user_id="user123",
    metadata={"scope": "AGENT"}
)

agent.add(
    "User-specific memory",
    user_id="user123",
    metadata={"scope": "USER"}
)

agent.add(
    "Group memory",
    user_id="user123",
    metadata={"scope": "GROUP"}
)

# Search by scope using filters parameter
# Note: For nested metadata fields, use the key path like "metadata.scope"
print("Agent-scoped memories:")
results = agent.search(
    query="memories",
    user_id="user123",
    filters={"metadata.scope": "AGENT"}
)
for result in results.get('results', []):
    print(f"  - {result['memory']}")

# Alternative: Search all memories and filter in Python
print("\nAll memories:")
all_results = agent.search(
    query="memories",
    user_id="user123"
)
for result in all_results.get('results', []):
    scope = result.get('metadata', {}).get('scope', 'Unknown')
    print(f"  [{scope}] {result['memory']}")
```

## Memory Isolation and Verification

Memory isolation is a fundamental feature of multi-agent memory. It ensures that each agent's memories are kept separate, preventing interference and maintaining privacy.

### Understanding Memory Isolation

Memory isolation means:

1. **Separate storage**: Each agent's memories are stored separately (tagged with `agent_id`)
2. **Default filtering**: Searches automatically filter to the agent's own memories
3. **Privacy**: Agents cannot accidentally access other agents' memories
4. **Organization**: Clear organization and separation of concerns

### How Isolation Works

When you create a `Memory` instance with an `agent_id`:

- **Automatic tagging**: All memories are tagged with the `agent_id`
- **Default search scope**: Searches default to that agent's memories
- **Explicit filtering**: You can explicitly filter by `agent_id` in searches
- **Cross-agent access**: You can still access other agents' memories when needed (via cross-agent search)

### Verifying Isolation

It's important to verify that memory isolation is working correctly:

1. **Add memories**: Each agent adds memories to their own space
2. **Search separately**: Each agent searches only their own memories
3. **Verify results**: Confirm that agents only see their own memories
4. **Test cross-agent**: Verify that cross-agent search works when needed

### Best Practices

- **Test isolation**: Regularly verify that memory isolation is working as expected
- **Use consistent agent_id**: Always use the same `agent_id` for the same agent
- **Monitor access**: Log or monitor memory access to ensure proper isolation
- **Document behavior**: Document how isolation works in your application

### Troubleshooting Isolation Issues

If you notice isolation issues:

- **Check agent_id**: Verify that `agent_id` is being set correctly
- **Verify tagging**: Confirm that memories are being tagged with the correct `agent_id`
- **Review search parameters**: Ensure search parameters are correctly filtering by `agent_id`
- **Check metadata**: Verify that `agent_id` is present in memory metadata

Here's how to verify that memories are properly isolated by agent:

```python
from powermem import Memory, auto_config

config = auto_config()
user_id = "user123"

# Create two agents
agent1 = Memory(config=config, agent_id="agent1")
agent2 = Memory(config=config, agent_id="agent2")

# Agent1 adds memory
agent1.add("Agent1's memory", user_id=user_id)

# Agent2 adds memory
agent2.add("Agent2's memory", user_id=user_id)

# Agent1 searches (should only see their own)
print("Agent1 search:")
results1 = agent1.search(query="memories", user_id=user_id, agent_id="agent1")
for result in results1.get('results', []):
    print(f"  - {result['memory']}")

# Agent2 searches (should only see their own)
print("\nAgent2 search:")
results2 = agent2.search(query="memories", user_id=user_id, agent_id="agent2")
for result in results2.get('results', []):
    print(f"  - {result['memory']}")
```

## Summary and Best Practices

Multi-agent memory in powermem provides a powerful way to manage memories for multiple AI agents while maintaining isolation and enabling collaboration. Here's a summary of key concepts and best practices:

### Key Takeaways

1. **Memory Isolation**: Each agent has its own isolated memory space, ensuring privacy and organization
2. **Agent Identification**: Use unique, descriptive `agent_id` values to identify each agent
3. **Flexible Search**: Choose between agent-specific and cross-agent searches based on your needs
4. **Project Organization**: Use `run_id` to organize memories by project or task
5. **Scope Control**: Use memory scopes for fine-grained visibility control

### Best Practices Checklist

**Agent Management:**
- ✅ Use descriptive, unique `agent_id` values
- ✅ Create agent instances once and reuse them
- ✅ Use consistent naming conventions
- ✅ Share configuration across agents for consistency

**Memory Management:**
- ✅ Always use consistent `user_id` for the same user across agents
- ✅ Add meaningful metadata to organize memories
- ✅ Use `run_id` for project or task organization
- ✅ Keep memories up-to-date as information changes

**Search Strategy:**
- ✅ Use agent-specific search by default for isolation
- ✅ Use cross-agent search only when needed for collaboration
- ✅ Always specify `user_id` in multi-user applications
- ✅ Use metadata filters for precise searches

**Isolation and Security:**
- ✅ Verify memory isolation is working correctly
- ✅ Test cross-agent access when needed
- ✅ Monitor memory access patterns
- ✅ Document isolation behavior in your application

### Common Patterns

**Pattern 1: Customer Support System**
```python
# Multiple agents for different aspects
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# Each agent maintains their own context
# Cross-agent search for comprehensive customer view
```

**Pattern 2: Development Team**
```python
# Multiple developers on same project
dev_agent = Memory(config=config, agent_id="dev_agent")
qa_agent = Memory(config=config, agent_id="qa_agent")

# Use run_id to group by project
# Cross-agent search for project-wide context
```

**Pattern 3: Enterprise Departments**
```python
# Different departments
hr_agent = Memory(config=config, agent_id="hr_agent")
finance_agent = Memory(config=config, agent_id="finance_agent")

# Isolated memories with selective sharing
# Use scopes for access control
```