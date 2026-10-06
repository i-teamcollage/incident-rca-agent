from incident_rca_agent.textsearch import BM25, tokenize

ARTICLES = {
    "returns": "How to return an item. You can return any item within 30 days of delivery.",
    "delivery": "Delivery times. Standard delivery takes 3 to 5 working days.",
    "invoices": "Getting a copy of your invoice. Invoices are in your account under Orders.",
}


def test_tokenize_lowercases_and_drops_punctuation_and_common_words():
    assert tokenize("How to RETURN an item?") == ["return", "item"]


def test_tokenize_keeps_numbers_and_identifiers():
    assert tokenize("Order NH-1042 failed with HTTP 429") == ["order", "nh", "1042", "failed", "http", "429"]


def test_most_relevant_document_comes_first():
    results = BM25(ARTICLES).search("can I return my item")
    assert results[0][0] == "returns"


def test_documents_without_any_query_word_are_left_out():
    results = BM25(ARTICLES).search("return")
    assert [doc_id for doc_id, _ in results] == ["returns"]


def test_k_limits_the_number_of_results():
    assert len(BM25(ARTICLES).search("delivery invoice return", k=2)) == 2


def test_empty_query_returns_nothing():
    assert BM25(ARTICLES).search("the and of") == []


def test_ties_are_broken_by_id_so_results_never_shuffle():
    index = BM25({"b": "same words here", "a": "same words here"})
    assert [doc_id for doc_id, _ in index.search("same words")] == ["a", "b"]
