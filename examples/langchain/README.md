# AI Healthcare Support Bot

This example demonstrates how to build an AI Healthcare Support Bot using **PowerMem** for intelligent memory management and **LangChain** for conversation handling, with **OceanBase** as the database backend.

## Features

- 🏥 **Patient Memory Management**: Persistent storage of patient information, symptoms, and medical history
- 🧠 **Intelligent Fact Extraction**: Automatic extraction of medical facts from conversations
- 💬 **Context-Aware Responses**: Personalized responses based on patient history
- 🔄 **Multi-Turn Conversations**: Support for continuous dialogue with context preservation
- 🔒 **Privacy Protection**: Patient data isolation through user_id
- 🚀 **Scalable Storage**: OceanBase database backend for enterprise-scale deployments

## Architecture

```
┌─────────────────┐
│  LangChain      │  Conversation handling and LLM integration
│  (Chat Chain)   │
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
│  (Database)     │  - Patient memories
│                 │  - Medical history
│                 │  - Symptom tracking
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

```bash
# Core dependencies
pip install powermem python-dotenv

# LangChain dependencies
pip install langchain langchain-community langchain-openai

# OceanBase dependencies (if not already installed)
pip install pyobvector sqlalchemy
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
DATABASE_HOST=localhost
DATABASE_PORT=2881
DATABASE_USER=root
DATABASE_PASSWORD=your_password
DATABASE_NAME=powermem
DATABASE_COLLECTION_NAME=healthcare_memories

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
cd examples/langchain
python healthcare_support_bot.py --mode demo
```

This will:
- Initialize the bot with OceanBase
- Run through a sample patient conversation
- Demonstrate memory storage and retrieval
- Show patient information summary

### Interactive Mode

Run the bot in interactive mode for real-time conversations:

```bash
cd examples/langchain
python healthcare_support_bot.py --mode interactive
```

**Interactive Commands:**
- Type your message to chat with the bot
- Type `summary` to see patient information summary
- Type `quit` or `exit` to end the conversation

### Custom Patient ID

Specify a patient ID for the conversation:

```bash
python healthcare_support_bot.py --mode interactive --patient-id patient_john_001
```

## How It Works

### 1. Memory Integration

The `HealthcarePowerMemMemory` class extends LangChain's `ConversationBufferMemory` to:

- **Save Context**: Automatically saves conversations to PowerMem with intelligent fact extraction
- **Load Context**: Retrieves relevant patient history based on current query
- **Privacy**: Isolates patient data by `user_id`

### 2. Intelligent Fact Extraction

PowerMem automatically extracts medical facts from conversations:

- **Symptoms**: Headaches, pain, discomfort, etc.
- **Medications**: Current prescriptions, dosages
- **Medical History**: Past conditions, diagnoses
- **Patient Information**: Demographics, preferences

### 3. Context-Aware Responses

The bot uses retrieved patient context to provide personalized responses:

```python
# Patient context is automatically included in prompts
Patient Context (from previous conversations):
- Patient Alice has been experiencing headaches
- Headaches occur in the afternoon, moderate intensity
- Currently taking ibuprofen 200mg twice daily
- Has history of migraines
```

### 4. OceanBase Storage

All patient memories are stored in OceanBase with:

- **Vector Embeddings**: For semantic search
- **Metadata**: Category, timestamps, importance scores
- **Scalability**: Handles large-scale patient data

## Example Conversation

```
Patient: Hello, I'm Alice. I've been experiencing headaches for the past few days.

Bot: Hello Alice, I'm sorry to hear about your headaches. I'm here to help provide 
     general guidance. Can you tell me more about when these headaches typically occur 
     and their intensity?

Patient: The headaches usually happen in the afternoon, and they're moderate in intensity.

Bot: Thank you for that information. Afternoon headaches can have various causes. 
     Are you experiencing any other symptoms along with the headaches?

Patient: I'm currently taking ibuprofen 200mg twice daily for the pain.

Bot: I understand you're managing the pain with ibuprofen. It's important to follow 
     the recommended dosage. Have you consulted with a healthcare professional about 
     these headaches?

[Memory stored: Patient Alice experiences afternoon headaches, moderate intensity, 
 taking ibuprofen 200mg twice daily]
```

## Patient Summary

The bot can provide a summary of stored patient information:

```python
summary = bot.get_patient_summary()
# Returns:
# {
#   "total_memories": 15,
#   "symptom_mentions": 8,
#   "medication_mentions": 3,
#   "history_mentions": 4,
#   "recent_memories": [...]
# }
```

## Configuration Options

### Database Settings

- `DATABASE_PROVIDER`: Set to `oceanbase`
- `DATABASE_HOST`: OceanBase server hostname
- `DATABASE_PORT`: OceanBase port (default: 2881)
- `DATABASE_NAME`: Database name
- `DATABASE_COLLECTION_NAME`: Collection/table name for memories

### LLM Settings

- `LLM_PROVIDER`: `qwen`, `openai`, or other supported providers
- `LLM_MODEL`: Model name (e.g., `qwen-plus`, `gpt-3.5-turbo`)
- `LLM_TEMPERATURE`: Response creativity (0.0-1.0)

### Embedding Settings

- `EMBEDDING_PROVIDER`: Embedding model provider
- `EMBEDDING_MODEL`: Embedding model name
- `EMBEDDING_DIMS`: Vector dimensions (must match model)

## Troubleshooting

### Connection Issues

**Problem**: Cannot connect to OceanBase

**Solution**:
1. Verify OceanBase is running: `mysql -h localhost -P 2881 -u root -p`
2. Check configuration in `.env`
3. Verify network connectivity and firewall settings

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'langchain'`

**Solution**:
```bash
pip install langchain langchain-community langchain-openai
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
2. Verify `infer=True` is set in `save_context`
3. Check database permissions
4. Review error messages in console

## Best Practices

1. **Patient Privacy**: Always use unique `user_id` for each patient
2. **Data Security**: Encrypt sensitive medical information
3. **Regular Backups**: Backup OceanBase database regularly
4. **Monitoring**: Monitor memory usage and database performance
5. **Compliance**: Ensure compliance with healthcare data regulations (HIPAA, etc.)

## Limitations & Disclaimers

⚠️ **Important**: This is a demonstration example and should NOT be used for actual medical diagnosis or treatment.

- The bot provides **general health information only**
- Always recommend consulting healthcare professionals
- Never diagnose medical conditions
- Not a replacement for professional medical advice

## Related Examples

- [Basic Usage](../basic_usage.py) - Simple memory operations
- [Agent Memory](../agent_memory.py) - Multi-agent memory management
- [Intelligent Memory](../intelligent_memory_demo.py) - Advanced memory features

## Support

For issues or questions:
- Check the [main README](../../README.md)
- Review [PowerMem documentation](../../docs/)
- Open an issue on GitHub

