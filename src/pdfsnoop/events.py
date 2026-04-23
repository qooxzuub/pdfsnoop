import gi
import cairo
from pikepdf.models import PdfImage

gi.require_version("Poppler", "0.18")
from gi.repository import Gdk, GLib, Poppler, GdkPixbuf
import os
import io

from .pdf_utils import (
    is_content_stream,
    disassemble_content_stream,
    is_page_with_index,
    is_font_with_page,
    is_annotation_with_page,
    is_link_with_page,
    JumpReference,
)
from .pdf_operators import ops

import pikepdf

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


class EventHandler:
    def __init__(self, app):
        self.app = app
        absolute_path = os.path.abspath(os.path.expanduser(self.app.pdf_path))
        uri = GLib.filename_to_uri(absolute_path, None)
        self.poppler_doc = Poppler.Document.new_from_file(uri, None)

    def on_tree_right_click(self, widget, event):
        """Intercepts right-clicks on the tree to show the context menu."""
        if event.button == 3:  # 3 is Right-click
            path_info = self.app.tree_view.get_path_at_pos(int(event.x), int(event.y))
            if path_info:
                path, col, cell_x, cell_y = path_info
                # Force selection of the row that was right-clicked
                self.app.tree_view.set_cursor(path, col, False)
                # Show popup
                self.app.context_menu.popup_at_pointer(event)
                return True
        return False

    def on_tree_row_activated(self, tree_view, path, column):
        """Triggered on Double-Click or pressing Enter on a row."""
        model = tree_view.get_model()
        pdf_obj = model[path][1]

        if isinstance(pdf_obj, JumpReference) and pdf_obj.target_node is not None:
            # The target is the saved TreePath
            target_path = pdf_obj.target_node

            # Jump to it!
            self.app.tree_view.expand_to_path(target_path)
            self.app.tree_view.set_cursor(target_path, None, False)
            self.app.tree_view.scroll_to_cell(target_path, None, True, 0.5, 0.0)

            # Optional UX touch: momentarily flash the row or keep focus
            self.app.tree_view.grab_focus()

    def on_tree_key_press(self, widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        # 1. Search shortcut (Ctrl+F)
        if (
            (event.state & Gdk.ModifierType.CONTROL_MASK)
            and event.keyval
            in (
                Gdk.KEY_f,
                Gdk.KEY_F,
            )
            or event.keyval == Gdk.KEY_slash
        ):
            self.app.search_bar.set_search_mode(True)
            self.app.search_entry.grab_focus()
            return True

        # 2. Map single-key shortcuts directly to our action functions
        if keyname == "q":
            Gtk.main_quit()
        elif keyname == "w":
            self.app.actions.action_save_pdf(None)
        elif keyname == "s":
            self.app.actions.action_extract(None)
        elif keyname == "e":
            self.app.actions.action_edit(None)
        elif keyname == "f":
            self.app.actions.action_normalize(None)
        elif keyname == "g":
            self.app.actions.action_jump_page(None)
        elif keyname == "Delete":
            self.app.actions.action_delete(None)

        # 3. Arrow Key navigation
        if event.keyval == Gdk.KEY_Right or keyname == "l":
            path, col = self.app.tree_view.get_cursor()
            if path:
                self.app.tree_view.expand_row(path, False)
                return True

        elif event.keyval == Gdk.KEY_Left or keyname == "h":
            path, col = self.app.tree_view.get_cursor()
            if path:
                if self.app.tree_view.row_expanded(path):
                    self.app.tree_view.collapse_row(path)
                elif len(path) > 1:
                    self.app.tree_view.set_cursor(path[:-1], None, False)
                return True

        elif keyname == "j":
            self.app.tree_view.emit("move-cursor", Gtk.MovementStep.DISPLAY_LINES, 1)
            return True

        elif keyname == "k":
            self.app.tree_view.emit("move-cursor", Gtk.MovementStep.DISPLAY_LINES, -1)
            return True

        return False

    def on_search_changed(self, entry):
        text = entry.get_text().lower()
        self.app.search_matches = []
        if not text:
            return

        def do_search(tree_iter):
            while tree_iter:
                raw_text = self.app.store[tree_iter][2]
                if raw_text and text in raw_text.lower():
                    self.app.search_matches.append(self.app.store.get_path(tree_iter))
                if self.app.store.iter_has_child(tree_iter):
                    do_search(self.app.store.iter_children(tree_iter))
                tree_iter = self.app.store.iter_next(tree_iter)

        do_search(self.app.store.get_iter_first())

        if self.app.search_matches:
            self.app.current_match_index = 0
            self.app._jump_to_current_match()

    def on_search_next(self, entry):
        if self.app.search_matches:
            self.app.current_match_index = (self.app.current_match_index + 1) % len(
                self.app.search_matches
            )
            self.app._jump_to_current_match()

    def on_search_prev(self, entry):
        if self.app.search_matches:
            self.app.current_match_index = (self.app.current_match_index - 1) % len(
                self.app.search_matches
            )
            self.app._jump_to_current_match()

    def on_search_cancel(self, entry):
        self.app.search_bar.set_search_mode(False)
        self.app.tree_view.grab_focus()
        # Note: We no longer revert the cursor! You stay where the search left you.

    def on_selection_changed(self, selection):
        model, treeiter = selection.get_selected()
        if treeiter is None:
            return

        # 1. Update Breadcrumbs and get ancestors
        ancestors = []
        path_names = []
        curr_iter = treeiter
        while curr_iter:
            path_names.insert(0, model[curr_iter][3])  # Get 'name' from col 3
            ancestors.append(model[curr_iter][1])
            curr_iter = model.iter_parent(curr_iter)
        self.app.breadcrumb_label.set_markup(
            '<b>Path:</b><span color="gray">' + " &gt; ".join(path_names) + "</span>"
        )

        # 2. Update Details vs Content Split
        pdf_obj = model[treeiter][1]
        name = model[treeiter][3]
        meta_buf = self.app.metadata_view.get_buffer()
        content_buf = self.app.content_view.get_buffer()

        meta_text = f"Type: {type(pdf_obj).__name__}\nRepr: {str(pdf_obj)[:200]}"

        # Add Backlinks Logic
        if hasattr(pdf_obj, "objgen") and pdf_obj.is_indirect:
            links = self.app.adapter.backlinks.get(pdf_obj.objgen, [])
            if links:
                meta_text += f"\n--- Referenced By ({len(links)}) ---\n"
                for source_id, key in sorted(links):
                    meta_text += f"• {source_id} via {key}\n"

        meta_buf.set_text(meta_text)
        content_buf.set_text("")  # Clear content by default

        # 1. Clear status bar
        self.app.statusbar.pop(0)

        target_obj = pdf_obj
        if isinstance(target_obj, JumpReference):
            target_obj = target_obj.pdf_obj
        # Check if the selected object is a Font
        is_font, font_page_idx = is_font_with_page(self.app.pdf, target_obj, ancestors)
        is_page, page_idx = is_page_with_index(self.app.pdf, target_obj)
        is_link, link_page_idx, link_rect = is_link_with_page(
            self.app.pdf, target_obj, ancestors
        )
        is_annot, annot_page_idx, annot_rect = is_annotation_with_page(
            self.app.pdf, target_obj, ancestors
        )

        if is_font:
            # We need to know WHICH page to render.
            # For now, let's assume the user is looking at the last viewed page,
            # or default to page 0.
            target_font_name = str(target_obj.BaseFont)  # e.g., "/ABCDEF+Arial"

            self.app.statusbar.push(
                0, f"Highlighting font: {target_font_name} on page {font_page_idx + 1}"
            )

            # Call a new render method
            rotation = self.app.pdf.pages[font_page_idx].get("/Rotate", 0) % 360
            self._render_page_with_highlight(font_page_idx, target_font_name, rotation)
            self.app.content_stack.set_visible_child_name("image")
        elif is_link and link_page_idx is not None:
            self.app.statusbar.push(0, f"Link annotation on page {link_page_idx + 1}")
            self._render_page_with_rect_highlight(
                link_page_idx, link_rect, (0.2, 0.6, 1.0, 0.5)
            )
            self.app.content_stack.set_visible_child_name("image")
        elif is_annot and annot_page_idx is not None:
            self.app.statusbar.push(0, f"Annotation on page {annot_page_idx + 1}")
            self._render_page_with_rect_highlight(
                annot_page_idx, annot_rect, (1.0, 0.4, 0.0, 0.5)
            )
            self.app.content_stack.set_visible_child_name("image")
        elif is_page and page_idx is not None:
            self._handle_page(target_obj, page_idx, content_buf, meta_buf)
        elif isinstance(target_obj, pikepdf.Stream):
            self._handle_stream(
                target_obj, treeiter, model, name, content_buf, meta_buf
            )
        elif isinstance(pdf_obj, JumpReference):
            meta_buf.set_text(
                "Jump Reference\nFollows an indirect object reference to another part of the tree.\n"
                + "Double-click or press Enter to follow link."
            )

            self.app.content_stack.set_visible_child_name("text")

    def _render_page_with_rect_highlight(self, page_idx, pdf_rect, rgba):
        surface, scaled_w, scaled_h, page, cr, fit_scale, page_w, page_h = (
            self._render_page_to_surface(page_idx)
        )

        x0, y0, x1, y1 = pdf_rect
        # Flip Y: PDF origin is bottom-left, Poppler/Cairo origin is top-left
        cairo_y0 = page_h - y1
        cairo_y1 = page_h - y0

        cr.set_source_rgba(*rgba)
        cr.rectangle(x0, cairo_y0, x1 - x0, cairo_y1 - cairo_y0)
        cr.fill()

        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, scaled_w, scaled_h)
        self.app.image_view.set_from_pixbuf(pixbuf)

    def _handle_page(self, pdf_obj, page_idx, content_buf, meta_buf):
        """Renders a PDF page fit to the current widget size."""
        if not getattr(self.app, "preview_pages_mode", True):
            self.app.statusbar.push(0, f"Page {page_idx + 1}, text mode")
            content_buf.set_text(f"Page {page_idx + 1} repr: \n\n{pdf_obj}")
            self.app.content_stack.set_visible_child_name("text")
            return

        try:
            self.app.statusbar.push(0, f"Page {page_idx + 1}, preview mode")
            self._render_page(pdf_obj, page_idx, content_buf, meta_buf)
            self.app.content_stack.set_visible_child_name("image")
        except Exception as e:
            content_buf.set_text(f"Page rendering failed: {e}\n\n{pdf_obj}")
            self.app.content_stack.set_visible_child_name("text")

    def _render_page(self, pdf_obj, page_idx, content_buf, meta_buf):
        surface, scaled_w, scaled_h = self._render_page_to_surface(page_idx)[:3]
        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, scaled_w, scaled_h)
        self.app.image_view.set_from_pixbuf(pixbuf)

    def _render_page_to_surface(self, page_idx):
        allocation = self.app.content_stack.get_allocation()
        target_w, target_h = allocation.width - 20, allocation.height - 20
        doc = self.poppler_doc
        page = doc.get_page(page_idx)
        width, height = page.get_size()

        fit_scale = min(target_w / width, target_h / height)
        if fit_scale <= 0:
            fit_scale = 1.5

        scaled_w, scaled_h = int(width * fit_scale), int(height * fit_scale)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, scaled_w, scaled_h)
        cr = cairo.Context(surface)
        cr.scale(fit_scale, fit_scale)

        # 1. Background and Page Render
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.paint()
        page.render(cr)
        return surface, scaled_w, scaled_h, page, cr, fit_scale, width, height

    def _render_page_with_highlight(self, page_idx, target_font_name, rotation):
        surface, scaled_w, scaled_h, page, cr, _, w, h = self._render_page_to_surface(page_idx)
        # 2. Highlight Logic
        # Get every character's position and its attributes
        success, rectangles = page.get_text_layout()
        attributes = page.get_text_attributes()

        # 1. Normalize the name from the TreeView
        # Remove leading slash and grab the part after the '+'
        clean_target = target_font_name.lstrip("/")
        base_target = clean_target.split("+")[-1].lower()

        if success and attributes:
            cr.set_source_rgba(1.0, 1.0, 0.0, 0.4)  # Semi-transparent yellow

            for attr in attributes:
                if not attr.font_name:
                    continue
                reported_name = attr.font_name.lower()
                # Check if the font name matches
                if base_target in reported_name or reported_name in base_target:
                    # Draw a rectangle for every character in this span
                    print(f"rectangles: {len(rectangles)}, attr span: {attr.start_index} to {attr.end_index}")
                    for i in range(attr.start_index, attr.end_index+1):
                        if i < len(rectangles):
                            r = rectangles[i]
                            width = abs(r.x2 - r.x1)
                            height = abs(r.y2 - r.y1)
                            # if rotation % 360 == 90:
                            #     width, height = height, width
                            rect = [r.x1, r.y1, width, height]
                            print(f"rect={rect}")
                            cr.rectangle(*rect)
                    cr.fill()

        # 3. Update UI
        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, scaled_w, scaled_h)
        self.app.image_view.set_from_pixbuf(pixbuf)

    def _handle_stream(self, pdf_obj, treeiter, model, name, content_buf, meta_buf):
        parent_iter = model.iter_parent(treeiter)
        parent_name = model[parent_iter][3] if parent_iter else ""
        content_stream_q = is_content_stream(pdf_obj, name, parent_name)
        image_q = str(pdf_obj.get("/Subtype", "")) == "/Image"

        # 1. IMAGE PREVIEW PATH
        if self.app.preview_images_mode and image_q:
            try:
                pdf_img = PdfImage(pdf_obj)
                pil_img = pdf_img.as_pil_image()
                byte_stream = io.BytesIO()
                pil_img.save(byte_stream, format="PNG")
                byte_stream.seek(0)
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(byte_stream.read())
                loader.close()

                self.app.image_view.set_from_pixbuf(loader.get_pixbuf())
                self.app.content_stack.set_visible_child_name("image")
                self.app.statusbar.push(0, "Stream Mode: Image, Preview")
                return  # SUCCESS: Stop here
            except Exception as e:
                # Fallback to text if preview fails
                content_buf.set_text(f"Image preview failed: {e}")
                self.app.content_stack.set_visible_child_name("text")

        # 2. DISASSEMBLY PATH
        if self.app.disassemble_mode and content_stream_q:
            text = disassemble_content_stream(pdf_obj)
            content_buf.set_text(text)
            self.app.content_stack.set_visible_child_name("text")
            self.app.statusbar.push(0, "Stream Mode: Content, Disassembly")
            return  # SUCCESS: Stop here

        # 3. RAW FALLBACK PATH (Always last)
        try:
            meta_buf.set_text(f"Stream Dictionary:\n{str(pdf_obj)}")

            # Determine status label
            status = "Stream Mode: Raw"
            if content_stream_q:
                status = "Stream Mode: Content, Raw"
            elif image_q:
                status = "Stream Mode: Image, Raw"
            self.app.statusbar.push(0, status)

            # Try to get uncompressed bytes first
            try:
                content_bytes = pdf_obj.read_bytes()
                content = content_bytes.decode("utf-8", errors="replace")
            except (pikepdf.PdfError, NotImplementedError) as e:
                # Handles JBIG2, JPX, or other unsupported filters
                content_bytes = pdf_obj.read_raw_bytes()

                # Truncate to prevent GUI freeze on massive binary streams
                preview_length = 2000
                byte_preview = repr(content_bytes[:preview_length])
                if len(content_bytes) > preview_length:
                    byte_preview += f"\n\n... [TRUNCATED {len(content_bytes) - preview_length} BYTES]"

                content = f"<Unfilterable Stream: {e}>\n<Showing Raw Encoded Data>\n\n{byte_preview}"

            content_buf.set_text(content)
            self.app.content_stack.set_visible_child_name("text")

        except Exception as e:
            content_buf.set_text(f"Error reading stream: {e}")
            self.app.content_stack.set_visible_child_name("text")

    def on_stream_cursor_moved(self, textview, step, count, extend_selection):
        # Get the current line text
        buffer = textview.get_buffer()
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
        start = insert_iter.copy()
        start.set_line_offset(0)
        end = insert_iter.copy()
        end.forward_to_line_end()
        line_text = buffer.get_text(start, end, False)

        # Simple check: find the operator in the line (it's between the operands and %)
        parts = line_text.split("%")
        if len(parts) > 0:
            content = parts[0].strip().split()
            if content:
                op = content[-1]  # The last word before the % is the operator
                if op in ops:
                    op_grammar, op_content_type, desc = ops[op]
                    self.app.statusbar.push(
                        0, f"[{op_grammar}/{op_content_type}] {op}: {desc}"
                    )
