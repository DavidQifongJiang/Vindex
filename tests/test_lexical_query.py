import unittest

from app.services.lexical_query import lexical_query, query_terms


class LexicalQueryTest(unittest.TestCase):
    def test_baseline_is_unchanged(self):
        self.assertEqual(lexical_query("why not use C++?"), {"match": {"text": "why not use C++?"}})

    def test_keyword_boost_preserves_question_and_negation(self):
        query = "Why does Dijkstra not work with negative weights?"
        result = lexical_query(query, "terms")["bool"]
        self.assertEqual(result["must"], [{"match": {"text": query}}])
        self.assertIn("not", result["should"][0]["match"]["text"]["query"].split())
        self.assertEqual(query_terms("Explain C++ and C#"), ["c++", "c#"])

    def test_phrase_boost_is_optional_and_not_query_string_syntax(self):
        result = lexical_query('explain "Fibonacci heap" OR *:*', "phrase")["bool"]
        self.assertEqual(result["should"], [{"match_phrase": {"text": {
            "query": "Fibonacci heap", "slop": 0, "boost": 2.0}}}])
        self.assertIn("OR *:*", result["must"][0]["match"]["text"])

    def test_short_term_phrase_and_bounded_work(self):
        self.assertIn("bool", lexical_query("binary search tree", "phrase"))
        self.assertNotIn("bool", lexical_query("why does it work", "phrase"))
        self.assertEqual(query_terms(" ".join("term" + str(i) for i in range(13))), [])
        self.assertLessEqual(len(lexical_query('"a" "b" "c" "d" "e"', "phrase")["bool"]["should"]), 4)

    def test_invalid_input_rejected(self):
        for query, profile in [("x", "unknown"), ("", "baseline"), ("x" * 501, "terms")]:
            with self.assertRaises(ValueError):
                lexical_query(query, profile)


if __name__ == "__main__":
    unittest.main()
