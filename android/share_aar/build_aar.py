"""Packs android/share_aar into aar/easyremix-share.aar (an AAR is a zip file)."""
import io
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "aar", "easyremix-share.aar")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
empty_jar = io.BytesIO()
with zipfile.ZipFile(empty_jar, "w") as jar:
    jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as aar:
    aar.write(os.path.join(HERE, "AndroidManifest.xml"), "AndroidManifest.xml")
    aar.write(os.path.join(HERE, "R.txt"), "R.txt")
    aar.write(os.path.join(HERE, "res", "xml", "easyremix_share_paths.xml"), "res/xml/easyremix_share_paths.xml")
    aar.writestr("classes.jar", empty_jar.getvalue())
print("wrote", OUT)
