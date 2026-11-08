"""Install curl_cffi from its official Android wheel (cp314, Android API 24, arm64-v8a).

yt-dlp needs curl_cffi to act like a real browser; TikTok refuses everything else. The wheel
ships the compiled module (``curl_cffi/_wrapper.abi3.so``, with curl-impersonate inside) and
its own C++ runtime (``curl_cffi.libs/libc++_shared-<hash>.so``, found via
``RUNPATH $ORIGIN/../curl_cffi.libs``). Its only Python-level native dependency is cffi's
``_cffi_backend``, which python-for-android's ``cffi`` recipe builds.
"""
import hashlib
import os
import urllib.request
import zipfile

from pythonforandroid.logger import info
from pythonforandroid.recipe import Recipe

VERSION = "0.17.0"
WHEEL_NAME = f"curl_cffi-{VERSION}-cp314-cp314-android_24_arm64_v8a.whl"
WHEEL_URL = ("https://files.pythonhosted.org/packages/ea/ef/40e7c64a01d9928c16f79cf0bf7f1afc9be02f331a8a32e4617b60b815f1/"
             + WHEEL_NAME)
WHEEL_SHA256 = "0d85bec9648bf7c14c3b43056ba82521740f72cd96ed7700a0e1973e7fc93f74"


class CurlCffiRecipe(Recipe):
    name = "curl_cffi"
    version = VERSION
    url = None  # downloaded in build_arch (a wheel, not a source archive)
    depends = ["python3", "cffi"]

    def should_build(self, arch):
        return not os.path.isfile(os.path.join(self.ctx.get_python_install_dir(arch.arch),
                                               "curl_cffi", "_wrapper.abi3.so"))

    def build_arch(self, arch):
        if arch.arch != "arm64-v8a":
            raise RuntimeError("curl_cffi: only an arm64-v8a Android wheel exists")
        target = self.ctx.get_python_install_dir(arch.arch)
        os.makedirs(target, exist_ok=True)
        wheel = os.path.join(self.ctx.packages_path, WHEEL_NAME)
        os.makedirs(os.path.dirname(wheel), exist_ok=True)
        if not os.path.isfile(wheel):
            info("Downloading " + WHEEL_URL)
            urllib.request.urlretrieve(WHEEL_URL, wheel)
        with open(wheel, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest != WHEEL_SHA256:
            os.remove(wheel)
            raise RuntimeError("curl_cffi wheel checksum mismatch: " + digest)
        info("Installing curl_cffi (Android wheel) into " + target)
        with zipfile.ZipFile(wheel) as z:
            z.extractall(target)


recipe = CurlCffiRecipe()
