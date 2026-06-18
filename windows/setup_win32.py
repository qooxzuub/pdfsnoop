# This file is derived from https://github.com/pdfarranger/pdfarranger/blob/main/setup_win32.py
# and is licensed under the GNU General Public License v3 or later.
# The rest of pdfsnoop is licensed under the Mozilla Public License 2.0.

VERSION = '0.1.0'

from cx_Freeze import setup, Executable

import os
import sys
import distutils.cmd
import shutil

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
        path = os.path.join('build', d)
        if d not in keep:
            shutil.rmtree(path, ignore_errors=True)

clean_build()


# -----------------------------
# GTK COMPLETENESS HELPERS
# -----------------------------
def safe_add(relpath, dest=None):
    src = os.path.join(sys.prefix, relpath)

    if os.path.isfile(src) or os.path.isdir(src):
        include_files.append((src, dest or relpath))
    else:
        print(f"[gtk] missing: {relpath}", file=sys.stderr)


def addfile(relpath):
    safe_add(relpath)


# -----------------------------
# ICONS (SIMPLIFIED + STABLE)
# -----------------------------
def addicons():
    safe_add("share/icons/hicolor")
    safe_add("share/icons/Adwaita")


# -----------------------------
# CLEAN BUILD FILE COLLECTION
# -----------------------------
required_dlls = ['poppler-glib-8']
for dll in required_dlls:
    safe_add(f"bin/lib{dll}.dll", f"lib{dll}.dll")


# -----------------------------
# GI TYPELIBS (COMPLETE SET)
# -----------------------------
GI_NAMESPACES = [
    "Gtk-3.0",
    "Gdk-3.0",
    "GdkPixbuf-2.0",
    "Pango-1.0",
    "PangoCairo-1.0",
    "HarfBuzz-0.0",
    "cairo-1.0",
    "GObject-2.0",
    "GLib-2.0",
    "Gio-2.0",
    "GModule-2.0",
    "Atk-1.0",
    "Poppler-0.18",
]

for ns in GI_NAMESPACES:
    safe_add(f"lib/girepository-1.0/{ns}.typelib")


# -----------------------------
# GTK RUNTIME DLLS (CRITICAL FIX)
# -----------------------------
GTK_DLLS = [
    "bin/libgtk-3-0.dll",
    "bin/libgdk-3-0.dll",
    "bin/libpango-1.0-0.dll",
    "bin/libpangocairo-1.0-0.dll",
    "bin/libpangoft2-1.0-0.dll",
    "bin/libharfbuzz-0.dll",
    "bin/libcairo-2.dll",
    "bin/libgobject-2.0-0.dll",
    "bin/libglib-2.0-0.dll",
]

for dll in GTK_DLLS:
    safe_add(dll, os.path.basename(dll))


# -----------------------------
# PIXBUF LOADERS
# -----------------------------
PIXBUF_BASE = "lib/gdk-pixbuf-2.0/2.10.0"

safe_add(f"{PIXBUF_BASE}/loaders.cache")

for loader in [
    "libpixbufloader-png.dll",
    "libpixbufloader-bmp.dll",
    "libpixbufloader-svg.dll",
]:
    safe_add(f"{PIXBUF_BASE}/loaders/{loader}")


# -----------------------------
# GLIB SCHEMAS
# -----------------------------
safe_add("share/glib-2.0/schemas/gschemas.compiled")


# -----------------------------
# POPPLER DATA
# -----------------------------
poppler_dir = os.path.join(sys.prefix, "share/poppler")
if os.path.isdir(poppler_dir):
    include_files.append((poppler_dir, "lib/share/poppler"))


# -----------------------------
# GSPAWN HELPER
# -----------------------------
safe_add("bin/gspawn-win64-helper.exe", "gspawn-win64-helper.exe")


# -----------------------------
# BUILD OPTIONS
# -----------------------------
build_options = dict(
    packages=['gi', 'pikepdf'],
    excludes=['tkinter', 'test'],
    include_files=include_files,
)


def get_target_name(suffix):
    return f'pdfsnoop-{VERSION}-windows-{suffix}'


# -----------------------------
# MSI (KEPT SIMPLE FOR CX_FREEZE 6.2)
# -----------------------------
msi_options = dict(
    upgrade_code='{d3f2a1b0-4e6c-11ee-be56-0242ac120002}',
)


# -----------------------------
# ZIP BUILDER
# -----------------------------
class bdist_zip(distutils.cmd.Command):
    description = "create a portable zip distribution"
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        build_base = self.get_finalized_command('build').build_base

        build_exe = self.get_finalized_command('build_exe')
        build_exe.build_exe = os.path.join(
            build_base,
            self.distribution.get_fullname()
        )

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


# -----------------------------
# EXECUTABLE
# -----------------------------
executables = [
    Executable(
        ENTRY_POINT,
        base='Win32GUI' if sys.platform == 'win32' else None,
        targetName='pdfsnoop.exe',
        shortcutName='pdfsnoop',
        shortcutDir='StartMenuFolder',
    )
]


# -----------------------------
# SETUP
# -----------------------------
setup(
    name='pdfsnoop',
    version=VERSION,
    description='GTK desktop tool for exploring PDF internals',
    options=dict(build_exe=build_options, bdist_msi=msi_options),
    cmdclass={'bdist_zip': bdist_zip},
    executables=executables
)
