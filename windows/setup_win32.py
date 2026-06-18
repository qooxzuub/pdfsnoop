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


# -----------------------------
# CLEAN BUILD
# -----------------------------
def clean_build():
    if not os.path.isdir('build'):
        return
    for d in os.listdir('build'):
        if d != 'lib':
            shutil.rmtree(os.path.join('build', d), ignore_errors=True)

clean_build()


# -----------------------------
# TREE BUNDLER
# -----------------------------
def add_tree(src_rel, dst_rel=None):
    """
    Recursively include a full GTK/GI/runtime tree.
    This replaces fragile per-library enumeration.
    """
    src = os.path.join(sys.prefix, src_rel)

    if not os.path.exists(src):
        print(f"[gtk] missing tree: {src_rel}", file=sys.stderr)
        return

    if os.path.isdir(src):
        for root, _, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, sys.prefix)
                include_files.append((full, rel))
    else:
        include_files.append((src, dst_rel or src_rel))


# -----------------------------
# GTK / GI COMPLETE RUNTIME
# -----------------------------
def add_gtk_runtime():
    # GI + introspection (fixes freetype, harfbuzz, pango, etc.)
    add_tree("lib/girepository-1.0")

    # GTK core runtime
    add_tree("lib/gtk-3.0")
    add_tree("lib/gdk-pixbuf-2.0")

    # Fonts + rendering stack (CRITICAL for freetype2 issue)
    add_tree("lib/fontconfig")
    add_tree("lib/freetype2")
    add_tree("etc/fonts")

    # System schemas + icons
    add_tree("share/glib-2.0")
    add_tree("share/icons")

    # Optional poppler data (PDF rendering backend)
    add_tree("share/poppler")

    # GDK pixbuf loaders must exist for image decoding
    add_tree("lib/gdk-pixbuf-2.0")


add_gtk_runtime()


# -----------------------------
# GTK DLL RUNTIME (NO MANUAL LISTING)
# -----------------------------
# Instead of fragile per-DLL selection, include full bin runtime
add_tree("bin")


# -----------------------------
# POPPLER EXTRA SAFETY
# -----------------------------
poppler_dir = os.path.join(sys.prefix, "share/poppler")
if os.path.isdir(poppler_dir):
    include_files.append((poppler_dir, "lib/share/poppler"))


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
# MSI (kept minimal for cx_Freeze 6.x compatibility)
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
