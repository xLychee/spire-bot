"""tools/scan_package.py searches an ONNX model's text (names, doc strings, metadata), not its weights: float bytes
hold any short word by chance (an earlier model's weights held one of the scan's words three times)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
from onnx import helper, numpy_helper  # noqa: E402

SCAN = Path(__file__).resolve().parents[1] / "tools" / "scan_package.py"
WORD = "zqxjkv"  # stands for a private word


def model(path: Path, doc: str, weight_bytes: bytes):
    w = np.frombuffer(weight_bytes.ljust(16, b"\0"), dtype=np.float32)
    init = numpy_helper.from_array(w, "w")
    node = helper.make_node("Identity", ["w"], ["y"])
    g = helper.make_graph([node], "g", [], [helper.make_tensor_value_info("y", onnx.TensorProto.FLOAT, [len(w)])], [init])
    m = helper.make_model(g, doc_string=doc)
    onnx.save(m, str(path))


def scan(folder: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    words = {k: [] for k in ("private", "internal", "bad_names", "internal_binary", "third")}
    words["private"] = [WORD]
    words["internal_binary"] = [WORD]
    wf = tmp_path / "words.json"
    wf.write_text(json.dumps(words))
    return subprocess.run([sys.executable, str(SCAN), str(folder)], capture_output=True, text=True,
                          env={**os.environ, "SCAN_WORDS_FILE": str(wf)})


def test_the_word_in_the_weights_is_not_a_finding(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    model(d / "model.onnx", "a model", f" {WORD} ".encode())
    assert WORD.encode() in (d / "model.onnx").read_bytes()
    r = scan(d, tmp_path)
    assert r.returncode == 0, r.stdout


def test_the_word_in_the_models_text_is_a_finding(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    model(d / "model.onnx", f"made by {WORD}", b"")
    r = scan(d, tmp_path)
    assert r.returncode == 1 and "model.onnx" in r.stdout, r.stdout
