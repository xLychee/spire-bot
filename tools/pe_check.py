"""Static checks of a Windows module that cannot be run where the package is built.

  python tools/pe_check.py FILE.pyd [python312.dll]

Reads the PE headers: the file must be a 64-bit x86 DLL, export PyInit__core, and import only python312.dll, KERNEL32
and the Windows universal C runtime (api-ms-win-crt-*), which every supported Windows has. With python312.dll given (the one the package ships), every
Python function the module imports must be among that DLL's exports. Prints what it found;
exit code 1 if a check fails. This does not prove the module loads, only that it asks for nothing a player may lack.
"""
import re
import struct
import sys

ALLOWED = re.compile(r"^(python312\.dll|kernel32\.dll|api-ms-win-crt-[a-z0-9-]+\.dll)$", re.I)


def read(path):
    d = open(path, "rb").read()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    assert d[pe:pe + 4] == b"PE\0\0", "not a PE file"
    machine, nsec, _, _, _, optsize, chars = struct.unpack_from("<HHIIIHH", d, pe + 4)
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    assert magic == 0x20B, "not PE32+ (64-bit)"
    dirs = opt + 112
    exp_rva, _ = struct.unpack_from("<II", d, dirs)
    imp_rva, _ = struct.unpack_from("<II", d, dirs + 8)
    secs = [struct.unpack_from("<8sIIII", d, opt + optsize + 40 * i) for i in range(nsec)]

    def off(rva):
        for _, vsize, va, rsize, raw in secs:
            if va <= rva < va + max(vsize, rsize):
                return raw + rva - va
        raise ValueError(f"rva {rva:#x} in no section")

    def cstr(rva):
        o = off(rva)
        return d[o:d.index(b"\0", o)].decode("ascii", "replace")

    imports, symbols, o = [], {}, off(imp_rva)
    while True:
        lookup, _, _, name, first = struct.unpack_from("<IIIII", d, o)
        if not name and not first:
            break
        dll = cstr(name)
        imports.append(dll)
        t, names = off(lookup or first), []
        while True:
            v, = struct.unpack_from("<Q", d, t)
            if not v:
                break
            if not v >> 63:  # by name (not by ordinal): a 2-byte hint, then the name
                names.append(cstr((v & 0x7FFFFFFF) + 2))
            t += 8
        symbols[dll.lower()] = names
        o += 20
    exports = []
    if exp_rva:
        e = off(exp_rva)
        n_names, = struct.unpack_from("<I", d, e + 24)
        names_rva, = struct.unpack_from("<I", d, e + 32)
        exports = [cstr(struct.unpack_from("<I", d, off(names_rva) + 4 * i)[0]) for i in range(n_names)]
    return {"machine": machine, "dll": bool(chars & 0x2000), "imports": imports, "exports": exports, "symbols": symbols}


def main():
    info = read(sys.argv[1])
    bad = [i for i in info["imports"] if not ALLOWED.match(i)]
    problems = []
    if info["machine"] != 0x8664:
        problems.append(f"machine {info['machine']:#x}, expected x86-64 (0x8664)")
    if not info["dll"]:
        problems.append("not a DLL")
    if "PyInit__core" not in info["exports"]:
        problems.append(f"PyInit__core not exported (exports: {info['exports'][:5]})")
    if bad:
        problems.append(f"imports beyond Python and the system: {bad}")
    wanted = info["symbols"].get("python312.dll", [])
    if len(sys.argv) > 2:  # the python312.dll the package ships: every Python function the module asks for must be in it
        have = set(read(sys.argv[2])["exports"])
        missing = [n for n in wanted if n not in have]
        if missing:
            problems.append(f"{len(missing)} function(s) not in {sys.argv[2]}: {missing[:8]}")
    print(f"{sys.argv[1]}: x86-64 DLL, exports PyInit__core: {'PyInit__core' in info['exports']}, imports "
          f"{sorted(info['imports'])}, {len(wanted)} functions from python312.dll"
          + (" (all present in the shipped DLL)" if len(sys.argv) > 2 and not any('not in' in p for p in problems) else ""))
    for p in problems:
        print("PROBLEM:", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
