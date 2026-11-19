"""Stops a package (or any folder) from going out with things that must not be in it.

  SCAN_WORDS_FILE=/path/to/scan_words.json python tools/scan_package.py FOLDER_OR_ZIP

The words are not part of this repository. SCAN_WORDS_FILE names a JSON file outside it with five lists of Python
regular expressions (UTF-8 text, matched against the bytes of every file):
  private         the build machine's paths and the project people's names/addresses
  internal        internal project and tool names, searched in text files
  bad_names       file-name patterns of files that must not ship
  internal_binary the internal part of the list, searched in binary files
  third           the smaller list searched in third-party runtime files
tools/scan_words.example.json shows the shape and contains no real words. Without SCAN_WORDS_FILE the scan does not
run. Besides the lists, the project's own issue numbers are searched in every file that is not third-party, text and
binary alike: notes like "(B<n>: ...)" and RD-/FH- numbers (ISSUE_IDS). Exit code 1 and one line per finding.
"""
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

TEXT = (".py", ".md", ".txt", ".toml", ".json", ".sh", ".bat", ".java", ".properties", "")
THIRD_PARTY = ("runtime/python/", "runtime/site-packages/numpy", "runtime/site-packages/onnxruntime")
KEYS = ("private", "internal", "bad_names", "internal_binary", "third")
# the project's issue numbers: not private words, so they live here rather than in the word file. The simulator
# module's help texts once carried two numbered notes, which the narrow binary list did not catch
ISSUE_IDS = [re.compile(rb"\(B[0-9]+:"), re.compile(rb"(?<![A-Za-z0-9])(?:RD|FH)-[0-9]+")]


def load_words():
    path = os.environ.get("SCAN_WORDS_FILE")
    if not path:
        sys.exit("SCAN_WORDS_FILE is not set: point it at the scan word list (it is not part of this repository; "
                 "tools/scan_words.example.json shows the shape)")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        sys.exit(f"SCAN_WORDS_FILE: cannot read {path}: {e}")
    except ValueError as e:
        sys.exit(f"SCAN_WORDS_FILE: {path} is not valid JSON: {e}")
    if not isinstance(data, dict):
        sys.exit(f"SCAN_WORDS_FILE: {path} must hold a JSON object")
    words = {}
    for k in KEYS:
        pats = data.get(k)
        if not isinstance(pats, list):
            sys.exit(f"SCAN_WORDS_FILE: {path} has no list {k!r}")
        try:
            words[k] = [re.compile(p.encode("utf-8")) for p in pats]
        except (re.error, AttributeError) as e:
            sys.exit(f"SCAN_WORDS_FILE: {path}: bad pattern in {k!r}: {e}")
    return words


def onnx_text(f: Path) -> bytes:
    """All the strings of an ONNX model but its tensors' raw data, one per line (needs the onnx package)."""
    import onnx
    m = onnx.load(str(f), load_external_data=False)
    out = [m.producer_name, m.producer_version, m.domain, m.doc_string, m.graph.name, m.graph.doc_string]
    out += [f"{p.key}={p.value}" for p in m.metadata_props]
    for n in m.graph.node:
        out += [n.name, n.op_type, n.domain, n.doc_string, *n.input, *n.output]
        out += [a.name for a in n.attribute] + [a.s.decode("utf-8", "replace") for a in n.attribute if a.s]
    for t in m.graph.initializer:
        out += [t.name, t.doc_string] + [d.key + "=" + d.value for d in t.external_data]
        out += [s.decode("utf-8", "replace") for s in t.string_data]
    for v in list(m.graph.input) + list(m.graph.output) + list(m.graph.value_info):
        out += [v.name, v.doc_string]
    out += [o.domain for o in m.opset_import]
    return "\n".join(x for x in out if x).encode("utf-8")


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: SCAN_WORDS_FILE=/path/to/scan_words.json python tools/scan_package.py FOLDER_OR_ZIP")
    root = Path(sys.argv[1])
    words = load_words()
    tmp = None
    if root.is_file() and zipfile.is_zipfile(root):
        # a zip: what is in it (as a folder path it would have been walked as nothing)
        tmp = tempfile.TemporaryDirectory(prefix="scan_")
        with zipfile.ZipFile(root) as z:
            z.extractall(tmp.name)
        shown, root = root, Path(tmp.name)
    else:
        shown = root
    found = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        rel = f.relative_to(root).as_posix()
        # the Steam Workshop item (tools/make_workshop.py): spire-bot/runtime/win|mac/... is the package's runtime/...
        pkg_rel = re.sub(r"^runtime/(win|mac)/", "runtime/", re.sub(r"^spire-bot/", "", rel))
        third = pkg_rel.startswith(THIRD_PARTY) or ".dist-info/" in rel
        if not third and any(p.search(f.name.encode("utf-8")) for p in words["bad_names"]):
            found.append(f"{rel}: file of a kind that must not be in the package")
        if f.suffix.lower() == ".onnx":
            # a model: every text it carries (names, doc strings, metadata, producer), searched as text; the weights
            # are float bytes in which a short word turns up by chance (three times in the first Workshop model)
            data, text = onnx_text(f), True
        else:
            data = f.read_bytes()
            text = f.suffix.lower() in TEXT
        pats = words["third"] if third else (words["private"] + (words["internal"] if text else words["internal_binary"])
                                             + ISSUE_IDS)
        for p in pats:
            m = p.search(data)
            if m:
                a = max(0, m.start() - 30)
                found.append(f"{rel}: {p.pattern.decode('utf-8', 'replace')!r} near {data[a:m.end() + 30]!r}")
    for x in found:
        print(x)
    print(f"scan: {len(found)} finding(s) in {shown}")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
