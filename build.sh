#!/bin/bash
# Local build recipe for miatoll (Redmi Note 9 Pro / sm6250, 4.14.357-openela).
#
# Pure clang/LLVM: clang 18 compiles, the integrated assembler (LLVM_IAS=1)
# assembles, ld.lld links and llvm-* (ar/nm/objcopy/readelf/strip) replace every
# GNU binutils call -- host programs included (HOSTCC=clang). There is no gcc
# and no aarch64 binutils dependency anywhere in this script.
#
# It also builds the in-tree AnyKernel3 flashable zip and runs the root-stack
# audit (KernelSU-Next + SuSFS v2.3.0 + NoMount v2.0.0) before compiling, so a
# half-integrated tree fails fast instead of producing a kernel without hooks.
#
# Usage:  ./build.sh [-c] [--no-zip]     (-c = clean out/ first)
set -e

TOP=$(cd "$(dirname "$0")" && pwd)
cd "$TOP"

DEFCONFIG=${DEFCONFIG:-vendor/xiaomi/miatoll_defconfig}
ARCH=arm64
export ARCH
LLVM_VER=${LLVM_VER:-18.1.8}
LLVM_TARBALL=${LLVM_TARBALL:-clang+llvm-${LLVM_VER}-x86_64-linux-gnu-ubuntu-18.04}
LLVM_DIR=$TOP/toolchains/llvm
CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
KSU_VERSION=${KSU_VERSION:-30000}
KSU_VERSION_TAG=${KSU_VERSION_TAG:-ksun-legacy-susfs-v2-d999a2af}
BUILD_ZIP=1
JOBS=${JOBS:-$(nproc --all)}

export KBUILD_BUILD_HOST=${KBUILD_BUILD_HOST:-android-build}
export KBUILD_BUILD_USER=${KBUILD_BUILD_USER:-kardebayan}
export LC_ALL=C

for a in "$@"; do
  case "$a" in
    -c|--clean) rm -rf "$TOP/out"; ;;
    --no-zip)  BUILD_ZIP=0; ;;
    *)         echo "unknown option: $a" >&2; exit 2; ;;
  esac
done

# ---- toolchain ------------------------------------------------------------
get_llvm() {
  [ -x "$LLVM_DIR/bin/clang" ] && return 0
  mkdir -p "$TOP/toolchains"
  local tmp="$TOP/toolchains/${LLVM_TARBALL}.tar.xz"
  echo "==> downloading LLVM ${LLVM_VER}"
  for url in \
    "https://github.com/llvm/llvm-project/releases/download/llvmorg-${LLVM_VER}/${LLVM_TARBALL}.tar.xz" \
    "https://releases.llvm.org/${LLVM_VER}/${LLVM_TARBALL}.tar.xz"; do
    if curl -fL --retry 3 -o "$tmp" "$url"; then break; fi
  done
  [ -s "$tmp" ] || { echo "!! could not fetch LLVM ${LLVM_VER}" >&2; exit 1; }
  tar -xf "$tmp" -C "$TOP/toolchains"
  rm -f "$tmp"
  mv "$TOP/toolchains/${LLVM_TARBALL}" "$LLVM_DIR"
}

get_llvm
PATH="$LLVM_DIR/bin:$PATH"; export PATH
clang --version | head -1
clang --version | grep -q "version 18" || { echo "!! clang 18 required" >&2; exit 1; }
command -v ld.lld >/dev/null || { echo "!! ld.lld missing" >&2; exit 1; }

# ---- audit the root stack before spending CPU -----------------------------
echo "==> auditing KernelSU / SuSFS / NoMount integration"
bash tools/root-integration/audit_hooks.sh "$TOP"

# ---- build ----------------------------------------------------------------
echo "==> configuring $DEFCONFIG"
make O=out ARCH=$ARCH CC=clang "$DEFCONFIG" >/dev/null
make O=out ARCH=$ARCH CC=clang olddefconfig >/dev/null

grep -E "^(CONFIG_KSU|CONFIG_KSU_SUSFS|CONFIG_NOMOUNT)(=| )" out/.config || {
  echo "!! root stack not enabled in out/.config" >&2; exit 1; }

echo "==> building Image.gz ($JOBS jobs, clang ${LLVM_VER}, ld.lld)"
make -j"$JOBS" O=out ARCH=$ARCH \
  LLVM=1 LLVM_IAS=1 \
  CC=clang CLANG_TRIPLE=aarch64-linux-gnu- CROSS_COMPILE="$CROSS_COMPILE" \
  LD=ld.lld AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy \
  OBJDUMP=llvm-objdump READELF=llvm-readelf STRIP=llvm-strip SIZE=llvm-size \
  HOSTCC=clang HOSTCXX=clang++ HOSTLD=ld.lld HOSTAR=llvm-ar \
  KCFLAGS="-Wno-error=implicit-int -Wno-error=strict-prototypes" \
  KSU_VERSION_OVERRIDE="$KSU_VERSION" \
  KSU_VERSION_TAG_OVERRIDE="$KSU_VERSION_TAG" \
  Image.gz

KIMG=out/arch/arm64/boot/Image.gz
[ -s out/arch/arm64/boot/Image.gz-dtb ] && KIMG=out/arch/arm64/boot/Image.gz-dtb
echo "==> kernel image: $KIMG"

# ---- package AnyKernel3 -----------------------------------------------------
if [ "$BUILD_ZIP" = "1" ]; then
  STAMP=$(date -u +"%Y%m%d-%H%M")
  ZIP="Stormbreaker-miatoll-${STAMP}.zip"
  rm -rf "$TOP/AK3" && cp -a "$TOP/AnyKernel3" "$TOP/AK3"
  find "$TOP/AK3" -name .gitignore -delete
  cp "$KIMG" "AK3/$(basename "$KIMG")"
  sed -i "s|^kernel.string=.*|kernel.string=OpenELA 4.14 + KernelSU-Next + SuSFS + NoMount|" AK3/anykernel.sh
  sed -i "s|^kernel.compiler=.*|kernel.compiler=clang ${LLVM_VER} + ld.lld (no gcc)|" AK3/anykernel.sh
  sed -i "s|^kernel.made=.*|kernel.made=$(whoami)@$(hostname)|" AK3/anykernel.sh
  sed -i "s|^kernel.version=.*|kernel.version=$(make -s kernelversion)|" AK3/anykernel.sh
  sed -i "s|^message.word=.*|message.word=KernelSU + SuSFS + NoMount|" AK3/anykernel.sh
  ( cd "$TOP/AK3" && zip -qr9 "../$ZIP" * )
  sha256sum "$ZIP" > "$ZIP.sha256sum"
  rm -rf "$TOP/AK3"
  echo "==> flashable zip: $ZIP ($(du -h "$ZIP" | cut -f1))"
  echo "    sha256: $(cut -d' ' -f1 "$ZIP.sha256sum")"
fi
