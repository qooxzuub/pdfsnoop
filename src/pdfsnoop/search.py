"""
search.py — Incremental, cancellable PDF tree search.

Two-phase search:
  Phase 1 (instant): walks only already-loaded nodes in the GTK store.
                     Results appear immediately, proportional to loaded nodes.
  Phase 2 (background): full lazy-loading walk of the entire PDF object graph
                        via GLib.idle_add, finding matches in unvisited nodes.

SearchController owns:
  - debounce timer
  - phase 1 (synchronous visible search)
  - phase 2 (GLib idle loop)
  - match list (deduped at finish)
  - cursor-relative ordering

Usage in gui.py:
    self.search = SearchController(self)

Usage in events.py:
    def on_search_changed(self, entry):
        self.app.search.start(entry.get_text())
    def on_search_cancel(self, entry):
        self.app.search.cancel()
    def on_search_next(self, entry):
        self.app.search.next_match()
    def on_search_prev(self, entry):
        self.app.search.prev_match()
"""

import pikepdf
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .pdf_utils import sort_pdf_keys, walk_one_level
from .gtk_adaptor import JumpReference

TICK_TARGET_SECONDS = 0.2
DEBOUNCE_MS = 300  # ms to wait after last keystroke before starting search


# ---------------------------------------------------------------------------
# Phase 1: visible-only store walk
# ---------------------------------------------------------------------------


def _iter_visible_nodes(store, cursor_path=None):
    """
    Walk only nodes already loaded in the GTK store, in depth-first order.
    Does not touch pikepdf at all — pure store traversal, very fast.
    Placeholder-only nodes are not descended into.
    Yields (raw_text, TreePath).
    If cursor_path is given, yields from cursor downward first, then wraps.
    """

    def _walk(it):
        while it:
            path = store.get_path(it)
            raw_text = store[it][2]
            yield raw_text, path
            # Descend only if expanded (first child is not a placeholder)
            child = store.iter_children(it)
            if child is not None and store[child][1] is not None:
                yield from _walk(child)
            it = store.iter_next(it)

    root = store.get_iter_first()
    if root is None:
        return

    if cursor_path is None:
        yield from _walk(root)
        return

    # Split into after-cursor and before-cursor, wrap around
    after = []
    before = []
    for raw_text, path in _walk(root):
        if path.compare(cursor_path) >= 0:
            after.append((raw_text, path))
        else:
            before.append((raw_text, path))
    yield from after
    yield from before


# ---------------------------------------------------------------------------
# Phase 2: full PDF object graph walk with lazy loading
# ---------------------------------------------------------------------------


def _find_existing_child(store, parent_iter, name):
    """Find a child of parent_iter whose name column (col 3) matches."""
    child = store.iter_children(parent_iter)
    while child:
        if store[child][3] == name:
            return child
        child = store.iter_next(child)
    return None


def _build_resume_stack(pdf_root, cursor_path, adapter):
    """
    Build the initial stack for iter_pdf_for_search so that traversal starts
    at cursor_path and proceeds forward, wrapping around at the end.

    For each ancestor of cursor_path, we push:
      - The siblings AFTER the child we descended through (processed next)
      - The siblings BEFORE the child we descended through (processed after wrap)

    Only works for already-expanded ancestors (those with real children in the
    store). Unexpanded ancestors fall back to root-order traversal.

    Returns a list of stack entries:
      (obj_getter, parent_TreeRowReference_or_None, name, parent_id)
    where obj_getter is a zero-arg callable returning the pikepdf object.
    """
    store = adapter.store

    if not cursor_path:
        return [(lambda p=pdf_root: p, None, "Trailer", "Trailer")]

    # Build ancestor chain: [root_path, ..., cursor_path]
    path_chain = []
    p = cursor_path.copy()
    while p.get_depth() > 0:
        path_chain.insert(0, p.copy())
        p.up()

    stack_before = []  # siblings before cursor at each level (for wrap)
    stack_after = []  # siblings after cursor at each level (immediate)
    parent_id = "Trailer"

    for depth in range(len(path_chain) - 1):
        ancestor_path = path_chain[depth]
        child_path = path_chain[depth + 1]

        ancestor_iter = store.get_iter(ancestor_path)
        ancestor_obj = store[ancestor_iter][1]

        if ancestor_obj is None or isinstance(ancestor_obj, JumpReference):
            break  # Can't reconstruct siblings from this point

        # Skip unexpanded nodes — they have a placeholder as first child
        if adapter.has_placeholder(ancestor_iter):
            break

        is_ind = getattr(ancestor_obj, "is_indirect", False)
        current_id = (
            f"{ancestor_obj.objgen[0]} {ancestor_obj.objgen[1]}"
            if is_ind
            else parent_id
        )

        child_iter = store.get_iter(child_path)
        child_name = store[child_iter][3]
        ancestor_ref = Gtk.TreeRowReference.new(store, ancestor_path)

        if isinstance(ancestor_obj, (pikepdf.Dictionary, pikepdf.Stream)):
            keys = sorted(ancestor_obj.items(), key=sort_pdf_keys)
            try:
                _add_stream_to_stack(
                    stack_before,
                    stack_after,
                    current_id,
                    child_name,
                    ancestor_obj,
                    ancestor_ref,
                    keys,
                )
            except StopIteration:
                break

        elif isinstance(ancestor_obj, pikepdf.Array):
            try:
                _add_array_to_stack(
                    stack_before,
                    stack_after,
                    current_id,
                    child_name,
                    ancestor_obj,
                    ancestor_ref,
                )
            except ValueError:
                break

        parent_id = current_id

    return _build_stack(
        stack_before, stack_after, store, cursor_path, path_chain, parent_id
    )


def _build_stack(stack_before, stack_after, store, cursor_path, path_chain, parent_id):
    # Cursor node itself
    cursor_iter = store.get_iter(cursor_path)
    cursor_obj = store[cursor_iter][1]
    cursor_name = store[cursor_iter][3]
    cursor_parent_ref = (
        Gtk.TreeRowReference.new(store, path_chain[-2]) if len(path_chain) > 1 else None
    )

    # Stack is LIFO: push in reverse of desired pop order.
    # Desired order: cursor first, then stack_after items (nearest first),
    # then stack_before items (wrap-around, furthest first).
    # stack_after and stack_before are already built reversed for correct LIFO pop.
    stack = []
    stack.extend(stack_before)  # processed last (wrap-around)
    stack.extend(stack_after)  # processed after cursor subtree
    stack.append(
        (lambda val=cursor_obj: val, cursor_parent_ref, cursor_name, parent_id)
    )
    return stack


def _add_array_to_stack(
    stack_before, stack_after, current_id, child_name, ancestor_obj, ancestor_ref
):
    idx = int(child_name.strip("[]"))
    for j in range(len(ancestor_obj) - 1, idx, -1):
        stack_after.append(
            (
                lambda arr=ancestor_obj, i=j: arr[i],
                ancestor_ref,
                f"[{j}]",
                current_id,
            )
        )
    for j in range(idx - 1, -1, -1):
        stack_before.append(
            (
                lambda arr=ancestor_obj, i=j: arr[i],
                ancestor_ref,
                f"[{j}]",
                current_id,
            )
        )


def _add_stream_to_stack(
    stack_before, stack_after, current_id, child_name, ancestor_obj, ancestor_ref, keys
):
    idx = next(i for i, (k, _) in enumerate(keys) if str(k) == child_name)
    # After: reversed so stack pops in forward order
    for k, v in reversed(keys[idx + 1 :]):
        stack_after.append((lambda val=v: val, ancestor_ref, str(k), current_id))
    # Before: reversed so stack pops in forward order
    for k, v in reversed(keys[:idx]):
        stack_before.append((lambda val=v: val, ancestor_ref, str(k), current_id))


# ---------------------------------------------------------------------------
# Phase 2 Search Helpers
# ---------------------------------------------------------------------------


def _is_visited_duplicate(
    obj, is_ind, parent_ref, name, parent_id, adapter, visited_direct
):
    """1. Basic deduplication for search traversal."""
    if is_ind:
        adapter.backlinks[obj.objgen].add((parent_id, name))
        return False
    else:
        p_str = (
            str(parent_ref.get_path()) if parent_ref and parent_ref.valid() else "None"
        )
        if (p_str, name) in visited_direct:
            return True
        visited_direct.add((p_str, name))
        return False


def _handle_root_node(
    obj, is_ind, current_id, store, visited_indirect, push_children_cb, stack
):
    """2. Process the root node, optionally pushing its children."""
    ui_node = store.get_iter_first()
    if ui_node is None:
        return None

    node_ref = Gtk.TreeRowReference.new(store, store.get_path(ui_node))

    should_push = True
    if is_ind:
        if obj.objgen in visited_indirect:
            should_push = False
        else:
            visited_indirect.add(obj.objgen)

    if should_push:
        push_children_cb(obj, node_ref, current_id, stack)

    return store[ui_node][2], node_ref


def _locate_or_create_ui_node(store, adapter, parent_ref, parent_ui, name, obj, is_ind):
    """3. Locate or create the UI node for this specific parent/key."""
    ui_node = _find_existing_child(store, parent_ui, name)

    if ui_node is not None:
        return ui_node

    # Node doesn't exist here yet — we MUST create it
    if adapter.has_placeholder(parent_ui):
        adapter.remove_placeholder(parent_ui)
        if not parent_ref.valid():
            return None
        parent_ui = store.get_iter(parent_ref.get_path())

    if is_ind and obj.objgen in adapter.registry:
        # Case: Indirect object seen before elsewhere -> Create Jump here
        adapter.create_jump(parent_ui, obj.objgen, name, obj)
        ui_node = _find_existing_child(store, parent_ui, name)
    else:
        # Case: New object or direct object -> Create Real node
        ui_node = adapter.create_node(parent_ui, name, obj)
        if is_ind and ui_node:
            # Register this as the "Master" copy for future encounters
            path = store.get_path(ui_node)
            adapter.registry[obj.objgen] = Gtk.TreeRowReference.new(store, path)

    return ui_node


def _prepare_descent(store, adapter, ui_node, obj, is_ind, visited_indirect):
    """5. Descent Logic (Determine if we should look inside this object)."""
    stored_obj = store[ui_node][1]

    # Don't descend into Jump nodes (they are leaf nodes pointing elsewhere)
    if isinstance(stored_obj, JumpReference):
        return False

    if is_ind:
        # Don't re-descend an indirect object we've already walked in this search pass
        if obj.objgen in visited_indirect:
            return False
        visited_indirect.add(obj.objgen)

        # If this master node was just lazy-loaded, ensure its children placeholders exist
        if adapter.has_placeholder(ui_node):
            adapter.remove_placeholder(ui_node)
            walk_one_level(obj, adapter, ui_node)

    return True


def _to_iter(ref, store):
    if ref is None or not ref.valid():
        return None
    return store.get_iter(ref.get_path())


def _push_children(obj, node_ref, current_id, stack):
    if not node_ref.valid():
        return
    if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
        for key, val in reversed(sorted(obj.items(), key=sort_pdf_keys)):
            stack.append((lambda v=val: v, node_ref, str(key), current_id))
    elif isinstance(obj, pikepdf.Array):
        for i in range(len(obj) - 1, -1, -1):
            stack.append(
                (lambda arr=obj, idx=i: arr[idx], node_ref, f"[{i}]", current_id)
            )


def iter_pdf_for_search(pdf_root, adapter, start_path=None):
    """
    Generator that walks the PDF object graph, starting from start_path and
    wrapping around. Populates un-visited nodes into the GTK store as it goes.
    """
    store = adapter.store
    stack = _build_resume_stack(pdf_root, start_path, adapter)
    visited_indirect = set()  # objgen tuples
    visited_direct = set()  # (parent_path_str, name)

    # -----------------------------------------------------------------------
    # Orchestrator Loop
    # -----------------------------------------------------------------------
    while stack:
        getter, parent_ref, name, parent_id = stack.pop()
        obj = getter()

        is_ind = getattr(obj, "is_indirect", False)
        current_id = f"{obj.objgen[0]} {obj.objgen[1]}" if is_ind else parent_id

        # 1. Basic deduplication for search traversal
        if _is_visited_duplicate(
            obj, is_ind, parent_ref, name, parent_id, adapter, visited_direct
        ):
            continue

        # 2. Root Handling
        if parent_ref is None:
            root_match = _handle_root_node(
                obj, is_ind, current_id, store, visited_indirect, _push_children, stack
            )
            if root_match:
                yield root_match
            continue

        parent_ui = _to_iter(parent_ref, store)
        if parent_ui is None:
            continue

        # 3. Locate or Create the UI node for THIS specific parent/key
        ui_node = _locate_or_create_ui_node(
            store, adapter, parent_ref, parent_ui, name, obj, is_ind
        )
        if ui_node is None:
            continue

        # 4. Yield the match
        node_ref = Gtk.TreeRowReference.new(store, store.get_path(ui_node))
        yield store[ui_node][2], node_ref

        # 5. Descent Logic (Should we look inside this object?)
        if _prepare_descent(store, adapter, ui_node, obj, is_ind, visited_indirect):
            _push_children(obj, node_ref, current_id, stack)


# ---------------------------------------------------------------------------
# SearchController
# ---------------------------------------------------------------------------


class SearchController:
    def __init__(self, app):
        self.app = app
        self._gen = None
        self._idle_id = None
        self._debounce_id = None
        self._text = ""
        self._cursor_path = None
        self._nodes_visited = 0
        self.matches = []  # list of TreeRowReference
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
        if not self.matches:
            return
        prev_index = self.current_index
        self.current_index = (self.current_index + 1) % len(self.matches)
        wrapped = self.current_index < prev_index
        self._jump_to_current()
        self._update_match_status(wrapped=wrapped, direction="next")

    def prev_match(self):
        if not self.matches:
            return
        prev_index = self.current_index
        self.current_index = (self.current_index - 1) % len(self.matches)
        wrapped = self.current_index > prev_index
        self._jump_to_current()
        self._update_match_status(wrapped=wrapped, direction="prev")

    def _update_match_status(self, wrapped=False, direction="next"):
        n = len(self.matches)
        i = self.current_index + 1  # 1-based for display
        wrap_msg = ""
        if wrapped:
            wrap_msg = (
                "  (wrapped to end)" if direction == "prev" else "  (wrapped to start)"
            )
        self.app.statusbar.pop(0)
        self.app.statusbar.push(0, f"Match {i} of {n} for '{self._text}'{wrap_msg}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _debounced_start(self):
        self._debounce_id = None

        _, start_iter = self.app.tree_view.get_selection().get_selected()
        self._cursor_path = self.app.store.get_path(start_iter) if start_iter else None

        # --- Phase 1: instant visible search ---
        self._run_phase1()

        # --- Phase 2: background exhaustive search ---
        self._gen = iter_pdf_for_search(
            self.app.pdf.trailer, self.app.adapter, self._cursor_path
        )
        self._nodes_visited = 0
        if self.matches:
            self.app.statusbar.push(
                0,
                f"{len(self.matches)} visible match"
                f"{'es' if len(self.matches) != 1 else ''}, searching more...",
            )
        else:
            self.app.statusbar.push(0, "Searching...")
        self._idle_id = GLib.idle_add(self._tick)
        return False

    def _run_phase1(self):
        """Synchronously search all visible (loaded) nodes. Returns immediately."""
        text = self._text
        store = self.app.store
        for raw_text, path in _iter_visible_nodes(store, self._cursor_path):
            if raw_text and text in raw_text.lower():
                ref = Gtk.TreeRowReference.new(store, path)
                self.matches.append(ref)
        if self.matches:
            self.current_index = 0
            self._jump_to_current()
            self.app.statusbar.push(
                0,
                f"Match 1 of {len(self.matches)} visible for '{self._text}', searching more...",
            )

    def _cancel_idle(self):
        if self._idle_id is not None:
            GLib.source_remove(self._idle_id)
            self._idle_id = None
        self._gen = None

    def _tick(self):
        if self._gen is None:
            return False
        text = self._text
        t_start = time.monotonic()
        exhausted = self._work_for_a_tick(t_start, text)
        if exhausted:
            self._idle_id = None
            self._gen = None
            self._finish()
            return False
        return True

    def _work_for_a_tick(self, t_start, text):
        while time.monotonic() - t_start < TICK_TARGET_SECONDS:
            try:
                raw_text, ref = next(self._gen)
            except StopIteration:
                return True  # exhausted
            self._nodes_visited += 1
            if raw_text and text in raw_text.lower():
                if ref.valid():
                    self.matches.append(ref)
                    if len(self.matches) == 1:
                        self.current_index = 0
                        self._jump_to_current()
        self._update_progress_status()
        return False

    def _update_progress_status(self):
        self.app.statusbar.pop(0)
        self.app.statusbar.push(
            0,
            f"Searching... {self._nodes_visited} nodes visited, "
            f"{len(self.matches)} matches so far",
        )

    def _finish(self):
        seen = set()
        deduped = []
        for ref in self.matches:
            if ref.valid():
                key = str(ref.get_path())
                if key not in seen:
                    seen.add(key)
                    deduped.append(ref)
        self.matches = deduped
        self.app.statusbar.pop(0)
        n = len(self.matches)
        if n == 0:
            self.app.statusbar.push(0, f"No matches for '{self._text}'")
        else:
            self.app.statusbar.push(
                0,
                f"Match 1 of {n} for '{self._text}' "
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
