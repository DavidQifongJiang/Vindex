"""Bounded lexical query experiments; the serving default remains baseline BM25."""
import re


PROFILES = ("baseline", "phrase", "terms", "terms_phrase")
# Remove question scaffolding only from the OPTIONAL boost. The full query is
# always retained, including negation, conditions, punctuation and identifiers.
SCAFFOLD = frozenset("a an the is are was were do does did how what why when where which "
                     "can could would should please explain describe show me tell about "
                     "of for to in on with and or".split())
TOKEN = re.compile(r"[^\W_]+(?:[._+#/-][^\W_]+)*[+#]*", re.UNICODE)


def query_terms(query):
    terms = list(dict.fromkeys(t.casefold() for t in TOKEN.findall(query)
                              if t.casefold() not in SCAFFOLD))
    # Do not silently truncate a complex question to an arbitrary keyword prefix.
    return terms if 2 <= len(terms) <= 12 else []


def lexical_query(query, profile="baseline"):
    """Return only relevance clauses; callers must add authoritative filters."""
    if profile not in PROFILES:
        raise ValueError("Unknown lexical query profile")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
        raise ValueError("Query must contain 1 to 500 characters")
    baseline = {"match": {"text": query}}
    if profile == "baseline":
        return baseline
    boosts = []
    if profile in {"terms", "terms_phrase"}:
        terms = query_terms(query)
        if terms:
            boosts.append({"match": {"text": {
                "query": " ".join(terms), "operator": "and", "boost": 2.0}}})
    if profile in {"phrase", "terms_phrase"}:
        phrases = list(dict.fromkeys(p.strip() for p in re.findall(r'"([^"\n]{1,160})"', query)
                                    if p.strip()))[:4]
        words = TOKEN.findall(query)
        if not phrases and 2 <= len(words) <= 6 and all(w.casefold() not in SCAFFOLD for w in words):
            phrases = [query.strip()]
        boosts.extend({"match_phrase": {"text": {"query": phrase, "slop": 0, "boost": 2.0}}}
                      for phrase in phrases)
    return {"bool": {"must": [baseline], "should": boosts}} if boosts else baseline
