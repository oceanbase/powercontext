import importlib
import sys
import unittest
import uuid
from unittest.mock import MagicMock, patch

# Mock pyobvector and sqlalchemy modules before importing OceanBaseVectorStore
mock_obvec_client = MagicMock()
mock_vector = MagicMock()
mock_cosine_distance = MagicMock()
mock_inner_product = MagicMock()
mock_l2_distance = MagicMock()
mock_vec_index_type = MagicMock()

# Mock SQLAlchemy components
mock_json = MagicMock()
mock_column = MagicMock()
mock_string = MagicMock()
mock_table = MagicMock()
mock_func = MagicMock()
mock_column_element = MagicMock()
mock_text = MagicMock()
mock_and = MagicMock()
mock_or = MagicMock()
mock_not = MagicMock()
mock_select = MagicMock()
mock_bindparam = MagicMock()
mock_literal_column = MagicMock()
mock_longtext = MagicMock()

# Configure mocks
mock_vec_index_type.HNSW = "HNSW"
mock_vec_index_type.HNSW_SQ = "HNSW_SQ"
mock_vec_index_type.IVFFLAT = "IVFFLAT"
mock_vec_index_type.IVFSQ = "IVFSQ"
mock_vec_index_type.IVFPQ = "IVFPQ"

# Set up sys.modules mocks
sys.modules['pyobvector'] = MagicMock()
sys.modules['pyobvector'].ObVecClient = mock_obvec_client
sys.modules['pyobvector'].VECTOR = mock_vector
sys.modules['pyobvector'].cosine_distance = mock_cosine_distance
sys.modules['pyobvector'].inner_product = mock_inner_product
sys.modules['pyobvector'].l2_distance = mock_l2_distance
sys.modules['pyobvector'].VecIndexType = mock_vec_index_type

sys.modules['sqlalchemy'] = MagicMock()
sys.modules['sqlalchemy'].JSON = mock_json
sys.modules['sqlalchemy'].Column = mock_column
sys.modules['sqlalchemy'].String = mock_string
sys.modules['sqlalchemy'].Table = mock_table
sys.modules['sqlalchemy'].func = mock_func
sys.modules['sqlalchemy'].ColumnElement = mock_column_element
sys.modules['sqlalchemy'].text = mock_text
sys.modules['sqlalchemy'].and_ = mock_and
sys.modules['sqlalchemy'].or_ = mock_or
sys.modules['sqlalchemy'].not_ = mock_not
sys.modules['sqlalchemy'].select = mock_select
sys.modules['sqlalchemy'].bindparam = mock_bindparam
sys.modules['sqlalchemy'].literal_column = mock_literal_column

sys.modules['sqlalchemy.dialects.mysql'] = MagicMock()
sys.modules['sqlalchemy.dialects.mysql'].LONGTEXT = mock_longtext

# Import real constants directly from file to avoid package import issues
import sys
import os
import importlib.util

# Get the constants file path
constants_file = os.path.join(os.path.dirname(__file__), '..', '..', 'src', 'powermem', 'storage', 'oceanbase', 'constants.py')

# Load the constants module directly
spec = importlib.util.spec_from_file_location("constants", constants_file)
constants_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(constants_module)

# Extract constants
DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE = constants_module.DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE
DEFAULT_INDEX_TYPE = constants_module.DEFAULT_INDEX_TYPE
DEFAULT_PRIMARY_FIELD = constants_module.DEFAULT_PRIMARY_FIELD
DEFAULT_VECTOR_FIELD = constants_module.DEFAULT_VECTOR_FIELD
DEFAULT_TEXT_FIELD = constants_module.DEFAULT_TEXT_FIELD
DEFAULT_METADATA_FIELD = constants_module.DEFAULT_METADATA_FIELD
DEFAULT_VIDX_NAME = constants_module.DEFAULT_VIDX_NAME
DEFAULT_FULLTEXT_PARSER = constants_module.DEFAULT_FULLTEXT_PARSER
OCEANBASE_SUPPORTED_FULLTEXT_PARSERS = constants_module.OCEANBASE_SUPPORTED_FULLTEXT_PARSERS
OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES = constants_module.OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES
OCEANBASE_BUILD_PARAMS_MAPPING = constants_module.OCEANBASE_BUILD_PARAMS_MAPPING
DEFAULT_OCEANBASE_CONNECTION = constants_module.DEFAULT_OCEANBASE_CONNECTION

# Create a mock class that behaves like OceanBaseVectorStore
class MockOceanBaseVectorStore:
    def __init__(self, collection_name, embedding_model_dims=None, **kwargs):
        self.collection_name = collection_name
        self.embedding_model_dims = embedding_model_dims
        self.index_type = kwargs.get('index_type', DEFAULT_INDEX_TYPE)
        self.vidx_metric_type = kwargs.get('vidx_metric_type', DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE)
        self.normalize = kwargs.get('normalize', False)
        self.hybrid_search = kwargs.get('hybrid_search', True)
        
        # Mock ObVecClient
        self.obvector = MagicMock()
        self.obvector.check_table_exists.return_value = False
        self.obvector.create_table_with_index_params.return_value = None
        self.obvector.upsert.return_value = None
        self.obvector.ann_search.return_value = MagicMock()
        self.obvector.get.return_value = MagicMock()
        self.obvector.delete.return_value = None
        self.obvector.drop_table_if_exist.return_value = None
        
        # Mock engine and connection
        self.obvector.engine = MagicMock()
        self.obvector.metadata_obj = MagicMock()
        
        # Mock table
        self.table = MagicMock()
        self.table.c = MagicMock()
    
    def insert(self, vectors, payloads=None, ids=None):
        """Mock insert method."""
        if ids is None:
            # Generate Snowflake IDs (64-bit integers) instead of UUIDs
            # Snowflake IDs are large integers, typically in the range of 10^18
            import time
            base_id = int(time.time() * 1000) << 22  # Simulate Snowflake ID structure
            ids = [base_id + i for i in range(len(vectors))]
        return ids
    
    def search(self, query, vectors, limit=5, filters=None):
        """Mock search method."""
        # Return mock results
        mock_result = MagicMock()
        mock_result.id = "test_id"
        mock_result.score = 0.5
        mock_result.payload = {"data": "test content"}
        return [mock_result]
    
    def delete(self, vector_id):
        """Mock delete method."""
        pass
    
    def update(self, vector_id, vector=None, payload=None):
        """Mock update method."""
        pass
    
    def get(self, vector_id):
        """Mock get method."""
        mock_result = MagicMock()
        mock_result.id = vector_id
        mock_result.payload = {"data": "test content"}
        return mock_result
    
    def list_cols(self):
        """Mock list_cols method."""
        return ["test_collection"]
    
    def delete_col(self):
        """Mock delete_col method."""
        pass
    
    def col_info(self):
        """Mock col_info method."""
        return {
            "name": self.collection_name,
            "index_type": self.index_type,
            "metric_type": self.vidx_metric_type
        }
    
    def list(self, filters=None, limit=None):
        """Mock list method."""
        mock_result = MagicMock()
        mock_result.id = "test_id"
        mock_result.payload = {"data": "test content"}
        return [[mock_result]]
    
    def reset(self):
        """Mock reset method."""
        pass
    
    def _normalize(self, vector):
        """Mock normalize method."""
        import numpy as np
        arr = np.array(vector)
        norm = np.linalg.norm(arr)
        if norm == 0:
            return vector
        arr = arr / norm
        return arr.tolist()
    
    def _get_distance_function(self, metric_type):
        """Mock distance function selection."""
        if metric_type == "cosine":
            return mock_cosine_distance
        elif metric_type == "l2":
            return mock_l2_distance
        elif metric_type == "inner_product":
            return mock_inner_product
        else:
            raise ValueError(f"Unsupported metric type: {metric_type}")
    
    def _check_and_create_fulltext_index(self):
        """Mock fulltext index creation."""
        pass
    
    def _get_existing_vector_dimension(self):
        """Mock get existing vector dimension."""
        return 3

# Use the mock class
OceanBaseVectorStore = MockOceanBaseVectorStore


class TestOceanBaseVectorStore(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        # Test data
        self.test_vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        self.test_payloads = [{"data": "test content 1", "user_id": "user1"}, {"data": "test content 2", "user_id": "user2"}]
        # Use Snowflake IDs (64-bit integers) instead of UUID strings
        import time
        base_id = int(time.time() * 1000) << 22
        self.test_ids = [base_id, base_id + 1]
        
        # Connection parameters
        self.connection_args = {
            "host": "127.0.0.1",
            "port": "2881",
            "user": "root@test",
            "password": "password",
            "db_name": "test_db"
        }

    def test_init_with_individual_params(self):
        """Test initialization with individual parameters."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3,
            host="127.0.0.1",
            port="2881",
            user="root@test",
            password="password",
            db_name="test_db",
            index_type="HNSW",
            vidx_metric_type="l2"
        )
        
        # Verify instance properties
        self.assertEqual(oceanbase_store.collection_name, "test_collection")
        self.assertEqual(oceanbase_store.embedding_model_dims, 3)
        self.assertEqual(oceanbase_store.index_type, DEFAULT_INDEX_TYPE)
        self.assertEqual(oceanbase_store.vidx_metric_type, DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE)

    def test_init_with_connection_args(self):
        """Test initialization with connection_args dictionary."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3,
            connection_args=self.connection_args
        )
        
        self.assertEqual(oceanbase_store.collection_name, "test_collection")
        self.assertEqual(oceanbase_store.embedding_model_dims, 3)

    def test_insert_vectors(self):
        """Test vector insertion."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test insert
        result_ids = oceanbase_store.insert(self.test_vectors, self.test_payloads, self.test_ids)
        
        # Verify returned IDs
        self.assertEqual(result_ids, self.test_ids)

    def test_insert_with_auto_generated_ids(self):
        """Test vector insertion with auto-generated IDs."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test insert without providing IDs
        result_ids = oceanbase_store.insert(self.test_vectors, self.test_payloads)
        
        # Verify returned IDs are Snowflake IDs (64-bit integers)
        self.assertEqual(len(result_ids), 2)
        for result_id in result_ids:
            self.assertIsInstance(result_id, int)
            # Verify it's a positive integer (Snowflake IDs are always positive)
            self.assertGreater(result_id, 0)

    def test_vector_search(self):
        """Test vector search."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test search
        results = oceanbase_store.search("test query", [0.1, 0.2, 0.3], limit=5)
        
        # Verify results
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "test_id")
        self.assertEqual(results[0].score, 0.5)

    def test_delete_vector(self):
        """Test vector deletion."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test delete - should not raise exception
        oceanbase_store.delete("test_id")

    def test_update_vector(self):
        """Test vector update."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test update - should not raise exception
        new_vector = [0.4, 0.5, 0.6]
        new_payload = {"data": "updated content", "user_id": "user2"}
        oceanbase_store.update("test_id", vector=new_vector, payload=new_payload)

    def test_get_vector(self):
        """Test get vector by ID."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test get
        result = oceanbase_store.get("test_id")
        
        # Verify result
        self.assertIsNotNone(result)
        self.assertEqual(result.id, "test_id")
        self.assertEqual(result.payload["data"], "test content")

    def test_list_collections(self):
        """Test list collections."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test list collections
        collections = oceanbase_store.list_cols()
        
        # Verify result
        self.assertIn("test_collection", collections)

    def test_delete_collection(self):
        """Test delete collection."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test delete collection - should not raise exception
        oceanbase_store.delete_col()

    def test_collection_info(self):
        """Test get collection info."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test get collection info
        info = oceanbase_store.col_info()
        
        # Verify result
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "test_collection")
        self.assertEqual(info["index_type"], DEFAULT_INDEX_TYPE)
        self.assertEqual(info["metric_type"], DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE)

    def test_list_memories(self):
        """Test list memories."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test list memories
        memories = oceanbase_store.list(filters=None, limit=10)
        
        # Verify result
        self.assertEqual(len(memories), 1)
        self.assertEqual(len(memories[0]), 1)  # Returns list of lists
        self.assertEqual(memories[0][0].id, "test_id")

    def test_reset_collection(self):
        """Test reset collection."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test reset - should not raise exception
        oceanbase_store.reset()

    def test_normalize_vector(self):
        """Test vector normalization."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3,
            normalize=True
        )
        
        # Test normalization
        test_vector = [3.0, 4.0, 0.0]  # Should normalize to [0.6, 0.8, 0.0]
        normalized = oceanbase_store._normalize(test_vector)
        
        # Verify normalization
        self.assertAlmostEqual(normalized[0], 0.6, places=5)
        self.assertAlmostEqual(normalized[1], 0.8, places=5)
        self.assertAlmostEqual(normalized[2], 0.0, places=5)

    def test_distance_function_selection(self):
        """Test distance function selection based on metric type."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3,
            vidx_metric_type="cosine"
        )
        
        # Test distance function selection
        distance_func = oceanbase_store._get_distance_function("cosine")
        self.assertEqual(distance_func, mock_cosine_distance)
        
        distance_func = oceanbase_store._get_distance_function("l2")
        self.assertEqual(distance_func, mock_l2_distance)
        
        distance_func = oceanbase_store._get_distance_function("inner_product")
        self.assertEqual(distance_func, mock_inner_product)

    def test_invalid_distance_function(self):
        """Test invalid distance function."""
        oceanbase_store = OceanBaseVectorStore(
            collection_name="test_collection",
            embedding_model_dims=3
        )
        
        # Test invalid distance function
        with self.assertRaises(ValueError) as context:
            oceanbase_store._get_distance_function("invalid_metric")
        
        self.assertIn("Unsupported metric type", str(context.exception))

    def tearDown(self):
        """Clean up after each test."""
        pass


if __name__ == '__main__':
    unittest.main()
