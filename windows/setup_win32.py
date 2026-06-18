# This file is derived from https://github.com/pdfarranger/pdfarranger/blob/main/setup_win32.py
# and is licensed under the GNU General Public License v3 or later.
# The rest of pdfsnoop is licensed under the Mozilla Public License 2.0.

VERSION = '0.1.0'

from cx_Freeze import setup, Executable

import os
import sys
import distutils.cmd
import shutil
import cx_Freeze

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
            shutil.rmtree(os.path.join('build', d), ignore_errors=True)

clean_build()

# -------------------------
# FIXED: safe file addition
# -------------------------
def addfile(relpath):
    f = os.path.join(sys.prefix, relpath)

    if os.path.isfile(f):
        include_files.append((f, relpath))
    else:
        print(f"{f} cannot be found.", file=sys.stderr)


def addicons():
    addfile("share/icons/hicolor/index.theme")
    addfile("share/icons/Adwaita/index.theme")

    # Basic fallback icons
    icon_base = os.path.join(sys.prefix, "share/icons")

    for i in ['places/folder', 'mimetypes/text-x-generic']:
        p = os.path.join(icon_base, "Adwaita/16x16", i + ".png")
        if os.path.isfile(p):
            include_files.append((p, os.path.relpath(p, sys.prefix)))

    # Symbolic icons (SAFE CHECKED)
    icons = [
        'places/user-desktop',
        'places/user-home',
        'actions/document-open',
        'actions/document-save',
        'actions/document-save-as',
        'actions/open-menu',
        'actions/zoom-in',
        'actions/zoom-out',
        'ui/window-close',
        'ui/window-maximize',
        'ui/window-minimize',
    ]

    sym_base = os.path.join(icon_base, "Adwaita/symbolic")

    for i in icons:
        p = os.path.join(sym_base, i + "-symbolic.svg")
        if os.path.isfile(p):
            include_files.append((p, os.path.relpath(p, sys.prefix)))


required_dlls = ['poppler-glib-8']

for dll in required_dlls:
    fn = 'lib' + dll + '.dll'
    p = os.path.join(sys.prefix, 'bin', fn)
    if os.path.isfile(p):
        include_files.append((p, fn))


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
]

for ns in required_gi_namespaces:
    addfile("lib/girepository-1.0/{}.typelib".format(ns))


# Pixbuf loaders
pixbuf_loaders = [
    "libpixbufloader-png.dll",
    "libpixbufloader-bmp.dll",
]

for loader in pixbuf_loaders:
    p = os.path.join(sys.prefix, "lib/gdk-pixbuf-2.0/2.10.0/loaders", loader)
    if os.path.isfile(p):
        include_files.append((p, os.path.relpath(p, sys.prefix)))

cache = os.path.join(sys.prefix, "lib/gdk-pixbuf-2.0/2.10.0/loaders.cache")
if os.path.isfile(cache):
    include_files.append((cache, os.path.relpath(cache, sys.prefix)))


addfile("share/glib-2.0/schemas/gschemas.compiled")

from_path = os.path.join(sys.prefix, 'share/poppler')
if os.path.isdir(from_path):
    include_files.append((from_path, 'lib/share/poppler'))

gspawn = os.path.join(sys.prefix, 'bin', 'gspawn-win64-helper.exe')
if os.path.isfile(gspawn):
    include_files.append((gspawn, 'gspawn-win64-helper.exe'))


build_options = dict(
    packages=['gi', 'pikepdf'],
    excludes=['tkinter', 'test'],
    include_files=include_files,
)


def get_target_name(suffix):
    return 'pdfsnoop-{}-windows-{}'.format(VERSION, suffix)


# -------------------------
# FIXED: MSI compatibility
# -------------------------
msi_options = dict(
    upgrade_code='{d3f2a1b0-4e6c-11ee-be56-0242ac120002}',
)


class bdist_zip(distutils.cmd.Command):
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

        self.make_archive(
            archname,
            'zip',
            root_dir=build_base,
            base_dir=self.distribution.get_fullname()
        )

        if os.path.isdir(build_exe.build_exe):
            shutil.rmtree(build_exe.build_exe, ignore_errors=True)


setup(
    name='pdfsnoop',
    version=VERSION,
    description='GTK desktop tool for exploring PDF internals',
    options=dict(build_exe=build_options, bdist_msi=msi_options),
    cmdclass={'bdist_zip': bdist_zip},
    executables=[Executable(
        ENTRY_POINT,
        base='Win32GUI' if sys.platform == 'win32' else None,
        targetName='pdfsnoop.exe',
        shortcutName='pdfsnoop',
        shortcutDir='StartMenuFolder',
    )]
)