import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from rag.engine import PgVectorRAGEngine

class TestRAGResilience(unittest.TestCase):
    @patch('rag.engine.get_conn')
    @patch('rag.engine.VertexEmbedder')
    def test_search_hybrid_fallback(self, mock_embedder_class, mock_get_conn):
        # Setup mock embedder that fails
        mock_embedder = mock_embedder_class.return_value
        mock_embedder.embed.side_effect = Exception("Connection Timeout")
        mock_embedder.dimension = 768

        # Setup mock DB connection and cursor
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        
        # Mock RRF results (simulating text-only matches)
        mock_cur.fetchall.return_value = [
            ("doc1", "Show logs", {"tool_name": "analyze_agent_logs"}, 0.016),
            ("doc2", "List agents", {"tool_name": "ae.agent.list_running"}, 0.015)
        ]

        # Initialize engine
        with patch('rag.engine._has_pgvector', return_value=True):
            engine = PgVectorRAGEngine()
            
        # Perform search with failing embedding
        results = engine.search("show logs", collection="tools", hybrid=True)
        
        # Verify results were returned despite embedding failure
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], "doc1")
        self.assertEqual(results[1]["id"], "doc2")
        
        # Verify that embed was called (and failed)
        self.assertTrue(mock_embedder.embed.called)

    @patch('rag.engine.get_conn')
    @patch('rag.engine.VertexEmbedder')
    def test_search_numpy_fallback(self, mock_embedder_class, mock_get_conn):
        # Setup mock embedder that fails
        mock_embedder = mock_embedder_class.return_value
        mock_embedder.embed.side_effect = Exception("Connection Timeout")
        mock_embedder.dimension = 768

        # Setup mock DB with some documents
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        mock_cur.fetchall.return_value = [
            ("doc1", "show logs for agent", {"tool_name": "analyze_agent_logs"}, [0.1]*768),
            ("doc2", "other tool", {}, [0.2]*768)
        ]

        # Initialize engine without pgvector
        with patch('rag.engine._has_pgvector', return_value=False):
            engine = PgVectorRAGEngine()
            
        # Perform search
        results = engine.search("show logs", collection="tools")
        
        # Verify results (doc1 should have higher keyword score)
        self.assertEqual(results[0]["id"], "doc1")
        self.assertGreater(results[0]["rrf_score"], results[1]["rrf_score"])

if __name__ == '__main__':
    unittest.main()
