"""
search.py — Incremental, cancellable PDF tree search.

SearchController owns:
  - debounce timer
  - GLib idle loop
  - match list + cursor-relative sorting
  - the iter_pdf_for_search generator

Usage in gui.py:
    self.search = SearchController(self)
"""

import pikepdf

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .pdf_utils import sort_pdf_keys
from .gtk_adaptor import JumpReference


CHUNK_SIZE = 500  # PDF objects processed per idle tick
DEBOUNCE_MS = 300  # ms to wait after last keystroke before starting search


def _find_existing_child(store, parent_iter, name):
    """Find a child of parent_iter whose name column (col 3) matches."""
    child = store.iter_children(parent_iter)
    while child:
        if store[child][3] == name:
            return child
        child = store.iter_next(child)
    return None


def build_search_stack(pdf_root, cursor_path, adapter):
    """
    Constructs a LIFO search stack that starts at the cursor, finishes the
    subsequent siblings/children, and then perfectly wraps around to the
    top of the document to process prior siblings.
    """
    store = adapter.store

    if not cursor_path:
        return [(lambda p=pdf_root: p, None, "Trailer", "Trailer")]

    path_chain = []
    p = cursor_path.copy()
    while p.get_depth() > 0:
        path_chain.insert(0, p.copy())
        p.up()

    stack = []
    all_priors = []
    all_subsequents = []
    parent_id = "Trailer"

    for i in range(len(path_chain) - 1):
        path = path_chain[i]
        next_path = path_chain[i + 1]

        iter_ = store.get_iter(path)
        obj = store[iter_][1]

        is_ind = getattr(obj, "is_indirect", False)
        current_id = f"{obj.objgen[0]} {obj.objgen[1]}" if is_ind else parent_id

        next_iter = store.get_iter(next_path)
        next_name = store[next_iter][3]
        node_ref = Gtk.TreeRowReference.new(store, path)

        priors = []
        subsequents = []

        # Split siblings. Dictionaries are small enough to evaluate upfront.
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            keys = sorted(obj.items(), key=sort_pdf_keys)
            try:
                idx = next(
                    idx for idx, (k, v) in enumerate(keys) if str(k) == next_name
                )
                for k, v in reversed(keys[:idx]):
                    priors.append((lambda val=v: val, node_ref, str(k), current_id))
                for k, v in reversed(keys[idx + 1 :]):
                    subsequents.append(
                        (lambda val=v: val, node_ref, str(k), current_id)
                    )
            except StopIteration:
                pass
        # Arrays can be massive. Defer evaluation via lambda.
        elif isinstance(obj, pikepdf.Array):
            try:
                idx = int(next_name[1:-1])
                for j in range(idx - 1, -1, -1):
                    priors.append(
                        (lambda arr=obj, i=j: arr[i], node_ref, f"[{j}]", current_id)
                    )
                for j in range(len(obj) - 1, idx, -1):
                    subsequents.append(
                        (lambda arr=obj, i=j: arr[i], node_ref, f"[{j}]", current_id)
                    )
            except ValueError:
                pass

        all_priors.append(priors)
        all_subsequents.append(subsequents)
        parent_id = current_id

    for priors in reversed(all_priors):
        stack.extend(priors)
    for subsequents in all_subsequents:
        stack.extend(subsequents)

    cursor_path_obj = path_chain[-1]
    cursor_iter = store.get_iter(cursor_path_obj)
    cursor_obj = store[cursor_iter][1]
    cursor_name = store[cursor_iter][3]

    if len(path_chain) > 1:
        cursor_parent_ref = Gtk.TreeRowReference.new(store, path_chain[-2])
    else:
        cursor_parent_ref = None

    stack.append(
        (lambda val=cursor_obj: val, cursor_parent_ref, cursor_name, parent_id)
    )
    return stack


def iter_pdf_for_search(pdf_root, adapter, start_path=None):
    store = adapter.store

    def to_iter(ref):
        if ref is None or not ref.valid():
            return None
        return store.get_iter(ref.get_path())

    def push_children(obj, node_ref, current_id):
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            for key, val in reversed(sorted(obj.items(), key=sort_pdf_keys)):
                stack.append((lambda v=val: v, node_ref, str(key), current_id))
        elif isinstance(obj, pikepdf.Array):
            for i in range(len(obj) - 1, -1, -1):
                # Lazily fetch the array element when popped
                stack.append(
                    (lambda arr=obj, idx=i: arr[idx], node_ref, f"[{i}]", current_id)
                )

    stack = build_search_stack(pdf_root, start_path, adapter)
    visited_direct = set()

    while stack:
        # Execute the closure to get the actual pikepdf object only when needed
        getter, parent_ref, name, parent_id = stack.pop()
        obj = getter()

        is_ind = getattr(obj, "is_indirect", False)
        current_id = f"{obj.objgen[0]} {obj.objgen[1]}" if is_ind else parent_id

        if is_ind:
            adapter.backlinks[obj.objgen].add((parent_id, name))
        else:
            p_path_str = (
                str(parent_ref.get_path())
                if parent_ref and parent_ref.valid()
                else "None"
            )
            direct_key = (p_path_str, name)
            if direct_key in visited_direct:
                continue
            visited_direct.add(direct_key)

        if parent_ref is None:
            ui_node = store.get_iter_first()
            if ui_node is None:
                continue
            node_ref = Gtk.TreeRowReference.new(store, store.get_path(ui_node))
            yield store[ui_node][2], node_ref
            push_children(obj, node_ref, current_id)
            continue

        parent_ui = to_iter(parent_ref)
        if parent_ui is None:
            continue

        if adapter.has_placeholder(parent_ui):
            adapter.remove_placeholder(parent_ui)

        ui_node = _find_existing_child(store, parent_ui, name)

        is_jump = False
        if ui_node is not None:
            stored_obj = store[ui_node][1]
            if isinstance(stored_obj, JumpReference):
                is_jump = True
        else:
            if is_ind and obj.objgen in adapter.registry:
                ui_node = adapter.create_jump(parent_ui, obj.objgen, name, obj)
                is_jump = True
            else:
                ui_node = adapter.create_node(parent_ui, name, obj)
                if is_ind:
                    path = store.get_path(ui_node)
                    adapter.registry[obj.objgen] = Gtk.TreeRowReference.new(store, path)

        if ui_node is None:
            continue

        node_ref = Gtk.TreeRowReference.new(store, store.get_path(ui_node))
        yield store[ui_node][2], node_ref

        if is_jump:
            continue

        push_children(obj, node_ref, current_id)


class SearchController:
    def __init__(self, app):
        self.app = app
        self._gen = None
        self._idle_id = None
        self._debounce_id = None
        self._text = ""
        self._cursor_path = None
        self._nodes_visited = 0
        self.matches = []
        self.current_index = -1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, text):
        """Called on every search-changed event. Debounces, then kicks off search."""
        self._cancel_idle()
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None

        self._text = text.strip().lower()
        self.matches = []
        self.current_index = -1

        if not self._text:
            self.app.statusbar.pop(0)
            return

        self._debounce_id = GLib.timeout_add(DEBOUNCE_MS, self._debounced_start)

    def cancel(self):
        """Stop any running search and clear state."""
        self._cancel_idle()
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None
        self.matches = []
        self.current_index = -1
        self.app.statusbar.pop(0)
        self.app.search_bar.set_search_mode(False)
        self.app.tree_view.grab_focus()

    def next_match(self):
        if self.matches:
            self.current_index = (self.current_index + 1) % len(self.matches)
            self._jump_to_current()

    def prev_match(self):
        if self.matches:
            self.current_index = (self.current_index - 1) % len(self.matches)
            self._jump_to_current()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _debounced_start(self):
        self._debounce_id = None

        # Snapshot cursor position before search starts
        _, start_iter = self.app.tree_view.get_selection().get_selected()
        self._cursor_path = self.app.store.get_path(start_iter) if start_iter else None

        # Seed generator with cursor path
        self._gen = iter_pdf_for_search(
            self.app.pdf.trailer, self.app.adapter, self._cursor_path
        )
        self._nodes_visited = 0
        self.app.statusbar.push(0, "Searching...")
        self._idle_id = GLib.idle_add(self._tick)
        return False  # don't repeat timeout

    def _cancel_idle(self):
        if self._idle_id is not None:
            GLib.source_remove(self._idle_id)
            self._idle_id = None
        self._gen = None

    def _tick(self):
        """Process CHUNK_SIZE objects, yield back to GTK, return True to continue."""
        if self._gen is None:
            return False

        text = self._text
        try:
            for _ in range(CHUNK_SIZE):
                raw_text, path_ref = next(self._gen)
                self._nodes_visited += 1
                if raw_text and text in raw_text.lower():
                    self.matches.append(path_ref)

                    # Jump instantly on the very first match found
                    if len(self.matches) == 1:
                        self.current_index = 0
                        self._jump_to_current()

            self.app.statusbar.pop(0)
            self.app.statusbar.push(
                0,
                f"Searching... {self._nodes_visited} nodes visited, "
                f"{len(self.matches)} matches so far",
            )
            return True  # more to do

        except StopIteration:
            self._idle_id = None
            self._gen = None
            self._finish()
            return False  # done

    def _finish(self):
        # We no longer need to sort matches here because the traversal stack
        # naturally yielded them in perfect wrapped document-order from the cursor!
        self.matches = [ref for ref in self.matches if ref.valid()]

        self.app.statusbar.pop(0)
        n = len(self.matches)
        if n == 0:
            self.app.statusbar.push(0, f"No matches for '{self._text}'")
        else:
            self.app.statusbar.push(
                0,
                f"{n} match{'es' if n != 1 else ''} for '{self._text}' "
                f"({self._nodes_visited} nodes searched)",
            )

    def _jump_to_current(self):
        if not self.matches or self.current_index < 0:
            return

        ref = self.matches[self.current_index]
        if not ref.valid():
            return

        path = ref.get_path()
        self.app.tree_view.expand_to_path(path)
        self.app.tree_view.set_cursor(path, None, False)
        self.app.tree_view.scroll_to_cell(path, None, True, 0.5, 0.0)
