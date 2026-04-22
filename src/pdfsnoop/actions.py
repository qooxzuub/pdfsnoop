import os
import gi
import subprocess
import shlex
import tempfile

import pikepdf

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


class ActionHandler:
    def __init__(self, app):
        self.app = app

    def _show_info(self, title, message):
        """Helper for quick GUI info alerts."""
        dialog = Gtk.MessageDialog(
            transient_for=self.app,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def _show_error(self, title, message):
        """Helper for quick GUI error alerts."""
        dialog = Gtk.MessageDialog(
            transient_for=self.app,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def get_selected_pdf_obj(self):
        """Helper to get the currently selected object in the tree."""
        model, treeiter = self.app.tree_view.get_selection().get_selected()
        if treeiter:
            return model[treeiter][1]
        return None

    def action_save_pdf(self, widget):
        """Pops up a native Save dialog and writes the modified PDF to disk."""
        dialog = Gtk.FileChooserDialog(
            title="Save PDF As...",
            parent=self.app,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE,
            Gtk.ResponseType.ACCEPT,
        )
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name("modified.pdf")

        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            try:
                self.app.pdf.save(dialog.get_filename())
                print(f"Saved successfully to {dialog.get_filename()}")
            except Exception as e:
                print(f"Failed to save PDF: {e}")

        dialog.destroy()

    def action_delete(self, widget):
        """Deletes the selected node from both the UI and the actual Pikepdf structure."""
        model, treeiter = self.app.tree_view.get_selection().get_selected()
        if not treeiter:
            return

        parent_iter = model.iter_parent(treeiter)
        if not parent_iter:
            return  # Can't delete the root trailer!

        parent_obj = model[parent_iter][1]
        node_name = model[treeiter][3]  # The raw name (e.g. "/Font" or "[0]")

        try:
            # 1. Dictionary Deletion
            if isinstance(parent_obj, pikepdf.Dictionary):
                old_len = len(parent_obj)
                del parent_obj[node_name]
                new_len = len(parent_obj)

                # Update Parent UI Label
                p_markup = model[parent_iter][0].replace(
                    f"Dict[{old_len}]", f"Dict[{new_len}]"
                )
                p_raw = model[parent_iter][2].replace(
                    f"Dict[{old_len}]", f"Dict[{new_len}]"
                )
                model.set_value(parent_iter, 0, p_markup)
                model.set_value(parent_iter, 2, p_raw)

                model.remove(treeiter)

            # 2. Array Deletion (Requires UI Renumbering)
            elif isinstance(parent_obj, pikepdf.Array):
                old_len = len(parent_obj)
                idx = int(node_name.strip("[]"))
                del parent_obj[idx]
                new_len = len(parent_obj)

                # Update Parent UI Label
                p_markup = model[parent_iter][0].replace(
                    f"Array[{old_len}]", f"Array[{new_len}]"
                )
                p_raw = model[parent_iter][2].replace(
                    f"Array[{old_len}]", f"Array[{new_len}]"
                )
                model.set_value(parent_iter, 0, p_markup)
                model.set_value(parent_iter, 2, p_raw)

                # model.remove returns True and modifies treeiter to point to the next row
                valid = model.remove(treeiter)
                current_idx = idx

                while valid:
                    new_name = f"[{current_idx}]"
                    old_name = f"[{current_idx + 1}]"

                    # Safely replace the old index string in the markup and raw text
                    markup = model[treeiter][0].replace(
                        f"<b>{old_name}</b>", f"<b>{new_name}</b>", 1
                    )
                    raw_text = model[treeiter][2].replace(
                        f"{old_name}", f"{new_name}", 1
                    )

                    # Update columns 0 (markup), 2 (raw), and 3 (name)
                    model.set_value(treeiter, 0, markup)
                    model.set_value(treeiter, 2, raw_text)
                    model.set_value(treeiter, 3, new_name)

                    current_idx += 1

                    # Advance to the next sibling
                    treeiter = model.iter_next(treeiter)
                    valid = treeiter is not None

        except Exception as e:
            self._show_error("Deletion Failed", str(e))

        self.app.tree_view.get_selection().emit("changed")

    def action_jump_page(self, widget):
        """Prompts for a page number and jumps the tree to that PDF Dictionary."""
        total_pages = len(self.app.pdf.pages)
        if total_pages == 0:
            return

        # 1. Create the dialog
        dialog = Gtk.MessageDialog(
            transient_for=self.app,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Jump to Page",
        )
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.format_secondary_text(f"Enter page number (1 - {total_pages}):")

        entry = Gtk.Entry()
        entry.set_activates_default(True)
        dialog.get_message_area().pack_end(entry, False, False, 0)
        dialog.show_all()

        response = dialog.run()
        page_text = entry.get_text()
        dialog.destroy()

        # Helper function for GUI error messages
        def show_error(message):
            self._show_error("Invalid Input", message)

        # 2. Process the input
        if response == Gtk.ResponseType.OK:
            try:
                page_num = int(page_text)  # This will raise ValueError on "asdkfjh"

                if 1 <= page_num <= total_pages:
                    target_page = self.app.pdf.pages[page_num - 1]
                    objgen = target_page.objgen
                    target_iter = self.app.adapter.registry.get(objgen)

                    if target_iter:
                        path = self.app.store.get_path(target_iter)
                        self.app.tree_view.expand_to_path(path)
                        self.app.tree_view.set_cursor(path, None, False)
                        self.app.tree_view.scroll_to_cell(path, None, True, 0.5, 0.0)
                        self.app.tree_view.grab_focus()
                    else:
                        show_error("Page node not found in the UI tree registry.")
                else:
                    show_error(
                        f"Invalid page number.\nMust be between 1 and {total_pages}."
                    )

            except ValueError:
                # Catches non-numbers and weird characters
                show_error(
                    f"Please enter a valid whole number between 1 and {total_pages}."
                )

    def expand_to_pages(self):
        """Recursively search the TreeStore for the /Pages dictionary and expand to it."""

        def search_for_pages(tree_iter):
            while tree_iter:
                pdf_obj = self.app.store[tree_iter][1]

                # Check if this node is a Dictionary and its /Type is /Pages
                if isinstance(pdf_obj, pikepdf.Dictionary):
                    if str(pdf_obj.get("/Type", "")) == "/Pages":
                        return self.app.store.get_path(tree_iter)

                # Recurse into children
                if self.app.store.iter_has_child(tree_iter):
                    child_iter = self.app.store.iter_children(tree_iter)
                    result = search_for_pages(child_iter)
                    if result:
                        return result

                tree_iter = self.app.store.iter_next(tree_iter)
            return None

        # Start search from the root (Trailer)
        first_iter = self.app.store.get_iter_first()
        pages_path = search_for_pages(first_iter)

        if pages_path:
            self.app.tree_view.expand_to_path(pages_path)
            self.app.tree_view.set_cursor(pages_path, None, False)
            # Scroll so the /Pages node is exactly in the middle of the screen
            self.app.tree_view.scroll_to_cell(pages_path, None, True, 0.5, 0.0)

    def action_extract(self, widget):
        """Extracts the selected Stream or Image to a file."""
        obj = self.get_selected_pdf_obj()
        if not isinstance(obj, pikepdf.Stream):
            self._show_error("Invalid Selection", "Selected object is not a stream.")
            return

        is_image = str(obj.get("/Subtype", "")) == "/Image"

        dialog = Gtk.FileChooserDialog(
            title="Extract Image (Provide Prefix)"
            if is_image
            else "Extract Stream To...",
            parent=self.app,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE,
            Gtk.ResponseType.ACCEPT,
        )
        dialog.set_do_overwrite_confirmation(True)

        if is_image:
            # PdfImage will append the correct extension (e.g., .jpg, .png)
            dialog.set_current_name("extracted_image")
        else:
            dialog.set_current_name("extracted_stream.bin")

        if dialog.run() == Gtk.ResponseType.ACCEPT:
            filepath = dialog.get_filename()

            if is_image:
                try:
                    from pikepdf.models import PdfImage

                    pdf_img = PdfImage(obj)
                    saved_path = pdf_img.extract_to(fileprefix=filepath)
                    self._show_info(
                        "Success", f"Image successfully extracted to:\n{saved_path}"
                    )
                except ImportError:
                    self._show_error(
                        "Missing Dependency",
                        "Pillow is required for image extraction. Run: pip install Pillow",
                    )
                except Exception as e:
                    self._show_error("Extraction Failed", str(e))
            else:
                try:
                    raw_bytes = obj.read_bytes()
                    with open(filepath, "wb") as f:
                        f.write(raw_bytes)
                    self._show_info(
                        "Success", f"Saved {len(raw_bytes)} bytes to:\n{filepath}"
                    )
                except Exception as e:
                    self._show_error("Export Failed", str(e))

        dialog.destroy()

    def action_edit(self, widget):
        """Dumps stream to a temp file, opens in $EDITOR, and writes back on close."""
        obj = self.get_selected_pdf_obj()
        if not isinstance(obj, pikepdf.Stream):
            self._show_error(
                "Invalid Selection", "Currently, only Stream editing is supported."
            )
            return

        editor_env = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if not editor_env:
            self._show_error(
                "No Editor Set",
                "Please set the $EDITOR environment variable before running.\n"
                "Example: export EDITOR=nano  (or 'code --wait')",
            )
            return

        try:
            # 1. Dump to a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
                tmp.write(obj.read_bytes())
                temp_path = tmp.name

            # 2. Parse editor command and wait for it to finish
            editor_cmd = shlex.split(editor_env) + [temp_path]
            subprocess.run(editor_cmd, check=True)

            # 3. Read the modified bytes back
            with open(temp_path, "rb") as f:
                new_bytes = f.read()

            # 4. Write back if changed and update the UI
            if new_bytes != obj.read_bytes():
                obj.write(new_bytes)
                self.app.tree_view.get_selection().emit("changed")

            # Clean up
            os.remove(temp_path)

        except Exception as e:
            self._show_error("Edit Failed", str(e))

    def action_normalize(self, widget):
        """Parses and unparses a content stream to format operators to one-per-line."""
        obj = self.get_selected_pdf_obj()
        if not isinstance(obj, pikepdf.Stream):
            self._show_error(
                "Invalid Selection", "Please select a stream to normalize."
            )
            return

        try:
            parsed = pikepdf.parse_content_stream(obj)
            normalized_bytes = pikepdf.unparse_content_stream(parsed)
            old_bytes = obj.read_bytes()

            if normalized_bytes != old_bytes:
                obj.write(normalized_bytes)
                self._show_info(
                    "Stream Normalized",
                    f"Successfully formatted stream.\nLength: {len(old_bytes)} -> {len(normalized_bytes)} bytes.",
                )
                # Re-emit selection to update the right-side content pane automatically
                self.app.tree_view.get_selection().emit("changed")
            else:
                self._show_info(
                    "Unchanged", "Stream is already formatted or unchanged."
                )
        except Exception as e:
            self._show_error(
                "Normalization Failed",
                f"This might not be a valid content stream:\n{str(e)}",
            )

    def action_checkbox_toggle_and_refresh(self, checkmenuitem):
        """Refreshes the current view when a view mode is toggled."""
        # Trigger a refresh of the right-hand pane
        selection = self.app.tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter:
            self.app.events.on_selection_changed(selection)
