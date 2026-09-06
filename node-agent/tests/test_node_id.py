from app.node_id import sort_key


def test_double_digit_sorts_after_single_digit():
    # The bug this fixes: plain string comparison puts "edge10" before
    # "edge2" because '1' < '2' as characters.
    assert sort_key("regA-c1-edge10") > sort_key("regA-c1-edge2")
    assert sort_key("regA-c1-edge2") < sort_key("regA-c1-edge10")


def test_orders_a_run_of_node_ids_numerically():
    ids = ["regA-c1-edge9", "regA-c1-edge10", "regA-c1-edge2", "regA-c1-edge1"]
    assert sorted(ids, key=sort_key) == [
        "regA-c1-edge1",
        "regA-c1-edge2",
        "regA-c1-edge9",
        "regA-c1-edge10",
    ]


def test_non_numeric_ids_still_compare_lexicographically():
    assert sort_key("regA-c1-alpha") < sort_key("regA-c1-beta")


def test_equal_ids_produce_equal_keys():
    assert sort_key("regA-c1-edge1") == sort_key("regA-c1-edge1")
