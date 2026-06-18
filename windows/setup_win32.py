# This file is derived from https://github.com/pdfarranger/pdfarranger/blob/main/setup_win32.py
# and is licensed under the GNU General Public License v3 or later.
# The rest of pdfsnoop is licensed under the Mozilla Public License 2.0.

VERSION = '0.1.0'

from cx_Freeze import setup, Executable

import os
import sys
import distutils.cmd
import shutil

# Run with cwd = repo root regardless of where this script lives
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
os.chdir(REPO_ROOT)

ENTRY_POINT = os.path.join(REPO_ROOT, 'src', 'pdfsnoop', '__main__.py')

include_files = []

def clean_build():
    if not os.path.isdir('build'):
        return
    keep = ['lib']
    for d in os.listdir('build'):
        if d not in keep:
            shutil.rmtree(os.path.join('build', d))

clean_build()

def addfile(relpath, warn_missing=True):
    f = os.path.join(sys.prefix, relpath)
    if warn_missing and not os.path.isfile(f):
        print("{} cannot be found.".format(f), file=sys.stderr)
    else:
        include_files.append((f, relpath))

def addicons():
    addfile("share/icons/hicolor/index.theme", warn_missing=False)
    addfile("share/icons/Adwaita/index.theme", warn_missing=False)

    # Basic fallback icons
    for i in ['places/folder', 'mimetypes/text-x-generic']:
        addfile(os.path.join('share/icons/Adwaita/16x16/', i + '.png'), warn_missing=False)

    # Symbolic icons used by GTK file chooser and window decorations
    icons = [
        'places/user-desktop',
        'places/user-home',
        'actions/document-open',
        'actions/document-save',
        'actions/document-save-as',
        'actions/open-menu',
        'actions/zoom-in',
        'actions/zoom-out',
        'ui/pan-down',
        'ui/pan-end',
        'ui/pan-start',
        'ui/pan-up',
        'ui/window-close',
        'ui/window-maximize',
        'ui/window-minimize',
        'ui/window-restore',
        'devices/drive-harddisk',
        'places/folder-documents',
        'places/folder-download',
    ]
    for i in icons:
        addfile(os.path.join('share/icons/Adwaita/symbolic/', i + '-symbolic.svg'), warn_missing=False)

# DLLs that cx_Freeze won't auto-discover via import analysis
required_dlls = [
    'poppler-glib-8',
]
for dll in required_dlls:
    fn = 'lib' + dll + '.dll'
    include_files.append((os.path.join(sys.prefix, 'bin', fn), fn))

# GObject introspection typelibs — one per namespace used by pdfsnoop
required_gi_namespaces = [
    "Gtk-3.0",
    "Gdk-3.0",
    "cairo-1.0",
    "Pango-1.0",
    "GObject-2.0",
    "GLib-2.0",
    "Gio-2.0",
    "GdkPixbuf-2.0",
    "GModule-2.0",
    "Atk-1.0",
    "Poppler-0.18",
    "HarfBuzz-0.0",
    "freetype2-2.0",
]
for ns in required_gi_namespaces:
    addfile("lib/girepository-1.0/{}.typelib".format(ns))

# Pixbuf loaders (needed to render anything on screen)
addfile("lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-png.dll")
addfile("lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-bmp.dll")
addfile("lib/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-svg.dll", warn_missing=False)
addfile("lib/gdk-pixbuf-2.0/2.10.0/loaders.cache")

# GLib settings schemas (GTK needs these to start)
addfile("share/glib-2.0/schemas/gschemas.compiled")

addicons()

# Poppler encoding data (needed for non-Latin PDFs)
from_path = os.path.join(sys.prefix, 'share/poppler/')
to_path = 'lib/share/poppler/'
if os.path.isdir(from_path):
    include_files.append((from_path, to_path))

# gspawn helper — needed for any Gio.AppInfo / URI launching
from_path = os.path.join(sys.prefix, 'bin', 'gspawn-win64-helper.exe')
if os.path.isfile(from_path):
    include_files.append((from_path, 'gspawn-win64-helper.exe'))

build_options = dict(
    packages=['gi', 'pikepdf'],
    excludes=['tkinter', 'test'],
    include_files=include_files,
)

def get_target_name(suffix):
    return 'pdfsnoop-{}-windows-{}'.format(VERSION, suffix)

msi_options = dict(
    upgrade_code='{d3f2a1b0-4e6c-11ee-be56-0242ac120002}',
    extensions=[{
        "extension": "pdf",
        "verb": "open",
        "executable": "pdfsnoop.exe",
        "argument": '"%1"',
    }]
)

class bdist_zip(distutils.cmd.Command):
    """Create a portable Windows .zip distribution."""
    description = "create a \"zip\" distribution"
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        build_base = self.get_finalized_command('build').build_base
        build_exe = self.get_finalized_command('build_exe')
        build_exe.build_exe = os.path.join(build_base, self.distribution.get_fullname())
        build_exe.run()
        dist_dir = self.get_finalized_command('bdist').dist_dir
        archname = os.path.join(dist_dir, get_target_name('portable'))
        self.make_archive(archname, 'zip', root_dir=build_base,
                          base_dir=self.distribution.get_fullname())
        shutil.rmtree(build_exe.build_exe)

setup(
    name='pdfsnoop',
    version=VERSION,
    description='GTK desktop tool for exploring PDF internals',
    options=dict(build_exe=build_options, bdist_msi=msi_options),
    cmdclass={'bdist_zip': bdist_zip},
    packages=['pdfsnoop'],
    executables=[Executable(
        ENTRY_POINT,
        base='Win32GUI' if sys.platform == 'win32' else None,
        targetName='pdfsnoop.exe',
        shortcutName='pdfsnoop',
        shortcutDir='StartMenuFolder',
    )]
)
