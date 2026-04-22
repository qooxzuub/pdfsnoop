import gi

gi.require_version("Gtk", "3.0")

import unicodedata

from collections import defaultdict

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
    def __init__(self, target_node, pdf_obj):
        self.target_node = target_node
        self.pdf_obj = pdf_obj


class DeferredJumpReference:
    """Stores the actual object so we can build it later if it turns out to be an orphan."""

    def __init__(self, pdf_obj, original_name):
        self.pdf_obj = pdf_obj
        self.original_name = original_name


class TreeAdapter:
    """Interface to be implemented by TUI and GUI."""

    def create_node(self, parent, pdf_obj, name, label_type):
        pass

    def create_jump(self, parent, target_node, name, obj):
        pass

    def create_deferred(self, parent, pdf_obj, name):
        pass

    def resolve_deferred(self, deferred_node, target_node, name, is_orphan):
        pass


def walk_pdf(pdf_root, adapter, name="Trailer"):
    registry = {}
    deferred = []
    # New: Track backlinks. Mapping: target_objgen -> set((source_id, key_name))
    backlinks = defaultdict(set)

    # Updated stack: (obj, ui_parent, name, is_kid, nearest_indirect_parent_id)
    stack = [(pdf_root, None, name, False, "Trailer")]

    while True:
        while stack:
            obj, parent_ui, n, is_kid, parent_id = stack.pop()
            is_ind = getattr(obj, "is_indirect", False)
            current_obj_id = f"{obj.objgen[0]} {obj.objgen[1]}" if is_ind else parent_id

            # 1. Record the backlink if this is an indirect reference
            if is_ind:
                backlinks[obj.objgen].add((parent_id, n))

            # 2. Handle Jumps (Cycles)
            if is_ind and obj.objgen in registry:
                adapter.create_jump(parent_ui, registry[obj.objgen], n, obj)
                continue

            # 3. Handle Deferred Pages
            obj_type = obj.get("/Type") if isinstance(obj, pikepdf.Dictionary) else None
            if is_ind and str(obj_type) == "/Page" and not is_kid:
                ui_handle = adapter.create_deferred(parent_ui, obj, n)
                deferred.append((ui_handle, obj, n, current_obj_id))  # Pass ID through
                continue

            # 4. Create Standard Node
            if isinstance(obj, pikepdf.Dictionary):
                node_type = "Dictionary"
            elif isinstance(obj, pikepdf.Array):
                node_type = "Array"
            elif isinstance(obj, pikepdf.Stream):
                node_type = "Stream"
            else:
                node_type = type(obj).__name__

            ui_handle = adapter.create_node(parent_ui, obj, n, node_type)
            if is_ind:
                registry[obj.objgen] = ui_handle

            # 5. Discovery (Push children to stack)
            # Pass the current_obj_id down so children know who their indirect ancestor is
            if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                for key, val in reversed(sorted(obj.items(), key=sort_pdf_keys)):
                    stack.append((val, ui_handle, str(key), False, current_obj_id))
            elif isinstance(obj, pikepdf.Array):
                is_kids_array = n == "/Kids"
                for i, val in reversed(list(enumerate(obj))):
                    stack.append(
                        (val, ui_handle, f"[{i}]", is_kids_array, current_obj_id)
                    )

        # --- PHASE 2: Orphan Recovery ---
        found_new_orphans = False
        current_deferred = deferred[:]
        deferred.clear()

        for ui_handle, obj, n, p_id in current_deferred:
            if obj.objgen in registry:
                adapter.resolve_deferred(
                    ui_handle, registry[obj.objgen], n, is_orphan=False
                )
            else:
                found_new_orphans = True
                registry[obj.objgen] = ui_handle
                adapter.resolve_deferred(ui_handle, obj, n, is_orphan=True)
                current_id = f"{obj.objgen[0]} {obj.objgen[1]}"
                for key, val in reversed(sorted(obj.items(), key=sort_pdf_keys)):
                    stack.append((val, ui_handle, str(key), False, current_id))

        if not found_new_orphans and not stack:
            break

    # Attach the backlinks to the adapter so the GUI can find them later
    adapter.backlinks = backlinks


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
