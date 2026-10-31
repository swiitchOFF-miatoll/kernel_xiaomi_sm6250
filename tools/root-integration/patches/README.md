# Provenance

`root-integration-core.patch` is the diff of every **tracked kernel file** this
integration touched (`fs/`, `drivers/`, `include/`, `kernel/`, `mm/`, `security/`,
`arch/arm64/configs/`, `build.sh`): the SuSFS kernel-side hooking, all KernelSU
manual-hook call sites, the Kconfig/Makefile wiring and the defconfig. Apply with
`git apply -p1` or `patch -p1`. It does not include the CI workflow or the docs. The two vendored upstream trees (`KernelSU-Next/`,
`fs/nomount/`) are committed as-is — see `../upstreams.json` for their pins — so they
are intentionally not part of this diff.

`susfs/` holds the upstream SuSFS v2.3.0-for-4.14 patch that was applied here, so the
integration can be replayed on another legacy tree.

Applying the core patch elsewhere needs the vendored trees to be in place first
(see ../../docs/root-integration.md, "Reproducing the integration from a clean tree").
