# powermem Architecture

powermem is an AI-powered intelligent memory management system that mimics human memory mechanisms to provide persistent memory capabilities for LLM applications. This document provides a comprehensive overview of the system architecture.

## Table of Contents

- [System Overview](#system-overview)
- [Architecture Layers](#architecture-layers)
- [Memory Lifecycle](#memory-lifecycle)
- [Core Components](#core-components)
- [Storage Layer](#storage-layer)
- [Model Layer](#model-layer)
- [API Layer](#api-layer)
- [Multi-Agent Support](#multi-agent-support)

## System Overview

powermem implements a sophisticated memory management system inspired by cognitive science, particularly the Ebbinghaus forgetting curve theory. The system manages information through a multi-layered architecture that processes, evaluates, stores, and retrieves memories intelligently.

### Key Design Principles

1. **Human-like Memory Management**: Implements working memory, short-term memory, and long-term memory layers
2. **Intelligent Evaluation**: AI-powered importance and periodicity evaluation
3. **Reinforcement Learning**: Dynamic memory retention based on usage patterns
4. **Automatic Optimization**: Forgetting decay and automatic cleanup mechanisms
5. **Multi-Agent Support**: Isolated memory spaces with collaboration capabilities

## Architecture Layers

The powermem system is organized into five main layers:

```
┌─────────────────────────────────────────┐
│  External Layer: Multi-Agents & Users   │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│         API Layer                       │
│  (Python SDK, MCP Server)               │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│      Core Layer (Memory Engine)         │
│  • Memory Lifecycle Management          │
│  • Intelligent Memory Processor         │
│  • Layered Memory Structure             │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│      Model Layer (Embedding/LLM)        │
│  (Qwen, OpenAI, Anthropic, etc.)        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│    Storage Layer (Scalar/Vector/Graph)  │
│  (OceanBase, PostgreSQL, SQLite, etc.)  │
└─────────────────────────────────────────┘
```

### 1. External Layer

The external layer consists of:
- **Multi-Agents**: Automated AI agents that interact with the system
- **Users**: Human users accessing the memory system

Both agents and users interact with the system through the API layer.

### 2. API Layer

The API layer provides multiple interfaces for accessing the memory system:

- **Python SDK**: Primary programmatic interface for Python applications
- **MCP (Model Context Protocol)**: Standardized protocol for model context management

The API layer handles request routing, authentication, and provides a unified interface to the core memory engine.

### 3. Core Layer (Memory Engine)

The core layer is the heart of powermem, containing three main sub-components:

#### 3.1 Memory Full Lifecycle Management

This component manages the complete memory lifecycle, mimicking human memory processes:

- **Time/Frequency Reinforcement Learning**: Implements learning mechanisms based on how often and when information is accessed
- **Ebbinghaus Forgetting Curve Theory**: Core algorithm for memory retention and decay based on the psychological research of Hermann Ebbinghaus

#### 3.2 Intelligent Memory Processor

The intelligent memory processor handles core memory operations:

- **Memory Add**: Create new memories with intelligent processing
- **Memory Update**: Modify existing memories based on new information
- **Memory Query**: Retrieve memories with intelligent ranking
- **Memory Compression**: Optimize storage by consolidating similar memories

#### 3.3 Layered Memory Structure

The system organizes memories into different layers and scopes:

- **User Profile**: Personalized memory and data related to specific users
- **Private**: Memory designated for individual, private use
- **Short-term**: Short-duration memory storage with automatic expiration
- **Long-term**: Persistent memory storage for important information
- **Shared**: Memory accessible by multiple entities (agents or users)

### 4. Model Layer

The model layer provides AI capabilities:

- **Embedding Models**: Convert text into vector representations for semantic search
  - Supported providers: Qwen, OpenAI, HuggingFace, Azure, AWS Bedrock, Ollama, etc.
- **LLM Providers**: Large language models for importance evaluation and content understanding
  - Supported providers: Qwen, OpenAI, Anthropic, DeepSeek, Ollama, etc.

The core layer communicates bidirectionally with the model layer to leverage AI capabilities for memory processing.

### 5. Storage Layer

The storage layer handles persistent data storage:

- **Scalar Storage**: Traditional relational data storage
- **Vector Storage**: High-dimensional vector storage for embeddings
- **Graph Storage**: Relationship-based graph storage for complex memory relationships

Supported storage backends:
- **OceanBase**: Default enterprise-grade, scalable vector database
- **PostgreSQL**: Open-source vector database solution with pgvector extension
- **SQLite**: Lightweight, file-based storage for development
- **Custom Adapters**: Extensible architecture for additional storage backends

## Memory Lifecycle

The memory lifecycle follows a sophisticated flow that mimics human memory processing:

```
        New Information Input
              ↓
        Temporary Storage
              ↓
         Working Memory
             ↓
    AI Intelligent Evaluation / Multi-dimensional Analysis
             ↓
    Periodicity Evaluation
             ↓
     Importance Evaluation
             ↓
    ┌────────┴──────────────────┐
    │                           │
    │                           │
┌───┴──────┐  ┌────────-─┐  ┌──────────┐
│ Medium   │  │   High   │  │   Low    │
│Importance│  │Importance│  │Importance│
└───┬──────┘  └───┬─-──-─┘  └────┬─────┘
    │             │              │
    │             │              │
    │      ┌──────┴──────┐       │
    │      │Reinforcement│       │
    │      │  Learning   │       │
    │      └──────┬──────┘       │
    │             │              │
    │      ┌──────┴──────┐       │
    │      │ Importance  │       │
    │      │  Increase   │       │
    │      └──────┬──────┘       │
    │             │              │
    │      ┌──────┴──────┐       │
    │      │Long-term    │       │
    │      │   Memory    │       │
    │      └──────┬──────┘       │
    │             │              │
    │      ┌──────┴──────┐       │
    │      │  Permanent  │       │
    │      │   Storage   │       │
    │      └──────┬──────┘       │
    │             │              │
    │      ┌──────┴──────┐       │
    │      │ Knowledge   │       │
    │      │    Base     │       │
    │      └─────────────┘       │
    │                            │
    │                  ┌─────────┴─────────┐
    │                  │  Forgetting Decay │
    │                  └─────────┬─────────┘
    │                            │
    │                  ┌─────────┴─────────┐
    │                  │  Importance       │
    │                  │   Decrease        │
    │                  └─────────┬─────────┘
    │                            │
    │                  ┌─────────┴─────────┐
    │                  │  Automatic        │
    │                  │    Cleanup        │
    │                  └───────────────────┘
┌───┴─────-─────────┐
│  Short-term       │
│    Memory         │
└───────────────────┘
```

### Stage 1: Information Input & Temporary Storage

New information enters the system and is initially stored in temporary storage. This allows for immediate availability while evaluation processes begin.

### Stage 2: Working Memory

Information moves to working memory for active processing. Working memory has limited capacity and is used for active information manipulation.

### Stage 3: AI Intelligent Evaluation

The system performs multi-dimensional analysis using AI models:

- **Semantic Analysis**: Understanding the meaning and context of information
- **Importance Evaluation**: Determining the significance of the information
- **Periodicity Evaluation**: Assessing frequency and regularity patterns

### Stage 4: Importance-Based Routing

Based on the evaluation results, memories are routed to different paths:

#### High Importance / Frequent Use
- Triggers **Reinforcement Learning** mechanisms
- Results in **Importance Increase**
- Promoted to **Long-term Memory**
- Moved to **Permanent Storage**
- Eventually stored in **Knowledge Base**
- Uses **Ebbinghaus Forgetting Curve Algorithm** for retention management

#### Medium Importance
- Directly routed to **Short-term Memory**
- Subject to periodic review and potential promotion

#### Low Importance / Seldom Used
- Triggers **Forgetting Decay** process
- Results in **Importance Decrease**
- Moved to **Automatic Cleanup**
- Eventually removed from active storage

## Core Components

### Intelligent Memory Manager

The `IntelligentMemoryManager` orchestrates the complete memory management process:

- **Metadata Processing**: Enhances memory metadata with importance scores and memory types
- **Search Result Processing**: Applies intelligent ranking and decay factors to search results
- **Memory Optimization**: Automatically promotes, demotes, or removes memories based on usage patterns

### Importance Evaluator

The `ImportanceEvaluator` uses LLM capabilities to evaluate memory importance:

- Analyzes content semantic meaning
- Considers context and metadata
- Generates importance scores (0.0 - 1.0)
- Determines appropriate memory type classification

### Ebbinghaus Algorithm

The `EbbinghausAlgorithm` implements the forgetting curve theory:

- **Decay Calculation**: `R = e^(-t/S)` where R is retention, t is time, S is strength
- **Reinforcement**: Increases retention strength when memories are accessed
- **Memory Promotion**: Automatically promotes memories between layers based on retention scores
- **Forgetting Detection**: Identifies memories that should be forgotten or archived

### Memory Processor

The memory processor handles CRUD operations:

- **Add**: Creates new memories with intelligent processing
- **Update**: Modifies existing memories, potentially consolidating with similar memories
- **Query**: Retrieves memories with semantic search and intelligent ranking
- **Compression**: Consolidates similar memories to optimize storage

## Storage Layer

### Storage Types

powermem supports multiple storage paradigms:

1. **Scalar Storage**: Traditional relational database storage for metadata and structured data
2. **Vector Storage**: High-dimensional vector storage for embedding-based semantic search
3. **Graph Storage**: Relationship-based storage for complex memory interconnections

### Storage Backends

#### OceanBase (Default)

OceanBase is the default and recommended storage backend for powermem. It provides enterprise-grade distributed database capabilities with native vector storage support, making it ideal for production deployments requiring high scalability and performance.

**Key Features:**
- Enterprise-grade distributed database
- Native vector storage support with optimized vector indexing
- High scalability and performance
- Production-ready with ACID guarantees
- Advanced hybrid search capabilities (vector + full-text)
- Graph storage support for complex memory relationships

**OceanBase Optimizations**

powermem includes extensive optimizations specifically designed for OceanBase to maximize performance and efficiency:

##### 1. Automatic Vector Index Configuration

The system automatically configures OceanBase's vector index settings for optimal performance:

- **Memory Optimization**: Automatically sets `ob_vector_memory_limit_percentage = 30` to optimize memory usage for vector operations
- **Index Type Selection**: Supports multiple vector index types optimized for different use cases:
  - **HNSW**: Hierarchical Navigable Small World - Best for high-dimensional similarity search
  - **HNSW_SQ**: HNSW with Scalar Quantization - Memory-efficient variant
  - **IVFFLAT**: Inverted File Flat - Good balance of speed and accuracy
  - **IVFSQ**: Inverted File with Scalar Quantization - Memory-efficient IVF variant
  - **IVFPQ**: Inverted File with Product Quantization - Maximum compression
- **Index Parameters**: Automatically configures optimal index parameters based on index type and data characteristics

##### 2. Hybrid Search Architecture

powermem implements a sophisticated hybrid search system that combines vector similarity search with full-text search:

- **Parallel Execution**: Vector search and full-text search execute concurrently using thread pools for maximum performance
- **Multiple Fusion Methods**:
  - **RRF (Reciprocal Rank Fusion)**: Combines results from both searches using rank-based scoring (default, recommended)
  - **Weighted Fusion**: Traditional weighted score combination for fine-tuned control
- **Full-Text Search Support**: 
  - Multiple parser support: `ik`, `ngram`, `ngram2`, `beng`, `space`
  - Automatic full-text index creation and management
  - Parameterized queries for security and performance
  - Fallback mechanisms for compatibility

##### 3. Database Operation Optimizations

Multiple optimizations ensure efficient database operations:

- **Snowflake ID Generation**: Uses Snowflake algorithm for distributed ID generation instead of auto-increment, enabling:
  - Unique IDs across distributed systems
  - No conflicts in multi-instance deployments
  - Time-ordered IDs for temporal queries
- **Upsert Operations**: Uses `REPLACE INTO` (upsert) for efficient insert/update operations:
  - Atomic operations ensuring data consistency
  - Automatic handling of duplicates
  - Reduced round-trips for updates
- **Transaction Management**: Automatic transaction handling for:
  - Atomic multi-step operations
  - Consistency guarantees
  - Rollback on errors
- **Complex Query Building**: Advanced WHERE clause generation supporting:
  - Nested AND/OR logic
  - Multiple comparison operators (eq, ne, gt, gte, lt, lte, in, nin, like, ilike)
  - JSON metadata filtering
  - Efficient query optimization

##### 4. Graph Storage Optimizations

Specialized optimizations for graph-based memory storage:

- **Multi-Hop Graph Traversal**: Efficient multi-hop search (up to 3 hops) with:
  - Early stopping when result limit is satisfied
  - Cycle prevention to avoid infinite loops
  - Transaction-based consistency across hops
  - Memory-efficient edge limiting (max_edges_per_hop)
- **Performance Optimizations**:
  - Query result sorting by mentions and creation time
  - Efficient indexing strategy (covering indexes)
  - Batch operations for entity and relationship updates
  - Mention counting for relationship relevance

##### 5. Performance Enhancements

Additional performance optimizations:

- **Concurrent Operations**: Parallel execution of independent operations:
  - Concurrent vector and full-text searches
  - Multi-threaded query execution where applicable
- **Query Optimization**:
  - Index-aware query planning
  - Efficient result pagination
  - Early result termination when possible
- **Memory Management**:
  - Configurable memory limits for vector operations
  - Efficient data structures for result processing
  - Heap-based top-K selection for large result sets

##### 6. Advanced Features

- **Vector Dimension Validation**: Automatic validation of vector dimensions to prevent runtime errors
- **Table Schema Management**: Automatic table creation with proper schema including:
  - Vector columns with correct dimensions
  - Metadata columns (JSON type)
  - Standard fields (user_id, agent_id, run_id, etc.)
  - Full-text search columns
- **Index Management**: Automatic index creation and validation:
  - Vector indexes with appropriate parameters
  - Full-text indexes with specified parsers
  - Regular indexes for common query patterns

These optimizations ensure that powermem delivers optimal performance when using OceanBase as the storage backend, making it suitable for enterprise-scale deployments with high throughput and low latency requirements.

#### PostgreSQL (pgvector)

- Open-source vector database solution
- pgvector extension for vector operations
- Strong ecosystem and tooling support

#### SQLite

- Lightweight, file-based storage
- Ideal for development and testing
- Single-file deployment

#### Custom Adapters

The storage layer is designed with an adapter pattern, allowing easy integration of new storage backends.

## Model Layer

### Embedding Providers

Embedding models convert text into vector representations:

- **Qwen**: Alibaba Cloud's embedding models
- **OpenAI**: text-embedding-3-large and other models
- **HuggingFace**: Community-driven embedding models
- **Azure OpenAI**: Microsoft's Azure-hosted embeddings
- **AWS Bedrock**: Amazon's embedding services
- **Ollama**: Local embedding model support

### LLM Providers

Large language models provide intelligence capabilities:

- **Qwen**: Alibaba Cloud's LLM models
- **OpenAI**: GPT-4, GPT-3.5, and other models
- **Anthropic**: Claude models
- **DeepSeek**: Advanced reasoning models
- **Ollama**: Local LLM support
- **Google Gemini**: Google's language models

## API Layer

### Python SDK

The primary interface for Python applications:

```python
from powermem import Memory, create_memory

# Simple creation
memory = create_memory()

# Add memory
memory.add("User prefers Python programming", user_id="user123")

# Search memories
results = memory.search("programming preferences", user_id="user123")
```

### MCP Server

Model Context Protocol (MCP) server provides standardized access:

- RESTful API endpoints
- JSON-RPC protocol support
- Standardized memory operations
- Multi-language client support

## Multi-Agent Support

powermem provides comprehensive multi-agent capabilities:

### Agent Isolation

Each agent has isolated memory spaces:
- **Private Memory**: Agent-specific memories not shared with others
- **Working Memory**: Active processing memory per agent
- **Short-term Memory**: Temporary storage per agent
- **Long-term Memory**: Persistent storage per agent

### Agent Collaboration

Agents can collaborate through:
- **Shared Memory**: Memories accessible by multiple agents
- **Collaborative Memory**: Memories created through agent interactions
- **Group Consensus**: Memories validated by multiple agents

### Memory Scopes

The system supports different memory scopes:

- **Private**: Individual agent or user memory
- **Agent Group**: Shared within a group of agents
- **User Group**: Shared among a group of users
- **Public**: Publicly accessible memories

### Access Control

Fine-grained permission control:
- Read/write permissions per agent
- Scope-based access control
- Privacy protection mechanisms
- Audit logging for compliance

## Data Flow

### Memory Addition Flow

```
User/Agent → API Layer → Core Memory Engine
                              ↓
                    Intelligent Memory Processor
                              ↓
                    Importance Evaluator (LLM)
                              ↓
                    Ebbinghaus Algorithm Processing
                              ↓
                    Memory Type Determination
                              ↓
                    Storage Layer (Vector + Scalar)
                              ↓
                    Response with Memory ID
```

### Memory Query Flow

```
User/Agent → API Layer → Core Memory Engine
                              ↓
                    Query Processing
                              ↓
                    Embedding Generation
                              ↓
                    Vector Search (Storage Layer)
                              ↓
                    Ebbinghaus Decay Application
                              ↓
                    Relevance Ranking
                              ↓
                    LLM-based Reranking (Optional)
                              ↓
                    Return Ranked Results
```

### Memory Optimization Flow

```
Scheduled Task → Intelligent Memory Manager
                        ↓
            Ebbinghaus Algorithm
                        ↓
            Decay Calculation
                        ↓
        ┌───────────────┴───────────────┐
        │                               │
    Promotion Check              Forgetting Check
        │                               │
    Move to Higher Layer          Automatic Cleanup
        │                               │
    Update Retention Score         Remove from Storage
```

## Performance Considerations

### Scalability

- **Horizontal Scaling**: Storage layer supports distributed architectures
- **Caching**: Intelligent caching of frequently accessed memories
- **Batch Processing**: Batch operations for bulk memory updates

### Optimization

- **Memory Compression**: Automatic consolidation of similar memories
- **Periodic Cleanup**: Scheduled removal of forgotten memories
- **Index Optimization**: Efficient vector indexing for fast retrieval

### Monitoring

- **Telemetry**: Built-in telemetry for performance monitoring
- **Audit Logging**: Comprehensive audit trails for all operations
- **Memory Statistics**: Real-time memory statistics and health metrics

## Security & Privacy

### Data Protection

- **Encryption**: Data encryption at rest and in transit
- **Access Control**: Fine-grained permission management
- **Privacy Protection**: Built-in privacy controls for sensitive data

### Compliance

- **Audit Logging**: Complete audit trails for compliance
- **Data Retention**: Configurable retention policies
- **GDPR Support**: Privacy controls for regulatory compliance

## Future Enhancements

The architecture is designed to support future enhancements:

- **Graph-based Memory Relationships**: Enhanced relationship modeling
- **Advanced Reinforcement Learning**: More sophisticated learning algorithms
- **Distributed Memory**: Cross-system memory synchronization
- **Real-time Collaboration**: Live memory updates across agents

## Conclusion

powermem's architecture provides a robust, scalable, and intelligent memory management system that mimics human memory processes while leveraging modern AI capabilities. The layered design ensures flexibility, extensibility, and performance for production deployments.