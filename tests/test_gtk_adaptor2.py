import pytest
from unittest.mock import MagicMock, patch
import pikepdf
from pdfsnoop.gtk_adaptor import GtkAdapter
from pdfsnoop.pdf_utils import JumpReference


class FakeStore:
    """A minimal mock of Gtk.TreeStore behavior."""

    def __init__(self):
        self.data = {}
        self.children = {}  # parent_iter -> [child_iter, ...]
        self.counter = 0

    def append(self, parent, row):
        self.counter += 1
        new_iter = f"iter_{self.counter}"
        self.data[new_iter] = list(row)
        if parent not in self.children:
            self.children[parent] = []
        self.children[parent].append(new_iter)
        return new_iter

    def __getitem__(self, tree_iter):
        return self.data[tree_iter]

    def set_value(self, tree_iter, column, value):
        self.data[tree_iter][column] = value

    def get_path(self, tree_iter):
        return f"path_{tree_iter}"

    def get_iter_first(self):
        children = self.children.get(None, [])
        return children[0] if children else None

    def iter_children(self, tree_iter):
        children = self.children.get(tree_iter, [])
        return children[0] if children else None

    def iter_next(self, tree_iter):
        # Find which parent has this iter, return next sibling
        for parent, siblings in self.children.items():
            if tree_iter in siblings:
                idx = siblings.index(tree_iter)
                if idx + 1 < len(siblings):
                    return siblings[idx + 1]
        return None

    def remove(self, tree_iter):
        for parent, siblings in self.children.items():
            if tree_iter in siblings:
                siblings.remove(tree_iter)
                del self.data[tree_iter]
                return True
        return False


class FakeTreeRowReference:
    """Fake TreeRowReference that just holds a path string."""

    def __init__(self, store, path):
        self._path = path

    def valid(self):
        return True

    def get_path(self):
        return self._path


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def adapter(store):
    with patch(
        "pdfsnoop.gtk_adaptor.Gtk.TreeRowReference.new",
        side_effect=FakeTreeRowReference,
    ):
        yield GtkAdapter(store)


# ---------------------------------------------------------------------------
# _get_markup_etc
# ---------------------------------------------------------------------------


class TestGetMarkupEtc:
    def test_dictionary(self, adapter):
        pdf_obj = MagicMock()
        pdf_obj.is_indirect = False
        pdf_obj.__len__ = MagicMock(return_value=3)
        markup, raw = adapter._get_markup_etc(
            pdf_obj, "/Font", False, label_type="Dictionary"
        )
        assert "Dict[3]" in markup
        assert "<b>/Font</b>" in markup
        assert "Dict[3]" in raw
        assert "/Font" in raw

    def test_dictionary_indirect(self, adapter):
        pdf_obj = MagicMock()
        pdf_obj.is_indirect = True
        pdf_obj.objgen = (5, 0)
        pdf_obj.__len__ = MagicMock(return_value=2)
        markup, raw = adapter._get_markup_etc(
            pdf_obj, "/Root", True, label_type="Dictionary"
        )
        assert "Obj 5:0" in markup
        assert "Obj 5:0" in raw

    def test_array(self, adapter):
        pdf_obj = MagicMock()
        pdf_obj.is_indirect = False
        pdf_obj.__len__ = MagicMock(return_value=7)
        markup, raw = adapter._get_markup_etc(
            pdf_obj, "/Kids", False, label_type="Array"
        )
        assert "Array[7]" in markup
        assert "Array[7]" in raw

    def test_stream(self, adapter):
        pdf_obj = MagicMock(spec=pikepdf.Stream)
        pdf_obj.is_indirect = False
        markup, raw = adapter._get_markup_etc(
            pdf_obj, "/XObject", False, label_type="Stream"
        )
        assert "Stream" in markup
        assert "Stream" in raw

    def test_string(self, adapter):
        pdf_obj = MagicMock(spec=pikepdf.String)
        pdf_obj.is_indirect = False
        with patch("pdfsnoop.gtk_adaptor.format_pdf_string", return_value="Hello"):
            markup, raw = adapter._get_markup_etc(
                pdf_obj, "/Title", False, label_type="String"
            )
        assert "Hello" in markup
        assert "/Title" in raw

    def test_string_truncated(self, adapter):
        pdf_obj = MagicMock(spec=pikepdf.String)
        pdf_obj.is_indirect = False
        long_val = "x" * 80
        with patch("pdfsnoop.gtk_adaptor.format_pdf_string", return_value=long_val):
            markup, raw = adapter._get_markup_etc(
                pdf_obj, "/Author", False, label_type="String"
            )
        assert "…" in markup

    def test_scalar_fallback(self, adapter):
        pdf_obj = pikepdf.Name("/Catalog")
        markup, raw = adapter._get_markup_etc(pdf_obj, "/Type", False)
        assert "/Type" in markup
        assert "/Catalog" in markup

    def test_label_type_inferred_dict(self, adapter):
        with pikepdf.Pdf.new():
            d = pikepdf.Dictionary(A=pikepdf.Name("/B"))
            markup, raw = adapter._get_markup_etc(d, "/Test", False)
        assert "Dict[1]" in markup

    def test_label_type_inferred_array(self, adapter):
        arr = pikepdf.Array([pikepdf.Name("/A"), pikepdf.Name("/B")])
        markup, raw = adapter._get_markup_etc(arr, "/Kids", False)
        assert "Array[2]" in markup

    def test_label_type_inferred_stream(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            stream = pikepdf.Stream(pdf, b"data")
            markup, raw = adapter._get_markup_etc(stream, "/Contents", False)
        assert "Stream" in markup


# ---------------------------------------------------------------------------
# create_node
# ---------------------------------------------------------------------------


class TestCreateNode:
    def test_dictionary_node(self, adapter):
        with pikepdf.Pdf.new():
            d = pikepdf.Dictionary(A=1, B=2)
            it = adapter.create_node(None, "/Root", d)
        assert it is not None
        assert "Dict[2]" in adapter.store[it][0]
        assert adapter.store[it][3] == "/Root"

    def test_indirect_registered(self, adapter):
        pdf_obj = MagicMock()
        pdf_obj.is_indirect = True
        pdf_obj.objgen = (10, 0)
        pdf_obj.__len__ = MagicMock(return_value=1)
        with patch.object(adapter, "_get_markup_etc", return_value=("markup", "raw")):
            adapter.create_node(None, "/X", pdf_obj)
        assert (10, 0) in adapter.registry

    def test_indirect_not_duplicate_registered(self, adapter):
        pdf_obj = MagicMock()
        pdf_obj.is_indirect = True
        pdf_obj.objgen = (10, 0)
        pdf_obj.__len__ = MagicMock(return_value=1)
        with patch.object(adapter, "_get_markup_etc", return_value=("markup", "raw")):
            it1 = adapter.create_node(None, "/X", pdf_obj)
            adapter.create_node(None, "/Y", pdf_obj)
        # Registry still points to first
        assert adapter.registry[(10, 0)].get_path() == adapter.store.get_path(it1)

    def test_no_placeholder_for_empty_dict(self, adapter):
        pdf_obj = MagicMock()
        pdf_obj.is_indirect = False
        pdf_obj.__len__ = MagicMock(return_value=0)
        with patch.object(adapter, "_get_markup_etc", return_value=("markup", "raw")):
            it = adapter.create_node(None, "/D", pdf_obj)
        assert not adapter.has_placeholder(it)

    def test_scalar_no_placeholder(self, adapter):
        it = adapter.create_node(None, "/Type", pikepdf.Name("/Catalog"))
        assert not adapter.has_placeholder(it)


# ---------------------------------------------------------------------------
# create_jump
# ---------------------------------------------------------------------------


class TestCreateJump:
    def test_jump_node_created(self, adapter):
        adapter.create_jump(None, (20, 0), "/Ref", None)
        it = adapter.store.get_iter_first()
        stored = adapter.store[it][1]
        assert isinstance(stored, JumpReference)
        assert stored.objgen == (20, 0)

    def test_jump_markup_contains_name(self, adapter):
        adapter.create_jump(None, (5, 0), "/MyRef", None)
        it = adapter.store.get_iter_first()
        assert "↪ /MyRef" in adapter.store[it][0]

    def test_jump_markup_contains_objgen(self, adapter):
        adapter.create_jump(None, (5, 2), "/X", None)
        it = adapter.store.get_iter_first()
        assert "5" in adapter.store[it][0]
        assert "2" in adapter.store[it][0]

    def test_jump_raw_text(self, adapter):
        adapter.create_jump(None, (7, 0), "/Link", None)
        it = adapter.store.get_iter_first()
        assert "↪ /Link" in adapter.store[it][2]


# ---------------------------------------------------------------------------
# has_placeholder / remove_placeholder
# ---------------------------------------------------------------------------


class TestPlaceholder:
    def test_has_placeholder_false_after_remove(self, adapter):
        pdf_obj = MagicMock()
        pdf_obj.is_indirect = False
        pdf_obj.__len__ = MagicMock(return_value=2)
        with patch.object(adapter, "_get_markup_etc", return_value=("m", "r")):
            it = adapter.create_node(None, "/D", pdf_obj)
        adapter.remove_placeholder(it)
        assert not adapter.has_placeholder(it)

    def test_has_placeholder_no_children(self, adapter):
        it = adapter.create_node(None, "/Type", pikepdf.Name("/Catalog"))
        assert not adapter.has_placeholder(it)

    def test_remove_placeholder_idempotent(self, adapter):
        pdf_obj = MagicMock()
        pdf_obj.is_indirect = False
        pdf_obj.__len__ = MagicMock(return_value=1)
        with patch.object(adapter, "_get_markup_etc", return_value=("m", "r")):
            it = adapter.create_node(None, "/D", pdf_obj)
        adapter.remove_placeholder(it)
        adapter.remove_placeholder(it)  # Should not raise
        assert not adapter.has_placeholder(it)
