import gi

gi.require_version("Gtk", "3.0")

import unicodedata


import pikepdf

from .pdf_operators import ops


def is_content_stream(stream: pikepdf.Stream, name: str, parent_name: str = "") -> bool:
    # Fast exit: image codecs mean raw pixel data
    image_filters = {"/DCTDecode", "/JPXDecode", "/CCITTFaxDecode", "/JBIG2Decode"}
    filters = stream.get("/Filter")
    if filters is not None:
        filter_list = (
            [str(filters)]
            if not isinstance(filters, pikepdf.Array)
            else [str(f) for f in filters]
        )
        if any(f in image_filters for f in filter_list):
            return False

    obj_type = str(stream.get("/Type", ""))
    obj_subtype = str(stream.get("/Subtype", ""))

    if name == "/Contents" or parent_name == "/Contents":
        return True
    if obj_type == "/Pattern":  # PatternType 1 tiling patterns
        return True
    if obj_type == "/XObject" and obj_subtype == "/Form":
        return True
    # Appearance streams have no /Type but are content streams
    if name in ("/N", "/R", "/D") and obj_type == "":
        return True  # heuristic — could false-positive on other anonymous streams

    return False


def sort_pdf_keys(item):
    key, val = item
    str_key = str(key)
    if str_key == "/Type":
        priority = -1
    elif str_key in ("/Root", "/Pages", "/Kids"):
        priority = 0
    elif isinstance(val, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
        priority = 2
    else:
        priority = 1
    return (priority, str_key)


class JumpReference:
    """Represents a link to an indirect object already present elsewhere in the tree."""

    def __init__(self, objgen):
        self.objgen = objgen


class TreeAdapter:
    """Interface to be implemented by GUI."""

    def create_node(self, parent, pdf_obj, name, label_type):
        pass

    def create_jump(self, parent, target_node, name, obj):
        pass


def disassemble_content_stream(stream_obj):
    lines = []
    # pikepdf handles the heavy lifting of parsing operands and operators
    # operands is a list (e.g., [10, 20]), operator is the command (e.g., "Td")
    for operands, operator in pikepdf.parse_content_stream(stream_obj):
        op_name = str(operator)

        # Get the description from your 'ops' dictionary
        # Note: using index [2] because of your new 3-tuple format
        info = ops.get(op_name, ("unknown", "unknown", "Unknown operator"))
        description = info[2]

        # Format operands: strings need their parens back for valid syntax
        formatted_ops = []
        for arg in operands:
            if isinstance(arg, pikepdf.String):
                # Put the parens back so it's valid PDF syntax
                formatted_ops.append(f"({str(arg)})")
            else:
                formatted_ops.append(str(arg))

        ops_str = " ".join(formatted_ops)

        # Align the output: Operands (Left), Operator (Center), Comment (Right)
        line = f"{ops_str:<40} {op_name:<6} % {description}"
        lines.append(line)

    return "\n".join(lines)


def is_human_readable(s: str) -> bool:
    """
    Determines if a string is likely intended for human eyes.
    Filters out binary blobs, encrypted data, and mojibake.
    """
    if not s:
        return True

    # PDF IDs often contain the null byte; text strings almost never do.
    if "\x00" in s:
        return False

    unprintable_count = 0
    for char in s:
        cat = unicodedata.category(char)
        # Cc: Control, Cs: Surrogate, Co: Private Use
        if cat in ("Cc", "Cs", "Co"):
            # We explicitly allow common whitespace
            if char not in "\n\r\t":
                unprintable_count += 1

    # If the string is mostly garbage (unprintables), it's binary data.
    # A 15% threshold allows for occasional weird characters in text.
    if (unprintable_count / len(s)) > 0.15:
        return False

    return True


def format_pdf_string(pdf_obj):
    """
    Finds the best human-readable representation of a PDF string.
    Tries UTF-8, UTF-16, and Latin-1 before falling back to Hex.
    """
    try:
        raw_bytes = bytes(pdf_obj)
    except (TypeError, ValueError):
        return str(pdf_obj)

    # Encodings to try in order of likelihood/strictness
    # latin-1 is the crucial fallback for 'Gauß' (0xDF)

    encodings = ["utf-8"]
    if raw_bytes.startswith(b"\xfe\xff"):
        encodings.append("utf-16")
    elif raw_bytes.startswith(b"\xff\xfe"):
        encodings.append("utf-16-le")
    encodings.append("latin-1")

    for enc in encodings:
        try:
            decoded = raw_bytes.decode(enc)
            if is_human_readable(decoded):
                return decoded
        except (UnicodeDecodeError, ValueError):
            continue

    # If all decoders produce junk or fail, return the clean hex format
    return f"<{raw_bytes.hex().upper()}>"


def is_page_with_index(pdf, pdf_obj):
    is_page = (
        isinstance(pdf_obj, pikepdf.Dictionary)
        and hasattr(pdf_obj, "Type")
        and pdf_obj.Type == pikepdf.Name("/Page")
    )
    page_idx = None
    if is_page:
        try:
            page_idx = pdf.pages.index(pdf_obj)
        except ValueError:
            pass
    return is_page, page_idx


def is_font_with_page(pdf, target_obj, ancestors):
    """Return (True, page_idx) if target_obj is a font dict reachable from a page."""
    if not (isinstance(target_obj, pikepdf.Dictionary) and "/BaseFont" in target_obj):
        return False, None
    # Walk ancestors looking for a Page, regardless of depth
    for ancestor in ancestors:
        is_page, page_idx = is_page_with_index(pdf, ancestor)
        if is_page and page_idx is not None:
            return True, page_idx
    return False, None


def is_annotation_with_page(pdf, target_obj, ancestors):
    """Return (True, page_idx, rect) if target_obj is an annotation dict."""
    if not (
        isinstance(target_obj, pikepdf.Dictionary)
        and "/Rect" in target_obj
        and "/Subtype" in target_obj
    ):
        return False, None, None
    for ancestor in ancestors:
        is_page, page_idx = is_page_with_index(pdf, ancestor)
        if is_page and page_idx is not None:
            rect = [float(x) for x in target_obj.Rect]
            return True, page_idx, rect
    return False, None, None


def is_link_with_page(pdf, target_obj, ancestors):
    """Return (True, page_idx, rect) if target_obj is a link annotation."""
    is_annot, page_idx, rect = is_annotation_with_page(pdf, target_obj, ancestors)
    if is_annot and target_obj.Subtype == pikepdf.Name("/Link"):
        return True, page_idx, rect
    return False, None, None


def prepopulate_spine(pdf, adapter):
    """Eagerly populates the Trailer, Root, and the Page tree so they are correctly structured and accessible."""
    trailer_node = adapter.create_node(None, "Trailer", pdf.trailer)

    # Trace the true path down to the Pages tree
    if "/Root" in pdf.trailer:
        _force_expand(pdf.trailer, adapter, trailer_node)
        root_node = _find_child_by_name(adapter, trailer_node, "/Root")

        if root_node and "/Pages" in pdf.trailer.Root:
            _force_expand(pdf.trailer.Root, adapter, root_node)
            pages_node = _find_child_by_name(adapter, root_node, "/Pages")

            if pages_node:
                _eager_load_page_tree(pdf.trailer.Root.Pages, adapter, pages_node)


def _force_expand(pdf_obj, adapter, node_iter):
    """Manually triggers the lazy-load expansion for a specific node exactly as the GUI would."""
    child_iter = adapter.store.iter_children(node_iter)
    # If the first child is the placeholder (pdf_obj is None)
    if child_iter and adapter.store[child_iter][1] is None:
        adapter.store.remove(child_iter)
        walk_one_level(pdf_obj, adapter, node_iter)


def _find_child_by_name(adapter, parent_iter, name):
    """Helper to find a child node by its name column (column 3)."""
    child_iter = adapter.store.iter_children(parent_iter)
    while child_iter:
        if adapter.store[child_iter][3] == name:
            return child_iter
        child_iter = adapter.store.iter_next(child_iter)
    return None


def _eager_load_page_tree(pdf_obj, adapter, node_iter):
    """Recursively forces expansion of /Pages dicts and /Kids arrays to register all pages."""
    _force_expand(pdf_obj, adapter, node_iter)

    if isinstance(pdf_obj, pikepdf.Dictionary):
        # Find the /Kids array node and expand it
        kids_node = _find_child_by_name(adapter, node_iter, "/Kids")
        if kids_node and "/Kids" in pdf_obj:
            _eager_load_page_tree(pdf_obj.Kids, adapter, kids_node)

    elif isinstance(pdf_obj, pikepdf.Array):
        # We are iterating a /Kids array. Its children are either /Pages or /Page.
        child_iter = adapter.store.iter_children(node_iter)
        i = 0
        while child_iter:
            kid_obj = pdf_obj[i]
            if (
                isinstance(kid_obj, pikepdf.Dictionary)
                and kid_obj.get("/Type") == "/Pages"
            ):
                # It's an intermediate /Pages tree node, keep expanding!
                _eager_load_page_tree(kid_obj, adapter, child_iter)

            # If it's a leaf /Page, we do nothing. The fact that walk_one_level created
            # the row means it is safely in the registry for jumping.

            child_iter = adapter.store.iter_next(child_iter)
            i += 1


def walk_one_level(pdf_obj, adapter, parent_ui):
    """Populates the immediate children of a node for lazy loading."""
    if isinstance(pdf_obj, (pikepdf.Dictionary, pikepdf.Stream)):
        # Apply the sort here! No reversed() needed since we append in-order.
        for key, val in sorted(pdf_obj.items(), key=sort_pdf_keys):
            _create_child(str(key), val, adapter, parent_ui)

    elif isinstance(pdf_obj, pikepdf.Array):
        for i, val in enumerate(pdf_obj):
            _create_child(f"[{i}]", val, adapter, parent_ui)


def _create_child(name, val, adapter, parent_ui):
    """Helper to decide whether to create a real node or a jump."""
    is_indirect = getattr(val, "is_indirect", False)
    if is_indirect and val.objgen in adapter.registry:
        adapter.create_jump(parent_ui, val.objgen, name, val)
    else:
        adapter.create_node(parent_ui, name, val)
