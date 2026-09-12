# STM32F103 button input example

This program configures PA0 as an input with an internal pull-down and PA5 as a
push-pull output. Each sampled input change is copied to PA5. The accompanying
integration check connects a simulated BUTTON between 3.3 V and PA0, presses and
releases it through the Python API, and checks the MCU's GPIO trace for 0 → 1 → 0.

Run the check from the repository root:

```powershell
py -3 api/check_button_mcu.py
```

The check requires Proteus 8.16 with the STM32F103 simulation model and the
installed `STMCubeMX LED Blink` example. Its `--sample` and `--executable` options
support other installation paths. The original sample is read without changes;
all generated projects and logs are kept in a separate run directory.

The included HEX and ELF were built from the accompanying C source. No HAL,
startup library, or third-party runtime is linked. To rebuild, put GNU Arm
Embedded tools on PATH and run `build.ps1` in this directory. The checked toolchain
was GCC 10.3.1, GNU Arm Embedded 10.3-2021.10. `check_build.py` verifies the ELF and
HEX payloads, checksums, address bounds, and reset vectors. The loaded image is
168 bytes; it uses only the first 4 KiB of Flash and SRAM.

GPIO and RCC register definitions were checked against ST's `stm32f103x6.h`
(V4.1.0, 29 April 2016) and the accompanying STM32F1 HAL GPIO implementation,
included in Proteus's STM32 Clock example. Those files are neither copied nor
linked. The program keeps the reset clock configuration and inserts 800 NOP loop
iterations between input samples. This delay is uncalibrated and does not debounce
a physical switch.
