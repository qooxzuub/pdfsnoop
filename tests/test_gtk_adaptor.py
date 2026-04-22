import pytest
from unittest.mock import MagicMock
from pdfsnoop.gtk_adaptor import GtkAdapter
from pdfsnoop.pdf_utils import JumpReference


class FakeStore:
    """A minimal mock of Gtk.TreeStore behavior."""

    def __init__(self):
        self.data = {}
        self.counter = 0

    def append(self, parent, row):
        self.counter += 1
        new_iter = f"iter_{self.counter}"
        self.data[new_iter] = row
        return new_iter

    def __getitem__(self, tree_iter):
        # Supports: store[iter][col]
        return self.data[tree_iter]

    def set_value(self, tree_iter, column, value):
        self.data[tree_iter][column] = value

    def get_path(self, tree_iter):
        return [tree_iter]  # Mock path

    def get_iter_first(self):
        return "iter_1" if "iter_1" in self.data else None


@pytest.fixture
def adapter():
    store = FakeStore()
    return GtkAdapter(store)


def test_create_node_dictionary(adapter):
    # Mock a pikepdf Dictionary
    pdf_dict = MagicMock()
    pdf_dict.is_indirect = True
    pdf_dict.objgen = (10, 0)
    pdf_dict.__len__.return_value = 5

    it = adapter.create_node(None, pdf_dict, "Root", "Dictionary")

    # Check if markup contains expected fragments
    markup = adapter.store[it][0]
    raw_text = adapter.store[it][2]

    assert "Dict[5]" in markup
    assert "(Obj 10:0)" in markup
    assert "<b>Root</b>" in markup
    assert "Dict[5]" in raw_text

    # Check registry tracking
    assert adapter.registry[(10, 0)] == it


def test_get_iter_from_objgen_string(adapter):
    # Setup registry
    adapter.registry[(5, 0)] = "iter_target"

    # Test valid lookup
    assert adapter.get_iter_from_objgen_string("5 0") == "iter_target"

    # Test Trailer lookup
    adapter.store.append(None, ["markup", "obj", "text", "name"])  # Creates iter_1
    assert adapter.get_iter_from_objgen_string("Trailer") == "iter_1"

    # Test invalid
    assert adapter.get_iter_from_objgen_string("99 99") is None
    assert adapter.get_iter_from_objgen_string("garbage") is None


def test_resolve_deferred_orphan(adapter):
    # Create a deferred node
    ui_iter = adapter.create_deferred(None, None, "PendingObj")

    # Resolve as orphan (becomes a Dictionary)
    pdf_dict = MagicMock()
    pdf_dict.is_indirect = True
    pdf_dict.objgen = (30, 0)
    pdf_dict.__len__.return_value = 2

    adapter.resolve_deferred(ui_iter, pdf_dict, "ResolvedName", is_orphan=True)

    assert "Dict[2]" in adapter.store[ui_iter][0]
    assert adapter.store[ui_iter][1] == pdf_dict


import pytest
from unittest.mock import MagicMock
from pdfsnoop.gtk_adaptor import GtkAdapter
from pdfsnoop.pdf_utils import JumpReference

# ... Keep the FakeStore class and adapter fixture from the previous message ...


def test_get_og_label_direct_object(adapter):
    """Hits line 32: returns empty string for direct objects."""
    pdf_obj = MagicMock()
    pdf_obj.is_indirect = False
    assert adapter._get_og_label(pdf_obj) == ""


def test_create_node_various_types(adapter):
    """Hits lines 55-65: Array, Stream, and default types."""
    # 1. Array
    pdf_arr = MagicMock()
    pdf_arr.is_indirect = False
    pdf_arr.__len__.return_value = 3
    it_arr = adapter.create_node(None, pdf_arr, "MyArray", "Array")
    assert "Array[3]" in adapter.store[it_arr][0]

    # 2. Stream
    pdf_stm = MagicMock()
    pdf_stm.is_indirect = False
    it_stm = adapter.create_node(None, pdf_stm, "MyStream", "Stream")
    assert "Stream" in adapter.store[it_stm][0]

    # 3. Scalar/Else (e.g. a String or Name)
    pdf_val = "HelloWorld"
    it_val = adapter.create_node(None, pdf_val, "MyKey", "Scalar")
    assert "HelloWorld" in adapter.store[it_val][0]
    assert "MyKey" in adapter.store[it_val][2]


def test_create_jump(adapter):
    # Setup a target node
    pdf_obj = MagicMock()
    pdf_obj.is_indirect = True
    pdf_obj.objgen = (20, 0)
    target_it = adapter.store.append(None, ["markup", pdf_obj, "raw", "name"])

    adapter.create_jump(None, target_it, "MyLink", None)

    # Find the jump node (it should be the second item in the store)
    jump_it = "iter_2"
    jump_ref = adapter.store[jump_it][1]

    assert isinstance(jump_ref, JumpReference)
    # Corrected attribute: target_node instead of path
    assert jump_ref.target_node == ["iter_1"]
    assert "↪ MyLink" in adapter.store[jump_it][0]


def test_resolve_deferred_jump(adapter):
    """Hits lines 102-109: Resolving a deferred node as a Jump."""
    ui_iter = adapter.create_deferred(None, None, "PendingJump")

    # In this case, 'target' is a Gtk.TreeIter (our mock string)
    target_iter = "iter_target_location"

    adapter.resolve_deferred(ui_iter, target_iter, "JumpName", is_orphan=False)

    markup = adapter.store[ui_iter][0]
    jump_ref = adapter.store[ui_iter][1]

    assert "Jump" in markup
    assert isinstance(jump_ref, JumpReference)
    # Corrected attribute: target_node instead of path
    assert jump_ref.target_node == ["iter_target_location"]
