#!/usr/bin/env python

import sys

import gi

import pikepdf

from .pdf_utils import prepopulate_spine
from .gtk_adaptor import GtkAdapter
from .actions import ActionHandler
from .events import EventHandler
from .search import SearchController

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


class PDFSnoopGUI(Gtk.Window):
    def __init__(self, pdf_path):
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-theme-name", "Adwaita-dark")
        super().__init__(title=f"pdfsnoop - {pdf_path}")

        self.pdf_path = pdf_path
        self.set_default_size(1200, 700)

        try:
            self.pdf = pikepdf.Pdf.open(pdf_path)
        except Exception as e:
            print(f"Failed to open PDF: {e}")
            sys.exit(1)

        self.actions = ActionHandler(self)
        self.events = EventHandler(self)

        # --- MASTER LAYOUT ---
        self.main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(self.main_vbox)

        # 1. Menu Bar
        self.setup_menus()
        self.main_vbox.pack_start(self.menubar, False, False, 0)

        # 2. Paned Window (Left/Right)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_vbox.pack_start(self.paned, True, True, 0)

        # --- LEFT: TREE & SEARCH ---
        left_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.store = Gtk.TreeStore(str, object, str, str)
        self.tree_view = Gtk.TreeView(model=self.store)
        self.tree_view.set_enable_search(False)
        self.search = SearchController(self)

        self.adapter = GtkAdapter(self.store)

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("PDF Structure", renderer, markup=0)
        self.tree_view.append_column(column)

        sw_tree = Gtk.ScrolledWindow()
        sw_tree.add(self.tree_view)
        left_vbox.pack_start(sw_tree, True, True, 0)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry()
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.add(self.search_entry)
        left_vbox.pack_end(self.search_bar, False, False, 0)

        self.paned.pack1(left_vbox, True, False)
        self.paned.set_position(400)

        # --- RIGHT: DETAILS & CONTENT ---
        right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        b = Gtk.Label(label="<b>Path:</b> /", use_markup=True)
        b.set_halign(Gtk.Align.START)
        b.set_margin_top(6)
        b.set_margin_bottom(6)
        b.set_margin_start(6)
        right_vbox.pack_start(b, False, False, 0)
        self.breadcrumb_label = b

        self.right_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        right_vbox.pack_start(self.right_paned, True, True, 0)

        m = Gtk.TextView()
        m.set_editable(False)
        m.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.metadata_view = m

        sw_meta = Gtk.ScrolledWindow()
        sw_meta.add(self.metadata_view)
        self.right_paned.pack1(sw_meta, False, False)
        self.right_paned.set_position(100)  # Or whatever height you prefer

        # Content Stack (Swaps between Text and Image)
        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # 1. Text View (Page)
        self.content_view = Gtk.TextView()
        self.content_view.set_editable(False)
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"textview { font: 10pt monospace; }")
        self.content_view.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        sw_content = Gtk.ScrolledWindow()
        sw_content.add(self.content_view)
        self.content_stack.add_named(sw_content, "text")

        # 2. Image View (Page)
        self.image_view = Gtk.Image()
        sw_image = Gtk.ScrolledWindow()
        sw_image.add(self.image_view)
        self.content_stack.add_named(sw_image, "image")

        self.right_paned.pack2(self.content_stack, True, False)
        self.content_stack.show_all()
        self.paned.pack2(right_vbox, True, True)

        # Search State
        self.search_matches = []
        self.current_match_index = -1

        # Connections
        self.tree_view.get_selection().connect(
            "changed", self.events.on_selection_changed
        )
        self.tree_view.connect("key-press-event", self.events.on_tree_key_press)
        self.tree_view.connect("button-press-event", self.events.on_tree_right_click)
        self.tree_view.connect("row-activated", self.events.on_tree_row_activated)
        self.tree_view.connect("row-expanded", self.events.on_row_expanded)

        self.content_view.connect("move-cursor", self.events.on_stream_cursor_moved)

        self.search_entry.connect("search-changed", self.events.on_search_changed)
        self.search_entry.connect("next-match", self.events.on_search_next)
        self.search_entry.connect("previous-match", self.events.on_search_prev)
        self.search_entry.connect("stop-search", self.events.on_search_cancel)
        self.connect("destroy", Gtk.main_quit)

        self.populate_ui_tree()

        self.statusbar = Gtk.Statusbar()
        # pack_end puts it at the very bottom of the window
        self.main_vbox.pack_end(self.statusbar, False, False, 0)

        self.show_all()

        self.actions.expand_to_pages()

    # ==========================================
    # MENU & ACTION SETUP
    # ==========================================
    def setup_menus(self):
        """Builds both the top MenuBar and the Context Menu."""
        self.menubar = Gtk.MenuBar()
        self.context_menu = Gtk.Menu()

        def append_menuitems(items, parent):
            for item in items:
                item.set_use_underline(True)
                parent.append(item)

        # File Menu (Top Bar only)
        file_menu = Gtk.Menu()
        file_item = Gtk.MenuItem(label="_File")
        file_item.set_submenu(file_menu)
        append_menuitems([file_item], self.menubar)

        item_save = Gtk.MenuItem(label="_Save PDF As... (w)")
        item_save.connect("activate", self.actions.action_save_pdf)
        item_quit = Gtk.MenuItem(label="E_xit (Ctrl+q)")
        item_quit.connect("activate", Gtk.main_quit)
        append_menuitems([item_save, item_quit], file_menu)

        # View Menu (Top Bar only)
        view_menu = Gtk.Menu()
        view_item = Gtk.MenuItem(label="_View")
        view_item.set_submenu(view_menu)
        append_menuitems([view_item], self.menubar)

        # View Toggles
        self.item_disassemble = Gtk.CheckMenuItem(label="_Disassemble content streams")
        self.item_disassemble.set_active(True)  # Default to ON
        self.item_disassemble.connect(
            "toggled", self.actions.action_checkbox_toggle_and_refresh
        )
        self.item_preview_images = Gtk.CheckMenuItem(label="_Image previews")
        self.item_preview_images.set_active(True)  # Default to ON
        self.item_preview_images.connect(
            "toggled", self.actions.action_checkbox_toggle_and_refresh
        )
        self.item_preview_pages = Gtk.CheckMenuItem(label="_Page previews")
        self.item_preview_pages.set_active(True)  # Default to ON
        self.item_preview_pages.connect(
            "toggled", self.actions.action_checkbox_toggle_and_refresh
        )

        append_menuitems(
            [
                self.item_disassemble,
                self.item_preview_images,
                self.item_preview_pages,
            ],
            view_menu,
        )

        # Action Menu (Shared between Top Bar and Context Menu)
        action_menu = Gtk.Menu()
        action_item = Gtk.MenuItem(label="_Actions")
        action_item.set_submenu(action_menu)
        append_menuitems([action_item], self.menubar)

        # Define actions tuple: (Label, Handler)
        actions = [
            ("_Edit Stream / Value (e)", self.actions.action_edit),
            ("E_xtract Stream / Image (s)", self.actions.action_extract),
            ("_Normalize Stream (f)", self.actions.action_normalize),
            ("_Delete Node (Del)", self.actions.action_delete),
            ("_Jump to Page (g)", self.actions.action_jump_page),
        ]

        # Populate both menus
        for label, handler in actions:
            # Top Menu
            top_mi = Gtk.MenuItem(label=label)
            top_mi.connect("activate", handler)
            append_menuitems([top_mi], action_menu)
            # Context Menu
            ctx_mi = Gtk.MenuItem(label=label)
            ctx_mi.connect("activate", handler)
            append_menuitems([ctx_mi], self.context_menu)

        # Required so the context menu items are visible when popped up
        self.context_menu.show_all()

    @property
    def disassemble_mode(self):
        """Helper to check the menu state from other parts of the app."""
        return self.item_disassemble.get_active()

    @property
    def preview_images_mode(self):
        """Helper to check the menu state from other parts of the app."""
        return self.item_preview_images.get_active()

    @property
    def preview_pages_mode(self):
        """Helper to check the menu state from other parts of the app."""
        return self.item_preview_pages.get_active()

    def populate_ui_tree(self):
        self.store.clear()
        self.adapter.registry.clear()  # Reset registry for new load
        prepopulate_spine(self.pdf, self.adapter)

    def navigate_to_objgen(self, objgen):
        """Universal helper to safely navigate the tree to a specific PDF object ID, handling lazy-loading."""
        from gi.repository import Gtk

        target_ref = self.adapter.registry.get(objgen)
        if not target_ref or not target_ref.valid():
            return False

        # 1. Expand top-down, one level at a time to trigger lazy-loading safely
        path = target_ref.get_path()
        for i in range(1, path.get_depth()):
            # Re-fetch path because lazy-loading shifts sibling indices!
            current_path = target_ref.get_path()
            if not current_path:
                break
            ancestor_path = Gtk.TreePath.new_from_indices(
                current_path.get_indices()[:i]
            )
            self.tree_view.expand_row(ancestor_path, False)

        # 2. Get the final stable path after all lazy-loads have resolved
        final_path = target_ref.get_path()
        if final_path:
            self.tree_view.set_cursor(final_path, None, False)
            self.tree_view.scroll_to_cell(final_path, None, True, 0.5, 0.5)
            self.tree_view.grab_focus()
            return True

        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m src.pdfsnoop.gui <file.pdf>")
        sys.exit(1)

    PDFSnoopGUI(sys.argv[1])
    Gtk.main()


if __name__ == "__main__":
    main()
