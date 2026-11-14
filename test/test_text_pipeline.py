import unittest

from processor.text_pipeline import clean_text, detect_language, segment_text, extract_entities, extract_events, run


class TextPipelineTests(unittest.TestCase):
    def test_clean_language_and_segments(self):
        text = clean_text('<p>美国公务机坠毁。</p>  2026年11月5日，造成3人受伤。')
        self.assertNotIn('<p>', text)
        self.assertEqual('zh', detect_language(text))
        self.assertGreaterEqual(len(segment_text(text)), 2)

    def test_entities_and_events_keep_source_reference(self):
        text = '美国公务机于2026年11月5日坠毁，造成3人受伤。'
        entities = extract_entities(text, 'src-1')
        events = extract_events(text, 'src-1')
        self.assertTrue(any(x['type'] == 'date' for x in entities))
        self.assertTrue(any(x['type'] == 'number' for x in entities))
        self.assertTrue(events)
        self.assertTrue(all(x['source_id'] == 'src-1' for x in events))

    def test_dedup_keeps_duplicate_ids(self):
        sources = [{'source_id': 'a', 'title': '<b>事故</b>', 'summary': '造成3人受伤', 'url': 'u1'}, {'source_id': 'b', 'title': '事故', 'summary': '造成3人受伤', 'url': 'u2'}]
        result = run(sources)
        self.assertEqual(1, result['stats']['output_count'])
        self.assertEqual(1, result['stats']['duplicate_count'])
        self.assertEqual(['b'], result['sources'][0]['duplicate_source_ids'])


if __name__ == '__main__':
    unittest.main()
