$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -O2 -g -ffreestanding -fno-builtin -fno-unwind-tables -fno-asynchronous-unwind-tables -Wall -Wextra -Werror -nostdlib '-Wl,--build-id=none' '-Wl,-Map=pa0_to_pa5.map' -T stm32f103_minimal.ld pa0_to_pa5.c -o pa0_to_pa5.elf
    if ($LASTEXITCODE) { throw 'ARM compilation failed' }
    & arm-none-eabi-objcopy -O ihex pa0_to_pa5.elf pa0_to_pa5.hex
    if ($LASTEXITCODE) { throw 'HEX conversion failed' }
    & arm-none-eabi-objdump -d -s -j .isr_vector -j .text pa0_to_pa5.elf | Set-Content -LiteralPath pa0_to_pa5.disassembly.txt -Encoding utf8
    if ($LASTEXITCODE) { throw 'Disassembly failed' }
    & arm-none-eabi-size pa0_to_pa5.elf
    if ($LASTEXITCODE) { throw 'Size check failed' }
    & py -3 -B check_build.py
    if ($LASTEXITCODE) { throw 'ELF/HEX validation failed' }
} finally {
    Pop-Location
}
