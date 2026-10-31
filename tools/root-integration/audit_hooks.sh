#!/bin/bash
# Audit of the in-tree root stack for this 4.14 non-GKI kernel.
#
# Checks that KernelSU-Next, SuSFS v2.3.0 (NON-GKI) and NoMount v2.0.0 are wired
# into the build, that every manual hook call site exists with the signature this
# KernelSU fork actually declares, and that the defconfig enables the stack.
# Run from anywhere; the tree root is derived from the script location.
#
#   ./tools/root-integration/audit_hooks.sh [kernel-tree]
#
# Exit code 0 = integration complete.  This is the gate used by
# .github/workflows/kernel-build.yml before compiling.
set -u
ROOT=${1:-"$(cd "$(dirname "$0")/../.." && pwd)"}
cd "$ROOT" || { echo "no kernel tree at $ROOT"; exit 1; }
fail=0; warn=0
DEF=arch/arm64/configs/vendor/xiaomi/miatoll_defconfig
[ -f "$DEF" ] || DEF=$(ls arch/arm64/configs/vendor/*/*_defconfig 2>/dev/null | head -1)
ok()   { printf '[ OK ] %s\n' "$1"; }
bad()  { printf '[FAIL] %s\n' "$1"; fail=$((fail+1)); }
soft() { printf '[WARN] %s\n' "$1"; warn=$((warn+1)); }
note() { printf '       %s\n' "$1"; }

sec() { echo; echo "== $1 =="; }

sec "vendored trees"
[ -e drivers/kernelsu/Kconfig ] && ok "drivers/kernelsu -> KernelSU-Next/kernel (symlink resolves)" || bad "drivers/kernelsu/Kconfig missing (symlink broken?)"
[ -f drivers/kernelsu/Kbuild ]  && ok "drivers/kernelsu/Kbuild present" || bad "drivers/kernelsu/Kbuild missing"
[ -f fs/susfs.c ]        && ok "fs/susfs.c (SuSFS core)" || bad "fs/susfs.c missing -> apply tools/root-integration/patches/susfs/susfs_patch_to_4.14.patch"
[ -f include/linux/susfs.h ] && ok "include/linux/susfs.h" || bad "include/linux/susfs.h missing"
[ -f include/linux/susfs_def.h ] && ok "include/linux/susfs_def.h" || bad "include/linux/susfs_def.h missing"
[ -f fs/nomount/nomount.c ] && ok "fs/nomount/nomount.c (NoMount v2.0.0)" || bad "fs/nomount/ missing"
[ -f fs/nomount/Kconfig ] && ok "fs/nomount/Kconfig" || bad "fs/nomount/Kconfig missing"

SUSV=$(grep -m1 'define SUSFS_VERSION' include/linux/susfs.h 2>/dev/null | awk '{print $3}' | tr -d '"')
SUSVAR=$(grep -m1 'define SUSFS_VARIANT' include/linux/susfs.h 2>/dev/null | awk '{print $3}' | tr -d '"')
[ "${SUSV:-}" = "v2.3.0" ] && ok "SuSFS version $SUSV ($SUSVAR)" || bad "SuSFS version is '${SUSV:-none}', expected v2.3.0"
[ "${SUSVAR:-}" = "NON-GKI" ] && ok "SuSFS variant NON-GKI" || soft "SuSFS variant is '${SUSVAR:-none}', expected NON-GKI"
NMV=$(grep -m1 'define NOMOUNT_VERSION' fs/nomount/nomount.h 2>/dev/null | awk '{print $3}' | tr -d '"')
[ "${NMV:-}" = "20" ] && ok "NOMOUNT_VERSION=$NMV (NoMount v2.x ABI)" || soft "NOMOUNT_VERSION='${NMV:-none}', expected 20 (v2.0.0)"

sec "build wiring"
grep -Eq '^obj-\$\(CONFIG_KSU\)[[:space:]]*\+= kernelsu/$' drivers/Makefile && ok "drivers/Makefile: obj-\$(CONFIG_KSU) += kernelsu/" || bad "drivers/Makefile has no kernelsu entry"
grep -Eq '^source "drivers/kernelsu/Kconfig"$' drivers/Kconfig && ok "drivers/Kconfig sources the KSU Kconfig" || bad "drivers/Kconfig does not source drivers/kernelsu/Kconfig"
grep -Eq '^obj-\$\(CONFIG_NOMOUNT\)[[:space:]]*\+= nomount/$' fs/Makefile && ok "fs/Makefile: obj-\$(CONFIG_NOMOUNT) += nomount/" || bad "fs/Makefile has no nomount entry"
grep -Eq '^source "fs/nomount/Kconfig"$' fs/Kconfig && ok "fs/Kconfig sources fs/nomount/Kconfig" || bad "fs/Kconfig does not source fs/nomount/Kconfig"

sec "SuSFS kernel hooks (from the v2.3.0 patch)"
# file -> at least one susfs_*/ksu_* call the patch is supposed to have inserted
# file -> a hook/macros the v2.3.0 patch is supposed to have inserted there
declare -A SUSFS_SITES=(
  [fs/super.c]='susfs_is_current_ksu_domain'
  [fs/namespace.c]='susfs_alloc_unshare_ksu_vfsmnt|susfs_get_non_sus_mnt_id_from_mnt'
  [fs/namei.c]='susfs_open_redirect_spoof_do_sys_openat|susfs_is_inode_sus_path'
  [fs/readdir.c]='susfs_get_data_path|susfs_is_inode_sus_path'
  [fs/stat.c]='susfs_sus_kstat_spoof_generic_fillattr'
  [fs/statfs.c]='susfs_sus_kstat_spoof_vfs_statfs'
  [fs/proc/base.c]='susfs_open_redirect_spoof_do_proc_readlink'
  [fs/proc/cmdline.c]='susfs_spoof_cmdline_or_bootconfig'
  [fs/proc/fd.c]='susfs_sus_kstat_spoof_proc_fd_seq_show'
  [fs/proc/task_mmu.c]='susfs_sus_kstat_spoof_show_map_vma|SUSFS_IS_INODE_SUS_MAP'
  [fs/proc_namespace.c]='susfs_show_vfsmnt|susfs_show_mountinfo'
  [fs/notify/fdinfo.c]='susfs_sus_kstat_spoof_inotify_fdinfo'
  [kernel/kallsyms.c]='susfs_starts_with'
  [kernel/sys.c]='susfs_spoof_uname'
  [mm/memory.c]='SUSFS_IS_INODE_SUS_MAP'
  [security/selinux/avc.c]='susfs_is_avc_log_spoofing_enabled'
)
for f in "${!SUSFS_SITES[@]}"; do
  pat=${SUSFS_SITES[$f]}
  if [ -f "$f" ] && grep -Eq "$pat" "$f"; then ok "$f has SuSFS hooking"; else bad "$f lacks SuSFS hooking (expected: $pat)"; fi
done

sec "SuSFS port fixups (the upstream 4.14 port forgets these)"
if grep -q "#include <linux/susfs_def.h>" fs/stat.c; then
  ok "fs/stat.c includes <linux/susfs_def.h> (STATX_SUS_KSTAT + susfs_is_current_app_uid)"
else
  bad "fs/stat.c lacks <linux/susfs_def.h> -> 'undeclared identifier STATX_SUS_KSTAT'; run apply_ksu_hooks.py"
fi

sec "SuSFS symbol resolution (fork calls vs what the port provides)"
if python3 tools/root-integration/check_susfs_symbols.py . "$DEF"; then :; else bad "susfs_* symbol referenced by the fork has no definition (see above)"; fi

sec "KernelSU manual hook call sites (KSU_MANUAL_HOOK)"
python3 tools/root-integration/apply_ksu_hooks.py --verify . || bad "hook call sites incomplete"


sec "defconfig"
if [ -f "$DEF" ]; then
  ok "defconfig: $DEF"
  for c in CONFIG_KSU=y CONFIG_KSU_MANUAL_HOOK=y CONFIG_KSU_SUSFS=y CONFIG_NOMOUNT=y CONFIG_KALLSYMS_ALL=y; do
    grep -qx "$c" "$DEF" && ok "  $c" || bad "  missing: $c"
  done
  for c in CONFIG_KSU_SUSFS_SUS_PATH CONFIG_KSU_SUSFS_SUS_MOUNT CONFIG_KSU_SUSFS_SUS_KSTAT CONFIG_KSU_SUSFS_SPOOF_UNAME CONFIG_KSU_SUSFS_ENABLE_LOG CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG CONFIG_KSU_SUSFS_OPEN_REDIRECT CONFIG_KSU_SUSFS_SUS_MAP; do
    grep -qx "$c=y" "$DEF" && ok "  $c=y" || soft "  not enabled: $c"
  done
  dups=$(grep -E '^CONFIG_(KSU|NOMOUNT|KALLSYMS_ALL)' "$DEF" | sort | uniq -d)
  [ -z "$dups" ] && ok "  no duplicate KSU/NOMOUNT/KALLSYMS lines" || { bad "  duplicate lines:"; note "$dups"; }
  grep -q '^# CONFIG_KPROBES is not set' "$DEF" && note "CONFIG_KPROBES off -> KSU_MANUAL_HOOK is mandatory (expected on 4.14)"
  if grep -qx 'CONFIG_KSU_SUSFS_TRY_UMOUNT=y' "$DEF"; then
    bad "CONFIG_KSU_SUSFS_TRY_UMOUNT=y but this SuSFS port provides no susfs_try_umount()/susfs_add_try_umount() -> build/link error"
  else
    ok "  CONFIG_KSU_SUSFS_TRY_UMOUNT off (fork's own ksu_handle_umount() path is used)"
  fi
else
  soft "no defconfig found; skipping defconfig checks"
fi

sec "userspace expectations (cannot be checked from the kernel tree)"
note "Manager must match the vendored KernelSU-Next (this fork ships root as a metamodule for legacy kernels)."
note "susfs4ksu module must provide the v2.x 'ksu_susfs' helper; v1.5.x 'susfs4ksu.sh' is incompatible."
note "NoMount's 'nm' CLI + profiles ship as a KSU/APatch metamodule; rules live in the kernel keyring, not in /dev."

echo
if [ "$fail" -ne 0 ]; then
  echo "AUDIT FAILED: $fail error(s), $warn warning(s)"; exit 1
fi
echo "AUDIT PASSED (0 errors, $warn warning(s))"
