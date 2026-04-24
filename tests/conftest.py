"""Shared fixtures for pdfsnoop tests."""

import pikepdf
import pytest


@pytest.fixture
def simple_pdf(tmp_path):
    """A minimal single-page PDF with one content stream."""
    pdf = pikepdf.Pdf.new()
    cs = pikepdf.Stream(pdf, b"BT /F1 12 Tf 100 700 Td (Hello World) Tj ET")
    page = pikepdf.Page(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=cs,
        )
    )
    pdf.pages.append(page)
    path = tmp_path / "simple.pdf"
    pdf.save(path)
    return path


@pytest.fixture
def multipage_pdf(tmp_path):
    """A 3-page PDF where each page has two content streams (Contents is an Array)."""
    pdf = pikepdf.Pdf.new()
    for i in range(3):
        cs1 = pikepdf.Stream(pdf, f"BT (Page {i} stream 1) Tj ET".encode())
        cs2 = pikepdf.Stream(pdf, f"BT (Page {i} stream 2) Tj ET".encode())
        page = pikepdf.Page(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Page"),
                MediaBox=pikepdf.Array([0, 0, 612, 792]),
                Contents=pikepdf.Array([cs1, cs2]),
            )
        )
        pdf.pages.append(page)
    path = tmp_path / "multipage.pdf"
    pdf.save(path)
    return path


@pytest.fixture
def xobject_pdf(tmp_path):
    """A PDF with a Form XObject and an Image XObject."""
    pdf = pikepdf.Pdf.new()

    # Form XObject (normalizable content stream)
    form = pikepdf.Stream(pdf, b"BT /F1 12 Tf (form content) Tj ET")
    form["/Type"] = pikepdf.Name("/XObject")
    form["/Subtype"] = pikepdf.Name("/Form")
    form["/BBox"] = pikepdf.Array([0, 0, 100, 100])

    # Image XObject (NOT normalizable - pixel data)
    img = pikepdf.Stream(pdf, bytes(range(48)))
    img["/Type"] = pikepdf.Name("/XObject")
    img["/Subtype"] = pikepdf.Name("/Image")
    img["/Width"] = 4
    img["/Height"] = 4
    img["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    img["/BitsPerComponent"] = 8

    cs = pikepdf.Stream(pdf, b"BT (page) Tj ET")
    page = pikepdf.Page(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=cs,
            Resources=pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(
                    Fm0=form,
                    Im0=img,
                )
            ),
        )
    )
    pdf.pages.append(page)
    path = tmp_path / "xobject.pdf"
    pdf.save(path)
    return path
