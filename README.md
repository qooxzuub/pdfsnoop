# pdfsnoop

A GTK desktop tool for exploring PDF internals.

| feature                        | screenshot                                                                                                               |
|--------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| content disassembly            | <img width="400" src="https://raw.githubusercontent.com/qooxzuub/pdfsnoop/main/.github/assets/pdfsnoop_disassemble.png"> |
| page preview/font highlighting | <img width="400" src="https://raw.githubusercontent.com/qooxzuub/pdfsnoop/main/.github/assets/pdfsnoop_font.png">        |
| annotation highlighting        | <img width="400" src="https://raw.githubusercontent.com/qooxzuub/pdfsnoop/main/.github/assets/pdfsnoop_annotation.png">  |

## What it does

Opens a PDF file and displays its internal object tree, allowing you to inspect
dictionaries, arrays, streams, and indirect references. Selecting an object in
the tree shows its contents in a detail pane. For page objects, a rendered
preview is shown. Content streams are disassembled into annotated PDF operators.

Selecting certain objects highlights them on the page preview:

- **Font objects** — highlights text using that font in yellow
- **Annotation objects** — highlights the annotation rectangle
- **Link annotations** — highlights the link rectangle in blue

## Installation

Requires Python 3.10+, GTK 3, and Poppler.

On Debian/Ubuntu you may need system packages:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-poppler-0.18 libgirepository-2.0-dev
```
If your repo doesn't have `libgirepository-2.0-dev`, try `libgirepository1.0-dev`. (One less hyphen!)

Then run these commands to install
```
SNOOPDIR=~/software/pdfsnoop  # or wherever you like
mkdir -p $SNOOPDIR && cd $SNOOPDIR
python -m venv --system-site-packages pdfsnoop-venv
source pdfsnoop-venv/bin/activate
git clone https://github.com/qooxzuub/pdfsnoop
pip install ./pdfsnoop
```
If you add `$SNOOPDIR/pdfsnoop-venv/bin` to your `PATH` then running `pdfsnoop` should work.

_FIXME: publish to PyPI for easier installation_


## Usage

```bash
pdfsnoop file.pdf
```

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `j` / `k` | Move down / up |
| `h` / `l` | Collapse / expand node |
| `g` | Jump to page number |
| `e` | Edit value inline or stream in `$EDITOR` |
| `s` | Extract stream or image |
| `f` | Normalize content stream |
| `w` | Save PDF as... |
| `/` or `Ctrl+F` | Search |
| `q` | Quit |

## License

Mozilla Public License 2.0
