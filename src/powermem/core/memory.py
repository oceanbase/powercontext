"""
Synchronous memory management implementation

This module provides the synchronous memory management interface.
"""

import logging
import hashlib
import json
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
from powermem.utils.utils import get_current_datetime
from copy import deepcopy

from .base import MemoryBase
from ..configs import MemoryConfig
from ..integrations.embeddings.config.sparse_base import BaseSparseEmbedderConfig, SparseEmbedderConfig
from ..storage.factory import VectorStoreFactory, GraphStoreFactory
from ..storage.adapter import StorageAdapter, SubStorageAdapter
from ..intelligence.manager import IntelligenceManager
from ..integrations.llm.factory import LLMFactory
from ..integrations.embeddings.factory import EmbedderFactory
from ..integrations.embeddings.sparse_factory import SparseEmbedderFactory
from ..integrations.rerank.factory import RerankFactory
from .telemetry import TelemetryManager
from .audit import AuditLogger
from ..intelligence.plugin import IntelligentMemoryPlugin, EbbinghausIntelligencePlugin
from ..utils.utils import remove_code_blocks, convert_config_object_to_dict, parse_vision_messages
from ..prompts.intelligent_memory_prompts import (
    FACT_RETRIEVAL_PROMPT,
    FACT_EXTRACTION_PROMPT,
    get_memory_update_prompt,
    parse_messages_for_facts
)

logger = logging.getLogger(__name__)


def _auto_convert_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert legacy powermem config to format for compatibility.
    
    Now powermem uses field names directly.
    
    Args:
        config: Configuration dictionary (legacy format)
        
    Returns:
        configuration dictionary
    """
    if not config:
        return config

    # First, convert any ConfigObject instances to dicts
    config = convert_config_object_to_dict(config)

    # Check if legacy powermem format (has database or embedding)
    if "database" in config or ("llm" in config and "embedding" in config):
        converted = config.copy()

        # Convert llm
        if "llm" in config:
            converted["llm"] = config["llm"]
        
        # Convert embedding to embedder
        if "embedding" in config:
            converted["embedder"] = config["embedding"]
            converted.pop("embedding", None)

        # Convert database to vector_store
        if "database" in config:
            db_config = config["database"]
            converted["vector_store"] = {
                "provider": db_config.get("provider", "oceanbase"),
                "config": db_config.get("config", {})
            }
            converted.pop("database", None)
        elif "vector_store" not in converted:
            converted["vector_store"] = {
                "provider": "oceanbase",
                "config": {}
            }
        
        logger.info("Converted legacy powermem config format")
        return converted

    return config


class Memory(MemoryBase):
    """
    Synchronous memory management implementation.
    
    This class provides the main interface for synchronous memory operations.
    """
    
    def __init__(
        self,
        config: Optional[Dict[str, Any] | MemoryConfig] = None,
        storage_type: Optional[str] = None,
        llm_provider: Optional[str] = None,
        embedding_provider: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        """
        Initialize the memory manager.

        Compatible with both dict config and MemoryConfig object.

        Args:
            config: Configuration dictionary or MemoryConfig object containing all settings.
                   Dict format supports style (llm, embedder, vector_store)
                   and powermem style (database, llm, embedding)
            storage_type: Type of storage backend to use (overrides config)
            llm_provider: LLM provider to use (overrides config)
            embedding_provider: Embedding provider to use (overrides config)
            agent_id: Agent identifier for multi-agent scenarios
        
        Example:
            ```python
            # Method 1: Using MemoryConfig object (recommended)

            config = MemoryConfig(
                vector_store=VectorStoreConfig(provider="oceanbase", config={...}),
                llm=LlmConfig(provider="qwen", config={...}),
                embedder=EmbedderConfig(provider="qwen", config={...})
            )
            memory = Memory(config)

            # Method 2: Using dict (backward compatible - powermem style)
            memory = Memory({
                "database": {"provider": "oceanbase", "config": {...}},
                "llm": {"provider": "qwen", "config": {...}},
            })

            # Method 3: Using dict
            memory = Memory({
                "llm": {"provider": "openai", "config": {...}},
                "embedder": {"provider": "openai", "config": {...}},
                "vector_store": {"provider": "chroma", "config": {...}},
            })
            ```
        """
        # Handle MemoryConfig object or dict

        if isinstance(config, MemoryConfig):
            # Use MemoryConfig object directly
            self.memory_config = config
            # For backward compatibility, also store as dict
            self.config = config.model_dump()
        else:
            # Convert dict config
            dict_config = config or {}
            dict_config = _auto_convert_config(dict_config)
            self.config = dict_config
            # Try to create MemoryConfig from dict, fallback to dict if fails
            try:
                self.memory_config = MemoryConfig(**dict_config)
            except Exception as e:
                logger.warning(f"Could not parse config as MemoryConfig: {e}, using dict mode")
                self.memory_config = None

        self.agent_id = agent_id
        
        # Extract providers from config with fallbacks
        self.storage_type = storage_type or self._get_provider('vector_store', 'oceanbase')
        self.llm_provider = llm_provider or self._get_provider('llm', 'mock')
        self.embedding_provider = embedding_provider or self._get_provider('embedder', 'mock')

        # Initialize reranker if configured
        reranker = None
        if self.memory_config and hasattr(self.memory_config, 'reranker'):
            rerank_obj = self.memory_config.reranker
            if rerank_obj.enabled:
                try:
                    provider = rerank_obj.provider
                    reranker_params = rerank_obj.config if rerank_obj.config else {}
                    reranker = RerankFactory.create(provider, reranker_params)
                    logger.info(f"Reranker initialized from MemoryConfig: {provider}")
                except Exception as e:
                    logger.warning(f"Failed to initialize reranker from MemoryConfig: {e}")
                    reranker = None
        else:
            rerank_config = self.config.get('reranker', {})
            if rerank_config is not None and rerank_config.get('enabled', False):
                try:
                    provider = rerank_config.get('provider', 'qwen')
                    reranker_params = rerank_config.get('config', {})
                    reranker = RerankFactory.create(provider, reranker_params)
                    logger.info(f"Reranker initialized from JSON config: {provider}")
                except Exception as e:
                    logger.warning(f"Failed to initialize reranker from JSON config: {e}")
                    reranker = None
        
        # Initialize components
        vector_store_config = self._get_component_config('vector_store')
        
        # Pass reranker to vector store if it's OceanBase
        if self.storage_type.lower() == 'oceanbase' and reranker:
            vector_store_config['reranker'] = reranker
            logger.debug("Reranker passed to OceanBase vector store")
        
        vector_store = VectorStoreFactory.create(self.storage_type, vector_store_config)

        # Extract graph_store config
        self.enable_graph = self._get_graph_enabled()
        self.graph_store = None
        if self.enable_graph:
            logger.debug("Graph store enabled")
            graph_store_config = self.config.get("graph_store", {})
            if graph_store_config:
                provider = graph_store_config.get("provider", "oceanbase")
                config_to_pass = self.memory_config if self.memory_config else self.config
                self.graph_store = GraphStoreFactory.create(provider, config_to_pass)


        # Extract LLM config
        llm_config = self._get_component_config('llm')
        self.llm = LLMFactory.create(self.llm_provider, llm_config)

        # Extract audio_llm config (optional, for audio transcription)
        audio_llm_config = self._get_component_config('audio_llm')
        audio_llm_provider = self._get_provider('audio_llm', None)
        self.audio_llm = None
        if audio_llm_provider and audio_llm_config:
            try:
                self.audio_llm = LLMFactory.create(audio_llm_provider, audio_llm_config)
                logger.info(f"Audio LLM initialized: {audio_llm_provider}")
            except Exception as e:
                logger.warning(f"Failed to initialize audio_llm: {e}")

        # Extract embedder config
        embedder_config = self._get_component_config('embedder')
        # Pass vector_store_config so factory can extract embedding_model_dims for mock embeddings
        self.embedding = EmbedderFactory.create(self.embedding_provider, embedder_config, vector_store_config)
        
        # Initialize sparse embedder if configured
        self.sparse_embedder = None
        # Check if include_sparse is enabled in vector_store config
        include_sparse = vector_store_config.get('include_sparse', False)
        if self.storage_type.lower() == 'oceanbase' and include_sparse:
            sparse_config_obj = None
            sparse_embedder_provider = None
            
            if self.memory_config and hasattr(self.memory_config, 'sparse_embedder') and self.memory_config.sparse_embedder:
                sparse_config_obj = self.memory_config.sparse_embedder
            elif self.config.get('sparse_embedder'):
                sparse_config_obj = self.config.get('sparse_embedder')
            
            if sparse_config_obj:
                try:
                    # Handle SparseEmbedderConfig (BaseModel with provider and config) or dict format
                    if hasattr(sparse_config_obj, 'provider') and hasattr(sparse_config_obj, 'config'):
                        # It's a SparseEmbedderConfig (BaseModel) object
                        sparse_embedder_provider = sparse_config_obj.provider
                        config_dict = sparse_config_obj.config or {}
                    elif isinstance(sparse_config_obj, dict):
                        # It's a dict with provider and config keys
                        sparse_embedder_provider = sparse_config_obj.get('provider')
                        config_dict = sparse_config_obj.get('config', {})
                    else:
                        logger.warning(f"Unknown sparse_embedder config format: {type(sparse_config_obj)}. Expected SparseEmbedderConfig or dict with 'provider' and 'config' keys.")
                        sparse_embedder_provider = None
                        config_dict = {}
                    
                    if sparse_embedder_provider:
                        self.sparse_embedder = SparseEmbedderFactory.create(sparse_embedder_provider, config_dict)
                        logger.info(f"Sparse embedder initialized: {sparse_embedder_provider}")
                except Exception as e:
                    logger.warning(f"Failed to initialize sparse embedder: {e}")
        
        # Initialize storage adapter with embedding service and sparse embedder service
        # Automatically select adapter based on sub_stores configuration
        sub_stores_list = self.config.get('sub_stores', [])
        if sub_stores_list and self.storage_type.lower() == 'oceanbase':
            # Use SubStorageAdapter if sub stores are configured and using OceanBase
            self.storage = SubStorageAdapter(vector_store, self.embedding, self.sparse_embedder)
            logger.info("Using SubStorageAdapter with sub-store support")
        else:
            if sub_stores_list:
                logger.warning("The sub_stores function currently only supports oceanbase")
            self.storage = StorageAdapter(vector_store, self.embedding, self.sparse_embedder)
            logger.info("Using basic StorageAdapter")

        self.intelligence = IntelligenceManager(self.config)
        self.telemetry = TelemetryManager(self.config)
        self.audit = AuditLogger(self.config)

        # Save custom prompts from config
        if self.memory_config:
            self.custom_fact_extraction_prompt = self.memory_config.custom_fact_extraction_prompt
            self.custom_update_memory_prompt = self.memory_config.custom_update_memory_prompt
        else:
            self.custom_fact_extraction_prompt = self.config.get('custom_fact_extraction_prompt')
            self.custom_update_memory_prompt = self.config.get('custom_update_memory_prompt')

        # Intelligent memory plugin (pluggable)
        merged_cfg = self._get_intelligent_memory_config()

        plugin_type = merged_cfg.get("plugin", "ebbinghaus")
        self._intelligence_plugin: Optional[IntelligentMemoryPlugin] = None
        if merged_cfg.get("enabled", False):
            try:
                if plugin_type == "ebbinghaus":
                    self._intelligence_plugin = EbbinghausIntelligencePlugin(merged_cfg)
                else:
                    logger.warning(f"Unknown intelligence plugin: {plugin_type}")
            except Exception as e:
                logger.warning(f"Failed to initialize intelligence plugin: {e}")
                self._intelligence_plugin = None

        
        # Sub stores configuration (support multiple)
        self.sub_stores_config: List[Dict] = []

        # Initialize sub stores
        self._init_sub_stores()

        logger.info(f"Memory initialized with storage: {self.storage_type}, LLM: {self.llm_provider}, agent: {self.agent_id or 'default'}")
        self.telemetry.capture_event("memory.init", {"storage_type": self.storage_type, "llm_provider": self.llm_provider, "agent_id": self.agent_id})

    def _get_provider(self, component: str, default: str) -> str:
        """
        Helper method to get component provider uniformly.

        Args:
            component: Component name ('vector_store', 'llm', 'embedder')
            default: Default provider name

        Returns:
            Provider name string
        """
        if self.memory_config:
            component_obj = getattr(self.memory_config, component, None)
            return component_obj.provider if component_obj else default
        else:
            return self.config.get(component, {}).get('provider', default)

    def _get_component_config(self, component: str) -> Dict[str, Any]:
        """
        Helper method to get component configuration uniformly.

        Args:
            component: Component name ('vector_store', 'llm', 'embedder', 'graph_store')

        Returns:
            Component configuration dictionary
        """
        if self.memory_config:
            component_obj = getattr(self.memory_config, component, None)
            return component_obj.config or {} if component_obj else {}
        else:
            return self.config.get(component, {}).get('config', {})

    def _get_graph_enabled(self) -> bool:
        """
        Helper method to get graph store enabled status.

        Returns:
            Boolean indicating whether graph store is enabled
        """
        if self.memory_config:
            return self.memory_config.graph_store.enabled if self.memory_config.graph_store else False
        else:
            graph_store_config = self.config.get('graph_store', {})
            return graph_store_config.get('enabled', False) if graph_store_config else False

    def _get_intelligent_memory_config(self) -> Dict[str, Any]:
        """
        Helper method to get intelligent memory configuration.
        Supports both "intelligence" and "intelligent_memory" config keys for backward compatibility.

        Returns:
            Merged intelligent memory configuration dictionary
        """
        if self.memory_config and self.memory_config.intelligent_memory:
            # Use MemoryConfig's intelligent_memory
            cfg = self.memory_config.intelligent_memory.model_dump()
            # Merge custom_importance_evaluation_prompt from top level if present
            if self.memory_config.custom_importance_evaluation_prompt:
                cfg["custom_importance_evaluation_prompt"] = self.memory_config.custom_importance_evaluation_prompt
            return cfg
        else:
            # Fallback to dict access
            intelligence_cfg = (self.config or {}).get("intelligence", {})
            intelligent_memory_cfg = (self.config or {}).get("intelligent_memory", {})
            merged_cfg = {**intelligence_cfg, **intelligent_memory_cfg}
            # Merge custom_importance_evaluation_prompt from top level if present
            if "custom_importance_evaluation_prompt" in self.config:
                merged_cfg["custom_importance_evaluation_prompt"] = self.config["custom_importance_evaluation_prompt"]
            return merged_cfg

    def _extract_facts(self, messages: Any) -> List[str]:
        """
        Extract facts from messages using LLM.
        Integrates with IntelligenceManager for enhanced processing.
        
        Args:
            messages: Messages (list of dicts, single dict, or str)
            
        Returns:
            List of extracted facts
        """
        try:
            # Parse messages into conversation format
            conversation = parse_messages_for_facts(messages)
            
            # Use custom prompt if provided, otherwise use default
            if self.custom_fact_extraction_prompt:
                system_prompt = self.custom_fact_extraction_prompt
                user_prompt = f"Input:\n{conversation}"
            else:
                system_prompt = FACT_RETRIEVAL_PROMPT
                user_prompt = f"Input:\n{conversation}"
            
            # Call LLM to extract facts
            try:
                response = self.llm.generate_response(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"}
                )
            except Exception as e:
                logger.error(f"Error in fact extraction: {e}")
                response = ""
            
            # Parse response
            try:
                # Remove code blocks if present (LLM sometimes wraps JSON in code blocks)
                response = remove_code_blocks(response)
                facts_data = json.loads(response)
                facts = facts_data.get("facts", [])
                
                # Log for debugging
                logger.debug(f"Extracted {len(facts)} facts: {facts}")
                
                return facts
            except Exception as e:
                logger.error(f"Error in new_retrieved_facts: {e}")
                return []
                
        except Exception as e:
            logger.error(f"Error extracting facts: {e}")
            return []
    
    def _decide_memory_actions(
        self, 
        new_facts: List[str], 
        existing_memories: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Use LLM to decide memory actions (ADD/UPDATE/DELETE/NONE).
        
        Args:
            new_facts: List of newly extracted facts
            existing_memories: List of existing memories with 'id' and 'text'
            user_id: User identifier
            agent_id: Agent identifier
            
        Returns:
            List of memory action dictionaries
        """
        try:
            if not new_facts:
                logger.debug("No new facts to process")
                return []
            
            # Format existing memories for prompt
            old_memory = []
            for mem in existing_memories:
                # Support both "memory" and "content" field names for compatibility
                content = mem.get("memory", "") or mem.get("content", "")
                old_memory.append({
                    "id": mem.get("id", "unknown"),
                    "text": content
                })
            
            # Generate update prompt with custom prompt if provided
            custom_prompt = None
            if hasattr(self, 'custom_update_memory_prompt') and self.custom_update_memory_prompt:
                custom_prompt = self.custom_update_memory_prompt
            update_prompt = get_memory_update_prompt(old_memory, new_facts, custom_prompt)
            
            # Call LLM
            try:
                response = self.llm.generate_response(
                    messages=[{"role": "user", "content": update_prompt}],
                    response_format={"type": "json_object"}
                )
            except Exception as e:
                logger.error(f"Error in new memory actions response: {e}")
                response = ""
            
            # Parse response
            try:
                response = remove_code_blocks(response)
                actions_data = json.loads(response)
                actions = actions_data.get("memory", [])
                return actions
            except Exception as e:
                logger.error(f"Invalid JSON response: {e}")
                return []
                
        except Exception as e:
            logger.error(f"Error deciding memory actions: {e}")
            return []
    
    def add(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
        infer: bool = True,
    ) -> Dict[str, Any]:
        """Add a new memory with optional intelligent processing.
        
        Returns:
            Dict[str, Any]: A dictionary containing the add operation results with the following structure:
                - "results" (List[Dict]): List of memory operation results, where each result contains:
                    - "id" (int): Memory ID
                    - "memory" (str): The memory content
                    - "event" (str): Operation event type (e.g., "ADD", "UPDATE", "DELETE")
                    - "user_id" (str, optional): User ID associated with the memory
                    - "agent_id" (str, optional): Agent ID associated with the memory
                    - "run_id" (str, optional): Run ID associated with the memory
                    - "metadata" (Dict, optional): Metadata dictionary
                    - "created_at" (str, optional): Creation timestamp in ISO format
                    - "previous_memory" (str, optional): Previous memory content (for UPDATE events)
                - "relations" (Dict, optional): Graph relations if graph store is enabled, containing:
                    - "deleted_entities" (List): List of deleted graph entities
                    - "added_entities" (List): List of added graph entities
        """
        try:
            # Handle messages parameter
            if messages is None:
                raise ValueError("messages must be provided (str, dict, or list[dict])")
            
            # Normalize input format
            if isinstance(messages, str):
                messages = [{"role": "user", "content": messages}]
            elif isinstance(messages, dict):
                messages = [messages]
            elif not isinstance(messages, list):
                raise ValueError("messages must be str, dict, or list[dict]")
            
            # Vision-aware message processing
            llm_cfg = {}
            try:
                llm_cfg = (self.config or {}).get("llm", {}).get("config", {})
            except Exception:
                llm_cfg = {}
            if llm_cfg.get("enable_vision"):
                messages = parse_vision_messages(messages, self.llm, llm_cfg.get("vision_details"), self.audio_llm)
            else:
                messages = parse_vision_messages(messages, None, None, self.audio_llm)
            
            # Use self.agent_id as fallback if agent_id is not provided
            agent_id = agent_id or self.agent_id
            
            # Check if intelligent memory should be used
            use_infer = infer and isinstance(messages, list) and len(messages) > 0
            
            # If not using intelligent memory, fall back to simple mode
            if not use_infer:
                return self._simple_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
            
            # Intelligent memory mode: extract facts, search similar memories, and consolidate
            return self._intelligent_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
            
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            self.telemetry.capture_event("memory.add.error", {"error": str(e)})
            raise
    
    def _simple_add(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Simple add mode: direct storage without intelligence.
        
        Returns:
            Dict[str, Any]: A dictionary containing the add operation results with the following structure:
                - "results" (List[Dict]): List containing a single memory operation result with:
                    - "id" (int): Memory ID
                    - "memory" (str): The memory content
                    - "event" (str): Operation event type ("ADD")
                    - "user_id" (str, optional): User ID associated with the memory
                    - "agent_id" (str, optional): Agent ID associated with the memory
                    - "run_id" (str, optional): Run ID associated with the memory
                    - "metadata" (Dict, optional): Metadata dictionary
                    - "created_at" (str): Creation timestamp in ISO format
                - "relations" (Dict, optional): Graph relations if graph store is enabled, containing:
                    - "deleted_entities" (List): List of deleted graph entities
                    - "added_entities" (List): List of added graph entities
        """
        # Parse messages into content
        if isinstance(messages, str):
            content = messages
        elif isinstance(messages, dict):
            content = messages.get("content", "")
        elif isinstance(messages, list):
            content = "\n".join([msg.get("content", "") for msg in messages if isinstance(msg, dict) and msg.get("content")])
        else:
            raise ValueError("messages must be str, dict, or list[dict]")
        
        # Validate content is not empty
        if not content or not content.strip():
            logger.error(f"Cannot store empty content. Messages: {messages}")
            raise ValueError(f"Cannot create memory with empty content. Original messages: {messages}")
        
        # Select embedding service based on metadata (for sub-store routing)
        embedding_service = self._get_embedding_service(metadata)

        # Generate embedding
        embedding = embedding_service.embed(content, memory_action="add")
        
        # Disabled LLM-based importance evaluation to save tokens
        # Process with intelligence manager
        # enhanced_metadata = self.intelligence.process_metadata(content, metadata)
        enhanced_metadata = metadata  # Use original metadata without LLM evaluation

        # Intelligent plugin annotations
        extra_fields = {}
        if self._intelligence_plugin and self._intelligence_plugin.enabled:
            extra_fields = self._intelligence_plugin.on_add(content=content, metadata=enhanced_metadata)
        

        # Generate content hash for deduplication
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

        # Extract category from enhanced metadata if present
        category = ""
        if enhanced_metadata and isinstance(enhanced_metadata, dict):
            category = enhanced_metadata.get("category", "")
            # Remove category from metadata to avoid duplication
            enhanced_metadata = {k: v for k, v in enhanced_metadata.items() if k != "category"}

        # Final validation before storage
        if not content or not content.strip():
            raise ValueError(f"Refusing to store empty content. Original messages: {messages}")
        
        # Use self.agent_id as fallback if agent_id is not provided
        agent_id = agent_id or self.agent_id
        
        # Store in database
        memory_data = {
            "content": content,
            "embedding": embedding,
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "hash": content_hash,
            "category": category,
            "metadata": enhanced_metadata or {},
            "filters": filters or {},
            "created_at": get_current_datetime(),
            "updated_at": get_current_datetime(),
        }

        if extra_fields:
            memory_data.update(extra_fields)
        
        memory_id = self.storage.add_memory(memory_data)
        
        # Log audit event
        self.audit.log_event("memory.add", {
            "memory_id": memory_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "content_length": len(content)
        }, user_id=user_id, agent_id=agent_id)
        
        # Capture telemetry
        self.telemetry.capture_event("memory.add", {
            "memory_id": memory_id,
            "user_id": user_id,
            "agent_id": agent_id
        })
        
        graph_result = self._add_to_graph(messages, filters, user_id, agent_id, run_id)
        
        result: Dict[str, Any] = {
            "results": [{
                "id": memory_id,
                "memory": content,
                "event": "ADD",
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "metadata": metadata,
                "created_at": memory_data["created_at"].isoformat() if isinstance(memory_data["created_at"], datetime) else memory_data["created_at"],
            }]
        }
        if graph_result:
            result["relations"] = graph_result
        return result
    
    def _intelligent_add(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Intelligent add mode: extract facts, consolidate with existing memories."""
        # Use self.agent_id as fallback if agent_id is not provided
        agent_id = agent_id or self.agent_id
        
        # Get intelligent memory config to check fallback setting
        intelligent_config = self._get_intelligent_memory_config()
        fallback_to_simple = intelligent_config.get("fallback_to_simple_add", False)
        
        # Step 1: Extract facts from messages
        logger.info("Extracting facts from messages...")
        facts = self._extract_facts(messages)
        
        if not facts:
            logger.debug("No facts extracted, skip intelligent add")
            if fallback_to_simple:
                logger.warning("No facts extracted from messages, falling back to simple add mode")
                return self._simple_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
            return {"results": []}

        logger.info(f"Extracted {len(facts)} facts: {facts}")
        
        # Step 2: Search for similar memories for each fact
        existing_memories = []
        fact_embeddings = {}
        
        # Select embedding service based on metadata (for sub-store routing)
        embedding_service = self._get_embedding_service(metadata)

        for fact in facts:
            fact_embedding = embedding_service.embed(fact, memory_action="add")
            fact_embeddings[fact] = fact_embedding
            
            # Merge metadata into filters for correct routing
            search_filters = filters.copy() if filters else {}
            if metadata:
                # Filter metadata to only include simple values (strings, numbers, booleans, None)
                # This prevents nested dicts like {'agent': {'agent_id': ...}} from causing issues
                # when OceanBase's build_condition tries to parse them as operators
                simple_metadata = {
                    k: v for k, v in metadata.items()
                    if not isinstance(v, (dict, list)) and k not in ['agent_id', 'user_id', 'run_id']
                }
                search_filters.update(simple_metadata)

            # Search for similar memories with reduced limit to reduce noise
            # Pass fact text to enable hybrid search for better results
            similar = self.storage.search_memories(
                query_embedding=fact_embedding,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=search_filters,
                limit=5,
                query=fact  # Enable hybrid search
            )
            existing_memories.extend(similar)
        
        # Improved deduplication: prefer memories with better similarity scores
        unique_memories = {}
        for mem in existing_memories:
            mem_id = mem.get("id")
            if mem_id and mem_id not in unique_memories:
                unique_memories[mem_id] = mem
            elif mem_id:
                # If duplicate ID, keep the one with better similarity (lower distance)
                existing = unique_memories.get(mem_id)
                mem_distance = mem.get("distance", float('inf'))
                existing_distance = existing.get("distance", float('inf')) if existing else float('inf')
                if mem_distance < existing_distance:
                    unique_memories[mem_id] = mem
        
        # Limit candidates to avoid LLM prompt overload
        existing_memories = list(unique_memories.values())[:10]  # Max 10 memories
        
        logger.info(f"Found {len(existing_memories)} existing memories to consider (after dedup and limiting)")
        
        # Mapping IDs with integers for handling ID hallucinations
        # Maps temporary string indices to real Snowflake IDs (integers)
        temp_uuid_mapping = {}
        for idx, item in enumerate(existing_memories):
            temp_uuid_mapping[str(idx)] = item["id"]
            existing_memories[idx]["id"] = str(idx)
        
        # Step 3: Let LLM decide memory actions (only if we have new facts)
        actions = []
        if facts:
            actions = self._decide_memory_actions(facts, existing_memories, user_id, agent_id)
            logger.info(f"LLM decided on {len(actions)} memory actions")
        else:
            logger.debug("No new facts, skipping LLM decision step")
        
        # Step 4: Execute actions
        results = []
        action_counts = {"ADD": 0, "UPDATE": 0, "DELETE": 0, "NONE": 0}
        
        if not actions:
            logger.warning("No actions returned from LLM, skip intelligent add")
            if fallback_to_simple:
                logger.warning("No actions returned from LLM, falling back to simple add mode")
                return self._simple_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
            return {"results": []}

        for action in actions:
            action_text = action.get("text", "") or action.get("memory", "")
            event_type = action.get("event", "NONE")
            action_id = action.get("id", "")
            
            # Skip actions with empty text UNLESS it's a NONE event (duplicates may have empty text)
            if not action_text and event_type != "NONE":
                logger.warning(f"Skipping action with empty text: {action}")
                continue
            
            logger.debug(f"Processing action: {event_type} - '{action_text[:50] if action_text else 'NONE'}...' (id: {action_id})")
            
            try:
                if event_type == "ADD":
                    # Add new memory
                    memory_id = self._create_memory(
                        content=action_text,
                        user_id=user_id,
                        agent_id=agent_id,
                        run_id=run_id,
                        metadata=metadata,
                        filters=filters,
                        existing_embeddings=fact_embeddings
                    )
                    results.append({
                        "id": memory_id,
                        "memory": action_text,
                        "event": event_type,
                        "metadata": metadata or {}
                    })
                    action_counts["ADD"] += 1
                    
                elif event_type == "UPDATE":
                    # Use ID mapping to get the real memory ID (Snowflake ID - integer)
                    real_memory_id = temp_uuid_mapping.get(str(action_id))
                    if real_memory_id:
                        self._update_memory(
                            memory_id=real_memory_id,
                            content=action_text,
                            user_id=user_id,
                            agent_id=agent_id,
                            existing_embeddings=fact_embeddings
                        )
                        results.append({
                            "id": real_memory_id,
                            "memory": action_text,
                            "event": event_type,
                            "previous_memory": action.get("old_memory")
                        })
                        action_counts["UPDATE"] += 1
                    else:
                        logger.warning(f"Could not find real memory ID for action ID: {action_id}")
                        
                elif event_type == "DELETE":
                    # Use ID mapping to get the real memory ID (Snowflake ID - integer)
                    real_memory_id = temp_uuid_mapping.get(str(action_id))
                    if real_memory_id:
                        self.delete(real_memory_id, user_id, agent_id)
                        results.append({
                            "id": real_memory_id,
                            "memory": action_text,
                            "event": event_type
                        })
                        action_counts["DELETE"] += 1
                    else:
                        logger.warning(f"Could not find real memory ID for action ID: {action_id}")
                        
                elif event_type == "NONE":
                    logger.debug("No action needed for memory (duplicate detected)")
                    action_counts["NONE"] += 1
                    
            except Exception as e:
                logger.error(f"Error executing memory action {event_type}: {e}")
        
        # Log audit event for intelligent add operation
        self.audit.log_event("memory.intelligent_add", {
            "user_id": user_id,
            "agent_id": agent_id,
            "facts_count": len(facts),
            "action_counts": action_counts,
            "results_count": len(results)
        }, user_id=user_id, agent_id=agent_id)
        
        # Add to graph store and get relations
        graph_result = self._add_to_graph(messages, filters, user_id, agent_id, run_id)

        # If we have results, return them
        if results:
            result: Dict[str, Any] = {"results": results}
            if graph_result:
                result["relations"] = graph_result
            return result
        # If we processed actions but they were all NONE (duplicates detected), return empty results
        elif action_counts.get("NONE", 0) > 0:
            logger.info(f"All actions were NONE (duplicates detected), returning empty results")
            result: Dict[str, Any] = {"results": []}
            if graph_result:
                result["relations"] = graph_result
            return result
        # If we had actions but no results (all failed), check fallback setting
        else:
            logger.warning("Actions were processed but no results were created")
            if fallback_to_simple:
                logger.warning("Falling back to simple add mode")
                return self._simple_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
            return {"results": []}

    def _add_to_graph(
        self,
        messages,
        filters: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Add messages to graph store and return relations.
        
        Returns:
            dict with added_entities and deleted_entities, or None if graph store is disabled
        """
        if not self.enable_graph:
            return None
        
        # Extract content from messages for graph processing
        if isinstance(messages, str):
            data = messages
        elif isinstance(messages, dict):
            data = messages.get("content", "")
        elif isinstance(messages, list):
            data = "\n".join([
                msg.get("content", "") 
                for msg in messages 
                if isinstance(msg, dict) and msg.get("content") and msg.get("role") != "system"
            ])
        else:
            data = ""
        
        if not data:
            return None
        
        graph_filters = {**(filters or {}), "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
        if graph_filters.get("user_id") is None:
            graph_filters["user_id"] = "user"
        
        return self.graph_store.add(data, graph_filters)
    
    def _create_memory(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        existing_embeddings: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Create a memory with optional embeddings."""
        # Validate content is not empty
        if not content or not content.strip():
            raise ValueError(f"Cannot create memory with empty content: '{content}'")
        
        # Select embedding service based on metadata (for sub-store routing)
        embedding_service = self._get_embedding_service(metadata)

        # Generate or use existing embedding
        if existing_embeddings and content in existing_embeddings:
            embedding = existing_embeddings[content]
        else:
            embedding = embedding_service.embed(content, memory_action="add")
        
        # Disabled LLM-based importance evaluation to save tokens
        # Process metadata
        # enhanced_metadata = self.intelligence.process_metadata(content, metadata)
        enhanced_metadata = metadata  # Use original metadata without LLM evaluation
        
        # Generate content hash
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        
        # Extract category
        category = ""
        if enhanced_metadata and isinstance(enhanced_metadata, dict):
            category = enhanced_metadata.get("category", "")
            enhanced_metadata = {k: v for k, v in enhanced_metadata.items() if k != "category"}
        
        # Use self.agent_id as fallback if agent_id is not provided
        agent_id = agent_id or self.agent_id
        
        # Create memory data
        memory_data = {
            "content": content,
            "embedding": embedding,
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "hash": content_hash,
            "category": category,
            "metadata": enhanced_metadata or {},
            "filters": filters or {},
            "created_at": get_current_datetime(),
            "updated_at": get_current_datetime(),
        }
        
        memory_id = self.storage.add_memory(memory_data)
        
        return memory_id
    
    def _update_memory(
        self,
        memory_id: int,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        existing_embeddings: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Update a memory with optional embeddings."""
        # Use self.agent_id as fallback if agent_id is not provided
        agent_id = agent_id or self.agent_id
        
        # Validate content is not empty
        if not content or not content.strip():
            raise ValueError(f"Cannot update memory with empty content: '{content}'")
        
        # Generate or use existing embedding
        if existing_embeddings and content in existing_embeddings:
            embedding = existing_embeddings[content]
        else:
            # If no metadata provided, try to get existing memory's metadata
            if metadata is None:
                existing = self.storage.get_memory(memory_id, user_id, agent_id)
                if existing:
                    metadata = existing.get("metadata", {})

            # Select embedding service based on metadata (for sub-store routing)
            embedding_service = self._get_embedding_service(metadata)

            embedding = embedding_service.embed(content, memory_action="update")
        
        # Generate content hash
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        
        update_data = {
            "content": content,
            "embedding": embedding,
            "hash": content_hash,  # Update hash
            "updated_at": get_current_datetime(),
        }
        
        logger.debug(f"Updating memory {memory_id} with content: '{content[:50]}...'")
        
        self.storage.update_memory(memory_id, update_data, user_id, agent_id)
    
    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 30,
        threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Search for memories.
        
        Returns:
            Dict[str, Any]: A dictionary containing search results with the following structure:
                - "results" (List[Dict]): List of memory search results, where each result contains:
                    - "memory" (str): The memory content
                    - "metadata" (Dict): Metadata associated with the memory
                    - "score" (float): Similarity score for the result
                    - "id" (int, optional): Memory ID
                    - "created_at" (datetime, optional): Creation timestamp
                    - "updated_at" (datetime, optional): Update timestamp
                    - "user_id" (str, optional): User ID
                    - "agent_id" (str, optional): Agent ID
                    - "run_id" (str, optional): Run ID
                - "relations" (List, optional): Graph relations if graph store is enabled
        """
        try:
            # Select embedding service based on filters (for sub-store routing)
            embedding_service = self._get_embedding_service(filters)

            # Generate query embedding
            query_embedding = embedding_service.embed(query, memory_action="search")
            

            # Search in storage - pass query text to enable hybrid search
            results = self.storage.search_memories(
                query_embedding=query_embedding,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filters,
                limit=limit,
                query=query  # Pass query text for hybrid search (vector + full-text + sparse vector)
            )
            
            # Process results with intelligence manager (only if enabled to avoid unnecessary calls)
            if self.intelligence.enabled:
                processed_results = self.intelligence.process_search_results(results, query)
            else:
                processed_results = results

            # Intelligent plugin lifecycle management on search
            if self._intelligence_plugin and self._intelligence_plugin.enabled:
                updates, deletes = self._intelligence_plugin.on_search(processed_results)
                for mem_id, upd in updates:
                    try:
                        self.storage.update_memory(mem_id, {**upd}, user_id, agent_id)
                    except Exception:
                        continue
                for mem_id in deletes:
                    try:
                        self.storage.delete_memory(mem_id, user_id, agent_id)
                    except Exception:
                        continue
            
            # Transform results to match benchmark expected format
            # Benchmark expects: {"results": [{"memory": ..., "metadata": {...}, "score": ...}], "relations": [...]}
            transformed_results = []
            for result in processed_results:
                score = result.get("score", 0.0)
                
                # Get quality score for threshold filtering
                # Quality score represents absolute similarity quality (0-1 range)
                # It's calculated from weighted average of all search paths' similarity scores
                metadata = result.get("metadata", {})
                quality_score = metadata.get("_quality_score")
                
                # If quality_score is not available (e.g., from older data or non-hybrid search),
                # fall back to using the ranking score
                if quality_score is None:
                    quality_score = score
                
                # Apply threshold filtering using quality score
                # Only include results if threshold is None or quality_score >= threshold
                if threshold is not None and quality_score < threshold:
                    continue
                
                transformed_result = {
                    "memory": result.get("memory", ""),
                    "metadata": metadata,  # Keep metadata as-is from storage (includes debug info like _quality_score)
                    "score": score,
                }
                # Preserve other fields if needed
                for key in ["id", "created_at", "updated_at", "user_id", "agent_id", "run_id"]:
                    if key in result:
                        transformed_result[key] = result[key]
                
                # Ensure memory_id field exists (for API compatibility)
                if "id" in transformed_result and "memory_id" not in transformed_result:
                    transformed_result["memory_id"] = transformed_result["id"]
                transformed_results.append(transformed_result)
            
            # Log audit event
            self.audit.log_event("memory.search", {
                "query": query,
                "user_id": user_id,
                "agent_id": agent_id,
                "results_count": len(transformed_results)
            }, user_id=user_id, agent_id=agent_id)
            
            # Capture telemetry
            self.telemetry.capture_event("memory.search", {
                "user_id": user_id,
                "agent_id": agent_id,
                "results_count": len(transformed_results),
                "threshold": threshold
            })

            # Search in graph store
            if self.enable_graph:
                filters = {**(filters or {}), "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
                graph_results = self.graph_store.search(query, filters, limit)
                return {"results": transformed_results, "relations": graph_results}

            # Return in benchmark expected format
            return {"results": transformed_results}
            
        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            self.telemetry.capture_event("memory.search.error", {"error": str(e)})
            raise
    
    def get(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID.
        
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the memory data if found, None otherwise.
                The dictionary contains the following fields:
                    - "id" (int): Memory ID
                    - "content" (str): The memory content
                    - "user_id" (str, optional): User ID associated with the memory
                    - "agent_id" (str, optional): Agent ID associated with the memory
                    - "run_id" (str, optional): Run ID associated with the memory
                    - "metadata" (Dict): Metadata dictionary associated with the memory
                    - "created_at" (datetime, optional): Creation timestamp
                    - "updated_at" (datetime, optional): Update timestamp
                Returns None if the memory is not found or access is denied.
        """
        try:

            result = self.storage.get_memory(memory_id, user_id, agent_id)
            
            if result:
                # Intelligent plugin lifecycle on get
                if self._intelligence_plugin and self._intelligence_plugin.enabled:
                    updates, delete_flag = self._intelligence_plugin.on_get(result)
                    try:
                        if delete_flag:
                            self.storage.delete_memory(memory_id, user_id, agent_id)
                            return None
                        if updates:
                            self.storage.update_memory(memory_id, {**updates}, user_id, agent_id)
                    except Exception:
                        pass
                self.audit.log_event("memory.get", {
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "agent_id": agent_id
                }, user_id=user_id, agent_id=agent_id)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            raise
    
    def update(
        self,
        memory_id: int,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing memory.
        
        Returns:
            Dict[str, Any]: A dictionary containing the updated memory data if successful, None if memory not found or access denied.
                The dictionary contains the following fields:
                    - "id" (int): Memory ID
                    - "content" (str): The updated memory content (stored as "data" in payload)
                    - "user_id" (str, optional): User ID associated with the memory
                    - "agent_id" (str, optional): Agent ID associated with the memory
                    - "run_id" (str, optional): Run ID associated with the memory
                    - "metadata" (Dict): Metadata dictionary associated with the memory
                    - "created_at" (str, optional): Creation timestamp in ISO format
                    - "updated_at" (str): Update timestamp in ISO format
                    - "hash" (str): Content hash for deduplication
                    - "category" (str, optional): Category of the memory
                Returns None if the memory is not found or access is denied.
        """
        try:
            # Validate content is not empty
            if not content or not content.strip():
                raise ValueError(f"Cannot update memory with empty content: '{content}'")

            # If no metadata provided, try to get existing memory's metadata
            if metadata is None:
                existing = self.storage.get_memory(memory_id, user_id, agent_id)
                if existing:
                    metadata = existing.get("metadata", {})

            # Select embedding service based on metadata (for sub-store routing)
            embedding_service = self._get_embedding_service(metadata)

            # Generate new embedding
            embedding = embedding_service.embed(content, memory_action="update")
            
            # Process metadata with intelligence manager (if enabled)
            # Disabled LLM-based importance evaluation to save tokens (consistent with add method)
            # enhanced_metadata = self.intelligence.process_metadata(content, metadata)
            enhanced_metadata = metadata  # Use original metadata without LLM evaluation

            # Intelligent plugin annotations
            extra_fields = {}
            if self._intelligence_plugin and self._intelligence_plugin.enabled:
                # Get existing memory for context
                existing_memory = self.get(memory_id, user_id=user_id)
                if existing_memory:
                    # Plugin can process update event
                    extra_fields = self._intelligence_plugin.on_add(content=content, metadata=enhanced_metadata)
            
            # Generate content hash for deduplication
            content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

            # Extract category from enhanced metadata if present
            category = ""
            if enhanced_metadata and isinstance(enhanced_metadata, dict):
                category = enhanced_metadata.get("category", "")
                # Remove category from metadata to avoid duplication
                enhanced_metadata = {k: v for k, v in enhanced_metadata.items() if k != "category"}

            # Merge extra fields from intelligence plugin
            if extra_fields and isinstance(extra_fields, dict):
                enhanced_metadata = {**(enhanced_metadata or {}), **extra_fields}

            # Update in storage
            update_data = {
                "content": content,
                "embedding": embedding,
                "metadata": enhanced_metadata,
                "hash": content_hash,  # Update hash
                "category": category,
                "updated_at": get_current_datetime(),
            }
            
            result = self.storage.update_memory(memory_id, update_data, user_id, agent_id)
            
            # Log audit event
            self.audit.log_event("memory.update", {
                "memory_id": memory_id,
                "user_id": user_id,
                "agent_id": agent_id
            }, user_id=user_id, agent_id=agent_id)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            raise
    
    def delete(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory."""
        try:

            result = self.storage.delete_memory(memory_id, user_id, agent_id)
            
            if result:
                self.audit.log_event("memory.delete", {
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "agent_id": agent_id
                }, user_id=user_id, agent_id=agent_id)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise
    
    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> bool:
        """Delete all memories for given identifiers."""
        try:
            result = self.storage.clear_memories(user_id, agent_id, run_id)
            
            if result:
                self.audit.log_event("memory.delete_all", {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "run_id": run_id
                }, user_id=user_id, agent_id=agent_id)
                
                self.telemetry.capture_event("memory.delete_all", {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "run_id": run_id
                })

            if self.enable_graph:
                filters = {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}
                self.graph_store.delete_all(filters)

            return result
            
        except Exception as e:
            logger.error(f"Failed to delete all memories: {e}")
            raise
    
    def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get all memories with optional filtering.
        
        Returns:
            dict[str, list[dict[str, Any]]]: A dictionary containing all memories with the following structure:
                - "results" (List[Dict]): List of memory dictionaries, where each memory contains:
                    - "id" (int): Memory ID
                    - "content" (str): The memory content
                    - "user_id" (str, optional): User ID associated with the memory
                    - "agent_id" (str, optional): Agent ID associated with the memory
                    - "run_id" (str, optional): Run ID associated with the memory
                    - "metadata" (Dict): Metadata dictionary associated with the memory
                    - "created_at" (datetime or str, optional): Creation timestamp
                    - "updated_at" (datetime or str, optional): Update timestamp
                - "relations" (List[Dict], optional): Graph relations if graph store is enabled
        """
        try:
            results = self.storage.get_all_memories(user_id, agent_id, run_id, limit, offset)
            
            self.audit.log_event("memory.get_all", {
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "limit": limit,
                "offset": offset,
                "results_count": len(results)
            }, user_id=user_id, agent_id=agent_id)

            # get from graph store
            if self.enable_graph:
                filters = {**(filters or {}), "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
                graph_results = self.graph_store.get_all(filters, limit + offset)
                results.extend(graph_results)
                return {"results": results, "relations": graph_results}

            return {"results": results}
            
        except Exception as e:
            logger.error(f"Failed to get all memories: {e}")
            raise
    
    def reset(self):
        """
        Reset the memory store by:
            Deletes the vector store collection
            Resets the database
            Recreates the vector store with a new client
        """
        logger.warning("Resetting all memories")
        
        try:
            # Reset vector store
            if hasattr(self.storage.vector_store, "reset"):
                self.storage.vector_store.reset()
            else:
                logger.warning("Vector store does not support reset. Skipping.")
                self.storage.vector_store.delete_col()
                # Recreate vector store
                from ..storage.factory import VectorStoreFactory
                vector_store_config = self._get_component_config('vector_store')
                self.storage.vector_store = VectorStoreFactory.create(self.storage_type, vector_store_config)
                # Update storage adapter
                self.storage = StorageAdapter(self.storage.vector_store, self.embedding, self.sparse_embedder)
            
            # Reset graph store if enabled
            if self.enable_graph and hasattr(self.graph_store, "reset"):
                self.graph_store.reset()
            
            # Log telemetry event
            self.telemetry.capture_event("memory.reset", {"sync_type": "sync"})
            
            logger.info("Memory store reset completed successfully")
            
        except Exception as e:
            logger.error(f"Failed to reset memory store: {e}")
            raise
    
    def _init_sub_stores(self):
        """Initialize multiple sub stores configuration"""
        if self.sub_stores_config:
            logger.info(f"Sub stores enabled: {len(self.sub_stores_config)} stores")

        sub_stores_list = self.config.get('sub_stores', [])

        if not sub_stores_list:
            logger.info("No sub stores configured")
            return

        # Sub store feature only supports OceanBase storage
        if self.storage_type.lower() != 'oceanbase':
            logger.warning(f"Sub store feature only supports OceanBase storage, current storage: {self.storage_type}")
            logger.warning("Sub stores configuration will be ignored")
            return

        # Get main table information
        main_collection_name = self.config.get('vector_store', {}).get('config', {}).get('collection_name', 'memories')
        main_embedding_dims = self.config.get('vector_store', {}).get('config', {}).get('embedding_model_dims', 1536)

        # Iterate through configs and initialize each sub store
        for index, sub_config in enumerate(sub_stores_list):
            try:
                self._init_single_sub_store(index, sub_config, main_collection_name, main_embedding_dims)
            except Exception as e:
                logger.error(f"Failed to initialize sub store {index}: {e}")
                continue

    def _init_single_sub_store(
        self,
        index: int,
        sub_config: Dict,
        main_collection_name: str,
        main_embedding_dims: int
    ):
        """Initialize a single sub store"""

        # 1. Determine sub store name (default: {main_table_name}_sub_{index})
        sub_store_name = sub_config.get(
            'collection_name',
            f"{main_collection_name}_sub_{index}"
        )

        # 2. Get routing rules (required)
        routing_filter = sub_config.get('routing_filter')
        if not routing_filter:
            logger.warning(f"Sub store {index} has no routing_filter, skipping")
            return

        # 3. Determine vector dimension (default: same as main table)
        embedding_model_dims = sub_config.get('embedding_model_dims', main_embedding_dims)

        # 4. Initialize sub store's embedding service
        sub_embedding_config = sub_config.get('embedder', sub_config.get('embedding', {}))

        if sub_embedding_config:
            # Has independent embedding configuration
            sub_embedding_provider = sub_embedding_config.get('provider', self.embedding_provider)
            sub_embedding_params = sub_embedding_config.get('config', {})

            # Inherit api_key and other configs from main table
            main_embedding_config = self.config.get('embedding', {}).get('config', {})
            for key in ['api_key', 'openai_base_url', 'timeout']:
                if key not in sub_embedding_params and key in main_embedding_config:
                    sub_embedding_params[key] = main_embedding_config[key]

            # Create a config dict with embedding_model_dims for mock embeddings
            sub_vector_config = {'embedding_model_dims': embedding_model_dims}
            sub_embedding = EmbedderFactory.create(
                sub_embedding_provider,
                sub_embedding_params,
                sub_vector_config
            )
            logger.info(f"Created sub embedding service for store {index}: {sub_embedding_provider}")
        else:
            # Reuse main table's embedding service
            sub_embedding = self.embedding
            logger.info(f"Sub store {index} using main embedding service")

        # 5. Create sub store storage instance
        db_config = self.config.get('vector_store', {}).get('config', {}).copy()
        
        # Override with sub store specific vector_store config if provided
        sub_vector_store_config = sub_config.get('vector_store', {})
        if sub_vector_store_config:
            db_config.update(sub_vector_store_config)
            logger.info(f"Sub store {index} using custom vector_store config: {list(sub_vector_store_config.keys())}")
        
        # Always override these critical fields
        db_config['collection_name'] = sub_store_name
        db_config['embedding_model_dims'] = embedding_model_dims

        sub_vector_store = VectorStoreFactory.create(self.storage_type, db_config)

        # 6. Register sub store in Adapter (with embedding service for migration)
        if isinstance(self.storage, SubStorageAdapter):
            self.storage.register_sub_store(
                store_name=sub_store_name,
                routing_filter=routing_filter,
                vector_store=sub_vector_store,
                embedding_service=sub_embedding,
            )

        # 7. Save sub store configuration
        self.sub_stores_config.append({
            'name': sub_store_name,
            'routing_filter': routing_filter,
            'embedding_service': sub_embedding,
            'embedding_dims': embedding_model_dims,
        })

        logger.info(f"Registered sub store {index}: {sub_store_name} (dims={embedding_model_dims})")

    def _get_embedding_service(self, filters_or_metadata: Optional[Dict] = None):
        """
        Select appropriate embedding service based on filters or metadata

        Args:
            filters_or_metadata: Query filters (for search) or memory metadata (for add)

        Returns:
            Corresponding embedding service instance
        """
        if not filters_or_metadata or not self.sub_stores_config:
            return self.embedding

        # Iterate through all sub stores to find a match
        if isinstance(self.storage, SubStorageAdapter):
            for sub_config in self.sub_stores_config:
                # Check if sub store is ready
                if not self.storage.is_sub_store_ready(sub_config['name']):
                    continue

                # Check if filters_or_metadata matches routing rules
                routing_filter = sub_config['routing_filter']
                if all(
                    key in filters_or_metadata and filters_or_metadata[key] == value
                    for key, value in routing_filter.items()
                ):
                    logger.debug(f"Using sub embedding for store: {sub_config['name']}")
                    return sub_config['embedding_service']

        logger.debug("Using main embedding service")
        return self.embedding


    def migrate_to_sub_store(self, sub_store_index: int = 0, delete_source: bool = False) -> int:
        """
        Migrate data to specified sub store

        Args:
            sub_store_index: Sub store index (default 0, i.e., first sub store)
            delete_source: Whether to delete source data

        Returns:
            Number of migrated records
        """
        if not self.sub_stores_config:
            raise ValueError("No sub stores configured.")

        if sub_store_index >= len(self.sub_stores_config):
            raise ValueError(f"Sub store index {sub_store_index} out of range")

        sub_config = self.sub_stores_config[sub_store_index]

        logger.info(f"Starting migration to sub store: {sub_config['name']}")

        # Call adapter's migration method
        if isinstance(self.storage, SubStorageAdapter):
            migrated_count = self.storage.migrate_to_sub_store(
                store_name=sub_config['name'],
                delete_source=delete_source
            )

            logger.info(f"Migration completed: {migrated_count} records migrated")
            return migrated_count
        else:
            raise ValueError("Storage adapter does not support migration")

    def migrate_all_sub_stores(self, delete_source: bool = True) -> Dict[str, int]:
        """
        Migrate all sub stores

        Args:
            delete_source: Whether to delete source data

        Returns:
            Dict[str, int]: A dictionary mapping sub store names to the number of migrated records.
                Each key is a sub store name (str), and each value is the count of migrated records (int).
                If migration fails for a sub store, its count will be 0.
        """
        results = {}
        for index, sub_config in enumerate(self.sub_stores_config):
            try:
                count = self.migrate_to_sub_store(index, delete_source)
                results[sub_config['name']] = count
            except Exception as e:
                logger.error(f"Failed to migrate sub store {index}: {e}")
                results[sub_config['name']] = 0

        return results

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None, **kwargs):
        """
        Create Memory instance from configuration.
        
        Args:
            config: Configuration dictionary
            **kwargs: Additional parameters
        
        Returns:
            Memory instance
            
        Example:
            ```python
            memory = Memory.from_config({
                "llm": {"provider": "openai", "config": {"api_key": "..."}},
                "embedder": {"provider": "openai", "config": {"api_key": "..."}},
                "vector_store": {"provider": "oceanbase", "config": {...}},
            })
            ```
        """
        if config is None:
            # Use auto config from environment
            from ..config_loader import auto_config
            config = auto_config()

        converted_config = _auto_convert_config(config)
        
        return cls(config=converted_config, **kwargs)