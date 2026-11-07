"""Local python-for-android recipe for PyAV, overriding the built-in one.

The built-in recipe (p4a v2026.05.09) pins PyAV 13.1 and depends on p4a's ``cython``
recipe (Cython 0.29), which no longer compiles for Python 3.14, and PyAV 13 uses APIs
that FFmpeg 8 removed. PyAV 17.1 supports FFmpeg 8 (the version p4a's ffmpeg recipe
builds) and compiles with a modern Cython installed into the host Python instead.
"""
import glob
import os
import shutil

from pythonforandroid.logger import info
from pythonforandroid.recipe import CythonRecipe, Recipe


class PyAVRecipe(CythonRecipe):
    name = "av"
    version = "17.1.0"
    url = "https://files.pythonhosted.org/packages/source/a/av/av-{version}.tar.gz"

    depends = ["python3", "ffmpeg", "av_codecs"]
    opt_depends = ["openssl"]
    hostpython_prerequisites = ["Cython>=3.1.4,<3.3", "setuptools>=77"]

    def build_arch(self, arch):
        # CythonRecipe does not install host prerequisites by itself
        self.install_hostpython_prerequisites()
        super().build_arch(arch)

    def get_recipe_env(self, arch, with_flags_in_cc=True):
        env = super().get_recipe_env(arch, with_flags_in_cc)
        ffmpeg_dir = Recipe.get_recipe("ffmpeg", self.ctx).get_build_dir(arch.arch)
        self.setup_extra_args = ["--ffmpeg-dir={}".format(ffmpeg_dir)]
        return env

    def install_python_package(self, arch, name=None, env=None, is_dir=True):
        """Copy the already cross-compiled package into site-packages.

        The default ``pip install .`` would rebuild PyAV in an isolated environment that
        knows nothing about our FFmpeg (and rejects ``--ffmpeg-dir``). p4a renames the
        ``*.cpython-314-x86_64-linux-gnu.so`` files to ``*.so`` when it bundles them.
        """
        build_dir = self.get_build_dir(arch.arch)
        target = os.path.join(self.ctx.get_python_install_dir(arch.arch), "av")
        info("Installing av into site-packages: {}".format(target))
        copy_built_package(build_dir, target)


def copy_built_package(build_dir, target):
    """Compiled modules from build/lib.*/av, plus the .py modules that were not compiled."""
    lib_dirs = glob.glob(os.path.join(build_dir, "build", "lib.*", "av"))
    if not lib_dirs:
        raise RuntimeError("PyAV build output (build/lib.*/av) not found in " + build_dir)
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(lib_dirs[0], target)
    src_root = os.path.join(build_dir, "av")
    for root, _dirs, files in os.walk(src_root):
        dest = os.path.join(target, os.path.relpath(root, src_root))
        for f in files:
            if not (f.endswith(".py") or f == "py.typed"):
                continue
            stem = f[:-3]
            if f.endswith(".py") and (glob.glob(os.path.join(dest, stem + ".*.so"))
                                      or os.path.exists(os.path.join(dest, stem + ".so"))):
                continue  # compiled version exists
            os.makedirs(dest, exist_ok=True)
            shutil.copy2(os.path.join(root, f), dest)


recipe = PyAVRecipe()
