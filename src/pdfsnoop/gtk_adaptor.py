import html
import pikepdf
from collections import defaultdict

from .pdf_utils import TreeAdapter, JumpReference, format_pdf_string


import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class GtkAdapter(TreeAdapter):
    PLACEHOLDER = "Loading..."

    def __init__(self, store):
        self.store = store
        self.registry = {}
        self.backlinks = defaultdict(set)

    def _get_markup_etc(self, pdf_obj, name, is_ind, label_type=None):
        # Markup labels
        if label_type is None:
            if isinstance(pdf_obj, pikepdf.Dictionary):
                label_type = "Dictionary"
            elif isinstance(pdf_obj, pikepdf.Array):
                label_type = "Array"
            elif isinstance(pdf_obj, pikepdf.String):
                label_type = "String"
            elif isinstance(pdf_obj, pikepdf.Stream):
                label_type = "Stream"

        obj_label = (
            f" <span color='#c4a000'>(Obj {pdf_obj.objgen[0]}:{pdf_obj.objgen[1]})</span>"
            if is_ind
            else ""
        )
        # Raw text labels
        raw_obj_label = (
            f" (Obj {pdf_obj.objgen[0]}:{pdf_obj.objgen[1]})" if is_ind else ""
        )
        if label_type == "Dictionary":
            markup = f"<span color='#729fcf'><b>{name}</b></span>{obj_label} <span color='gray'>Dict[{len(pdf_obj)}]</span>"
            raw_text = f"{name}{raw_obj_label} Dict[{len(pdf_obj)}]"
        elif label_type == "Array":
            markup = f"<span color='#8ae234'><b>{name}</b></span>{obj_label} <span color='gray'>Array[{len(pdf_obj)}]</span>"
            raw_text = f"{name}{raw_obj_label} Array[{len(pdf_obj)}]"
        elif label_type == "Stream":
            markup = f"<span color='#ef2929'><b>{name}</b></span>{obj_label} <span color='gray'>Stream</span>"
            raw_text = f"{name}{raw_obj_label} Stream"
        elif label_type == "String":
            raw_val_formatted = format_pdf_string(pdf_obj)
            raw_val_to_show = (
                raw_val_formatted
                if len(raw_val_formatted) < 60
                else raw_val_formatted[:60] + "…"
            )
            val_str = html.escape(raw_val_to_show)
            markup = f"<span color='#cc9999'><b>{name}</b></span>{obj_label}: {val_str} <span color='gray'>String</span>"
            raw_text = f"{name}{raw_obj_label}: {raw_val_to_show} ---- {html.escape(str(pdf_obj)[:60])}"
        else:
            raw_val = str(pdf_obj)[:60]
            val_str = html.escape(raw_val)
            markup = f"<span color='#34e2e2'><b>{name}</b></span>: {val_str}"
            raw_text = f"{name}{raw_obj_label}: {raw_val}"

        return markup, raw_text

    def create_node(self, parent_iter, name, pdf_obj):
        """Creates a standard node with restored color scheme."""

        # Color Logic: Blue keys, Gold object IDs, Gray metadata
        obj_info = ""
        is_ind = getattr(pdf_obj, "is_indirect", False)
        if is_ind:
            obj_info = f" <span color='#c4a000'>({pdf_obj.objgen[0]} {pdf_obj.objgen[1]} obj)</span>"

        # Restore the colors: Blue for name, Gray for description
        # markup = f"<span color='#729fcf'><b>{name}</b></span>{obj_info} <span color='#888a85'>{description}</span>"
        # raw_text = f"{name} {description}"
        markup, raw_text = self._get_markup_etc(pdf_obj, name, is_ind)

        treeiter = self.store.append(parent_iter, [markup, pdf_obj, raw_text, name])

        # FIX: Use TreeRowReference for the registry (much more stable for jumping)
        if getattr(pdf_obj, "is_indirect", False):
            if pdf_obj.objgen not in self.registry:
                path = self.store.get_path(treeiter)
                self.registry[pdf_obj.objgen] = Gtk.TreeRowReference.new(
                    self.store, path
                )

        if isinstance(pdf_obj, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
            if isinstance(pdf_obj, pikepdf.Stream) or len(pdf_obj) > 0:
                self.store.append(
                    treeiter, [f"<i>{self.PLACEHOLDER}</i>", None, "", ""]
                )

        return treeiter

    def create_jump(self, parent_iter, objgen, name, pdf_obj):
        """Creates a 'Jump' node with blue italics and gold reference."""
        markup = f"<span color='#729fcf'><i>↪ {name}</i></span> <span color='#c4a000'>(Ref to {objgen[0]} {objgen[1]})</span>"
        raw_text = f"↪ {name} jump {objgen}"
        self.store.append(parent_iter, [markup, JumpReference(objgen), raw_text, name])

    def has_placeholder(self, node_iter):
        """Returns True if this node's only child is a placeholder (col 1 is None)."""
        child = self.store.iter_children(node_iter)
        if child is None:
            return False
        return self.store[child][1] is None

    def remove_placeholder(self, node_iter):
        """Remove the placeholder child from this node."""
        child = self.store.iter_children(node_iter)
        if child is not None and self.store[child][1] is None:
            self.store.remove(child)
