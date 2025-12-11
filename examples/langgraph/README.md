# AI Customer Service Bot with LangGraph + PowerMem + OceanBase

This example demonstrates how to build an AI Customer Service Bot using **PowerMem** for intelligent memory management, **LangGraph** for stateful conversation workflows, and **OceanBase** as the database backend.

## Features

- 🔄 **Stateful Workflows**: Multi-step conversation management with LangGraph state graphs
- 🧠 **Intelligent Memory**: Automatic extraction of customer information, orders, and preferences
- 💬 **Context-Aware Responses**: Personalized responses based on customer history
- 📊 **Multi-Step Processing**: Handles order inquiries, issue resolution, and general questions
- 🔒 **Privacy Protection**: Customer data isolation through user_id
- 🚀 **Scalable Storage**: OceanBase database backend for enterprise-scale deployments

## Architecture

```
┌─────────────────┐
│  LangGraph      │  Stateful workflow management
│  (State Graph)  │  - Intent classification
│                 │  - Multi-step routing
│                 │  - Conversation flow
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  PowerMem       │  Intelligent memory management
│  (Memory Layer) │  - Fact extraction
│                 │  - Semantic search
│                 │  - Context retrieval
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  OceanBase      │  Vector database for scalable storage
│  (Database)     │  - Customer memories
│                 │  - Order history
│                 │  - Preferences
└─────────────────┘
```

## Prerequisites

1. **Python 3.10+**
2. **OceanBase Database** (configured and running)
3. **API Keys**:
   - LLM API key (OpenAI, Qwen, etc.)
   - Embedding API key (if different from LLM)

## Installation

### 1. Install Dependencies

**Option 1: Install from requirements.txt (Recommended)**

```bash
cd examples/langgraph
pip install -r requirements.txt
```

**Option 2: Install manually**

```bash
# Core dependencies
pip install powermem python-dotenv

# LangGraph dependencies
pip install langgraph>=1.0.0 langchain>=1.1.0 langchain-core>=1.1.0 langchain-openai>=1.1.0 langchain-community>=0.4.1

# OceanBase dependencies (if not already installed)
pip install pyobvector sqlalchemy
```

**Option 3: Install all at once**

```bash
pip install powermem python-dotenv langgraph>=1.0.0 langchain>=1.1.0 langchain-core>=1.1.0 langchain-openai>=1.1.0 langchain-community>=0.4.1 pyobvector sqlalchemy
```

### 2. Configure OceanBase

Copy the configuration template and edit it:

```bash
# From project root
cp .env.example .env
```

Edit `.env` and configure:

```env
# Database Configuration
DATABASE_PROVIDER=oceanbase
OCEANBASE_HOST=127.0.0.1
OCEANBASE_PORT=2881
OCEANBASE_USER=root@sys
OCEANBASE_PASSWORD=your_password
OCEANBASE_DATABASE=powermem
OCEANBASE_COLLECTION=customer_memories

# LLM Configuration
LLM_PROVIDER=qwen  # or openai
LLM_API_KEY=your_llm_api_key
LLM_MODEL=qwen-plus  # or gpt-3.5-turbo

# Embedding Configuration
EMBEDDING_PROVIDER=qwen  # or openai
EMBEDDING_API_KEY=your_embedding_api_key
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMS=1536
```

### 3. Verify OceanBase Connection

Ensure your OceanBase instance is running and accessible:

```bash
# Test connection (adjust host/port as needed)
mysql -h localhost -P 2881 -u root -p
```

## Usage

### Demo Mode

Run a predefined conversation demonstration:

```bash
cd examples/langgraph
python customer_service_bot.py --mode demo
```

This will:
- Initialize the bot with OceanBase
- Run through a sample customer conversation
- Demonstrate stateful workflow management
- Show memory storage and retrieval
- Display customer information summary

### Interactive Mode

Run the bot in interactive mode for real-time conversations:

```bash
cd examples/langgraph
python customer_service_bot.py --mode interactive
```

**Interactive Commands:**
- Type your message to chat with the bot
- Type `summary` to see customer information summary
- Type `quit` or `exit` to end the conversation

### Custom Customer ID

Specify a customer ID for the conversation:

```bash
python customer_service_bot.py --mode interactive --customer-id customer_john_001
```

## How It Works

### 1. LangGraph State Management

The bot uses LangGraph's `StateGraph` to manage conversation flow:

```python
# State schema
class CustomerServiceState(TypedDict):
    messages: List[BaseMessage]
    customer_id: str
    intent: str  # "order_inquiry", "issue_resolution", "general"
    order_number: str
    issue_type: str
    context: Dict[str, Any]
    resolved: bool
```

### 2. Workflow Nodes

The graph consists of several nodes:

1. **load_context**: Loads customer context from PowerMem
2. **classify_intent**: Classifies customer intent (order inquiry, issue, general)
3. **handle_order_inquiry**: Processes order-related questions
4. **handle_issue_resolution**: Handles customer issues and complaints
5. **handle_general**: Handles general inquiries
6. **save_conversation**: Saves conversation to PowerMem

### 3. Conditional Routing

The graph uses conditional edges to route based on intent:

```python
workflow.add_conditional_edges(
    "classify_intent",
    route_intent,
    {
        "order_inquiry": "handle_order_inquiry",
        "issue_resolution": "handle_issue_resolution",
        "general": "handle_general",
    }
)
```

### 4. PowerMem Integration

PowerMem is used to:
- **Store conversations** with intelligent fact extraction
- **Retrieve context** based on current query
- **Track customer preferences** and order history
- **Maintain privacy** by isolating data by customer_id

### 5. OceanBase Storage

All customer memories are stored in OceanBase with:
- **Vector Embeddings**: For semantic search
- **Metadata**: Intent, order numbers, issue types, timestamps
- **Scalability**: Handles large-scale customer data

## Example Conversation Flow

```
Customer: Hello, I'd like to check the status of my order #ORD-12345

[Node: load_context] Loading context for customer customer_alice_001
[Node: classify_intent] Classifying intent...
  Classified intent: order_inquiry
[Node: handle_order_inquiry] Processing order inquiry...
[Node: save_conversation] Saving conversation to PowerMem...
  ✓ Conversation saved to PowerMem

Bot: I can help you with your order inquiry. I found some previous order 
     information in your history. Your order #ORD-12345 is currently being 
     processed and will be shipped within 2-3 business days.
```

## Customer Summary

The bot can provide a summary of stored customer information:

```python
summary = bot.get_customer_summary()
# Returns:
# {
#   "total_memories": 15,
#   "order_mentions": 8,
#   "issue_mentions": 3,
#   "preference_mentions": 4,
#   "recent_memories": [...]
# }
```

## Configuration Options

### Database Settings

- `DATABASE_PROVIDER`: Set to `oceanbase`
- `OCEANBASE_HOST`: OceanBase server hostname
- `OCEANBASE_PORT`: OceanBase port (default: 2881)
- `OCEANBASE_NAME`: Database name
- `OCEANBASE_COLLECTION`: Collection/table name for memories

### LLM Settings

- `LLM_PROVIDER`: `qwen`, `openai`, or other supported providers
- `LLM_MODEL`: Model name (e.g., `qwen-plus`, `gpt-3.5-turbo`)
- `LLM_TEMPERATURE`: Response creativity (0.0-1.0)

### Embedding Settings

- `EMBEDDING_PROVIDER`: Embedding model provider
- `EMBEDDING_MODEL`: Embedding model name
- `EMBEDDING_DIMS`: Vector dimensions (must match model)

## Advanced Usage

### Custom Intent Classification

You can enhance the intent classification by using an LLM:

```python
def _classify_intent(self, state: CustomerServiceState) -> CustomerServiceState:
    """Classify intent using LLM for better accuracy."""
    user_input = state["messages"][-1].content
    
    prompt = f"""Classify the customer's intent. Options: order_inquiry, issue_resolution, general.
    
Customer message: {user_input}
Intent:"""
    
    response = self.llm.invoke(prompt)
    intent = response.content.strip().lower()
    
    state["intent"] = intent
    return state
```

### Adding New Workflow Nodes

You can extend the workflow by adding new nodes:

```python
def _handle_product_inquiry(self, state: CustomerServiceState) -> CustomerServiceState:
    """Handle product information requests."""
    # Your custom logic here
    return state

# Add to graph
workflow.add_node("handle_product_inquiry", self._handle_product_inquiry)
workflow.add_edge("classify_intent", "handle_product_inquiry")
```

## Troubleshooting

### Connection Issues

**Problem**: Cannot connect to OceanBase

**Solution**:
1. Verify OceanBase is running: `mysql -h localhost -P 2881 -u root -p`
2. Check configuration in `.env`
3. Verify network connectivity and firewall settings

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'langgraph'`

**Solution**:
```bash
pip install langgraph>=1.0.0 langchain>=1.1.0 langchain-core>=1.1.0 langchain-openai>=1.1.0 langchain-community>=0.4.1
```

### API Key Issues

**Problem**: LLM or embedding API errors

**Solution**:
1. Verify API keys in `.env`
2. Check API key validity and quotas
3. Ensure correct provider is configured

### Memory Not Saving

**Problem**: Conversations not being stored

**Solution**:
1. Check OceanBase connection
2. Verify `infer=True` is set in `save_conversation`
3. Check database permissions
4. Review error messages in console

## Best Practices

1. **Customer Privacy**: Always use unique `customer_id` for each customer
2. **Data Security**: Encrypt sensitive customer information
3. **Regular Backups**: Backup OceanBase database regularly
4. **Monitoring**: Monitor memory usage and database performance
5. **State Management**: Keep state objects lightweight and focused
6. **Error Handling**: Implement robust error handling in workflow nodes

## Comparison with LangChain Example

| Feature | LangChain Example | LangGraph Example |
|---------|------------------|-------------------|
| **Framework** | LangChain Chains | LangGraph StateGraph |
| **State Management** | Memory-based | Explicit state objects |
| **Workflow** | Linear chain | Multi-step graph with routing |
| **Intent Handling** | Single handler | Conditional routing by intent |
| **Use Case** | Simple conversations | Complex multi-step workflows |

## Related Examples

- [LangChain Integration](../langchain/README.md) - Simple conversation chains
- [Basic Usage](../basic_usage.py) - Simple memory operations
- [Agent Memory](../agent_memory.py) - Multi-agent memory management
- [Intelligent Memory](../intelligent_memory_demo.py) - Advanced memory features

## Support

For issues or questions:
- Check the [main README](../../README.md)
- Review [PowerMem documentation](../../docs/)
- Open an issue on GitHub

