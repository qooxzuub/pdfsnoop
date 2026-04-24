import pytest
from unittest.mock import MagicMock
import pikepdf
from pdfsnoop.pdf_utils import (
    walk_one_level,
    _create_child,
    prepopulate_spine,
    _force_expand,
    _find_child_by_name,
    sort_pdf_keys,
    JumpReference,
)


# ---------------------------------------------------------------------------
# Minimal fake adapter for pdf_utils tests (no GTK needed)
# ---------------------------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.data = {}
        self.children = {}
        self.counter = 0

    def append(self, parent, row):
        self.counter += 1
        it = f"iter_{self.counter}"
        self.data[it] = list(row)
        self.children.setdefault(parent, []).append(it)
        return it

    def __getitem__(self, it):
        return self.data[it]

    def set_value(self, it, col, val):
        self.data[it][col] = val

    def get_path(self, it):
        return f"path_{it}"

    def iter_children(self, it):
        ch = self.children.get(it, [])
        return ch[0] if ch else None

    def iter_next(self, it):
        for siblings in self.children.values():
            if it in siblings:
                idx = siblings.index(it)
                return siblings[idx + 1] if idx + 1 < len(siblings) else None
        return None

    def remove(self, it):
        for siblings in self.children.values():
            if it in siblings:
                siblings.remove(it)
                del self.data[it]
                return True
        return False

    def get_iter_first(self):
        ch = self.children.get(None, [])
        return ch[0] if ch else None


class FakeAdapter:
    """Minimal adapter that tracks calls without needing GTK."""

    def __init__(self):
        self.store = FakeStore()
        self.registry = {}
        self.backlinks = {}
        self.nodes_created = []
        self.jumps_created = []

    def create_node(self, parent_iter, name, pdf_obj):
        it = self.store.append(parent_iter, ["markup", pdf_obj, "raw", name])
        self.nodes_created.append((name, pdf_obj))
        is_ind = getattr(pdf_obj, "is_indirect", False)
        if is_ind and pdf_obj.objgen not in self.registry:
            self.registry[pdf_obj.objgen] = it
        # Add placeholder for containers
        if isinstance(pdf_obj, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
            if isinstance(pdf_obj, pikepdf.Stream) or len(pdf_obj) > 0:
                self.store.append(it, ["placeholder", None, "", ""])
        return it

    def create_jump(self, parent_iter, objgen, name, pdf_obj):
        it = self.store.append(
            parent_iter, ["jump markup", JumpReference(objgen), "jump raw", name]
        )
        self.jumps_created.append((name, objgen))
        return it

    def has_placeholder(self, it):
        child = self.store.iter_children(it)
        if child is None:
            return False
        return self.store[child][1] is None

    def remove_placeholder(self, it):
        child = self.store.iter_children(it)
        if child is not None and self.store[child][1] is None:
            self.store.remove(child)


@pytest.fixture
def adapter():
    return FakeAdapter()


# ---------------------------------------------------------------------------
# sort_pdf_keys
# ---------------------------------------------------------------------------


class TestSortPdfKeys:
    def test_type_first(self):
        items = [
            ("/Kids", MagicMock(spec=pikepdf.Array)),
            ("/Type", pikepdf.Name("/Pages")),
            ("/Count", pikepdf.Integer(5)),
        ]
        sorted_items = sorted(items, key=sort_pdf_keys)
        assert sorted_items[0][0] == "/Type"

    def test_root_pages_kids_second(self):
        items = [
            ("/MediaBox", pikepdf.Array()),
            ("/Pages", MagicMock(spec=pikepdf.Dictionary)),
            ("/Type", pikepdf.Name("/Catalog")),
        ]
        sorted_items = sorted(items, key=sort_pdf_keys)
        assert sorted_items[0][0] == "/Type"
        assert sorted_items[1][0] == "/Pages"

    def test_scalars_before_containers(self):
        items = [
            ("/Font", MagicMock(spec=pikepdf.Dictionary)),
            ("/Count", pikepdf.Integer(10)),
        ]
        sorted_items = sorted(items, key=sort_pdf_keys)
        assert sorted_items[0][0] == "/Count"
        assert sorted_items[1][0] == "/Font"


# ---------------------------------------------------------------------------
# walk_one_level
# ---------------------------------------------------------------------------


class TestWalkOneLevel:
    def test_walks_dictionary_keys(self, adapter):
        with pikepdf.Pdf.new():
            d = pikepdf.Dictionary(
                Type=pikepdf.Name("/Page"),
                MediaBox=pikepdf.Array([0, 0, 612, 792]),
            )
            parent_it = adapter.store.append(None, ["parent", d, "raw", "Trailer"])
            walk_one_level(d, adapter, parent_it)

        names = [n for n, _ in adapter.nodes_created]
        assert "/Type" in names
        assert "/MediaBox" in names

    def test_walks_array_elements(self, adapter):
        arr = pikepdf.Array(
            [pikepdf.Name("/A"), pikepdf.Name("/B"), pikepdf.Name("/C")]
        )
        parent_it = adapter.store.append(None, ["parent", arr, "raw", "/Kids"])
        walk_one_level(arr, adapter, parent_it)
        names = [n for n, _ in adapter.nodes_created]
        assert "[0]" in names
        assert "[1]" in names
        assert "[2]" in names

    def test_creates_jump_for_registered_indirect(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            # Simulate an indirect object already in registry
            page = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Page")))
            adapter.registry[page.objgen] = "existing_iter"
            d = pikepdf.Dictionary(Page=page)
            parent_it = adapter.store.append(None, ["parent", d, "raw", "Parent"])
            walk_one_level(d, adapter, parent_it)

        assert len(adapter.jumps_created) == 1
        assert adapter.jumps_created[0][1] == page.objgen

    def test_empty_dict_creates_no_children(self, adapter):
        d = pikepdf.Dictionary()
        parent_it = adapter.store.append(None, ["parent", d, "raw", "Empty"])
        walk_one_level(d, adapter, parent_it)
        assert adapter.nodes_created == []
        assert adapter.jumps_created == []

    def test_stream_walks_dict_part(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            stream = pikepdf.Stream(pdf, b"data", Type=pikepdf.Name("/XObject"))
            parent_it = adapter.store.append(None, ["parent", stream, "raw", "/S"])
            walk_one_level(stream, adapter, parent_it)
        names = [n for n, _ in adapter.nodes_created]
        assert "/Type" in names


# ---------------------------------------------------------------------------
# _create_child
# ---------------------------------------------------------------------------


class TestCreateChild:
    def test_direct_object_creates_node(self, adapter):
        val = pikepdf.Name("/Catalog")
        parent_it = adapter.store.append(None, ["p", val, "r", "p"])
        _create_child("/Type", val, adapter, parent_it)
        assert len(adapter.nodes_created) == 1
        assert adapter.nodes_created[0][0] == "/Type"

    def test_unregistered_indirect_creates_node(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            obj = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Page")))
            parent_it = adapter.store.append(None, ["p", obj, "r", "p"])
            _create_child("/Page", obj, adapter, parent_it)
        assert len(adapter.nodes_created) == 1

    def test_registered_indirect_creates_jump(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            obj = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Page")))
            adapter.registry[obj.objgen] = "some_iter"
            parent_it = adapter.store.append(None, ["p", obj, "r", "p"])
            _create_child("/Ref", obj, adapter, parent_it)
        assert len(adapter.jumps_created) == 1
        assert len(adapter.nodes_created) == 0


# ---------------------------------------------------------------------------
# _find_child_by_name
# ---------------------------------------------------------------------------


class TestFindChildByName:
    def test_finds_existing_child(self, adapter):
        parent_it = adapter.store.append(None, ["p", None, "r", "Parent"])
        child_it = adapter.store.append(parent_it, ["c", None, "r", "/Font"])
        result = _find_child_by_name(adapter, parent_it, "/Font")
        assert result == child_it

    def test_returns_none_when_not_found(self, adapter):
        parent_it = adapter.store.append(None, ["p", None, "r", "Parent"])
        adapter.store.append(parent_it, ["c", None, "r", "/Font"])
        result = _find_child_by_name(adapter, parent_it, "/Missing")
        assert result is None

    def test_returns_none_for_no_children(self, adapter):
        parent_it = adapter.store.append(None, ["p", None, "r", "Parent"])
        result = _find_child_by_name(adapter, parent_it, "/Font")
        assert result is None


# ---------------------------------------------------------------------------
# prepopulate_spine
# ---------------------------------------------------------------------------


class TestPrepopulateSpine:
    def test_creates_trailer_node(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            prepopulate_spine(pdf, adapter)
        names = [n for n, _ in adapter.nodes_created]
        assert "Trailer" in names

    def test_creates_root_children(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            # pikepdf.Pdf.new() creates a minimal PDF with /Root
            prepopulate_spine(pdf, adapter)
        names = [n for n, _ in adapter.nodes_created]
        assert "/Root" in names

    def test_pages_nodes_registered(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            # Add a page so there's something in /Pages
            page = pikepdf.Dictionary(
                Type=pikepdf.Name("/Page"),
                MediaBox=pikepdf.Array([0, 0, 612, 792]),
            )
            pdf.pages.append(pikepdf.Page(page))
            prepopulate_spine(pdf, adapter)
        # All page objects should be in registry
        for page in pdf.pages:
            assert page.objgen in adapter.registry

    def test_no_root_is_safe(self, adapter):
        with pikepdf.Pdf.new() as pdf:
            # Remove /Root from trailer to test graceful handling
            del pdf.trailer["/Root"]
            prepopulate_spine(pdf, adapter)  # Should not raise
        # Only Trailer node created
        names = [n for n, _ in adapter.nodes_created]
        assert "Trailer" in names
        assert "/Root" not in names


# ---------------------------------------------------------------------------
# _force_expand
# ---------------------------------------------------------------------------


class TestForceExpand:
    def test_removes_placeholder_and_populates(self, adapter):
        with pikepdf.Pdf.new():
            d = pikepdf.Dictionary(Type=pikepdf.Name("/Catalog"))
            node_it = adapter.store.append(None, ["node", d, "raw", "/Root"])
            # Add placeholder
            adapter.store.append(node_it, ["placeholder", None, "", ""])
            assert adapter.has_placeholder(node_it)

            _force_expand(d, adapter, node_it)

            assert not adapter.has_placeholder(node_it)
            names = [n for n, _ in adapter.nodes_created]
            assert "/Type" in names

    def test_no_placeholder_does_nothing(self, adapter):
        with pikepdf.Pdf.new():
            d = pikepdf.Dictionary(Type=pikepdf.Name("/Catalog"))
            node_it = adapter.store.append(None, ["node", d, "raw", "/Root"])
            # No placeholder added
            _force_expand(d, adapter, node_it)
            # Should not have added children since there was no placeholder
            assert adapter.nodes_created == []
