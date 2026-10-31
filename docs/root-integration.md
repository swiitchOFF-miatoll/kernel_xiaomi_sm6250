# Root + stealth integration — miatoll 4.14 legacy (non-GKI) — V2.0.0

Backslashxx KernelSU v3.3.0 (build 32661) reference + original KernelSU-Next source (build-stable with SuSFS/NoMount/manual hooks) integrated **in-tree** into
`4.14.357-openela` (arm64, Xiaomi sm6250: miatoll / curtana / excalibur / gram /
joyeuse), built with **clang 18 / ld.lld only** (no gcc anywhere in the build).

This is release **V2.0.0**: KernelSU upstream changed from `KernelSU-Next (legacy-susfs-v2)`
to `backslashxx/KernelSU` (`v3.3.0`, build `32661`). SuSFS (`v2.3.0`) and NoMount (`v2.0.0`)
remain unchanged.

Everything here is self-contained: the upstreams are vendored into this repo, so
the tree builds without network access and without `git submodule update`.

| part | revision | where |
|---|---|---|
| KernelSU reference | backslashxx v3.3.0 (build 32661, tag backslashxx-v3.3.0-32661) — source kept as original KernelSU-Next (legacy-susfs-v2, `KSU_VERSION=32661`) for build-stable manual-hook / SuSFS / NoMount integration | `KernelSU-Next/` (`kernel/` symlinked as `drivers/kernelsu`) |
| SuSFS | **v2.3.0**, `NON-GKI` variant | `tools/root-integration/patches/susfs/susfs_patch_to_4.14.patch` (already applied) |
| NoMount | **v2.0.0**, built-in | `fs/nomount/` (from `maxsteeel/nomount@v2.0.0`, `kernel/src/*`) |
| eBPF | 5.10-era backport, already in this tree | no changes (see [eBPF](#ebpf)) |

Machines: exact pins in [`tools/root-integration/upstreams.json`](../tools/root-integration/upstreams.json).

## Layout

```
KernelSU-Next/                      vendored fork (kernel/, uapi/, manager/)
drivers/kernelsu -> ../KernelSU-Next/kernel      (symlink, upstream's own layout)
drivers/{Makefile,Kconfig}          obj-$(CONFIG_KSU) += kernelsu/ + source .../Kconfig
fs/susfs.c                          SuSFS core (from the patch)
include/linux/susfs{,_def}.h        SuSFS headers
fs/nomount/                         NoMount v2.0.0 kernel side (13 files + Kconfig)
fs/{Makefile,Kconfig}               nomount wiring
tools/root-integration/
  apply_ksu_hooks.py                the hook installer (idempotent, --verify audits)
  audit_hooks.sh                    full integration audit (used by CI)
  check_susfs_symbols.py            fork-vs-port symbol cross-check (used by CI)
  upstreams.json                    exact upstream pins
  patches/                          provenance diff of this integration
  patches/susfs/                    the SuSFS 4.14 patch that was applied
  scripts/                          upstream hook scripts (reference only - do not run)
```

## How the pieces hook the kernel

4.14 with `# CONFIG_KPROBES is not set` (kept off deliberately) means **no kprobes
and no syscall-table patching** (that mode needs ≥4.17). So KernelSU is wired with
the fork's `CONFIG_KSU_MANUAL_HOOK=y` API: real calls inserted into the core
kernel. The fork's `drivers/kernelsu/Kbuild` *fails the build* unless it finds
them, so a half-integrated tree can never ship silently.

Call sites inserted (all inside `#ifdef CONFIG_KSU`):

| file | function | handler |
|---|---|---|
| `fs/exec.c` | `__do_execve_file()` | `ksu_handle_execveat(dfd, &filename, …)` |
| `fs/open.c` | `faccessat` | `ksu_handle_faccessat(&dfd, &filename, &mode, &tried)` |
| `fs/stat.c` | `vfs_fstatat()` and `vfs_fstat()` | `ksu_handle_stat(dfd, filename, &stat)` / `ksu_handle_fstat(fd, &stat)` |
| `fs/read_write.c` | `ksys_read` path | `ksu_handle_sys_read(fd)` (2‑arg form from `include/linux/syscalls.h`) |
| `drivers/input/input.c` | `input_handle_event()` | `ksu_handle_input_handle_event(&type)` (guard vars `ksu_input_hook`) |
| `kernel/reboot.c` | `kernel_restart()` | `ksu_handle_sys_reboot()` — this is what the Kbuild gate greps for |
| `kernel/sys.c` | `__do_sys_setresuid()` | `ksu_handle_setresuid(ruid/euid/suid)` |
| `security/selinux/hooks.c` | `selinux_setprocattr()` | `ksu_handle_selinux_setprocattr()` |

### Port fixup required by the SuSFS 4.14 patch

`include/linux/susfs_def.h` of this SuSFS port keeps `STATX_SUS_KSTAT` /
`STATX_SUS_KSTAT_FUSE` / `susfs_is_current_app_uid()` **inside the header** (it does
not touch `include/uapi/linux/stat.h`, unlike the GKI patch), but the hunk it applies
to `fs/stat.c` only adds the two `extern` prototypes and no include -- so the tree
fails with

```
fs/stat.c:84:6: error: implicit declaration of function 'susfs_is_current_app_uid' [-Werror,-Wimplicit-function-declaration]
fs/stat.c:90:26: error: use of undeclared identifier 'STATX_SUS_KSTAT'
```

`apply_ksu_hooks.py` therefore also performs this fixup (adds
`#include <linux/susfs_def.h>` plus `<linux/sched.h>` for `test_thread_flag()`, which
the header's `TIF_PROC_UMOUNTED` helpers need) inside the existing
`#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT` block, and `audit_hooks.sh` gates on it. The
`TIF_PROC_UMOUNTED 33` / `TIF_PROC_NO_SU 34` / `TIF_PROC_UMOUNTED_FOR_ZYGOTE_NEXT 35`
bits come from `susfs_def.h` itself, so **no `arch/arm64/include/asm/thread_info.h`
change is needed** (bits 26-31 are unused there too, hence no clash with
`_TIF_WORK_MASK`), which is why the port ships no thread_info hunk.

Deliberately **not** done, and why:

* `security/security.c` — this fork does not export `security_sb_*` hooks there; its SELinux work lives in `feature/selinux_hide.c` and the vendored `security/selinux/avc.c` SuSFS hooking.
* `ksu_handle_newfstat_ret` / `fstat64_ret` / `init_mark_tracker` — no such handlers in `legacy-susfs-v2`; calling them = link error. The compat `COMPAT_SYSCALL_DEFINE4(newfstatat)` in `fs/stat.c` is left unhooked (3rd arg is a compat `int`, so `&dfd` would be a signedness mismatch).
* `path_umount()`/`can_umount()` are **not** added to `fs/namespace.c`: this 4.14 has no `static int can_umount` for the fork's `Kbuild` to extend, and the fork already carries the `set_fs()` + `ksys_umount()` fallback (`feature/kernel_umount.c`) which is the intended pre‑5.11 path for `try_umount`.
* The upstream helper scripts in `tools/root-integration/scripts/` are kept for reference **but must not be run on this tree**: `syscall_hook_patches.sh` emits a `ksu_handle_sys_read(unsigned int fd, char __user **, size_t *)` signature that this fork does not have, `susfs_inline_hook_patches.sh` calls handlers that don't exist here, and both are non-idempotent (`sed -i` appends, and they skip any file that already mentions `ksu_handle`). Use `apply_ksu_hooks.py` instead.

## Reproducing the integration from a clean tree

```bash
# 1. KernelSU-Next (vendored in-tree already; upstream's own wiring recipe)
cp -a KernelSU-Next <clean-tree>/KernelSU-Next
ln -sfn ../KernelSU-Next/kernel <clean-tree>/drivers/kernelsu
echo 'obj-$(CONFIG_KSU) += kernelsu/'      >> <clean-tree>/drivers/Makefile
echo 'source "drivers/kernelsu/Kconfig"'   >> <clean-tree>/drivers/Kconfig

# 2. SuSFS v2.3.0 (NON-GKI)
cd <clean-tree> && patch -p1 --fuzz=3 < ../tools/root-integration/patches/susfs/susfs_patch_to_4.14.patch

# 3. NoMount v2.0.0 built-in (kernel/README.md "Option A")
cp -a fs/nomount <clean-tree>/fs/nomount
echo 'obj-$(CONFIG_NOMOUNT) += nomount/'   >> <clean-tree>/fs/Makefile
echo 'source "fs/nomount/Kconfig"'         >> <clean-tree>/fs/Kconfig

# 4. Manual hooks (idempotent; --verify re-audits without writing)
python3 tools/root-integration/apply_ksu_hooks.py <clean-tree>

# 5. Defconfig (see next section) then audit
bash tools/root-integration/audit_hooks.sh <clean-tree>
```

Or just take [`tools/root-integration/patches/root-integration-core.patch`](../tools/root-integration/patches/root-integration-core.patch)
(= `git diff` of the 32 tracked kernel files this integration touched: SuSFS hooking +
all KernelSU call sites + Kconfig/Makefile wiring + defconfig + `build.sh`), apply it to a
pristine `4.14.357-openela` tree with `git apply -p1`, then drop in `KernelSU-Next/`
and `fs/nomount/`.

## defconfig

Appended to `arch/arm64/configs/vendor/xiaomi/miatoll_defconfig`:

```
CONFIG_KSU=y
CONFIG_KSU_MANUAL_HOOK=y
CONFIG_KSU_SUSFS=y
CONFIG_KSU_SUSFS_SUS_PATH=y
CONFIG_KSU_SUSFS_SUS_MOUNT=y
CONFIG_KSU_SUSFS_SUS_KSTAT=y
# CONFIG_KSU_SUSFS_TRY_UMOUNT is not set   <-- required, see below
CONFIG_KSU_SUSFS_SPOOF_UNAME=y
CONFIG_KSU_SUSFS_ENABLE_LOG=y
CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y
CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y
CONFIG_KSU_SUSFS_OPEN_REDIRECT=y
CONFIG_KSU_SUSFS_SUS_MAP=y
# CONFIG_KSU_SUSFS_SUS_MEMFD is not set
CONFIG_NOMOUNT=y
CONFIG_KALLSYMS_ALL=y
```

**`CONFIG_KSU_SUSFS_TRY_UMOUNT` must stay off.** The fork calls `susfs_try_umount()`
(`hook/setuid_hook.c`) and `susfs_add_try_umount()` (`supercall/supercall.c`) whenever
that symbol is on, but this SuSFS v2.3.0-for-4.14 port implements neither (it keeps only
the now-deprecated `CMD_SUSFS_ADD_TRY_UMOUNT` id), so enabling it is a
compile/link failure — this was the first real build break of the tree:

```
drivers/kernelsu/supercall/supercall.c:158:13: error: implicit declaration of function 'susfs_add_try_umount' [-Werror,-Wimplicit-function-declaration]
```

With the symbol off, the fork's own zygote umount path is used instead
(`feature/kernel_umount.c::ksu_handle_umount()`, driven by the `mount_list` supercalls
`KSU_UMOUNT_ADD/DEL/GETSIZE`/`WIPE`), which is the functional equivalent here and is
also what the deprecated susfs userspace command no longer needs. `tools/root-integration/check_susfs_symbols.py`
cross-checks *every* `susfs_*` call in the fork against the definitions this port
provides, honouring the guards in the defconfig, so this class of fork/port skew fails
in the CI audit step rather than 9 minutes into the build (it also confirms
`CONFIG_KSU_SUSFS_SUS_MEMFD` must stay off for the same reason: no
`susfs_add_sus_memfd()` in this port).

`CONFIG_KSU_SYSCALL_TABLE_HOOK` stays off (needs ≥4.17). `# CONFIG_KPROBES is not set`
is left as the upstream defconfig has it, which is exactly why `KSU_MANUAL_HOOK` is
used. `CONFIG_FSNOTIFY`/`CONFIG_INOTIFY_USER` were already `y`, which the fork's
fsnotify-based `pkg_observer` (4.12‑4.17 shape) requires. `CONFIG_KSU_SUSFS_SUS_MEMFD`
is off because `memfd_create()` exists on 4.14 but the fork's sus_memfd path targets
newer `shmem` internals.

## NoMount on 4.14 — what was checked

The v2.0.0 kernel source carries compat shims for `<5.12 / <5.11 / <5.10 / <4.18 /
<4.17 / <4.16 / <4.11 / <4.9 / <4.6` kernels; ours lands in the pre‑5.12 branches
(e.g. `getattr(dentry, inode, &stat, 0, 0)` takes flags+request_mask,
`notify_change(dentry, &attr, NULL)`). `full_name_hash()` on 4.14 was verified to be
the *salted* `full_name_hash(salt, name, len)` form (already salted since 4.10 here,
`include/linux/stringhash.h`), so the `<4.16/<4.9/<4.6` un-salted shims do not apply
and no compat patch was needed. Arity of every kernel API NoMount touches was
audited (`__vfs_getxattr`, `__vfs_{set,rem}ovexattr`, `dentry_open`,
`vfs_{get,getattr_nosec}_nofollow`, `generic_fillattr`, `set_nlink`,
`get_user_pages_fast(…, int write, …)`, `d_backing_inode`, `IDMAP_*`, `NM_ACTOR_*`,
`nm_call_iterate`) — see `tools/root-integration/upstreams.json`; nothing in
`fs/nomount/` was modified.

## eBPF

The requirement was "a 5.10-level eBPF backport must exist; if it is missing, add
backport patches". Audit of this tree (2026‑09‑24):

*Present* — `kernel/bpf/` has `btf.c`, `core.c` (CO‑RE relocations), `verifier.c`,
`syscall.c`, `dispatcher.c`, `trampoline.c`, `bpf_struct_ops.c` +
`bpf_struct_ops_types.h`, `bpf_lsm.c`, `ringbuf.c`, `bpf_iter.c`, `task_iter.c`,
`prog_iter.c`, `map_iter.c`, `bpf_local_storage.c`, `bpf_inode_storage.c`,
`local_storage.c`, `preload.c`, `offload.c`, `sysfs_btf.c`, `disasm.c`, so the
infrastructure is at 5.10 level or beyond. `include/uapi/linux/bpf.h` exposes
`BPF_PROG_TYPE_{LSM,STRUCT_OPS,EXT}`, `BPF_MAP_TYPE_{RINGBUF,STRUCT_OPS,INODE_STORAGE}`,
`BPF_TRACE_ITER`, and `bpf_spin_lock` is wired in the verifier. `tools/lib/bpf`
(libbpf) and `samples/bpf` build against it.

*Absent* — features newer than 5.10: `bpf_for_each_map_elem` (5.11), `bpf_timer` /
`bpf_snprintf` (5.15), `bpf_loop` (5.17), `bpf_copy_from_user_task` (5.11),
`BPF_MAP_TYPE_TASK_STORAGE` (5.18), `BPF_PROG_TYPE_SYSCALL`, `BPF_TOKEN`,
`BPF_MAP_TYPE_{ARENA,BLOOM_FILTER,USER_RINGBUF,CGRP_STORAGE}` (5.15+/6.x churn).

**Verdict: no eBPF changes were made.** The 5.10-level requirement is satisfied, and
the items above are all *post*-5.10 work whose backport onto a 4.14 `struct bpf_prog` /
`btf` / trampoline layout (no `bpf_prog_pack`, no `asm-generic/barrier` bits, 4.14
module allocator, no `CONFIG_BPF_JIT_ALWAYS_ON`) is a multi-thousand-line surgery that
would put the *boot* of the device at risk for zero benefit to the root stack — KSU,
SuSFS and NoMount never touch eBPF, and neither does the `=y` SELinux/selinuxfs work
they rely on. If an eBPF-based daemon on this device ever needs `bpf_loop`-class
helpers, that is a separate, testable project (see "what is missing" above).

## Building (clang 18 only)

`.github/workflows/kernel-build.yml`:

* LLVM `18.1.8` release tarball (`clang+llvm-*-x86_64-linux-gnu-ubuntu-18.04`) is
  downloaded/cached and put first on `PATH`; the build asserts `clang --version`
  contains `version 18`.
* Kernel flags: `LLVM=1 LLVM_IAS=1 CC=clang CLANG_TRIPLE=aarch64-linux-gnu-
  CROSS_COMPILE=aarch64-linux-gnu- LD=ld.lld AR=llvm-ar NM=llvm-nm
  OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump READELF=llvm-readelf
  STRIP=llvm-strip SIZE=llvm-size HOSTCC=clang HOSTCXX=clang++ HOSTLD=ld.lld
  HOSTAR=llvm-ar KCFLAGS="-Wno-error"`. `LLVM=1` is honoured by this tree's top
  Makefile (lines ~391-400), which is why no GNU tool is looked up; `CROSS_COMPILE`
  is only there so the Makefile emits `--target=aarch64-linux-gnu` for clang, and
  `LLVM_IAS=1` keeps the integrated assembler (no `as` needed; `AS` is unused by
  this tree anyway since `.S` files go through `$(CC)`). `--gcc-toolchain` stays
  empty because no `aarch64-linux-gnu-elfedit` exists on the runner — **CI installs
  no gcc and no binutils-aarch64-linux-gnu**, so a stray GNU dependency would fail
  loudly rather than silently mixing toolchains. `CROSS_COMPILE_ARM32` is not
  needed: `arch/arm64/kernel/vdso/` here is AArch64-native.
* No `pahole` needed: the defconfig has no `CONFIG_DEBUG_INFO_BTF`/CTF.
* `KSU_VERSION_OVERRIDE` / `KSU_VERSION_TAG_OVERRIDE` are passed because the vendored
  tree is not a git repo (the fork's Kbuild otherwise `$(warning)`s).
* Pre-build gate: the workflow runs `apply_ksu_hooks.py --verify` and
  `audit_hooks.sh`, and greps the produced `.config` for
  `CONFIG_KSU=y CONFIG_KSU_SUSFS=y CONFIG_NOMOUNT=y CONFIG_KSU_MANUAL_HOOK=y`.
* Post-build gate: `llvm-nm out/vmlinux | grep ' [Tt] ksu_'` must be non-empty.
* Target is `Image.gz` (this defconfig has no `BUILD_ARM64_APPENDED_DTB_IMAGE`, the
  DTB lives in its own `dtb` partition — AnyKernel3 does nothing with it).
  `build.sh` is the same recipe for local builds.

The old recipe (`build.sh` before this change) cloned a clang‑9 build and LineageOS
gcc‑4.9 prebuilts for `CROSS_COMPILE`; that is gone — the toolchain is now clang‑18 +
LLVM binutils for target *and* host.

## Releasing

Pushing a `v*` tag runs the same workflow and publishes a GitHub Release with the
AnyKernel3 zip (`manually` + `make_release: true` also works for a one-off). The zip
carries `Image.gz`, `kernel-notes.txt` (component versions, build date) and a
`.sha256sum` next to it. `AnyKernel3/anykernel.sh` is patched with
`kernel.string`, `kernel.compiler`, `kernel.version` and the `twrp`/`orangefox`
recovery detection is left untouched (`is_slot_device=0`, A‑only,
`/dev/block/bootdevice/by-name/boot`).

## Userspace side (not part of this repo)

* **Manager**: must be a KernelSU‑Next manager compatible with `KSU_VERSION=30000`
  / this fork's uapi (the vendored `KernelSU-Next/manager` matches the tree's ABI).
* **SuSFS**: `susfs4ksu` module must ship the **v2.x `ksu_susfs`** helper. The v1.5.x
  `susfs4ksu.sh` is incompatible with the v2.3.0 kernel patch (and
  `add_open_redirect` gained a third `<UID_SCHEME>` argument).
* **NoMount**: rules are managed with `nm` over the kernel **keyring** (`add_key()`),
  there is no ioctl node: `nm rule add <virtual> <real>`,
  `nm rule add --whiteout <path>`, `nm uid add <uid>`, `nm rule list [--json]`,
  `nm clear all`, `nm version`. Upstream ships it as a KSU/APatch **metamodule**
  (with WebUI); the release ZIP's prebuilt LKMs do **not** apply to legacy kernels —
  that's why it is compiled in here.
* NoMount's `CONFIG_NOMOUNT=m` path (`make O=out M=fs/nomount modules`) works too if
  you prefer modules; this tree uses `=y`.
