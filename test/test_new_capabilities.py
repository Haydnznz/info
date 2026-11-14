import os
import tempfile
import unittest
from pathlib import Path

from processor.embedding import BGEEmbedder, EmbeddingError
from processor.llm_graph import run_insight_graph
from processor.media_pipeline import extract_subtitles, link_evidence
from processor.graph_query import run as graph_run


class NewCapabilityTests(unittest.TestCase):
    def test_injected_embedding_backend(self):
        class Fake:
            def encode(self, texts, **kwargs): return [[1.0, 0.0] for _ in texts]
        model = BGEEmbedder(model=Fake())
        self.assertEqual([[1.0, 0.0]], model.embed(['evidence']))

    def test_missing_local_model_is_explicit(self):
        with self.assertRaises(EmbeddingError):
            BGEEmbedder(model_path='Z:/missing/bge').embed(['x'])

    def test_llm_unavailable_is_explicit(self):
        old = os.environ.get('LLM_ENABLED')
        os.environ['LLM_ENABLED'] = 'false'
        try:
            self.assertEqual('unavailable', run_insight_graph('q', [], {})['llm_status'])
        finally:
            if old is None: os.environ.pop('LLM_ENABLED', None)
            else: os.environ['LLM_ENABLED'] = old

    def test_media_sidecar_and_graph_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'clip.srt'
            media.write_text('1\n00:00:00 --> 00:00:01\n事故现场\n', encoding='utf-8')
            self.assertIn('事故现场', extract_subtitles(media))
        evidence = link_evidence({'title': 'image', 'ocr_text': '证据'}, 'src-1', 'r-1')
        graph = graph_run('主题', [{'source_id': 'src-1', 'title': '来源', 'type': 'news', 'url': 'u'}], {'risk_points': ['待核验'], 'sentiment': {'negative': 10}})
        self.assertEqual('src-1', evidence['source_id'])
        self.assertTrue(any(n['type'] == 'sentiment' for n in graph['nodes']))
        self.assertTrue(any(n['type'] == 'risk' for n in graph['nodes']))


if __name__ == '__main__':
    unittest.main()
