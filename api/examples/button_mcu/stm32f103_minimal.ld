ENTRY(Reset_Handler)

/* Only a 4 KiB subset of each memory is used by this fixture. */
MEMORY {
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 4K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 4K
}
__stack_top__ = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS {
    .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
    .text : { *(.text*) *(.rodata*) } > FLASH
    .data : { *(.data*) } > RAM AT > FLASH
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
    /DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.data) == 0, "This fixture has no data initialization routine")
ASSERT(SIZEOF(.bss) == 0, "This fixture has no bss initialization routine")
