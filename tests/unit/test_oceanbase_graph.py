import unittest
from unittest.mock import MagicMock, patch, Mock
import pytest
import uuid
from powermem.storage.oceanbase.oceanbase_graph import MemoryGraph
from powermem.storage.oceanbase import constants


class TestOceanBaseGraph(unittest.TestCase):
    """Test suite for the OceanBase Memory implementation."""

    def setUp(self):
        """Set up test fixtures before each test method."""

        # Create a mock config
        self.config = MagicMock()
        
        # Mock OceanBase config
        self.config.graph_store.config.host = "127.0.0.1"
        self.config.graph_store.config.port = "2881"
        self.config.graph_store.config.user = "root@test"
        self.config.graph_store.config.password = "password"
        self.config.graph_store.config.db_name = "test"
        self.config.graph_store.config.embedding_model_dims = 384
        self.config.graph_store.config.index_type = "HNSW"
        self.config.graph_store.config.vidx_metric_type = "l2"
        self.config.graph_store.config.vidx_name = "vidx"
        self.config.graph_store.config.max_hops = 3
        self.config.graph_store.config.vidx_algo_params = {"M": 16, "efConstruction": 200}
        
        # Mock embedder config
        self.config.embedder.provider = "openai"
        self.config.embedder.config = MagicMock()
        
        # Mock vector store config
        self.config.vector_store.config = MagicMock()
        
        # Mock LLM config
        self.config.llm.provider = "openai_structured"
        self.config.llm.config = MagicMock()
        self.config.graph_store.llm = None
        self.config.graph_store.custom_prompt = None

        # Create mocks for components
        self.mock_embedding_model = MagicMock()
        self.mock_llm = MagicMock()
        self.mock_client = MagicMock()
        self.mock_engine = MagicMock()
        self.mock_metadata = MagicMock()
        self.mock_graph_prompts = MagicMock()
        self.mock_graph_tools_prompts = MagicMock()

        # Patch the necessary components
        self.embedding_factory_patcher = patch("powermem.storage.oceanbase.oceanbase_graph.EmbedderFactory")
        self.mock_embedding_factory = self.embedding_factory_patcher.start()
        self.mock_embedding_factory.create.return_value = self.mock_embedding_model

        self.llm_factory_patcher = patch("powermem.storage.oceanbase.oceanbase_graph.LLMFactory")
        self.mock_llm_factory = self.llm_factory_patcher.start()
        self.mock_llm_factory.create.return_value = self.mock_llm

        self.obvec_client_patcher = patch("powermem.storage.oceanbase.oceanbase_graph.ObVecClient")
        self.mock_obvec_client = self.obvec_client_patcher.start()
        self.mock_obvec_client.return_value = self.mock_client
        self.mock_client.engine = self.mock_engine

        self.graph_prompts_patcher = patch("powermem.storage.oceanbase.oceanbase_graph.GraphPrompts")
        self.mock_graph_prompts_class = self.graph_prompts_patcher.start()
        self.mock_graph_prompts_class.return_value = self.mock_graph_prompts

        self.graph_tools_prompts_patcher = patch("powermem.storage.oceanbase.oceanbase_graph.GraphToolsPrompts")
        self.mock_graph_tools_prompts_class = self.graph_tools_prompts_patcher.start()
        self.mock_graph_tools_prompts_class.return_value = self.mock_graph_tools_prompts

        # Mock table existence checks
        self.mock_client.check_table_exists.return_value = False

        # Create the MemoryGraph instance
        self.memory_graph = MemoryGraph(self.config)

        # Set up common test data
        self.user_id = "test_user"
        self.test_filters = {"user_id": self.user_id}

    def tearDown(self):
        """Tear down test fixtures after each test method."""
        self.embedding_factory_patcher.stop()
        self.llm_factory_patcher.stop()
        self.obvec_client_patcher.stop()
        self.graph_prompts_patcher.stop()
        self.graph_tools_prompts_patcher.stop()

    def test_initialization(self):
        """Test that the MemoryGraph is initialized correctly."""
        self.assertEqual(self.memory_graph.embedding_model, self.mock_embedding_model)
        self.assertEqual(self.memory_graph.llm, self.mock_llm)
        self.assertEqual(self.memory_graph.llm_provider, "openai_structured")
        self.assertEqual(self.memory_graph.embedding_dims, 384)
        self.assertEqual(self.memory_graph.index_type, "HNSW")
        self.assertEqual(self.memory_graph.vidx_metric_type, "l2")
        self.assertEqual(self.memory_graph.vidx_name, "vidx")
        self.assertEqual(self.memory_graph.max_hops, 3)
        self.assertEqual(self.memory_graph.client, self.mock_client)

    def test_init_without_embedding_dims(self):
        """Test initialization fails without embedding_model_dims."""
        # Create a mock config without embedding_model_dims
        config_no_dims = MagicMock()
        config_no_dims.graph_store.config.host = "127.0.0.1"
        config_no_dims.graph_store.config.port = "2881"
        config_no_dims.graph_store.config.user = "root@test"
        config_no_dims.graph_store.config.password = "password"
        config_no_dims.graph_store.config.db_name = "test"
        config_no_dims.graph_store.config.embedding_model_dims = None
        config_no_dims.embedder.provider = "openai"
        config_no_dims.embedder.config = MagicMock()
        config_no_dims.vector_store.config = MagicMock()
        config_no_dims.llm.provider = "openai_structured"
        config_no_dims.llm.config = MagicMock()

        with pytest.raises(ValueError, match="embedding_model_dims is required"):
            MemoryGraph(config_no_dims)

    def test_get_llm_provider(self):
        """Test LLM provider resolution."""
        # Test with graph_store.llm.provider
        self.config.graph_store.llm = MagicMock()
        self.config.graph_store.llm.provider = "custom_provider"
        provider = self.memory_graph._get_llm_provider()
        self.assertEqual(provider, "custom_provider")

        # Test with config.llm.provider fallback
        self.config.graph_store.llm = None
        provider = self.memory_graph._get_llm_provider()
        self.assertEqual(provider, "openai_structured")

        # Test with default fallback
        self.config.llm.provider = None
        provider = self.memory_graph._get_llm_provider()
        self.assertEqual(provider, constants.DEFAULT_LLM_PROVIDER)

    def test_build_user_identity(self):
        """Test user identity building."""
        # Test with only user_id
        identity = self.memory_graph._build_user_identity(self.test_filters)
        self.assertEqual(identity, "user_id: test_user")

        # Test with additional fields
        filters_with_agent = {"user_id": "test_user", "agent_id": "agent_1", "run_id": "run_1"}
        identity = self.memory_graph._build_user_identity(filters_with_agent)
        self.assertEqual(identity, "user_id: test_user, agent_id: agent_1, run_id: run_1")

    def test_build_filter_conditions(self):
        """Test filter conditions building."""
        # Test with only user_id
        filter_parts, params = self.memory_graph._build_filter_conditions(self.test_filters)
        self.assertEqual(filter_parts, ["user_id = :user_id"])
        self.assertEqual(params, {"user_id": "test_user"})

        # Test with additional fields
        filters_with_agent = {"user_id": "test_user", "agent_id": "agent_1", "run_id": "run_1"}
        filter_parts, params = self.memory_graph._build_filter_conditions(filters_with_agent)
        expected_parts = ["user_id = :user_id", "agent_id = :agent_id", "run_id = :run_id"]
        expected_params = {"user_id": "test_user", "agent_id": "agent_1", "run_id": "run_1"}
        self.assertEqual(filter_parts, expected_parts)
        self.assertEqual(params, expected_params)

        # Test with prefix
        filter_parts, params = self.memory_graph._build_filter_conditions(self.test_filters, prefix="r.")
        self.assertEqual(filter_parts, ["r.user_id = :user_id"])

    def test_coerce_tool_response_to_dict(self):
        """Test tool response coercion."""
        # Test with dict input
        response_dict = {"key": "value"}
        result = self.memory_graph._coerce_tool_response_to_dict(response_dict)
        self.assertEqual(result, response_dict)

        # Test with JSON string input
        response_json = '{"key": "value"}'
        result = self.memory_graph._coerce_tool_response_to_dict(response_json)
        self.assertEqual(result, {"key": "value"})

        # Test with invalid input
        response_invalid = "invalid json"
        result = self.memory_graph._coerce_tool_response_to_dict(response_invalid)
        self.assertEqual(result, {})

    def test_add_method(self):
        """Test the add method with mocked components."""
        # Mock the necessary methods that add() calls
        self.memory_graph._retrieve_nodes_from_data = MagicMock(return_value={"alice": "person", "bob": "person"})
        self.memory_graph._establish_nodes_relations_from_data = MagicMock(
            return_value=[{"source": "alice", "relationship": "knows", "destination": "bob"}]
        )
        self.memory_graph._search_graph_db = MagicMock(return_value=[])
        self.memory_graph._get_delete_entities_from_search_output = MagicMock(return_value=[])
        self.memory_graph._delete_entities = MagicMock(return_value=[])
        self.memory_graph._add_entities = MagicMock(
            return_value=[{"source": "alice", "relationship": "knows", "target": "bob"}]
        )

        # Call the add method
        result = self.memory_graph.add("Alice knows Bob", self.test_filters)

        # Verify the method calls
        self.memory_graph._retrieve_nodes_from_data.assert_called_once_with("Alice knows Bob", self.test_filters)
        self.memory_graph._establish_nodes_relations_from_data.assert_called_once()
        self.memory_graph._search_graph_db.assert_called_once()
        self.memory_graph._get_delete_entities_from_search_output.assert_called_once()
        self.memory_graph._delete_entities.assert_called_once_with([], self.test_filters)
        self.memory_graph._add_entities.assert_called_once()

        # Check the result structure
        self.assertIn("deleted_entities", result)
        self.assertIn("added_entities", result)

    def test_search_method(self):
        """Test the search method with mocked components."""
        # Mock the necessary methods that search() calls
        self.memory_graph._retrieve_nodes_from_data = MagicMock(return_value={"alice": "person"})

        # Mock search results
        mock_search_results = [
            {"source": "alice", "relationship": "knows", "destination": "bob"},
            {"source": "alice", "relationship": "works_with", "destination": "charlie"},
        ]
        self.memory_graph._search_graph_db = MagicMock(return_value=mock_search_results)

        # Mock BM25Okapi
        with patch("powermem.storage.oceanbase.oceanbase_graph.BM25Okapi") as mock_bm25:
            mock_bm25_instance = MagicMock()
            mock_bm25.return_value = mock_bm25_instance

            # Mock get_scores to return scores for the 2 search results
            # Higher score for first result (0.8) than second (0.5) to ensure ordering
            mock_bm25_instance.get_scores.return_value = [0.8, 0.5]

            # Call the search method
            result = self.memory_graph.search("Find Alice", self.test_filters, limit=5)

            # Verify the method calls
            self.memory_graph._retrieve_nodes_from_data.assert_called_once_with("Find Alice", self.test_filters)
            self.memory_graph._search_graph_db.assert_called_once_with(node_list=["alice"], filters=self.test_filters, limit=5)

            # Check the result structure
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["source"], "alice")
            self.assertEqual(result[0]["relationship"], "knows")
            self.assertEqual(result[0]["destination"], "bob")

    def test_search_method_empty_results(self):
        """Test the search method with empty results."""
        # Mock empty search results
        self.memory_graph._retrieve_nodes_from_data = MagicMock(return_value={"alice": "person"})
        self.memory_graph._search_graph_db = MagicMock(return_value=[])

        # Call the search method
        result = self.memory_graph.search("Find Alice", self.test_filters)

        # Check the result
        self.assertEqual(result, [])

    def test_get_all_method(self):
        """Test the get_all method."""
        # Mock relationships results
        mock_relationships = [
            ("rel1", "entity1", "knows", "entity2", "2023-01-01 10:00:00"),
            ("rel2", "entity2", "works_with", "entity3", "2023-01-01 11:00:00"),
        ]
        mock_relationships_result = MagicMock()
        mock_relationships_result.fetchall.return_value = mock_relationships
        self.mock_client.get.return_value = mock_relationships_result

        # Mock entities results
        mock_entities = [
            ("entity1", "alice"),
            ("entity2", "bob"),
            ("entity3", "charlie"),
        ]
        mock_entities_result = MagicMock()
        mock_entities_result.fetchall.return_value = mock_entities
        self.mock_client.get.side_effect = [mock_relationships_result, mock_entities_result]

        # Call the get_all method
        result = self.memory_graph.get_all(self.test_filters, limit=10)

        # Verify the method calls
        self.assertEqual(self.mock_client.get.call_count, 2)

        # Check the result structure (results are sorted by updated_at descending)
        self.assertEqual(len(result), 2)
        # First result should be the most recent (rel2)
        self.assertEqual(result[0]["source"], "bob")
        self.assertEqual(result[0]["relationship"], "works_with")
        self.assertEqual(result[0]["target"], "charlie")
        # Second result should be rel1
        self.assertEqual(result[1]["source"], "alice")
        self.assertEqual(result[1]["relationship"], "knows")
        self.assertEqual(result[1]["target"], "bob")

    def test_get_all_method_empty_results(self):
        """Test the get_all method with empty results."""
        # Mock empty relationships results
        mock_relationships_result = MagicMock()
        mock_relationships_result.fetchall.return_value = []
        self.mock_client.get.return_value = mock_relationships_result

        # Call the get_all method
        result = self.memory_graph.get_all(self.test_filters)

        # Check the result
        self.assertEqual(result, [])

    def test_delete_all_method(self):
        """Test the delete_all method."""
        # Mock relationships results
        mock_relationships = [
            ("rel1", "entity1", "entity2"),
            ("rel2", "entity2", "entity3"),
        ]
        mock_relationships_result = MagicMock()
        mock_relationships_result.fetchall.return_value = mock_relationships
        self.mock_client.get.return_value = mock_relationships_result

        # Call the delete_all method
        self.memory_graph.delete_all(self.test_filters)

        # Verify the method calls
        self.assertEqual(self.mock_client.get.call_count, 1)
        self.assertEqual(self.mock_client.delete.call_count, 2)  # relationships and entities

    def test_search_node(self):
        """Test the _search_node method."""
        # Mock embedding
        mock_embedding = [0.1, 0.2, 0.3]

        # Mock Table and Column objects
        mock_table = MagicMock()
        mock_column = MagicMock()
        mock_table.c.embedding = mock_column

        # Mock ann_search results
        mock_search_results = MagicMock()
        mock_search_results.fetchall.return_value = [
            ("entity1", "alice", 0.5),
            ("entity2", "alice_similar", 0.7),
        ]
        self.mock_client.ann_search.return_value = mock_search_results

        # Mock Table creation and l2_distance
        with patch("powermem.storage.oceanbase.oceanbase_graph.Table") as mock_table_class, \
             patch("powermem.storage.oceanbase.oceanbase_graph.l2_distance") as mock_l2_distance:
            mock_table_class.return_value = mock_table
            # Create a mock that supports comparison
            mock_distance_expr = MagicMock()
            mock_distance_expr.__lt__ = lambda self, other: True  # Always return True for comparison
            mock_l2_distance.return_value = mock_distance_expr

            # Call the _search_node method with limit > 1
            result = self.memory_graph._search_node("alice", mock_embedding, self.test_filters, limit=2)

            # Verify the method calls
            self.mock_client.ann_search.assert_called_once()

            # Check the result
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["id"], "entity1")
            self.assertEqual(result[0]["name"], "alice")
            self.assertEqual(result[0]["distance"], 0.5)

    def test_search_node_single_result(self):
        """Test the _search_node method with single result."""
        # Mock embedding
        mock_embedding = [0.1, 0.2, 0.3]

        # Mock Table and Column objects
        mock_table = MagicMock()
        mock_column = MagicMock()
        mock_table.c.embedding = mock_column

        # Mock ann_search results
        mock_search_results = MagicMock()
        mock_search_results.fetchall.return_value = [("entity1", "alice", 0.5)]
        self.mock_client.ann_search.return_value = mock_search_results

        # Mock Table creation and l2_distance
        with patch("powermem.storage.oceanbase.oceanbase_graph.Table") as mock_table_class, \
             patch("powermem.storage.oceanbase.oceanbase_graph.l2_distance") as mock_l2_distance:
            mock_table_class.return_value = mock_table
            # Create a mock that supports comparison
            mock_distance_expr = MagicMock()
            mock_distance_expr.__lt__ = lambda self, other: True  # Always return True for comparison
            mock_l2_distance.return_value = mock_distance_expr

            # Call the _search_node method with limit = 1
            result = self.memory_graph._search_node("alice", mock_embedding, self.test_filters, limit=1)

            # Check the result
            self.assertIsInstance(result, dict)
            self.assertEqual(result["id"], "entity1")
            self.assertEqual(result["name"], "alice")
            self.assertEqual(result["distance"], 0.5)

    def test_search_node_no_results(self):
        """Test the _search_node method with no results."""
        # Mock embedding
        mock_embedding = [0.1, 0.2, 0.3]

        # Mock Table and Column objects
        mock_table = MagicMock()
        mock_column = MagicMock()
        mock_table.c.embedding = mock_column

        # Mock empty ann_search results
        mock_search_results = MagicMock()
        mock_search_results.fetchall.return_value = []
        self.mock_client.ann_search.return_value = mock_search_results

        # Mock Table creation and l2_distance
        with patch("powermem.storage.oceanbase.oceanbase_graph.Table") as mock_table_class, \
             patch("powermem.storage.oceanbase.oceanbase_graph.l2_distance") as mock_l2_distance:
            mock_table_class.return_value = mock_table
            # Create a mock that supports comparison
            mock_distance_expr = MagicMock()
            mock_distance_expr.__lt__ = lambda self, other: True  # Always return True for comparison
            mock_l2_distance.return_value = mock_distance_expr

            # Call the _search_node method
            result = self.memory_graph._search_node("alice", mock_embedding, self.test_filters)

            # Check the result
            self.assertIsNone(result)

    def test_create_entity(self):
        """Test the _create_entity method."""
        # Mock data
        name = "alice"
        entity_type = "person"
        embedding = [0.1, 0.2, 0.3]
        filters = self.test_filters

        # Mock upsert
        self.mock_client.upsert.return_value = None

        # Call the _create_entity method
        result = self.memory_graph._create_entity(name, entity_type, embedding, filters)

        # Verify the method calls
        self.mock_client.upsert.assert_called_once()

        # Check the result is a Snowflake ID (int)
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)  # Snowflake ID should be positive

    def test_create_or_update_relationship_new(self):
        """Test the _create_or_update_relationship method for new relationship."""
        source_id = 641905209349505024  # Use Snowflake ID format
        dest_id = 641905209349505025
        relationship_type = "knows"
        filters = self.test_filters

        # Mock get results (no existing relationship)
        mock_get_result = MagicMock()
        mock_get_result.fetchall.return_value = []
        self.mock_client.get.return_value = mock_get_result

        # Mock insert
        self.mock_client.insert.return_value = None

        # Mock entity names for return value
        mock_entity_result = MagicMock()
        mock_entity_result.fetchone.side_effect = [
            (source_id, "alice"),
            (dest_id, "bob")
        ]
        self.mock_client.get.side_effect = [mock_get_result, mock_entity_result, mock_entity_result]

        # Call the _create_or_update_relationship method
        result = self.memory_graph._create_or_update_relationship(source_id, dest_id, relationship_type, filters)

        # Verify the method calls
        self.mock_client.insert.assert_called_once()

        # Check the result
        self.assertEqual(result["source"], "alice")
        self.assertEqual(result["relationship"], "knows")
        self.assertEqual(result["target"], "bob")

    def test_create_or_update_relationship_existing(self):
        """Test the _create_or_update_relationship method for existing relationship."""
        source_id = 641905209349505024  # Use Snowflake ID format
        dest_id = 641905209349505025
        relationship_type = "knows"
        filters = self.test_filters

        # Mock get results (existing relationship)
        mock_get_result = MagicMock()
        mock_get_result.fetchall.return_value = [(641905209349505026,)]  # existing relationship (no mentions field)
        self.mock_client.get.return_value = mock_get_result

        # Mock entity names for return value
        mock_entity_result = MagicMock()
        mock_entity_result.fetchone.side_effect = [
            (source_id, "alice"),
            (dest_id, "bob")
        ]
        self.mock_client.get.side_effect = [mock_get_result, mock_entity_result, mock_entity_result]

        # Call the _create_or_update_relationship method
        result = self.memory_graph._create_or_update_relationship(source_id, dest_id, relationship_type, filters)

        # Verify insert is NOT called (relationship already exists)
        self.mock_client.insert.assert_not_called()

        # Check the result
        self.assertEqual(result["source"], "alice")
        self.assertEqual(result["relationship"], "knows")
        self.assertEqual(result["target"], "bob")

    def test_remove_spaces_from_entities(self):
        """Test the _remove_spaces_from_entities method."""
        entity_list = [
            {"source": "Alice Smith", "relationship": "knows", "destination": "Bob Johnson"},
            {"source": "Charlie Brown", "relationship": "works with", "destination": "David Wilson"},
        ]

        # Call the _remove_spaces_from_entities method
        result = self.memory_graph._remove_spaces_from_entities(entity_list)

        # Check the result
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["source"], "alice_smith")
        self.assertEqual(result[0]["relationship"], "knows")
        self.assertEqual(result[0]["destination"], "bob_johnson")
        self.assertEqual(result[1]["source"], "charlie_brown")
        self.assertEqual(result[1]["relationship"], "works_with")
        self.assertEqual(result[1]["destination"], "david_wilson")

    def test_search_source_node(self):
        """Test the _search_source_node method."""
        # Mock embedding
        mock_embedding = [0.1, 0.2, 0.3]

        # Mock _search_node
        self.memory_graph._search_node = MagicMock(return_value={"id": "entity1", "name": "alice", "distance": 0.5})

        # Call the _search_source_node method
        result = self.memory_graph._search_source_node(mock_embedding, self.test_filters, threshold=0.9, limit=1)

        # Verify the method calls
        self.memory_graph._search_node.assert_called_once_with("source", mock_embedding, self.test_filters, 0.9, 1)

        # Check the result
        self.assertEqual(result["id"], "entity1")
        self.assertEqual(result["name"], "alice")

    def test_search_destination_node(self):
        """Test the _search_destination_node method."""
        # Mock embedding
        mock_embedding = [0.1, 0.2, 0.3]

        # Mock _search_node
        self.memory_graph._search_node = MagicMock(return_value={"id": "entity2", "name": "bob", "distance": 0.6})

        # Call the _search_destination_node method
        result = self.memory_graph._search_destination_node(mock_embedding, self.test_filters, threshold=0.9, limit=1)

        # Verify the method calls
        self.memory_graph._search_node.assert_called_once_with("destination", mock_embedding, self.test_filters, 0.9, 1)

        # Check the result
        self.assertEqual(result["id"], "entity2")
        self.assertEqual(result["name"], "bob")

    def test_reset_method(self):
        """Test the reset method."""
        # Reset the mock call counts
        self.mock_client.reset_mock()
        
        # Mock table existence checks
        self.mock_client.check_table_exists.side_effect = [True, True]  # Both tables exist

        # Mock drop table methods
        self.mock_client.drop_table_if_exist.return_value = None

        # Mock _create_tables
        self.memory_graph._create_tables = MagicMock()

        # Call the reset method
        self.memory_graph.reset()

        # Verify the method calls
        self.assertEqual(self.mock_client.check_table_exists.call_count, 2)
        self.assertEqual(self.mock_client.drop_table_if_exist.call_count, 2)
        self.memory_graph._create_tables.assert_called_once()


if __name__ == "__main__":
    unittest.main()
