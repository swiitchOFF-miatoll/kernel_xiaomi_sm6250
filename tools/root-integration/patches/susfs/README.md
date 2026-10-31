# SuSFS v2.3.0 (NON-GKI) for legacy 4.14

`susfs_patch_to_4.14.patch` is the 4.14 port of upstream SuSFS v2.3.0, taken from
`JackA1ltman/NonGKI_Kernel_Build_2nd` → `Patches/Patch/susfs_patch_to_4.14.patch`
(byte-identical copy; that repo is where the non-GKI build guides get it from).
The official `simonpunk/susfs4ksu` only ships patches up to v1.5.1, which is why
this community port is used here.

It is **already applied** to this tree. To apply it to a clean `4.14.357-openela`
tree, run it *before* `tools/root-integration/apply_ksu_hooks.py`:

    patch -p1 --fuzz=3 < tools/root-integration/patches/susfs/susfs_patch_to_4.14.patch

Afterwards `include/linux/susfs.h` must read `#define SUSFS_VERSION "v2.3.0"` and
`SUSFS_VARIANT "NON-GKI"`; `tools/root-integration/audit_hooks.sh` checks exactly that.

Undo (e.g. to re-apply on top of new upstream changes):

    patch -p1 -R --fuzz=3 < tools/root-integration/patches/susfs/susfs_patch_to_4.14.patch

Notes:
- The patch provides `fs/susfs.c`, `include/linux/susfs{,_def}.h` and the kernel-side
  hooking (fs/super, fs/namespace, fs/namei, fs/stat, fs/statfs, fs/readdir,
  fs/proc/{base,cmdline,fd,task_mmu}, fs/proc_namespace, fs/notify/fdinfo,
  kernel/kallsyms, kernel/sys, mm/memory, security/selinux/avc).
- It does **not** provide the KernelSU hooks (execve/faccessat/uid/reboot/...): those come
  from `apply_ksu_hooks.py`, and SuSFS itself needs a KSU fork exposing
  `CONFIG_KSU_SUSFS_*` + `susfs_is_current_ksu_domain()` — supplied by the vendored
  `KernelSU-Next` (`legacy-susfs-v2`).
