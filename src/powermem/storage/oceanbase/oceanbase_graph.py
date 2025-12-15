"""
OceanBase graph storage implementation

This module provides OceanBase-based graph storage for memory data.
"""
import json
import logging
import warnings
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from sqlalchemy import bindparam, text, MetaData, Column, String, Index, Table, BigInteger
from sqlalchemy.dialects.mysql import TIMESTAMP
from sqlalchemy.exc import SAWarning

# Suppress SQLAlchemy warnings about unknown schema content from pyobvector
# These warnings occur because SQLAlchemy doesn't recognize OceanBase VECTOR index syntax
# This is harmless as pyobvector handles VECTOR types correctly
warnings.filterwarnings(
    "ignore",
    message="Unknown schema content",
    category=SAWarning,
    module="pyobvector.*"
)

# Suppress pkg_resources deprecation warning from jieba internal usage
# This warning is from jieba's internal use of deprecated pkg_resources API
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API",
    category=UserWarning,
    module="jieba.*"
)

from pyobvector import ObVecClient, l2_distance, VECTOR, VecIndexType

from powermem.integrations import EmbedderFactory, LLMFactory
from powermem.storage.base import GraphStoreBase
from powermem.utils.utils import format_entities, remove_code_blocks, generate_snowflake_id, get_current_datetime

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    raise ImportError("rank_bm25 is not installed. Please install it using pip install rank-bm25")

from powermem.prompts import GraphPrompts, GraphToolsPrompts

from powermem.storage.oceanbase import constants

logger = logging.getLogger(__name__)

# Try to import jieba for better Chinese text segmentation
try:
    import jieba
except ImportError:
    logger.warning("jieba is not installed. Falling back to simple space-based tokenization. "
                   "Install jieba for better Chinese text segmentation: pip install jieba")
    jieba = None


class MemoryGraph(GraphStoreBase):
    """OceanBase-based graph memory storage implementation."""

    def __init__(self, config: Any) -> None:
        """Initialize OceanBase graph memory.

        Args:
            config: Memory configuration containing graph_store, embedder, and llm configs.

        Raises:
            ValueError: If embedding_model_dims is not configured.
        """
        self.config = config

        # Get OceanBase config
        ob_config = self.config.graph_store.config

        # Helper function to get config value (supports both dict and object)
        def get_config_value(key: str, default: Any = None) -> Any:
            if isinstance(ob_config, dict):
                return ob_config.get(key, default)
            else:
                return getattr(ob_config, key, default)

        # Get embedding_model_dims (required)
        embedding_model_dims = get_config_value("embedding_model_dims")
        if embedding_model_dims is None:
            raise ValueError(
                "embedding_model_dims is required for OceanBase graph operations. "
                "Please configure embedding_model_dims in your OceanBaseGraphConfig."
            )
        self.embedding_dims = embedding_model_dims

        # Get vidx parameters with defaults.
        self.index_type = get_config_value("index_type", constants.DEFAULT_INDEX_TYPE)
        self.vidx_metric_type = get_config_value("vidx_metric_type",
                                                 constants.DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE)
        self.vidx_name = get_config_value("vidx_name", constants.DEFAULT_VIDX_NAME)

        # Get graph search parameters
        self.max_hops = get_config_value("max_hops", 3)

        # Set vidx_algo_params with defaults based on index_type.
        self.vidx_algo_params = get_config_value("vidx_algo_params", None)
        if not self.vidx_algo_params:
            # Set default parameters based on index type.
            self.vidx_algo_params = constants.get_default_build_params(self.index_type)

        # Initialize embedding model
        self.embedding_model = EmbedderFactory.create(
            self.config.embedder.provider,
            self.config.embedder.config,
            self.config.vector_store.config,
        )

        # Initialize OceanBase client
        host = get_config_value("host", "127.0.0.1")
        port = get_config_value("port", "2881")
        user = get_config_value("user", "root")
        password = get_config_value("password", "")
        db_name = get_config_value("db_name", "test")

        self.client = ObVecClient(
            uri=f"{host}:{port}",
            user=user,
            password=password,
            db_name=db_name,
        )
        self.engine = self.client.engine
        self.metadata = MetaData()

        # Create tables
        self._create_tables()

        # Initialize LLM.
        self.llm_provider = self._get_llm_provider()
        llm_config = self._get_llm_config()
        self.llm = LLMFactory.create(self.llm_provider, llm_config)

        # Initialize graph prompts and tools with config
        # Pass graph_store config or full config to prompts
        graph_config = {}
        if self.config.graph_store:
            # Convert GraphStoreConfig to dict if needed
            if hasattr(self.config.graph_store, 'model_dump'):
                graph_config = self.config.graph_store.model_dump()
            elif isinstance(self.config.graph_store, dict):
                graph_config = self.config.graph_store
        # Also include full config for fallback
        prompts_config = {"graph_store": graph_config}
        # Merge top-level config if it's a dict
        if isinstance(self.config, dict):
            prompts_config.update(self.config)
        
        self.graph_prompts = GraphPrompts(prompts_config)
        self.graph_tools_prompts = GraphToolsPrompts(prompts_config)

    def _get_llm_provider(self) -> str:
        """Get LLM provider from configuration with fallback.

        Returns:
            LLM provider name.
        """
        # Check graph_store.llm.provider first
        if (self.config.graph_store and
                self.config.graph_store.llm and
                self.config.graph_store.llm.provider):
            return self.config.graph_store.llm.provider

        # Check config.llm.provider
        if self.config.llm and self.config.llm.provider:
            return self.config.llm.provider

        # Default fallback
        return constants.DEFAULT_LLM_PROVIDER

    def _get_llm_config(self) -> Optional[Any]:
        """Get LLM config from configuration.

        Returns:
            LLM configuration object or None.
        """
        # Check graph_store.llm.config first
        if (self.config.graph_store and
                self.config.graph_store.llm and
                hasattr(self.config.graph_store.llm, "config")):
            return self.config.graph_store.llm.config

        # Check config.llm.config
        if hasattr(self.config.llm, "config"):
            return self.config.llm.config

        return None

    def _build_user_identity(self, filters: Dict[str, Any]) -> str:
        """Build user identity string from filters.

        Args:
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            Formatted user identity string.
        """
        identity_parts = [f"user_id: {filters['user_id']}"]

        if filters.get("agent_id"):
            identity_parts.append(f"agent_id: {filters['agent_id']}")

        if filters.get("run_id"):
            identity_parts.append(f"run_id: {filters['run_id']}")

        return ", ".join(identity_parts)

    def _build_filter_conditions(
            self,
            filters: Dict[str, Any],
            prefix: str = ""
    ) -> Tuple[List[str], Dict[str, Any]]:
        """Build SQL filter conditions and parameters from filters.

        Args:
            filters: Dictionary containing user_id, agent_id, run_id.
            prefix: Optional prefix for column names (e.g., "r." for table alias).

        Returns:
            Tuple of (filter_conditions_list, params_dict).
        """
        filter_parts = [f"{prefix}user_id = :user_id"]
        params = {"user_id": filters["user_id"]}

        if filters.get("agent_id"):
            filter_parts.append(f"{prefix}agent_id = :agent_id")
            params["agent_id"] = filters["agent_id"]

        if filters.get("run_id"):
            filter_parts.append(f"{prefix}run_id = :run_id")
            params["run_id"] = filters["run_id"]

        return filter_parts, params

    @staticmethod
    def _coerce_tool_response_to_dict(response: Any) -> Dict[str, Any]:
        """Ensure LLM tool response is a dict.

        Some LLM providers may return a JSON string instead of a parsed dict. This helper
        normalizes the response to a dictionary with safe fallbacks.

        Args:
            response: LLM tool call response, may be dict or JSON string.

        Returns:
            Normalized dictionary object, or empty dict if unparseable.
        """
        if isinstance(response, dict):
            return response
        if isinstance(response, str):
            try:
                cleaned = remove_code_blocks(response)
            except Exception:
                cleaned = response
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        # Fallback to empty dict if un-parseable
        return {}

    def _create_tables(self) -> None:
        """Create graph entities and relationships tables if they don't exist.

        Creates two tables:
            - graph_entities: Stores entity nodes and their vector embeddings
            - graph_relationships: Stores relationships between entities
        """

        if not self.client.check_table_exists(constants.TABLE_ENTITIES):
            # Define columns for entities table
            cols = [
                Column("id", BigInteger, primary_key=True, autoincrement=False),
                Column("name", String(255), nullable=False),
                Column("entity_type", String(64)),
                Column("embedding", VECTOR(self.embedding_dims)),
                Column("created_at", TIMESTAMP, server_default=text("CURRENT_TIMESTAMP")),
                Column("updated_at", TIMESTAMP, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            ]
            # Define regular indexes
            indexes = [
                Index("idx_name", "name"),
            ]

            # Map index_type string to VecIndexType enum
            index_type_map = constants.OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES

            # Create vector index parameters
            vidx_params = self.client.prepare_index_params()
            vidx_params.add_index(
                field_name="embedding",
                index_type=index_type_map.get(self.index_type, VecIndexType.HNSW),
                index_name=self.vidx_name,
                metric_type=self.vidx_metric_type,
                params=self.vidx_algo_params,
            )

            # Create table with vector index
            self.client.create_table_with_index_params(
                table_name=constants.TABLE_ENTITIES,
                columns=cols,
                indexes=indexes,
                vidxs=vidx_params,
                partitions=None,
            )

            logger.info("%s table created successfully", constants.TABLE_ENTITIES)
        else:
            logger.info("%s table already exists", constants.TABLE_ENTITIES)
            # Check vector dimension consistency
            existing_dim = self._get_existing_vector_dimension_for_entities()
            if existing_dim is not None and existing_dim != self.embedding_dims:
                raise ValueError(
                    f"Vector dimension mismatch: existing table '{constants.TABLE_ENTITIES}' has "
                    f"vector dimension {existing_dim}, but requested dimension is {self.embedding_dims}. "
                    f"Please use a different configuration or reset the graph."
                )

        # Create relationships table using pyobvector API
        if not self.client.check_table_exists(constants.TABLE_RELATIONSHIPS):

            # Define columns for relationships table
            cols = [
                Column("id", BigInteger, primary_key=True, autoincrement=False),
                Column("source_entity_id", BigInteger, nullable=False),
                Column("relationship_type", String(128), nullable=False),
                Column("destination_entity_id", BigInteger, nullable=False),
                Column("user_id", String(128)),
                Column("agent_id", String(128)),
                Column("run_id", String(128)),
                Column("created_at", TIMESTAMP, server_default=text("CURRENT_TIMESTAMP")),
                Column("updated_at", TIMESTAMP, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            ]

            # Define regular indexes
            indexes = [
                Index("idx_r_covering", "user_id","source_entity_id", "destination_entity_id","relationship_type"),
            ]

            # Create table without vector index (relationships table has no vectors)
            self.client.create_table_with_index_params(
                table_name=constants.TABLE_RELATIONSHIPS,
                columns=cols,
                indexes=indexes,
                vidxs=None,
                partitions=None,
            )

            logger.info("%s table created successfully", constants.TABLE_RELATIONSHIPS)
        else:
            logger.info("%s table already exists", constants.TABLE_RELATIONSHIPS)

    def _get_existing_vector_dimension_for_entities(self) -> Optional[int]:
        """Get the dimension of the existing vector field in entities table.

        Returns:
            Dimension of the vector field, or None if table doesn't exist or field not found.
        """
        if not self.client.check_table_exists(constants.TABLE_ENTITIES):
            return None

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(f"DESCRIBE {constants.TABLE_ENTITIES}"))
                columns = result.fetchall()

            for col in columns:
                if col[0] == "embedding":
                    col_type = col[1]
                    if col_type.startswith("VECTOR(") and col_type.endswith(")"):
                        dim_str = col_type[7:-1]
                        return int(dim_str)
            return None
        except Exception as e:
            logger.warning(
                "Failed to get vector dimension for %s: %s",
                constants.TABLE_ENTITIES,
                e
            )
            return None

    def _build_where_clause_with_filters(
            self,
            filters: Dict[str, Any],
            prefix: str = "",
            additional_params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Any], Dict[str, Any]]:
        """Build where clause and parameters from filters using bindparam.

        Args:
            filters: Dictionary containing user_id, agent_id, run_id.
            prefix: Optional prefix for column names (e.g., "r." for table alias).
            additional_params: Optional additional parameters to include.

        Returns:
            Tuple of (where_clause_list, params_dict).
        """
        where_conditions = []
        params = {"user_id": filters["user_id"]}
        where_conditions.append(f"{prefix}user_id = :user_id")

        if filters.get("agent_id"):
            where_conditions.append(f"{prefix}agent_id = :agent_id")
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            where_conditions.append(f"{prefix}run_id = :run_id")
            params["run_id"] = filters["run_id"]

        # Add additional params if provided
        if additional_params:
            params.update(additional_params)

        where_sql = " AND ".join(where_conditions)
        where_clause_with_params = text(where_sql).bindparams(**params)
        return [where_clause_with_params], params

    def add(self, data: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Add data to the graph.

        Args:
            data: The data to add to the graph.
            filters: Dictionary containing filters (user_id, agent_id, run_id).

        Returns:
            Dictionary containing deleted_entities and added_entities.
        """
        entity_type_map = self._retrieve_nodes_from_data(data, filters)
        to_be_added = self._establish_nodes_relations_from_data(data, filters, entity_type_map)
        search_output = self._search_graph_db(node_list=list(entity_type_map.keys()), filters=filters)
        to_be_deleted = self._get_delete_entities_from_search_output(search_output, data, filters)

        deleted_entities = self._delete_entities(to_be_deleted, filters)
        added_entities = self._add_entities(to_be_added, filters, entity_type_map)
        logger.debug("Deleted entities: %s, Added entities: %s", deleted_entities, added_entities)
        return {"deleted_entities": deleted_entities, "added_entities": added_entities}

    def search(self, query: str, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, str]]:
        """Search for memories and related graph data.

        Args:
            query: Query to search for.
            filters: Dictionary containing filters (user_id, agent_id, run_id).
            limit: Maximum number of nodes and relationships to retrieve. Defaults to 100.

        Returns:
            List of search results containing source, relationship, and destination.
        """
        entity_type_map = self._retrieve_nodes_from_data(query, filters)
        search_output = self._search_graph_db(node_list=list(entity_type_map.keys()), filters=filters, limit=limit)

        if not search_output:
            return []

        # Tokenize search outputs for BM25 with improved segmentation
        search_outputs_sequence = []
        for item in search_output:
            # Combine source, relationship, destination into a single text for better tokenization
            combined_text = f"{item['source']} {item['relationship']} {item['destination']}"
            tokenized_item = self._tokenize_text(combined_text)
            search_outputs_sequence.append(tokenized_item)
        
        bm25 = BM25Okapi(search_outputs_sequence)

        # Tokenize query using the same method
        tokenized_query = self._tokenize_text(query)
        
        # Get top N results based on BM25 scores
        scores = bm25.get_scores(tokenized_query)
        # Get indices sorted by score (descending)
        sorted_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        top_n_indices = sorted_indices[:constants.DEFAULT_BM25_TOP_N]
        
        # Build reranked results
        search_results = []
        for idx in top_n_indices:
            if idx < len(search_output):
                item = search_output[idx]
                search_results.append({
                    "source": item["source"], 
                    "relationship": item["relationship"], 
                    "destination": item["destination"]
                })

        logger.info("Returned %d search results (from %d candidates)", len(search_results), len(search_output))

        return search_results
    
    def _tokenize_text(self, text: str) -> List[str]:
        """Tokenize text using jieba for Chinese or simple split for other languages.
        
        Args:
            text: Text to tokenize.
            
        Returns:
            List of tokens.
        """
        if jieba is not None:
            # Use jieba for Chinese text segmentation
            # Convert to lowercase for better matching
            tokens = list(jieba.cut(text.lower()))
            # Filter out empty strings and single spaces
            tokens = [t for t in tokens if t.strip()]
            return tokens
        else:
            # Fallback to simple space-based tokenization
            return text.lower().split()

    def delete_all(self, filters: Dict[str, Any]) -> None:
        """Delete all graph data for the given filters.

        Args:
            filters: Filters containing user_id, agent_id, run_id.
        """
        where_clause, _ = self._build_where_clause_with_filters(filters)

        try:
            relationships_results = self.client.get(
                table_name=constants.TABLE_RELATIONSHIPS,
                ids=None,
                output_column_name=["id", "source_entity_id", "destination_entity_id"],
                where_clause=where_clause
            )

            # Collect unique entity IDs from relationships
            entity_ids = set()
            for rel in relationships_results.fetchall():
                entity_ids.add(rel[1])  # source_entity_id
                entity_ids.add(rel[2])  # destination_entity_id

            # Delete the relationships
            self.client.delete(
                table_name=constants.TABLE_RELATIONSHIPS,
                where_clause=where_clause
            )
            logger.info("Deleted relationships for filters: %s", filters)

            # Delete entities that were part of these relationships
            if entity_ids:
                self.client.delete(
                    table_name=constants.TABLE_ENTITIES,
                    ids=list(entity_ids),
                )
                logger.info("Deleted %d entities for filters: %s", len(entity_ids), filters)
        except Exception as e:
            logger.warning("Error deleting graph data: %s", e)

        logger.info("Deleted all graph data for filters: %s", filters)

    def get_all(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, str]]:
        """Retrieve all nodes and relationships from the graph database.

        Args:
            filters: Dictionary containing filters (user_id, agent_id, run_id).
            limit: Maximum number of relationships to retrieve. Defaults to 100.

        Returns:
            List of dictionaries containing source, relationship, and target.
        """

        where_clause, params = self._build_where_clause_with_filters(filters)

        relationships_results = self.client.get(
            table_name=constants.TABLE_RELATIONSHIPS,
            ids=None,
            output_column_name=["id", "source_entity_id", "relationship_type", "destination_entity_id", "updated_at"],
            where_clause=where_clause
        )

        relationships = relationships_results.fetchall()
        if not relationships:
            return []

        # Limit results if needed
        if len(relationships) > limit:
            relationships = relationships[:limit]

        # Extract unique entity IDs from relationships
        entity_ids = set()
        for rel in relationships:
            entity_ids.add(rel[1])  # source_entity_id
            entity_ids.add(rel[3])  # destination_entity_id

        # Get all entities that are referenced in relationships
        entities_results = self.client.get(
            table_name=constants.TABLE_ENTITIES,
            ids=list(entity_ids),
            output_column_name=["id", "name"]
        )

        # Create a mapping from entity_id to entity_name
        entity_map = {entity[0]: entity[1] for entity in entities_results.fetchall()}

        # Build final results with updated_at for sorting
        final_results = []
        for rel in relationships:
            rel_id, source_id, relationship_type, dest_id, updated_at = rel

            source_name = entity_map.get(source_id, f"Unknown_{source_id}")
            dest_name = entity_map.get(dest_id, f"Unknown_{dest_id}")

            final_results.append({
                "source": source_name,
                "relationship": relationship_type,
                "target": dest_name,
                "_updated_at": updated_at,  # Keep for sorting
            })

        # Sort by updated_at (descending)
        final_results.sort(key=lambda x: x["_updated_at"], reverse=True)

        # Remove the temporary _updated_at field
        for result in final_results:
            del result["_updated_at"]

        logger.info("Retrieved %d relationships", len(final_results))
        return final_results

    def _retrieve_nodes_from_data(self, data: str, filters: Dict[str, Any]) -> Dict[str, str]:
        """Extract all the entities mentioned in the query.

        Args:
            data: Input text to extract entities from.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            Dictionary mapping entity names to entity types.
        """
        _tools = [self.graph_tools_prompts.get_extract_entities_tool()]
        if constants.is_structured_llm_provider(self.llm_provider):
            _tools = [self.graph_tools_prompts.get_extract_entities_tool(structured=True)]

        search_results = self.llm.generate_response(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a smart assistant who understands entities and their types in a given text. "
                        f"If user message contains self reference such as 'I', 'me', 'my' etc. "
                        f"then use {filters['user_id']} as the source entity. "
                        f"Extract all the entities from the text. "
                        f"***DO NOT*** answer the question itself if the given text is a question."
                    ),
                },
                {"role": "user", "content": data},
            ],
            tools=_tools,
        )

        # Normalize potential string response to dict
        search_results = self._coerce_tool_response_to_dict(search_results)

        entity_type_map = {}

        try:
            for tool_call in search_results.get("tool_calls", []):
                if tool_call["name"] != "extract_entities":
                    continue
                for item in tool_call["arguments"]["entities"]:
                    entity_type_map[item["entity"]] = item["entity_type"]
        except Exception as e:
            logger.exception(
                "Error in search tool: %s, llm_provider=%s, search_results=%s",
                e,
                self.llm_provider,
                search_results
            )

        entity_type_map = {
            k.lower().replace(" ", "_"): v.lower().replace(" ", "_")
            for k, v in entity_type_map.items()
        }
        logger.debug("Entity type map: %s\n search_results=%s", entity_type_map, search_results)
        return entity_type_map

    def _establish_nodes_relations_from_data(
            self,
            data: str,
            filters: Dict[str, Any],
            entity_type_map: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Establish relations among the extracted nodes.

        Args:
            data: Input text to extract relationships from.
            filters: Dictionary containing user_id, agent_id, run_id.
            entity_type_map: Mapping of entity names to types.

        Returns:
            List of dictionaries containing source, destination, and relationship.
        """
        user_identity = self._build_user_identity(filters)

        if self.config.graph_store.custom_prompt:
            system_content = self.graph_prompts.get_system_prompt("extract_relations")
            system_content = system_content.replace("USER_ID", user_identity)
            system_content = system_content.replace("CUSTOM_PROMPT", f"4. {self.config.graph_store.custom_prompt}")
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": data},
            ]
        else:
            system_content = self.graph_prompts.get_system_prompt("extract_relations")
            system_content = system_content.replace("USER_ID", user_identity)
            messages = [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": f"List of entities: {list(entity_type_map.keys())}. \n\nText: {data}"
                },
            ]

        _tools = [self.graph_tools_prompts.get_relations_tool()]
        if constants.is_structured_llm_provider(self.llm_provider):
            _tools = [self.graph_tools_prompts.get_relations_tool(structured=True)]

        extracted_entities = self.llm.generate_response(
            messages=messages,
            tools=_tools,
        )

        # Normalize to dict for consistent access
        extracted_entities = self._coerce_tool_response_to_dict(extracted_entities)

        entities = []
        if extracted_entities.get("tool_calls"):
            first_call = (
                extracted_entities["tool_calls"][0]
                if extracted_entities["tool_calls"]
                else {}
            )
            entities = first_call.get("arguments", {}).get("entities", [])

        entities = self._remove_spaces_from_entities(entities)
        logger.debug("Extracted entities: %s", entities)
        return entities

    def _search_graph_db(
            self,
            node_list: List[str],
            filters: Dict[str, Any],
            limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Search similar nodes and their relationships using vector similarity with multi-hop support.

        Supports up to 3-hop graph traversal using recursive CTE. Results are prioritized by
        path length (1-hop first, then 2-hop, then 3-hop).

        Args:
            node_list: List of node names to search for.
            filters: Dictionary containing user_id, agent_id, run_id.
            limit: Maximum number of results to return. Defaults to 100.

        Returns:
            List of dictionaries containing source, relationship, destination and their IDs.
        """
        result_relations = []

        for node in node_list:
            n_embedding = self.embedding_model.embed(node)

            entities = self._search_node(None, n_embedding, filters, limit=limit)

            if not entities:
                continue

            # Ensure entities is always a list
            if isinstance(entities, dict):
                entities = [entities]

            entity_ids = [e.get("id") for e in entities]

            # Use multi-hop search with early stopping
            multi_hop_results = self._multi_hop_search(entity_ids, filters, limit)
            result_relations.extend(multi_hop_results)

        return result_relations

    def _execute_single_hop_query(
            self,
            source_entity_ids: List[int],
            filters: Dict[str, Any],
            hop_number: int,
            visited_edges: set = None,
            conn=None,
            max_edges_per_hop: int = 1000
    ) -> List[Dict[str, Any]]:
        """Execute a single hop query from given source entities.

        Args:
            source_entity_ids: List of source entity IDs to start from.
            filters: Dictionary containing user_id, agent_id, run_id.
            hop_number: Current hop number (for result annotation).
            visited_edges: Set of visited edges (source_id, dest_id) to avoid cycles.
            conn: Optional database connection to use. If None, creates a new connection.
            max_edges_per_hop: Maximum number of edges to retrieve per hop. Defaults to 1000.

        Returns:
            List of relationship dictionaries with hop_count.

        Note:
            - Prevents memory explosion from high-degree nodes
            - Results are sorted by created_at DESC before limiting
            - This ensures most recent edges are retrieved first
        """
        if not source_entity_ids:
            return []

        if visited_edges is None:
            visited_edges = set()

        # Build filter conditions
        filter_parts, params = self._build_filter_conditions(filters, prefix="")
        filter_conditions = " AND ".join(filter_parts)

        # Build query with LIMIT to prevent memory explosion from high-degree nodes
        query = f"""
            SELECT
                e1.name AS source,
                r.source_entity_id,
                r.relationship_type,
                r.id AS relation_id,
                e2.name AS destination,
                r.destination_entity_id
            FROM
                (
                SELECT
                    id,
                    source_entity_id,
                    destination_entity_id,
                    relationship_type,
                    created_at,
                    updated_at,
                    user_id
                FROM {constants.TABLE_RELATIONSHIPS}
                WHERE
                    source_entity_id IN :entity_ids
                    AND {filter_conditions}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT :max_edges_per_hop
            ) AS r
            JOIN {constants.TABLE_ENTITIES} e1 ON r.source_entity_id = e1.id
            JOIN {constants.TABLE_ENTITIES} e2 ON r.destination_entity_id = e2.id;
        """

        # Add parameters
        params["entity_ids"] = tuple(source_entity_ids)
        params["max_edges_per_hop"] = max_edges_per_hop
        logger.debug("Executing hop %d with max_edges_per_hop=%d\n query: %s\n params: %s",
                     hop_number, max_edges_per_hop, query, params)

        # Execute query - use provided connection or create new one
        if conn is not None:
            # Reuse existing connection (transactional)
            result = conn.execute(text(query), params)
            rows = result.fetchall()
        else:
            # Create new connection (backward compatibility)
            with self.engine.connect() as new_conn:
                result = new_conn.execute(text(query), params)
                rows = result.fetchall()

        # Format results and filter out cycles
        formatted_results = []
        for row in rows:
            source_id = row[1]
            dest_id = row[5]
            edge_key = (source_id, dest_id)

            # Skip if this edge was already visited (cycle detection)
            if edge_key in visited_edges:
                continue

            formatted_results.append({
                "source": row[0],
                "source_id": source_id,
                "relationship": row[2],
                "relation_id": row[3],
                "destination": row[4],
                "destination_id": dest_id,
                "hop_count": hop_number,
            })

        return formatted_results

    def _multi_hop_search(
            self,
            entity_ids: List[int],
            filters: Dict[str, Any],
            limit: int
    ) -> List[Dict[str, Any]]:
        """Perform multi-hop graph search with application-level early stopping.

        Args:
            entity_ids: List of seed entity IDs to start traversal from.
            filters: Dictionary containing user_id, agent_id, run_id.
            limit: Maximum number of results to return.

        Returns:
            List of dictionaries containing source, relationship, destination and their IDs.

        Note:
            Application-level optimization strategy:
            1. Execute 1-hop query first
            2. Check if results satisfy limit - if yes, return immediately
            3. If not, execute 2-hop query with cycle prevention
            4. Accumulate results and check limit again
            5. Continue until limit is satisfied or max_hops is reached
        """
        if not entity_ids:
            return []

        # Use a transaction to ensure consistent reads across all hops
        # This prevents phantom reads and non-repeatable reads during multi-hop traversal
        with self.engine.begin() as conn:
            logger.debug("Started transaction for multi-hop search")

            all_results = []
            visited_edges = set()  # Track visited edges to prevent cycles
            visited_nodes = set(entity_ids)  # Track all visited nodes (start with seed entities)
            current_source_ids = entity_ids  # Start from seed entities

            # Iteratively execute each hop until limit is satisfied or max_hops reached
            for hop in range(1, self.max_hops + 1):
                # Execute single hop query within the same transaction
                hop_results = self._execute_single_hop_query(
                    source_entity_ids=current_source_ids,
                    filters=filters,
                    hop_number=hop,
                    visited_edges=visited_edges,
                    conn=conn
                )

                # If no results at this hop, stop early
                if not hop_results:
                    logger.info("STOP early: No results at hop %s", hop)
                    break

                # Add results to accumulator
                all_results.extend(hop_results)

                # Update visited edges to prevent cycles in next hop
                for result in hop_results:
                    edge_key = (result["source_id"], result["destination_id"])
                    visited_edges.add(edge_key)

                # Check if we've satisfied the limit (early stopping)
                if len(all_results) >= limit:
                    logger.info("STOP early: Limit satisfied at hop %s", hop)
                    # Truncate to exact limit and return
                    return all_results[:limit]

                # Prepare source IDs for next hop (destination entities become new sources)
                next_source_ids = set([r["destination_id"] for r in hop_results])

                # Check if we have any new nodes that haven't been visited
                new_nodes = next_source_ids - visited_nodes

                # If no new nodes, all destinations are already visited - stop early
                # This means we've exhausted all reachable nodes in the graph
                if not new_nodes:
                    logger.info("STOP early: All destinations are already visited at hop %s", hop)
                    break

                # Update visited nodes and prepare for next hop
                visited_nodes.update(next_source_ids)
                current_source_ids = list(next_source_ids)

            # Return all accumulated results
            logger.debug("Transaction completed for multi-hop search, returning %d results", len(all_results))
            return all_results

    def _get_delete_entities_from_search_output(
            self,
            search_output: List[Dict[str, Any]],
            data: str,
            filters: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """Get the entities to be deleted from the search output.

        Args:
            search_output: Search results from graph database.
            data: New input data to compare against.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            List of dictionaries containing source, destination, and relationship to delete.
        """
        search_output_string = format_entities(search_output)
        user_identity = self._build_user_identity(filters)
        system_prompt, user_prompt = self.graph_prompts.get_delete_relations_prompt(search_output_string, data, user_identity)

        _tools = [self.graph_tools_prompts.get_delete_tool()]
        if constants.is_structured_llm_provider(self.llm_provider):
            _tools = [self.graph_tools_prompts.get_delete_tool(structured=True)]

        memory_updates = self.llm.generate_response(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=_tools,
        )

        # Normalize to dict before access
        memory_updates = self._coerce_tool_response_to_dict(memory_updates)

        to_be_deleted = []
        for item in memory_updates.get("tool_calls", []):
            if item.get("name") == "delete_graph_memory":
                to_be_deleted.append(item.get("arguments"))

        to_be_deleted = self._remove_spaces_from_entities(to_be_deleted)
        logger.debug("Deleted relationships: %s", to_be_deleted)
        return to_be_deleted

    def _delete_entities(
            self,
            to_be_deleted: List[Dict[str, str]],
            filters: Dict[str, Any]
    ) -> List[Dict[str, int]]:
        """Delete the specified relationships from the graph.

        Args:
            to_be_deleted: List of relationships to delete with source, destination, relationship.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            List of dictionaries containing deleted_count for each deletion operation.
        """
        results = []

        for item in to_be_deleted:
            source = item["source"]
            destination = item["destination"]
            relationship = item["relationship"]

            # First, find the source and destination entities by name
            source_entities = self.client.get(
                table_name=constants.TABLE_ENTITIES,
                ids=None,
                output_column_name=["id", "name"],
                where_clause=[text(f"name = :source_name").bindparams(
                    bindparam("source_name", source)
                )]
            )

            dest_entities = self.client.get(
                table_name=constants.TABLE_ENTITIES,
                ids=None,
                output_column_name=["id", "name"],
                where_clause=[text(f"name = :dest_name").bindparams(
                    bindparam("dest_name", destination)
                )]
            )

            # Get entity IDs
            source_rows = source_entities.fetchall() if source_entities else []
            dest_rows = dest_entities.fetchall() if dest_entities else []

            source_ids = [e[0] for e in source_rows]
            dest_ids = [e[0] for e in dest_rows]

            # Check if we found any entities
            if not source_ids or not dest_ids:
                logger.warning(
                    "Could not find entities: source='%s' (found %d), destination='%s' (found %d)",
                    source,
                    len(source_ids),
                    destination,
                    len(dest_ids)
                )
                results.append({"deleted_count": 0})
                continue

            # Build where clause for relationship deletion
            where_clauses = [
                "relationship_type = :rel_type",
                "user_id = :user_id",
            ]
            params = {
                "rel_type": relationship,
                "user_id": filters["user_id"],
            }

            if filters.get("agent_id"):
                where_clauses.append("agent_id = :agent_id")
                params["agent_id"] = filters["agent_id"]
            if filters.get("run_id"):
                where_clauses.append("run_id = :run_id")
                params["run_id"] = filters["run_id"]

            # Add source and destination entity ID conditions.
            source_conditions = " OR ".join([
                f"source_entity_id = :src_id_{i}"
                for i in range(len(source_ids))
            ])
            dest_conditions = " OR ".join([
                f"destination_entity_id = :dest_id_{i}"
                for i in range(len(dest_ids))
            ])

            where_clauses.append(f"({source_conditions}) AND ({dest_conditions})")

            # Add entity ID parameters
            for i, src_id in enumerate(source_ids):
                params[f"src_id_{i}"] = src_id
            for i, dest_id in enumerate(dest_ids):
                params[f"dest_id_{i}"] = dest_id

            where_str = " AND ".join(where_clauses)
            where_clause = text(where_str).bindparams(**params)

            # Delete relationships using pyobvector delete method.
            try:
                delete_result = self.client.delete(
                    table_name=constants.TABLE_RELATIONSHIPS,
                    where_clause=[where_clause]
                )
                deleted_count = (
                    delete_result.rowcount
                    if hasattr(delete_result, "rowcount")
                    else 1
                )
                results.append({"deleted_count": deleted_count})
            except Exception as e:
                logger.warning("Error deleting relationship: %s", e)
                results.append({"deleted_count": 0})

        return results

    def _add_entities(
            self,
            to_be_added: List[Dict[str, str]],
            filters: Dict[str, Any],
            entity_type_map: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Add new entities and relationships to the graph.

        Args:
            to_be_added: List of relationships to add with source, destination, relationship.
            filters: Dictionary containing user_id, agent_id, run_id.
            entity_type_map: Mapping of entity names to types.

        Returns:
            List of dictionaries containing added source, relationship, and target.
        """
        results = []

        for item in to_be_added:
            source = item["source"]
            destination = item["destination"]
            relationship = item["relationship"]

            source_embedding = self.embedding_model.embed(source)
            dest_embedding = self.embedding_model.embed(destination)

            # Search for existing similar nodes.
            source_node = self._search_source_node(source_embedding, filters,
                                                   threshold=constants.DEFAULT_SIMILARITY_THRESHOLD, limit=1)
            dest_node = self._search_destination_node(dest_embedding, filters,
                                                      threshold=constants.DEFAULT_SIMILARITY_THRESHOLD, limit=1)

            # Get or create source entity
            if source_node:
                source_id = source_node["id"]
            else:
                source_id = self._create_entity(source, entity_type_map.get(source, "entity"),
                                                source_embedding, filters)

            # Get or create destination entity
            if dest_node:
                dest_id = dest_node["id"]
            else:
                dest_id = self._create_entity(destination, entity_type_map.get(destination, "entity"),
                                              dest_embedding, filters)

            # Create or update relationship
            rel_result = self._create_or_update_relationship(source_id, dest_id, relationship, filters)
            results.append(rel_result)

        return results

    def _search_node(
            self,
            name: Optional[str],
            embedding: List[float],
            filters: Dict[str, Any],
            threshold: float = None,
            limit: int = 10
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Search for a node by embedding similarity within threshold.

        Args:
            name: Node name (not currently used, kept for compatibility).
            embedding: Vector embedding to search with.
            filters: Dictionary containing user_id, agent_id, run_id.
            threshold: Distance threshold for filtering results.
                      Defaults to constants.DEFAULT_SIMILARITY_THRESHOLD if None.
            limit: Maximum number of results to return. Defaults to 10.

        Returns:
            If limit==1: Single dict with id, name, distance, or None if no match.
            If limit>1: List of dicts with id, name, distance, or None if no match.
        """
        if threshold is None:
            threshold = constants.DEFAULT_SIMILARITY_THRESHOLD

        # Create Table object to access columns for WHERE clause.
        table = Table(constants.TABLE_ENTITIES, self.metadata, autoload_with=self.engine)
        vec_str = "[" + ",".join([str(np.float32(v)) for v in embedding]) + "]"
        distance_expr = l2_distance(table.c.embedding, vec_str)
        where_clause = [distance_expr < threshold]

        results = self.client.ann_search(
            table_name=constants.TABLE_ENTITIES,
            vec_data=embedding,
            vec_column_name="embedding",
            distance_func=l2_distance,
            with_dist=True,
            topk=limit,
            output_column_names=["id", "name"],
            where_clause=where_clause,
        )

        rows = results.fetchall()
        if rows:
            if limit == 1:
                row = rows[0]
                # Row format: (id, name, distance)
                entity_id, entity_name = row[0], row[1]
                distance = row[-1]  # Distance is always the last column

                return {"id": entity_id, "name": entity_name, "distance": distance}
            else:
                return [{"id": row[0], "name": row[1], "distance": row[-1]} for row in rows]

        return None

    def _create_entity(
            self,
            name: str,
            entity_type: str,
            embedding: List[float],
            filters: Dict[str, Any]
    ) -> int:
        """Create a new entity in the graph.

        Args:
            name: Entity name.
            entity_type: Type of the entity.
            embedding: Vector embedding of the entity.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            Snowflake ID of the created entity.
        """
        entity_id = generate_snowflake_id()
        current_time = get_current_datetime()

        # Prepare data for insertion using pyobvector API
        record = {
            "id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "embedding": embedding,
            "created_at": current_time,
            "updated_at": current_time,
        }

        # Use pyobvector upsert method
        self.client.upsert(
            table_name=constants.TABLE_ENTITIES,
            data=[record],
        )

        logger.debug("Created entity: %s with id: %s", name, entity_id)
        return entity_id

    def _create_or_update_relationship(
            self,
            source_id: int,
            dest_id: int,
            relationship_type: str,
            filters: Dict[str, Any]
    ) -> Dict[str, str]:
        """Create or update a relationship between two entities.

        Args:
            source_id: Snowflake ID of the source entity.
            dest_id: Snowflake ID of the destination entity.
            relationship_type: Type of the relationship.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            Dictionary containing source, relationship, and target names.
        """
        # First, check if relationship already exists
        where_clause, params = self._build_where_clause_with_filters(filters)

        # Add relationship-specific conditions to the where clause
        additional_conditions = " AND source_entity_id = :source_id AND destination_entity_id = :dest_id AND relationship_type = :rel_type"

        # Rebuild the where clause with additional conditions
        where_str = str(where_clause[0].text) + additional_conditions

        params.update({
            "source_id": source_id,
            "dest_id": dest_id,
            "rel_type": relationship_type
        })

        where_clause_with_params = text(where_str).bindparams(**params)

        # Check if relationship exists
        existing_relationships = self.client.get(
            table_name=constants.TABLE_RELATIONSHIPS,
            ids=None,
            output_column_name=["id"],
            where_clause=[where_clause_with_params]
        )

        existing_rows = existing_relationships.fetchall()
        if not existing_rows:
            # Relationship doesn't exist, create new one
            current_time = get_current_datetime()
            new_record = {
                "id": generate_snowflake_id(),
                "source_entity_id": source_id,
                "relationship_type": relationship_type,
                "destination_entity_id": dest_id,
                "user_id": filters["user_id"],
                "agent_id": filters.get("agent_id"),
                "run_id": filters.get("run_id"),
                "created_at": current_time,
                "updated_at": current_time,
            }

            self.client.insert(
                table_name=constants.TABLE_RELATIONSHIPS,
                data=[new_record],
            )

        # Get the names for return value using pyobvector get method
        # First get the entities
        source_entity = self.client.get(
            table_name=constants.TABLE_ENTITIES,
            ids=[source_id],
            output_column_name=["id", "name"]
        ).fetchone()

        dest_entity = self.client.get(
            table_name=constants.TABLE_ENTITIES,
            ids=[dest_id],
            output_column_name=["id", "name"]
        ).fetchone()

        return {
            "source": source_entity[1] if source_entity else None,
            "relationship": relationship_type,
            "target": dest_entity[1] if dest_entity else None,
        }

    def _remove_spaces_from_entities(self, entity_list: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Clean entity names by replacing spaces with underscores.

        Args:
            entity_list: List of dictionaries containing source, destination, relationship.

        Returns:
            Cleaned entity list with spaces replaced by underscores and lowercased.
        """
        for item in entity_list:
            item["source"] = item["source"].lower().replace(" ", "_")
            item["relationship"] = item["relationship"].lower().replace(" ", "_")
            item["destination"] = item["destination"].lower().replace(" ", "_")
        return entity_list

    def _search_source_node(
            self,
            source_embedding: List[float],
            filters: Dict[str, Any],
            threshold: float = None,
            limit: int = 10
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Search for a source node by embedding similarity (compatibility method).

        Args:
            source_embedding: Vector embedding to search with.
            filters: Dictionary containing user_id, agent_id, run_id.
            threshold: Distance threshold for filtering results.
                      Defaults to constants.DEFAULT_SIMILARITY_THRESHOLD if None.
            limit: Maximum number of results to return. Defaults to 10.

        Returns:
            Search results from _search_node method.
        """
        return self._search_node("source", source_embedding, filters, threshold, limit)

    def _search_destination_node(
            self,
            destination_embedding: List[float],
            filters: Dict[str, Any],
            threshold: float = None,
            limit: int = 10
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Search for a destination node by embedding similarity (compatibility method).

        Args:
            destination_embedding: Vector embedding to search with.
            filters: Dictionary containing user_id, agent_id, run_id.
            threshold: Distance threshold for filtering results.
                      Defaults to constants.DEFAULT_SIMILARITY_THRESHOLD if None.
            limit: Maximum number of results to return. Defaults to 10.

        Returns:
            Search results from _search_node method.
        """
        return self._search_node("destination", destination_embedding, filters, threshold, limit)

    def reset(self) -> None:
        """Reset the graph by clearing all nodes and relationships.

        This method drops both entities and relationships tables and recreates them.
        """
        logger.warning("Clearing graph...")

        # Use pyobvector API to drop tables
        if self.client.check_table_exists(constants.TABLE_RELATIONSHIPS):
            self.client.drop_table_if_exist(constants.TABLE_RELATIONSHIPS)
            logger.info("Dropped %s table", constants.TABLE_RELATIONSHIPS)

        if self.client.check_table_exists(constants.TABLE_ENTITIES):
            self.client.drop_table_if_exist(constants.TABLE_ENTITIES)
            logger.info("Dropped %s table", constants.TABLE_ENTITIES)

        # Recreate tables
        self._create_tables()

        logger.info("Graph reset completed")