"""Assembles the player's package for one platform into OUT/spire-bot/ and zips it.

  python tools/make_package.py mac-arm64|win-x64 --core FILE --python DIR_OR_ZIP --jar mod/build/spirebot.jar --out OUT

--core:   the compiled simulator module (_core.cpython-312-darwin.so or _core.cp312-win_amd64.pyd)
--python: mac: a python-build-standalone 3.12 folder (bin/, lib/); win: the official python-3.12.x-embed-amd64.zip
numpy and onnxruntime are fetched as the platform's official wheels with uv and unpacked (no dependencies: the bridge
imports nothing else). The last step is the content scan (tools/scan_package.py): anything it finds stops the build.
Developer tool; the package itself contains none of tools/ or tests/.
"""
import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"mac-arm64": "aarch64-apple-darwin", "win-x64": "x86_64-pc-windows-msvc"}
PINS = ("numpy==2.5.4", "onnxruntime==1.23.2")  # the same versions on every platform
BRIDGE = ("__init__.py", "bridge.py", "auto.py", "cm_state.py", "keys.py", "seed_string.py", "autosave.py", "policy.py", "search.py",
          "config.py", "preflight.py", "messages.py", "panel.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("platform", choices=PLATFORMS)
    ap.add_argument("--core", required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--jar", default=str(ROOT / "mod/build/spirebot.jar"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--no-scan", action="store_true", help="development only: build even if the content scan fails")
    a = ap.parse_args()
    win = a.platform == "win-x64"
    out = Path(a.out).resolve()
    pkg = out / "spire-bot"
    if pkg.exists():
        shutil.rmtree(pkg)
    for d in ("bridge/spire_bot", "runtime/site-packages/spire_sim", "data", "models", "mod", "LICENSES", "logs"):
        (pkg / d).mkdir(parents=True)
    for f in BRIDGE:
        shutil.copyfile(ROOT / "bridge/spire_bot" / f, pkg / "bridge/spire_bot" / f)
    shutil.copyfile(ROOT / "data/cm_move_table.json", pkg / "data/cm_move_table.json")
    shutil.copyfile(a.jar, pkg / "mod/spirebot.jar")
    shutil.copyfile(a.core, pkg / "runtime/site-packages/spire_sim" / Path(a.core).name)
    shutil.copyfile(ROOT / "package/spire_sim_init.py", pkg / "runtime/site-packages/spire_sim/__init__.py")
    for f in ("launch.py", "install.py", "spire-bot.toml") + (("install.bat", "uninstall.bat") if win else ("install.sh", "uninstall.sh")):
        shutil.copy(ROOT / "package" / f, pkg / f)
    shutil.copyfile(ROOT / "package/README.md", pkg / "README.md")
    shutil.copyfile(ROOT / "package/README.zh.md", pkg / "README.zh.md")
    for f in (ROOT / "LICENSES").iterdir():
        shutil.copyfile(f, pkg / "LICENSES" / f.name)
    (pkg / "models/PUT_MODEL_HERE.txt").write_text("Put the model file (model.onnx) into this folder. See README.md.\n")
    (pkg / "VERSION").write_text(f"{a.version} {a.platform}\n")
    # the Python runtime
    rt = pkg / "runtime/python"
    if win:
        with zipfile.ZipFile(a.python) as z:
            z.extractall(rt)
    else:
        shutil.copytree(a.python, rt, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "test", "tests", "idlelib", "tkinter", "turtledemo",
                                                      "ensurepip", "lib2to3", "include", "share", "pkgconfig", "config-3.12-darwin",
                                                      "libtcl*", "libtk*", "tcl8*", "tk8*", "itcl4*", "thread2*", "site-packages"))
    # numpy and onnxruntime, the platform's wheels, unpacked
    subprocess.run(["uv", "pip", "install", "--quiet", "--no-deps", "--target", str(pkg / "runtime/site-packages"),
                    "--python-platform", PLATFORMS[a.platform], "--python-version", "3.12", "--only-binary", ":all:",
                    "--no-compile", *PINS], check=True)
    for junk in list((pkg / "runtime/site-packages").glob("bin")) + list((pkg / "runtime/site-packages").rglob("__pycache__")):
        shutil.rmtree(junk, ignore_errors=True)
    (pkg / "runtime/site-packages/.lock").unlink(missing_ok=True)
    if win:  # the Windows module cannot be loaded here: check its headers against the Python the package ships
        subprocess.run([sys.executable, str(ROOT / "tools/pe_check.py"),
                        str(pkg / "runtime/site-packages/spire_sim" / Path(a.core).name), str(rt / "python312.dll")], check=True)
        for need in ("python.exe", "python312.dll", "python312.zip", "vcruntime140.dll", "_ctypes.pyd"):
            if not (rt / need).exists():
                sys.exit(f"the Windows Python runtime lacks {need}")
    subprocess.run([sys.executable, str(ROOT / "tools/scan_package.py"), str(pkg)], check=not a.no_scan)
    z = shutil.make_archive(str(out / f"spire-bot-{a.version}-{a.platform}"), "zip", out, "spire-bot")
    size = sum(f.stat().st_size for f in pkg.rglob("*") if f.is_file())
    print(f"{z}: {Path(z).stat().st_size / 1e6:.1f} MB zipped, {size / 1e6:.1f} MB unpacked")


if __name__ == "__main__":
    main()
