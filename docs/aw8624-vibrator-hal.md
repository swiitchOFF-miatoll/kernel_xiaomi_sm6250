# AW8624 haptic — Android vibrator HAL integration

## Problem

The AW8624 driver only registered a force-feedback **input** device
(`/dev/input/eventX`, `FF_CONSTANT` / `FF_PERIODIC`).  That is what low level
tools such as `fftest` talk to, and it is why they worked.

The Android framework never touches that input device.  `Vibrator` /
`VibratorManager` go through the **vibrator HAL**, and the HAL pokes at two
kernel *class* interfaces.  Neither of them existed on this kernel, so every
`vibrate()` call from an app or a game was silently dropped — no userspace or
Magisk/KernelSU workaround can fix that, because the control nodes have to come
from the kernel driver.

## What the driver registers now

`drivers/misc/aw8624_haptic/aw8624.c` registers, on top of the existing
force-feedback input device:

| interface | path | used by |
|---|---|---|
| timed_output class | `/sys/class/timed_output/vibrator/enable` | legacy HAL (`hardware/libhardware_legacy`), `hardware/interfaces/vibrator/1.0` on older stacks |
| LED "vibrator" class | `/sys/class/leds/vibrator/duration`<br>`/sys/class/leds/vibrator/activate`<br>`/sys/class/leds/vibrator/state`<br>`/sys/class/leds/vibrator/brightness` | AOSP / QTI vibrator HAL (`hardware/interfaces/vibrator/1.0/default`, vendor QTI services) |
| force feedback | `/dev/input/eventX` | `fftest`, `ffmemless` clients (unchanged) |

Semantics:

* `duration` — milliseconds to run for (clamped to `HAPTIC_MAX_TIMEOUT`, 10 s).
  Writing it does **not** start the motor.
* `activate` / `state` — `1` starts the motor for `duration` (1 s when no
  duration was set), `0` stops it.  `state` also reports `1` while running.
* `brightness` — `0` stops the motor, anything > 0 starts it for `duration`.
* `enable` (timed_output) — write `<ms>` to buzz, `0` to stop, read returns the
  number of milliseconds left.

The playback mode follows the device tree (`vib_mode`: `0` = RAM loop,
`1` = continuous — miatoll ships `vib_mode = <0>`), and both modes are cut short
by the hrtimer the vibrator workqueue arms with `duration`, so a HAL request can
never leave the motor running forever.

Kconfig: `CONFIG_AW8624_HAPTIC` now `select`s `ANDROID_TIMED_OUTPUT`,
`NEW_LEDS` and `LEDS_CLASS`, so the interfaces cannot be silently compiled out.

## Header fix (this is what broke the previous attempt)

A previous commit added the `timed_output_dev` plumbing but included the header
as `<linux/timed_output.h>` while the file only existed as
`drivers/staging/android/timed_output.h`, so the build died with

```
drivers/misc/aw8624_haptic/aw8624.h:25:10: fatal error: 'linux/timed_output.h' file not found
```

The canonical header now lives in `include/linux/timed_output.h` (matching
upstream Xiaomi trees); `drivers/staging/android/timed_output.h` is kept as a
one-line compatibility stub.

## Testing on the device

```sh
# LED vibrator class (what the HAL uses)
echo 500 > /sys/class/leds/vibrator/duration
echo 1   > /sys/class/leds/vibrator/activate
cat        /sys/class/leds/vibrator/state      # 1 while running
echo 0   > /sys/class/leds/vibrator/activate   # stop now

# legacy timed_output class
echo 300 > /sys/class/timed_output/vibrator/enable
cat        /sys/class/timed_output/vibrator/enable   # ms remaining
echo 0   > /sys/class/timed_output/vibrator/enable

# driver self test (same code path as the HAL)
echo 500 > /sys/bus/i2c/devices/*/activate_test
```

If all three buzz, the kernel side is done; an app that still does not vibrate
is a HAL/userspace problem (a ROM shipping an `android.hardware.vibrator@1.x`
service that expects a different node name — check `logcat`/`strace` on the
service, or `ls -l /sys/class/leds/vibrator/`).

## Files

| file | change |
|---|---|
| `drivers/misc/aw8624_haptic/aw8624.c` | vibrator class + timed_output registration |
| `drivers/misc/aw8624_haptic/aw8624.h` | `struct led_classdev vib_led` |
| `drivers/misc/aw8624_haptic/Kconfig` | `select ANDROID_TIMED_OUTPUT / NEW_LEDS / LEDS_CLASS` |
| `include/linux/timed_output.h` | canonical timed_output header (moved out of staging) |
| `drivers/staging/android/timed_output.{c,h}` | use the new header location |

(The stale `patches/aw8624_timed_output_integration.patch` from the earlier,
build-breaking attempt is removed — that directory is gitignored by design and
the change now lives in the tree proper.)
