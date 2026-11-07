"""Install charset-normalizer from its pure-Python wheel.

``requests`` (pulled in by Kivy) needs charset-normalizer. python-for-android's
dependency resolver asks pip for Android wheels, gets the compiled
``cp314-android_24_arm64_v8a`` wheel and then fails to install it with the host pip
("not a supported wheel on this platform"). A package that has a recipe is skipped by
that resolver, so this recipe installs the universal ``py3-none-any`` wheel instead.
"""
import hashlib
import os
import urllib.request
import zipfile

from pythonforandroid.logger import info
from pythonforandroid.recipe import Recipe

WHEEL_URL = ("https://files.pythonhosted.org/packages/fc/ad/d07d7862a62ffa6d79d68074d14823243dd235a77"
             "c45262acbf6adeb28bf/charset_normalizer-3.5.2-py3-none-any.whl")
WHEEL_SHA256 = "b6b751274acb69d77b3323d6b7dbaa3c7fdfc1eb829b7eb61d262f32e1af9685"


class CharsetNormalizerRecipe(Recipe):
    name = "charset_normalizer"
    version = "3.5.2"
    url = None  # downloaded in build_arch (a wheel, not a source archive)
    depends = ["python3"]

    def should_build(self, arch):
        return not os.path.isdir(os.path.join(self.ctx.get_python_install_dir(arch.arch),
                                              "charset_normalizer"))

    def build_arch(self, arch):
        target = self.ctx.get_python_install_dir(arch.arch)
        os.makedirs(target, exist_ok=True)
        wheel = os.path.join(self.ctx.packages_path, "charset_normalizer-3.5.2-py3-none-any.whl")
        os.makedirs(os.path.dirname(wheel), exist_ok=True)
        if not os.path.isfile(wheel):
            info("Downloading " + WHEEL_URL)
            urllib.request.urlretrieve(WHEEL_URL, wheel)
        with open(wheel, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest != WHEEL_SHA256:
            os.remove(wheel)
            raise RuntimeError("charset-normalizer wheel checksum mismatch: " + digest)
        info("Installing pure-Python charset-normalizer into " + target)
        with zipfile.ZipFile(wheel) as z:
            z.extractall(target)


recipe = CharsetNormalizerRecipe()
