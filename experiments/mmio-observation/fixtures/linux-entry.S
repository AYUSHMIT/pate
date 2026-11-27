/* Linux userspace launcher for QEMU. PATE starts at audit_entry, not _start.
 * Emit exactly eight big-endian bytes: output word, then final model state.
 * This launcher uses no libc and is outside the verified function.
 */
    .section .text
    .globl _start
    .type _start,@function
_start:
    bl audit_entry
    li 0,4
    li 3,1
    lis 4,audit_output@ha
    addi 4,4,audit_output@l
    li 5,4
    sc
    li 0,4
    li 3,1
    lis 4,audit_state@ha
    addi 4,4,audit_state@l
    li 5,4
    sc
    li 0,1
    li 3,0
    sc
    .size _start,.-_start
    .section .note.GNU-stack,"",@progbits
