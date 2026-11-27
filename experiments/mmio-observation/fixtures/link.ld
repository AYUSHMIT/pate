/* Match the upstream PPC32 fixture layout; model state is separate from output. */
SECTIONS
{
  .audit_state 0x10030000 : { *(.audit_state) }
  .output 0x1003f000 : { *(.output) }
}
