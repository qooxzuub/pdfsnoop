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
        self.pdf = None
        self.set_default_size(1200, 700)

        self.accel_group = Gtk.AccelGroup()
        self.add_accel_group(self.accel_group)

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

        # Edit Bar (hidden by default)
        self.edit_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.edit_bar.set_margin_start(4)
        self.edit_bar.set_margin_end(4)
        self.edit_bar.set_margin_top(2)
        self.edit_bar.set_margin_bottom(2)

        self.edit_type_combo = Gtk.ComboBoxText()
        for t in ("String", "Name", "Integer", "Real", "Boolean", "Indirect Ref"):
            self.edit_type_combo.append_text(t)

        self.edit_entry = Gtk.Entry()
        self.edit_entry.set_hexpand(True)

        edit_save_btn = Gtk.Button(label="Save")
        edit_cancel_btn = Gtk.Button(label="Cancel")

        self.edit_bar.pack_start(self.edit_type_combo, False, False, 0)
        self.edit_bar.pack_start(self.edit_entry, True, True, 0)
        self.edit_bar.pack_start(edit_save_btn, False, False, 0)
        self.edit_bar.pack_start(edit_cancel_btn, False, False, 0)

        left_vbox.pack_end(self.edit_bar, False, False, 0)
        left_vbox.pack_end(self.search_bar, False, False, 0)

        # Connections
        self.edit_entry.connect("activate", self.actions.action_commit_edit)
        self.edit_entry.connect("key-press-event", self.events.on_edit_entry_key_press)
        edit_save_btn.connect("clicked", self.actions.action_commit_edit)
        edit_cancel_btn.connect("clicked", self.actions.action_cancel_edit)

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

        self.statusbar = Gtk.Statusbar()
        # pack_end puts it at the very bottom of the window
        self.main_vbox.pack_end(self.statusbar, False, False, 0)

        self.show_all()
        self.edit_bar.hide()

        if pdf_path:
            self.load_new_pdf(pdf_path)

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

        def add_accel(menu_item, keypress_str):
            key, mod = Gtk.accelerator_parse(keypress_str)
            menu_item.add_accelerator(
                "activate", self.accel_group, key, mod, Gtk.AccelFlags.VISIBLE
            )

        # File Menu (Top Bar only)
        file_menu = Gtk.Menu()
        file_item = Gtk.MenuItem(label="_File")
        file_item.set_submenu(file_menu)
        append_menuitems([file_item], self.menubar)

        item_open = Gtk.MenuItem(label="_Open PDF...")
        item_open.connect("activate", self.actions.action_open)
        add_accel(item_open, "<Control>o")

        item_revert = Gtk.MenuItem(label="Re_vert")
        item_revert.connect("activate", self.actions.action_revert)

        item_save = Gtk.MenuItem(label="_Save PDF As... (w)")
        item_save.connect("activate", self.actions.action_save_pdf)

        item_quit = Gtk.MenuItem(label="E_xit (Ctrl+q)")
        item_quit.connect("activate", Gtk.main_quit)
        add_accel(item_quit, "<Control>q")

        append_menuitems([item_open, item_revert, item_save, item_quit], file_menu)

        edit_menu = Gtk.Menu()
        edit_item = Gtk.MenuItem(label="_Edit")
        edit_item.set_submenu(edit_menu)
        # Assuming you want it between File and View
        append_menuitems([edit_item], self.menubar)

        item_copy = Gtk.MenuItem(label="_Copy Value")
        item_copy.connect("activate", self.actions.action_copy)
        add_accel(item_copy, "<Control>c")

        append_menuitems([item_copy], edit_menu)

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
        add_accel(self.item_disassemble, "<Alt>1")
        self.item_preview_images = Gtk.CheckMenuItem(label="_Image previews")
        self.item_preview_images.set_active(True)  # Default to ON
        self.item_preview_images.connect(
            "toggled", self.actions.action_checkbox_toggle_and_refresh
        )
        add_accel(self.item_preview_images, "<Alt>2")
        self.item_preview_pages = Gtk.CheckMenuItem(label="_Page previews")
        self.item_preview_pages.set_active(True)  # Default to ON
        self.item_preview_pages.connect(
            "toggled", self.actions.action_checkbox_toggle_and_refresh
        )
        add_accel(self.item_preview_pages, "<Alt>3")

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
            ("_Copy Value", self.actions.action_copy),
            ("-", None),
            ("_Edit Stream / Value (e)", self.actions.action_edit),
            ("E_xtract Stream / Image (s)", self.actions.action_extract),
            ("_Normalize Stream (f)", self.actions.action_normalize),
            ("_Delete Node (Del)", self.actions.action_delete),
            ("_Jump to Page (g)", self.actions.action_jump_page),
        ]

        # Help Menu
        help_menu = Gtk.Menu()
        help_item = Gtk.MenuItem(label="_Help")
        help_item.set_submenu(help_menu)
        append_menuitems([help_item], self.menubar)

        # Documentation Item
        item_docs = Gtk.MenuItem(label="_Documentation")
        item_docs.connect("activate", self.actions.action_show_docs)
        add_accel(item_docs, "F1")

        # About Item
        item_about = Gtk.MenuItem(label="_About")
        item_about.connect("activate", self.actions.action_show_about)

        append_menuitems([item_docs, item_about], help_menu)

        # Populate both menus
        for label, handler in actions:
            if handler is None:
                self.context_menu.append(Gtk.SeparatorMenuItem())
                continue
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

    def load_new_pdf(self, file_path):
        """Shared helper to safely swap out the active pikepdf instance and reset the UI."""
        try:
            new_pdf = pikepdf.Pdf.open(file_path)
        except Exception as e:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Failed to open PDF",
            )
            dialog.format_secondary_text(f"Could not open '{file_path}':\n\n{e}")
            dialog.run()
            dialog.destroy()
            return

        self.store.clear()
        # Safely close the old file handle before swapping
        if hasattr(self, "pdf") and self.pdf:
            self.pdf.close()

        self.pdf_path = file_path
        self.pdf = new_pdf
        self.events.reset_poppler()

        # Update UI state
        self.set_title(f"pdfsnoop - {self.pdf_path}")
        self.populate_ui_tree()

        # Clear out the right-side panes
        self.breadcrumb_label.set_markup("<b>Path:</b> /")
        self.metadata_view.get_buffer().set_text("")
        self.content_view.get_buffer().set_text("")
        self.image_view.clear()
        self.edit_bar.hide()

        # Re-trigger your initial expansion state
        self.actions.expand_to_pages()

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
    PDFSnoopGUI(sys.argv[1] if len(sys.argv) > 1 else None)
    Gtk.main()


if __name__ == "__main__":
    main()
