#!/usr/bin/env python3
"""check_susfs_symbols.py -- make sure every SuSFS function the KernelSU fork (and
the SuSFS kernel hunks) calls actually exists in this tree.

Why: a SuSFS *port* and a KernelSU *fork* evolve independently. The 4.14 port of
SuSFS v2.3.0 dropped susfs_try_umount()/susfs_add_try_umount() (only the deprecated
CMD_SUSFS_ADD_TRY_UMOUNT id survives in susfs_def.h), while KernelSU-Next still calls
both under CONFIG_KSU_SUSFS_TRY_UMOUNT -- which shows up as

    drivers/kernelsu/supercall/supercall.c:158: error: implicit declaration of
        function 'susfs_add_try_umount' [-Werror,-Wimplicit-function-declaration]
    drivers/kernelsu/hook/setuid_hook.c:180: undefined reference to 'susfs_try_umount'

and, worse, as a *link* error for any susfs_* symbol that is merely declared extern.
This script cross-checks call sites against real definitions in the tree and against
the guards that enable them, so a susfs/KSU bump fails here instead of mid-build.

usage:
    python3 tools/root-integration/check_susfs_symbols.py [srctree] [defconfig]

Exit 0 = all called SuSFS symbols resolve (or are guarded off).
"""

import os
import re
import sys
import glob

ARGV = sys.argv[1:]
ROOT = os.path.abspath(ARGV[0] if ARGV else ".")
DEFCONFIG = ARGV[1] if len(ARGV) > 1 else "arch/arm64/configs/vendor/xiaomi/miatoll_defconfig"

# A definition is "name(...) {" with the brace before any ';' -- tolerant of
# __attribute__ prefixes and of the brace sitting on the next line.
NAME_DEF = re.compile(r"^[^\n;{}]*\b((?:susfs|ksu_susfs)_[a-z0-9_]+)\s*\(", re.M)
VARS = re.compile(
    r"^(?:DEFINE_STATIC_KEY(?:_ONCE)?_(?:TRUE|FALSE)|DEFINE_MUTEX|DEFINE_SRCU|LIST_HEAD|DECLARE_RWSEM)"
    r"\(\s*([a-z0-9_]+)\s*\)",
    re.M,
)
DATA_DEF = re.compile(r"^(?:[A-Za-z_][\w \t\*]*?\s)?\b(susfs_[a-z0-9_]+)\s*(?:\[[^\]]*\])?\s*=\s*[^;]*;", re.M)
HDR_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
HDR_INLINE = re.compile(r"\b(?:static inline|[A-Za-z_][\w \t\*]*)\s+\b(susfs_[a-z0-9_]+)\s*\(", re.M)
HDR_TYPE = re.compile(r"\b(?:struct|enum|union|typedef)\s+((?:st_)?susfs_[a-z0-9_]+)", re.M)
CALL = re.compile(r"\b((?:susfs|ksu_susfs|ksu_handle|ksu_is|ksu_get|ksu_set)[a-z0-9_]*)\s*\(")
GUARD = re.compile(r"^\s*#\s*if(?:n?def)?\b(.*)$")
SELFDECL = re.compile(r"^\s*extern\s+[^;]*?\b(susfs_[a-z0-9_]+|ksu_[a-z0-9_]+)\s*[;\(]", re.M)

# susfs_* symbols can only live in these subtrees; scanning the whole kernel twice
# would cost a minute for nothing.
SCAN_ROOTS = ["fs", "include", "kernel", "mm", "security", "drivers", "KernelSU-Next/kernel"]
EXCLUDE_DIRS = {".git", "out", "AK3", "toolchains", "Documentation", "samples"}


def iter_sources(roots=None, skip_vendored=False):
    for top in (roots or SCAN_ROOTS):
        base = os.path.join(ROOT, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if not fn.endswith((".c", ".h")):
                    continue
                p = os.path.relpath(os.path.join(dirpath, fn), ROOT)
                if skip_vendored and (p.startswith("KernelSU-Next/") or p.startswith("fs/nomount/")):
                    continue
                yield p, os.path.join(ROOT, p)


def read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def enabled_configs():
    text = read(os.path.join(ROOT, DEFCONFIG))
    on = set(re.findall(r"^CONFIG_([A-Z0-9_]+)=y", text, re.M))
    off = set(re.findall(r"^#\s*CONFIG_([A-Z0-9_]+) is not set", text, re.M))
    return on, off


def defined_symbols():
    """Every susfs_* symbol that has a real definition somewhere in the tree."""
    found = set()
    for rel, path in iter_sources():
        t = read(path)
        if not t:
            continue
        for m in NAME_DEF.finditer(t):
            ahead = t[m.end():m.end() + 400]
            brace, semi = ahead.find("{"), ahead.find(";")
            if brace >= 0 and (semi < 0 or brace < semi):
                found.add(m.group(1))
        for rx in (DATA_DEF, VARS):
            found.update(rx.findall(t))
        if rel.startswith("include/linux/susfs"):
            found.update(HDR_DEFINE.findall(t))
            found.update(HDR_INLINE.findall(t))
            found.update(HDR_TYPE.findall(t))
    # KSU fork definitions (e.g. susfs_ksu_sid lives in KernelSU-Next/kernel/selinux/selinux.c)
    for rel, path in iter_sources(roots=["drivers/kernelsu", "KernelSU-Next/kernel"]):
        found.update(re.findall(r"^\w[\w \t\*]*\b(susfs_[a-z0-9_]+)\s*(?:\(|=)", read(path), re.M))
    return found


def guarded_files():
    """Files whose susfs_* call sites must resolve: KSU kernel side + SuSFS hunks."""
    files = set(glob.glob(os.path.join(ROOT, "drivers/kernelsu/**/*.c"), recursive=True))
    files |= {os.path.join(ROOT, p) for p in (
        "fs/stat.c", "fs/statfs.c", "fs/namei.c", "fs/namespace.c", "fs/readdir.c",
        "fs/super.c", "fs/notify/fdinfo.c", "fs/proc/base.c", "fs/proc/cmdline.c",
        "fs/proc/fd.c", "fs/proc/task_mmu.c", "fs/proc_namespace.c", "fs/proc/cmdline.c",
        "kernel/kallsyms.c", "kernel/sys.c", "mm/memory.c", "security/selinux/avc.c",
        "fs/read_write.c", "fs/exec.c", "fs/open.c", "drivers/input/input.c",
        "kernel/reboot.c", "security/selinux/hooks.c",
    )}
    return sorted(f for f in files if os.path.exists(f))


def guard_stack(text_line_iter):
    """Yield (line_no, line, guards) where guards is the stack of #if-ish names."""
    stack = []
    for i, line in enumerate(text_line_iter, 1):
        m = GUARD.match(line)
        if m:
            cond = m.group(1).strip()
            name = re.search(r"CONFIG_([A-Z0-9_]+)", cond)
            stack.append(name.group(1) if name else cond[:40])
            yield i, line, tuple(stack)
            continue
        if re.match(r"^\s*#\s*(else|elif)\b", line):
            yield i, line, tuple(stack[:-1]) + ("else:" + (stack[-1] if stack else "?"),)
            continue
        if re.match(r"^\s*#\s*endif\b", line):
            if stack:
                stack.pop()
        yield i, line, tuple(stack)


def main():
    on, off = enabled_configs()
    defined = defined_symbols()
    problems = []
    checked = 0
    for path in guarded_files():
        rel = os.path.relpath(path, ROOT)
        text = read(path)
        localsyms = set(SELFDECL.findall(text)) | set(HDR_DEFINE.findall(text))
        includes_susfs = bool(re.search(r"#include\s+<linux/susfs(_def)?\.h>", text))
        for lineno, line, guards in guard_stack(text.splitlines()):
            for sym in CALL.findall(line):
                if not sym.startswith("susfs_"):
                    continue
                checked += 1
                enabled = tuple(g for g in guards if g in on)
                disabled = [g for g in guards if g in off]
                if disabled:
                    continue  # guarded off by a "is not set" config -> not compiled
                if sym in defined:
                    continue
                problems.append((rel, lineno, sym, "no definition in tree",
                                 (enabled or guards or ("(unguarded)",))))
                continue
            # declaration visibility for susfs_* symbols used from headers only
            for sym in re.findall(r"\b(susfs_[a-z0-9_]+)\b", line):
                if sym in defined or sym in localsyms:
                    continue
                continue  # the "no definition" report above is enough
                if includes_susfs:
                    continue  # header provides it (defined_symbols() covers headers)
                if not re.search(r"\b%s\s*\(" % re.escape(sym), line):
                    continue
                if sym in defined:
                    continue
                problems.append((rel, lineno, sym, "undeclared here and no susfs include", ()))

    print("== SuSFS symbol resolution check (%s) ==" % ROOT)
    print("   %d call sites checked, %d susfs_* definitions in tree" % (checked, len(defined)))
    if problems:
        for rel, lineno, sym, why, guards in sorted(set(problems)):
            print("[FAIL] %s:%d  %s: %s  guards=%s" % (rel, lineno, sym, why, ",".join(guards)))
        print("\n%s unresolved SuSFS symbol(s). Either the SuSFS patch is missing a" % len(problems))
        print("part the KernelSU fork expects, or the matching CONFIG_KSU_SUSFS_* must")
        print("be disabled because this port does not implement the feature.")
        sys.exit(1)
    print("[ OK ] every called susfs_* symbol resolves (or is guarded off)")


if __name__ == "__main__":
    main()
