import pytest
from unittest.mock import MagicMock, patch
import pikepdf
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
        self.data[new_iter] = list(row)
        return new_iter

    def __getitem__(self, tree_iter):
        return self.data[tree_iter]

    def set_value(self, tree_iter, column, value):
        self.data[tree_iter][column] = value

    def get_path(self, tree_iter):
        # Return a simple mock path
        return f"path_{tree_iter}"

    def iter_children(self, tree_iter):
        return None  # no children by default

    def get_iter_first(self):
        return "iter_1" if "iter_1" in self.data else None


class FakeTreeRowReference:
    """Fake TreeRowReference that just holds a path string."""

    def __init__(self, store, path):
        self.path = path

    def valid(self):
        return True

    def get_path(self):
        return self.path


@pytest.fixture
def adapter():
    store = FakeStore()
    return GtkAdapter(store)


@pytest.fixture
def adapter_patched():
    """Adapter with Gtk.TreeRowReference patched out."""
    store = FakeStore()
    with patch(
        "pdfsnoop.gtk_adaptor.Gtk.TreeRowReference.new",
        side_effect=FakeTreeRowReference,
    ):
        yield GtkAdapter(store)


def test_create_node_dictionary(adapter_patched):
    adapter = adapter_patched
    # Use a real pikepdf object so isinstance checks work
    with pikepdf.Pdf.new():
        pdf_dict = pikepdf.Dictionary(A=1, B=2, C=3, D=4, E=5)
        # Make it look indirect
        with patch.object(
            type(pdf_dict),
            "is_indirect",
            new_callable=lambda: property(lambda self: True),
        ):
            pass  # pikepdf objects can't easily be made indirect in isolation

    # Use MagicMock but patch _get_markup_etc to return known values
    pdf_dict = MagicMock()
    pdf_dict.is_indirect = True
    pdf_dict.objgen = (10, 0)
    pdf_dict.__len__.return_value = 5

    with patch.object(
        adapter,
        "_get_markup_etc",
        return_value=("Dict[5] markup (Obj 10:0) <b>Root</b>", "Dict[5] raw"),
    ):
        it = adapter.create_node(None, "Root", pdf_dict)

    markup = adapter.store[it][0]
    raw_text = adapter.store[it][2]
    assert "Dict[5]" in markup
    assert "(Obj 10:0)" in markup
    assert "Root" in markup
    assert "Dict[5]" in raw_text
    # Registry should have an entry
    assert (10, 0) in adapter.registry


def test_create_node_various_types(adapter_patched):
    adapter = adapter_patched

    # Array — patch _get_markup_etc
    pdf_arr = MagicMock()
    pdf_arr.is_indirect = False
    pdf_arr.__len__ = MagicMock()
    pdf_arr.__len__.return_value = 3
    with patch.object(
        adapter, "_get_markup_etc", return_value=("Array[3] markup", "Array[3] raw")
    ):
        it_arr = adapter.create_node(None, "MyArray", pdf_arr)
    assert "Array[3]" in adapter.store[it_arr][0]

    # Stream
    pdf_stm = MagicMock(spec=pikepdf.Stream)
    pdf_stm.is_indirect = False
    with patch.object(
        adapter, "_get_markup_etc", return_value=("Stream markup", "Stream raw")
    ):
        it_stm = adapter.create_node(None, "MyStream", pdf_stm)
    assert "Stream" in adapter.store[it_stm][0]

    # Scalar string
    with patch.object(
        adapter,
        "_get_markup_etc",
        return_value=("MyKey: HelloWorld markup", "MyKey: HelloWorld raw"),
    ):
        it_val = adapter.create_node(None, "MyKey", "HelloWorld")
    assert "HelloWorld" in adapter.store[it_val][0]
    assert "MyKey" in adapter.store[it_val][2]


def test_create_jump(adapter):
    # create_jump now takes (parent_iter, objgen_tuple, name, pdf_obj)
    objgen = (20, 0)
    pdf_obj = MagicMock()
    pdf_obj.is_indirect = True
    pdf_obj.objgen = objgen

    adapter.create_jump(None, objgen, "MyLink", pdf_obj)

    jump_it = "iter_1"
    jump_ref = adapter.store[jump_it][1]

    assert isinstance(jump_ref, JumpReference)
    assert jump_ref.objgen == (20, 0)
    assert "↪ MyLink" in adapter.store[jump_it][0]


def test_create_node_no_duplicate_registry(adapter_patched):
    """Second create_node for same objgen should not overwrite registry."""
    adapter = adapter_patched
    pdf_obj = MagicMock()
    pdf_obj.is_indirect = True
    pdf_obj.objgen = (42, 0)
    pdf_obj.__len__.return_value = 1

    with patch.object(adapter, "_get_markup_etc", return_value=("markup", "raw")):
        it1 = adapter.create_node(None, "First", pdf_obj)
        adapter.create_node(None, "Second", pdf_obj)

    # Registry should still point to first registration
    ref = adapter.registry[(42, 0)]
    assert ref.get_path() == adapter.store.get_path(it1)
