import gi

gi.require_version("Gtk", "3.0")

import pytest
from unittest.mock import MagicMock, patch
import pikepdf
from gi.repository import Gtk
from pdfsnoop.gtk_adaptor import GtkAdapter
from pdfsnoop.search import (
    SearchController,
    _iter_visible_nodes,
    _build_resume_stack,
    iter_pdf_for_search,
    _find_existing_child,
)


class FakeApp:
    """Mocking the application structure."""

    def __init__(self):
        self.store = Gtk.TreeStore(
            object, object, str, str
        )  # obj, raw_obj, markup, name
        self.tree_view = MagicMock()
        self.statusbar = MagicMock()
        self.adapter = MagicMock()
        self.pdf = MagicMock()
        self.search_bar = MagicMock()

        # Setup selection mock
        self.selection = MagicMock()
        self.tree_view.get_selection.return_value = self.selection
        self.selection.get_selected.return_value = (self.store, None)


@pytest.fixture
def app():
    return FakeApp()


@pytest.fixture
def controller(app):
    return SearchController(app)


### --- Phase 1 Tests (Store Traversal) ---


def test_iter_visible_nodes_finds_text(app):
    # Setup: Add items to the store
    # col 1 (obj) MUST NOT BE NONE, otherwise the walker thinks it's a placeholder
    it = app.store.append(None, ["FakeObj", "FakeObj", "Target Match", "Node1"])
    app.store.append(it, ["FakeChild", "FakeChild", "Child Match", "Node2"])

    results = list(_iter_visible_nodes(app.store))

    # Now it should successfully walk into the child
    assert len(results) == 2
    assert results[1][0] == "Child Match"


def test_search_controller_phase1_updates_matches(app, controller):
    # Setup store
    app.store.append(None, [None, None, "finding nemo", "Name"])

    controller._text = "nemo"
    controller._run_phase1()

    assert len(controller.matches) == 1
    assert controller.current_index == 0
    app.tree_view.set_cursor.assert_called()


### --- Phase 2 Tests (PDF Graph Walk) ---


def test_search_tick_finds_matches_sequentially(app, controller):
    """Test that _tick processes items and finds matches."""
    # Setup: Add a real node so the TreeRowReference has something to point to
    it = app.store.append(None, [None, None, "Matching Text", "Node"])
    path = app.store.get_path(it)
    ref = Gtk.TreeRowReference.new(app.store, path)

    # Ensure the reference actually created
    assert ref is not None

    def mock_gen():
        yield "Matching Text", ref
        yield "Boring Text", ref

    controller._gen = mock_gen()
    controller._text = "matching"

    # Manually trigger a tick
    controller._tick()

    assert len(controller.matches) >= 1
    assert controller._nodes_visited == 2


### --- API Logic Tests ---


def test_cancel_stops_timers(app, controller):
    with patch("gi.repository.GLib.source_remove") as mock_remove:
        controller._idle_id = 99
        controller._debounce_id = 100

        controller.cancel()

        assert controller._idle_id is None
        assert controller._gen is None
        assert mock_remove.call_count == 2


def test_next_prev_match_wrapping(app, controller):
    # Mock three valid matches
    m1, m2 = MagicMock(), MagicMock()
    m1.valid.return_value = True
    m2.valid.return_value = True

    controller.matches = [m1, m2]
    controller.current_index = 0

    controller.next_match()
    assert controller.current_index == 1

    controller.next_match()  # Wrap
    assert controller.current_index == 0

    controller.prev_match()  # Wrap back
    assert controller.current_index == 1


### --- Integration-ish Test with pikepdf ---


def test_build_resume_stack_root(app, controller):
    from pdfsnoop.search import _build_resume_stack

    with pikepdf.Pdf.new() as pdf:
        stack = _build_resume_stack(pdf.trailer, None, app.adapter)
        # Should return the starting point for the trailer
        assert len(stack) == 1
        assert stack[0][2] == "Trailer"


def test_build_resume_stack_from_root(app):
    # Testing the fallback/start case
    stack = _build_resume_stack(app.pdf.trailer, None, app.adapter)
    assert len(stack) == 1
    assert stack[0][2] == "Trailer"


def test_build_resume_stack_mid_tree(app):
    # USE A REAL STORE to keep the C-type-checker happy
    real_store = Gtk.TreeStore(object, object, str, str)
    app.adapter.store = real_store

    # Setup a small tree: Root -> [0, 1, 2]
    # We put a real pikepdf array in the store
    arr = pikepdf.Array([10, 20, 30])
    it_p = real_store.append(None, ["Root", arr, "Array", "Root"])
    it_c = real_store.append(it_p, ["Item1", 20, "20", "[1]"])
    path = real_store.get_path(it_c)

    # We must also mock has_placeholder to return False so it doesn't break the walk
    app.adapter.has_placeholder.return_value = False

    stack = _build_resume_stack(app.pdf.trailer, path, app.adapter)

    names = [entry[2] for entry in stack]
    assert "[1]" in names
    assert "[2]" in names
    assert "[0]" in names


def test_controller_navigation_wrap(app, controller):
    # Ensure the store has rows so paths "0" and "1" are valid
    app.store.append(None, [None, None, "Match 1", "Node1"])
    app.store.append(None, [None, None, "Match 2", "Node2"])

    ref1 = Gtk.TreeRowReference.new(app.store, Gtk.TreePath.new_from_string("0"))
    ref2 = Gtk.TreeRowReference.new(app.store, Gtk.TreePath.new_from_string("1"))

    controller.matches = [ref1, ref2]
    controller.current_index = 0

    controller.next_match()
    assert controller.current_index == 1

    controller.next_match()
    assert controller.current_index == 0


def test_iter_pdf_exhausts_graph(app):

    real_store = Gtk.TreeStore(object, object, str, str)
    adapter = GtkAdapter(real_store)

    # Root must exist
    real_store.append(None, ["Trailer", None, "Trailer", "Trailer"])

    data = pikepdf.Dictionary(A=pikepdf.Array([1, 2]))

    gen = iter_pdf_for_search(data, adapter, start_path=None)
    results = list(gen)

    # Get just the text part of the (text, ref) tuples
    yielded_strings = [r[0] for r in results]

    # Use 'any' with 'in' to handle the extra formatting like "Array[2]"
    assert any("Trailer" in s for s in yielded_strings)
    assert any("/A" in s for s in yielded_strings)
    assert any("1" in s for s in yielded_strings)
    assert any("2" in s for s in yielded_strings)


def test_search_complex_graph_with_cycles(app):
    # Setup a store and adapter
    real_store = Gtk.TreeStore(object, object, str, str)
    adapter = GtkAdapter(real_store)
    real_store.append(None, ["Trailer", None, "Trailer", "Trailer"])

    # Create a circular reference: A -> B -> A
    # This tests your 'visited_indirect' and 'registry' logic
    inner_dict = pikepdf.Dictionary(Name="Circular")
    outer_dict = pikepdf.Dictionary(Child=inner_dict)
    # Mock the indirect nature if not using a real PDF file
    inner_dict.objgen = (10, 0)
    outer_dict.objgen = (11, 0)

    gen = iter_pdf_for_search(outer_dict, adapter)
    results = list(gen)

    # If this doesn't hang and correctly identifies the 'Jump' node,
    # your deduplication logic is verified.
    assert len(results) > 0


# ---------------------------------------------------------------------------
# Fixtures & Mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    """
    Creates a headless Gtk.TreeStore matching the expected column layout.
    Based on search.py, the columns accessed are:
    Col 1: Actual Object
    Col 2: Raw Search Text
    Col 3: Name/Key
    """
    # Col 0: Label, Col 1: Obj, Col 2: RawText, Col 3: Name
    return Gtk.TreeStore(str, object, str, str)


class MockAdapter:
    """Mock TreeAdapter to bypass GUI placeholder logic."""

    def __init__(self, store):
        self.store = store

    def has_placeholder(self, iter_):
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_find_existing_child(store):
    root = store.append(None, ["RootLabel", None, "RootText", "RootName"])
    store.append(root, ["A_Label", None, "A_Text", "/A"])
    store.append(root, ["B_Label", None, "B_Text", "/B"])

    # Should find the exact child by name in column 3
    match = _find_existing_child(store, root, "/B")
    assert match is not None
    assert store[match][3] == "/B"

    # Should return None for missing children
    miss = _find_existing_child(store, root, "/Missing")
    assert miss is None


def test_iter_visible_nodes_no_cursor(store):
    root = store.append(None, ["Root", object(), "raw_root", "Trailer"])
    store.append(root, ["A", object(), "raw_a", "/A"])
    b = store.append(root, ["B", object(), "raw_b", "/B"])
    store.append(b, ["B1", object(), "raw_b1", "[0]"])

    # Walk without cursor should just yield standard depth-first
    results = list(_iter_visible_nodes(store, cursor_path=None))

    # Results are (raw_text, path)
    texts = [r[0] for r in results]
    assert texts == ["raw_root", "raw_a", "raw_b", "raw_b1"]


def test_iter_visible_nodes_with_cursor_wraparound(store):
    root = store.append(None, ["Root", object(), "raw_root", "Trailer"])
    store.append(root, ["A", object(), "raw_a", "/A"])
    b = store.append(root, ["B", object(), "raw_b", "/B"])
    store.append(b, ["B1", object(), "raw_b1", "[0]"])
    store.append(root, ["C", object(), "raw_c", "/C"])

    # Set cursor path to B
    cursor_path = store.get_path(b)

    results = list(_iter_visible_nodes(store, cursor_path=cursor_path))
    texts = [r[0] for r in results]

    # It should split: Everything >= cursor (B, B1, C) first, then wrapped (Root, A)
    assert texts == ["raw_b", "raw_b1", "raw_c", "raw_root", "raw_a"]


def test_build_resume_stack_dictionary(store):
    """Test that the generator stack builds in the correct LIFO order for Dicts."""
    obj = pikepdf.Dictionary({"/A": 1, "/B": 2, "/C": 3})
    adapter = MockAdapter(store)

    root = store.append(None, ["Root", obj, "raw_root", "Trailer"])
    # Pretend the user's cursor is currently on /B
    cursor_node = store.append(root, ["B", 2, "raw_b", "/B"])
    cursor_path = store.get_path(cursor_node)

    stack = _build_resume_stack(None, cursor_path, adapter)

    # The stack is LIFO.
    # Expected pop order: Cursor (/B), then Next (/C), then Wrap-around (/A)
    # Therefore, the internal list should be ordered: ["/A", "/C", "/B"]
    names_in_stack = [item[2] for item in stack]
    assert names_in_stack == ["/A", "/C", "/B"]

    # Verify getters actually return the right values
    popped_b = stack.pop()
    assert popped_b[2] == "/B"
    assert popped_b[0]() == 2  # The lambda getter

    popped_c = stack.pop()
    assert popped_c[2] == "/C"
    assert popped_c[0]() == 3

    popped_a = stack.pop()
    assert popped_a[2] == "/A"
    assert popped_a[0]() == 1


def test_build_resume_stack_array(store):
    """Test that the generator stack builds in the correct LIFO order for Arrays."""
    obj = pikepdf.Array([10, 20, 30])
    adapter = MockAdapter(store)

    root = store.append(None, ["Root", obj, "raw_root", "Trailer"])
    # Pretend cursor is on the middle element: index 1
    cursor_node = store.append(root, ["Idx1", 20, "raw_1", "[1]"])
    cursor_path = store.get_path(cursor_node)

    stack = _build_resume_stack(None, cursor_path, adapter)

    # Iterating an array forward: [1], then [2], then wrap to [0]
    # LIFO stack order: ["[0]", "[2]", "[1]"]
    names_in_stack = [item[2] for item in stack]
    assert names_in_stack == ["[0]", "[2]", "[1]"]
