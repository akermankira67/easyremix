"""Local python-for-android recipe for PyAV, overriding the built-in one.

The built-in recipe (p4a v2026.05.09) pins PyAV 13.1 and depends on p4a's ``cython``
recipe (Cython 0.29), which no longer compiles for Python 3.14, and PyAV 13 uses APIs
that FFmpeg 8 removed. PyAV 17.1 supports FFmpeg 8 (the version p4a's ffmpeg recipe
builds) and compiles with a modern Cython installed into the host Python instead.
"""
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


recipe = PyAVRecipe()
