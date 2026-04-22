import html

import pikepdf
from collections import defaultdict

from .pdf_utils import TreeAdapter, JumpReference, format_pdf_string


class GtkAdapter(TreeAdapter):
    def __init__(self, store):
        self.store = store
        self.registry = {}
        self.backlinks = defaultdict(set)

    def get_iter_from_objgen_string(self, objgen_str):
        """
        Converts "10 0" or "Trailer" back into a Gtk.TreeIter.
        Useful for jumping to a parent from the backlink list.
        """
        if objgen_str == "Trailer":
            return self.store.get_iter_first()

        try:
            # Split "10 0" into (10, 0)
            num, gen = map(int, objgen_str.split())
            return self.registry.get((num, gen))
        except (ValueError, AttributeError):
            return None

    def _get_og_label(self, pdf_obj, markup=True):
        """Helper to format the (Obj N:G) string."""
        if not getattr(pdf_obj, "is_indirect", False):
            return ""
        num, gen = pdf_obj.objgen
        if markup:
            return f" <span color='#c4a000'>(Obj {num}:{gen})</span>"
        return f" (Obj {num}:{gen})"

    def create_node(self, parent_iter, pdf_obj, name, label_type):
        is_ind = getattr(pdf_obj, "is_indirect", False)

        # Markup labels
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
        elif isinstance(pdf_obj, (str, pikepdf.String)):
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

        new_iter = self.store.append(parent_iter, [markup, pdf_obj, raw_text, name])
        if is_ind:
            self.registry[pdf_obj.objgen] = new_iter
        return new_iter

    def create_jump(self, parent_iter, target_iter, name, pdf_obj):
        # Peek at the target node to get its Object ID
        target_obj = self.store[target_iter][1]
        og_label = self._get_og_label(target_obj, markup=True)
        og_raw = self._get_og_label(target_obj, markup=False)

        markup = f"<span color='gray'><i>↪ {name} (Jump)</i></span>{og_label}"
        raw_text = f"↪ {name} (Jump){og_raw}"

        target_path = self.store.get_path(target_iter)
        self.store.append(
            parent_iter, [markup, JumpReference(target_path, pdf_obj), raw_text, name]
        )

    def create_deferred(self, parent_iter, pdf_obj, name):
        markup = f"<span color='gray'><i>{name} [Deferred]</i></span>"
        raw_text = f"{name} [Deferred]"
        return self.store.append(parent_iter, [markup, pdf_obj, raw_text, name])

    def resolve_deferred(self, ui_iter, target, name, is_orphan):
        if is_orphan:
            pdf_obj = target
            markup = f"<span color='#729fcf'><b>{name}</b></span> <span color='#c4a000'>(Obj {pdf_obj.objgen[0]}:{pdf_obj.objgen[1]})</span> <span color='gray'>Dict[{len(pdf_obj)}]</span>"
            raw_text = f"{name} (Obj {pdf_obj.objgen[0]}:{pdf_obj.objgen[1]}) Dict[{len(pdf_obj)}]"

            self.store.set_value(ui_iter, 0, markup)
            self.store.set_value(ui_iter, 1, pdf_obj)
            self.store.set_value(ui_iter, 2, raw_text)
            self.store.set_value(ui_iter, 3, name)
        else:
            markup = f"<span color='gray'><i>↪ {name} (Jump)</i></span>"
            raw_text = f"↪ {name} (Jump)"
            target_path = self.store.get_path(target)

            self.store.set_value(ui_iter, 0, markup)
            self.store.set_value(ui_iter, 1, JumpReference(target_path, target))
            self.store.set_value(ui_iter, 2, raw_text)
            self.store.set_value(ui_iter, 3, name)
