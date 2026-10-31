#!/usr/bin/env python3
"""
apply_ksu_hooks.py -- insert the KernelSU-Next / SUSFS manual hook call-sites
into a legacy (4.14) Qualcomm tree.

Why this exists instead of the upstream shell helpers
(`syscall_hook_patches.sh` / `susfs_inline_hook_patches.sh`, authored by
backslashxx @ GitHub): those target the hook API of the SukiSU / ReSukiSU line
(ksu_handle_post_execveat_sucompat, ksu_handle_vfs_fstat, ksu_handle_rename,
ksu_handle_setuid, ksu_handle_sys_read(fd, buf, count)).  The vendored
KernelSU-Next legacy branch exposes a slightly different set:

    ksu_handle_execveat(int *fd, struct filename **, void *argv, void *envp, int *flags)
    ksu_handle_faccessat(int *dfd, const char __user **filename_user, int *mode, int *flags)
    ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags)
    ksu_handle_sys_read(unsigned int fd)            <- guarded by ksu_init_rc_hook
    ksu_handle_input_handle_event(unsigned int *type, unsigned int *code, int *value)
    ksu_handle_setresuid(uid_t ruid, uid_t euid, uid_t suid)
    ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd, void __user **arg)
    ksu_hide_setprocattr(const char *name, void *value, size_t size)

Every call-site is `#ifdef`-guarded, so the tree still builds with
CONFIG_KSU / CONFIG_KSU_SUSFS disabled.  The script is idempotent and aborts
(exit 1) whenever an anchor is missing or ambiguous, so `--verify` can be used
as a CI self-check.

usage:
    python3 tools/root-integration/apply_ksu_hooks.py [srctree]
    python3 tools/root-integration/apply_ksu_hooks.py --verify [srctree]
"""

import os
import sys

ARGV = sys.argv[1:]
VERIFY = "--verify" in ARGV
pos = [a for a in ARGV if not a.startswith("--")]
SRCTREE = os.path.abspath(pos[0] if pos else ".")


def read(path):
    with open(os.path.join(SRCTREE, path), "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(path, data):
    with open(os.path.join(SRCTREE, path), "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(data)


class AnchorError(Exception):
    pass


def nth_occurrence(text, anchor, n):
    idxs, start = [], 0
    while True:
        i = text.find(anchor, start)
        if i < 0:
            break
        idxs.append(i)
        start = i + 1
    if len(idxs) < n:
        raise AnchorError("anchor found %d time(s), wanted #%d: %r"
                          % (len(idxs), n, anchor[:60]))
    return idxs[n - 1]


def before(text, anchor, block, marker, n=1):
    """Insert block before Nth occurrence of anchor. Returns (text, changed)."""
    if marker in text:
        return text, False
    i = nth_occurrence(text, anchor, n)
    return text[:i] + block + text[i:], True


def after(text, anchor, block, marker, n=1):
    """Insert block after Nth occurrence of anchor (anchor kept)."""
    if marker in text:
        return text, False
    i = nth_occurrence(text, anchor, n) + len(anchor)
    return text[:i] + block + text[i:], True


# ------------------------------------------------------------------ hunks ----
DECL_EXEC = """#ifdef CONFIG_KSU
__attribute__((hot))
extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr,
			       void *argv, void *envp, int *flags);
#endif

"""

DECL_OPEN = """#ifdef CONFIG_KSU
__attribute__((hot))
extern int ksu_handle_faccessat(int *dfd, const char __user **filename_user,
				int *mode, int *flags);
#endif

"""

DECL_STAT = """
#ifdef CONFIG_KSU
__attribute__((hot))
extern int ksu_handle_stat(int *dfd, const char __user **filename_user,
			   int *flags);
#endif
"""

DECL_RW = """#ifdef CONFIG_KSU
extern bool ksu_init_rc_hook __read_mostly;
extern __attribute__((cold)) void ksu_handle_sys_read(unsigned int fd);
#endif

"""

DECL_INPUT = """#ifdef CONFIG_KSU
extern bool ksu_input_hook __read_mostly;
extern __attribute__((cold)) int ksu_handle_input_handle_event(
	unsigned int *type, unsigned int *code, int *value);
#endif

"""

DECL_REBOOT = """#ifdef CONFIG_KSU
extern int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd,
				 void __user **arg);
#endif

"""

DECL_SYS = """#ifdef CONFIG_KSU
extern int ksu_handle_setresuid(uid_t ruid, uid_t euid, uid_t suid);
#endif

"""

DECL_SELINUX = """#ifdef CONFIG_KSU
extern int ksu_hide_setprocattr(const char *name, void *value, size_t size);
#endif

"""

STAT_VFS_ANCHOR = "\terror = vfs_fstatat(dfd, filename, &stat, flag);\n"

RW_READ_ANCHOR = (
    "SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)\n"
    "{\n"
    "\tstruct fd f = fdget_pos(fd);\n"
    "\tssize_t ret = -EBADF;\n"
)

EXEC_IS_ERR_ANCHOR = (
    "\tif (IS_ERR(filename))\n"
    "\t\treturn PTR_ERR(filename);\n"
)


def build_jobs():
    """Each entry: (path, callable(text) -> (text, note))"""

    def exec_hunk(s):
        notes = []
        s, c = before(s,
                      "static int __do_execve_file(int fd, struct filename *filename,",
                      DECL_EXEC, "extern int ksu_handle_execveat")
        notes.append("decl" if c else "decl already")
        s, c = after(s, EXEC_IS_ERR_ANCHOR,
                     "\n#ifdef CONFIG_KSU\n"
                     "\tksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);\n"
                     "#endif\n", "ksu_handle_execveat(&fd")
        notes.append("call" if c else "call already")
        return s, ", ".join(notes)

    def open_hunk(s):
        s, c1 = before(s,
                       "SYSCALL_DEFINE3(faccessat, int, dfd, const char __user *, filename, int, mode)",
                       DECL_OPEN, "extern int ksu_handle_faccessat")
        s, c2 = before(s, "\tif (mode & ~S_IRWXO)",
                       "#ifdef CONFIG_KSU\n"
                       "\tksu_handle_faccessat(&dfd, &filename, &mode, NULL);\n"
                       "#endif\n\n", "ksu_handle_faccessat(&dfd")
        return s, ("%s, %s" % ("decl" if c1 else "decl already",
                               "call" if c2 else "call already"))

    def stat_hunk(s):
        s, c1 = after(s,
                      "#if !defined(__ARCH_WANT_STAT64) || defined(__ARCH_WANT_SYS_NEWFSTATAT)",
                      DECL_STAT, "extern int ksu_handle_stat")
        # Only the two native syscalls get a call-site (newfstatat, fstatat64);
        # the third occurrence is the compat entry whose dfd is `unsigned int`.
        idxs, start = [], 0
        while True:
            i = s.find(STAT_VFS_ANCHOR, start)
            if i < 0:
                break
            idxs.append(i)
            start = i + 1
        targets = [i for i in idxs[:2]
                   if "ksu_handle_stat(&dfd" not in s[i + len(STAT_VFS_ANCHOR): i + len(STAT_VFS_ANCHOR) + 90]]
        ins = ("\n#ifdef CONFIG_KSU\n"
               "\tksu_handle_stat(&dfd, &filename, &flag);\n"
               "#endif\n")
        for i in reversed(targets):
            j = i + len(STAT_VFS_ANCHOR)
            s = s[:j] + ins + s[j:]
        if not idxs:
            raise AnchorError("no `vfs_fstatat` anchor in fs/stat.c")
        return s, "decl %s, %d call-site(s)" % ("ok" if c1 else "already", len(targets))

    def rw_hunk(s):
        s, c1 = before(s,
                       "SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)",
                       DECL_RW, "extern __attribute__((cold)) void ksu_handle_sys_read")
        s, c2 = after(s, RW_READ_ANCHOR,
                      "\n#ifdef CONFIG_KSU\n"
                      "\tif (unlikely(ksu_init_rc_hook))\n"
                      "\t\tksu_handle_sys_read(fd);\n"
                      "#endif\n", "ksu_handle_sys_read(fd)")
        return s, "%s / %s" % ("decl" if c1 else "decl already",
                               "call" if c2 else "call already")

    def input_hunk(s):
        s, c1 = before(s, "void input_event(struct input_dev *dev,", DECL_INPUT,
                       "ksu_handle_input_handle_event")
        s, c2 = before(s, "\tif (is_event_supported(type, dev->evbit, EV_MAX)) {",
                        "#ifdef CONFIG_KSU\n"
                        "\tif (unlikely(ksu_input_hook))\n"
                        "\t\tksu_handle_input_handle_event(&type, &code, &value);\n"
                        "#endif\n\n", "ksu_handle_input_handle_event(&type")
        return s, "%s / %s" % ("decl" if c1 else "decl already",
                               "call" if c2 else "call already")

    def reboot_hunk(s):
        s, c1 = before(s, "SYSCALL_DEFINE4(reboot, int, magic1, int, magic2, unsigned int, cmd,",
                       DECL_REBOOT, "extern int ksu_handle_sys_reboot")
        s, c2 = before(s, "\tif (!ns_capable(pid_ns->user_ns, CAP_SYS_BOOT))",
                       "#ifdef CONFIG_KSU\n"
                       "\tif (system_state == SYSTEM_RUNNING) {\n"
                       "\t\tksu_handle_sys_reboot(magic1, magic2, cmd, &arg);\n"
                       "\t}\n"
                       "#endif\n\n", "ksu_handle_sys_reboot(magic1")
        return s, "%s / %s" % ("decl" if c1 else "decl already",
                               "call" if c2 else "call already")

    def sys_hunk(s):
        s, c1 = before(s, "SYSCALL_DEFINE3(setresuid, uid_t, ruid, uid_t, euid, uid_t, suid)",
                       DECL_SYS, "extern int ksu_handle_setresuid")
        s, c2 = after(s, "\tkuid_t kruid, keuid, ksuid;\n",
                      "\n#ifdef CONFIG_KSU\n"
                      "\t(void)ksu_handle_setresuid(ruid, euid, suid);\n"
                      "#endif\n", "ksu_handle_setresuid(ruid, euid, suid)")
        return s, "%s / %s" % ("decl" if c1 else "decl already",
                               "call" if c2 else "call already")

    def selinux_hunk(s):
        s, c1 = before(s, "static int selinux_setprocattr(const char *name, void *value, size_t size)",
                       DECL_SELINUX, "extern int ksu_hide_setprocattr")
        s, c2 = after(s, "\tchar *str = value;\n",
                      "#ifdef CONFIG_KSU\n"
                      "\tksu_hide_setprocattr(name, value, size);\n"
                      "#endif\n\n", "ksu_hide_setprocattr(name, value, size)")
        return s, "%s / %s" % ("decl" if c1 else "decl already",
                               "call" if c2 else "call already")

    return [
        ("fs/exec.c", exec_hunk),
        ("fs/open.c", open_hunk),
        ("fs/stat.c", stat_hunk),
        ("fs/read_write.c", rw_hunk),
        ("drivers/input/input.c", input_hunk),
        ("kernel/reboot.c", reboot_hunk),
        ("kernel/sys.c", sys_hunk),
        ("security/selinux/hooks.c", selinux_hunk),
    ]


# ------------------------------------------------------------------ audit ----
REQUIRED_HOOKS = [
    ("fs/exec.c", "ksu_handle_execveat"),
    ("fs/open.c", "ksu_handle_faccessat"),
    ("fs/stat.c", "ksu_handle_stat"),
    ("fs/read_write.c", "ksu_handle_sys_read"),
    ("drivers/input/input.c", "ksu_handle_input_handle_event"),
    ("kernel/reboot.c", "ksu_handle_sys_reboot"),
    ("kernel/sys.c", "ksu_handle_setresuid"),
]

WIRING = [
    ("fs/susfs.c", "SUSFS_VERSION"),
    ("include/linux/susfs.h", '#define SUSFS_VERSION "v2.3.0"'),
    ("fs/Makefile", "obj-$(CONFIG_KSU_SUSFS) += susfs.o"),
    ("fs/Makefile", "nomount/"),
    ("fs/Kconfig", 'source "fs/nomount/Kconfig"'),
    ("fs/nomount/nomount.c", "nomount"),
    ("drivers/Makefile", "obj-$(CONFIG_KSU) += kernelsu/"),
    ("drivers/Kconfig", 'source "drivers/kernelsu/Kconfig"'),
]


# ------------------------------------------- post-susfs-patch fixups ----
# The 4.14 port of SuSFS v2.3.0 inserts an "#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT"
# block into fs/stat.c that declares two externs but never includes
# <linux/susfs_def.h> -- which is exactly where STATX_SUS_KSTAT,
# STATX_SUS_KSTAT_FUSE and susfs_is_current_app_uid() are defined by this port
# (the port keeps them out of <uapi/linux/stat.h> on purpose). Without the
# include the file does not compile:
#
#   fs/stat.c:84:6: error: implicit declaration of function 'susfs_is_current_app_uid'
#   fs/stat.c:90:26: error: use of undeclared identifier 'STATX_SUS_KSTAT'
#
# <linux/sched.h> is added too because susfs_def.h's TIF_* helpers call
# test_thread_flag(); it is a no-op when already pulled in transitively.
STAT_ANCHOR = ("#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT\n"
               "extern bool susfs_is_inode_sus_kstat")
STAT_INCLUDES = ("#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT\n"
                 "#include <linux/sched.h>\t\t/* for test_thread_flag(), used by susfs_def.h */\n"
                 "#include <linux/susfs_def.h>\n")


def fix_stat_c_susfs_include(src):
    if "#include <linux/susfs_def.h>" in src:
        return src, "fs/stat.c already includes <linux/susfs_def.h>"
    if "CONFIG_KSU_SUSFS_SUS_KSTAT" not in src:
        return src, "no SuSFS block in fs/stat.c (SuSFS not applied?) -- skipped"
    if src.count(STAT_ANCHOR) != 1:
        raise AnchorError("fs/stat.c: expected exactly one SuSFS SUS_KSTAT extern block, found %d"
                          % src.count(STAT_ANCHOR))
    src = src.replace(STAT_ANCHOR, STAT_INCLUDES + "extern bool susfs_is_inode_sus_kstat", 1)
    return src, "added <linux/susfs_def.h> to the SUS_KSTAT block"


FIXUPS = [("fs/stat.c", fix_stat_c_susfs_include)]


def audit():
    rc = 0
    print("== KernelSU / SUSFS / NoMount integration audit ==")
    print("-- manual hooks --")
    for path, hook in REQUIRED_HOOKS:
        try:
            src = read(path)
        except OSError as e:
            print("%-26s %-32s UNREADABLE (%s)" % (path, hook, e))
            rc = 1
            continue
        has_decl = ("extern int %s" % hook in src or "extern void %s" % hook in src
                    or "extern __attribute__((cold)) void %s" % hook in src
                    or "extern __attribute__((cold)) int %s" % hook in src
                    or "extern int %s(" % hook in src)
        has_call = ("%s(" % hook) in src and "#ifdef CONFIG_KSU" in src
        ok = has_decl and has_call
        print("%-26s %-32s %s" % (path, hook, "OK" if ok else "MISSING"))
        rc |= 0 if ok else 1
    print("-- post-susfs fixups --")
    try:
        ok = "#include <linux/susfs_def.h>" in read("fs/stat.c")
    except OSError:
        ok = False
    print("%-26s %-32s %s" % ("fs/stat.c", "susfs_def.h include", "OK" if ok else "MISSING"))
    rc |= 0 if ok else 1
    print("-- tree wiring --")
    for path, marker in WIRING:
        try:
            ok = marker in read(path)
        except OSError:
            ok = False
        print("%-26s %-32s %s" % (path, marker[:32], "OK" if ok else "MISSING"))
        rc |= 0 if ok else 1
    if rc:
        print("\nAUDIT FAILED")
        sys.exit(1)
    print("\nAUDIT PASSED")
    sys.exit(0)


def main():
    if VERIFY:
        audit()
    os.chdir(SRCTREE)
    print("== applying KernelSU manual hooks in %s ==" % SRCTREE)
    failed = False
    for path, fn in build_jobs() + FIXUPS:
        try:
            src = read(path)
            out, note = fn(src)
        except (AnchorError, OSError) as e:
            print("[FAIL] %-26s %s" % (path, e))
            failed = True
            continue
        if out != src:
            write(path, out)
            print("[ OK ] %-26s %s" % (path, note))
        else:
            print("[ no ] %-26s already up to date (%s)" % (path, note))
    if failed:
        print("\nhook application FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
