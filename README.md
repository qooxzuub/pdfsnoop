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

### Debian / Ubuntu

Install system packages:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-poppler-0.18 libgirepository-2.0-dev
```
If your repo doesn't have `libgirepository-2.0-dev`, try `libgirepository1.0-dev`. (One less hyphen!)

Then either run
```
sudo apt install pipx
pipx install pdfsnoop --system-site-packages
```
or if that doesn't work, run these commands to install manually:
```
SNOOPDIR=~/software/pdfsnoop  # or wherever you like
mkdir -p $SNOOPDIR && cd $SNOOPDIR
python -m venv --system-site-packages pdfsnoop-venv
source pdfsnoop-venv/bin/activate
git clone https://github.com/qooxzuub/pdfsnoop
pip install ./pdfsnoop
```
For the second method, if you add `$SNOOPDIR/pdfsnoop-venv/bin` to your `PATH`
then running `pdfsnoop` should work.

### macOS

The easiest route is [Homebrew](https://brew.sh). Install it if you haven't already, then:

```bash
# Install GTK 3, Poppler (with GObject introspection), and PyGObject
brew install gtk+3 pygobject3 poppler

# Clone and install pdfsnoop into a venv that can see the Homebrew packages
git clone https://github.com/qooxzuub/pdfsnoop
cd pdfsnoop
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install .
```

Run it with:
```bash
pdfsnoop file.pdf
```

> **Note:** GTK apps on macOS render through XQuartz or the Quartz backend and may look a little out of place — that's normal. If the window doesn't appear, make sure you haven't accidentally activated a venv without `--system-site-packages` (the Homebrew GTK libraries won't be visible otherwise).

### Windows

A pre-built installer (`.msi`) and portable `.zip` are available on the
[releases page](https://github.com/qooxzuub/pdfsnoop/releases) — download and
run, no setup required.

To build the installer locally, install the dependencies listed in the Windows CI workflow, then run:

```
/mingw64/bin/python3.exe windows/setup_win32.py bdist_msi
```

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
