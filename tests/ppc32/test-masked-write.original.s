	.file	"test-masked-write.original.c"
	.machine ppc
	.section	".text"
	.globl g
	.section	".sbss","aw",@nobits
	.align 2
g:
	.zero	4
	.size	g, 4
	.type	g, @object
	.globl h
	.align 2
h:
	.zero	4
	.size	h, 4
	.type	h, @object
	.section	".text"
	.align 2
	.globl f
	.type	f, @function
f:
.LFB0:
	.cfi_startproc
	stwu 1,-16(1)
	.cfi_def_cfa_offset 16
	stw 31,12(1)
	.cfi_offset 31, -4
	mr 31,1
	.cfi_def_cfa_register 31
	lis 9,g@ha
	li 10,3
	stw 10,g@l(9)
	lis 9,h@ha
	lwz 9,h@l(9)
	addi 10,9,1
	lis 9,h@ha
	stw 10,h@l(9)
	nop
	addi 11,31,16
	lwz 31,-4(11)
	.cfi_def_cfa 11, 0
	mr 1,11
	.cfi_restore 31
	.cfi_def_cfa_register 1
	blr
	.cfi_endproc
.LFE0:
	.size	f,.-f
	.align 2
	.globl _start
	.type	_start, @function
_start:
.LFB1:
	.cfi_startproc
	stwu 1,-16(1)
	.cfi_def_cfa_offset 16
	mflr 0
	stw 0,20(1)
	stw 31,12(1)
	.cfi_offset 65, 4
	.cfi_offset 31, -4
	mr 31,1
	.cfi_def_cfa_register 31
	lis 9,g@ha
	li 10,2
	stw 10,g@l(9)
	lis 9,h@ha
	li 10,1
	stw 10,h@l(9)
	bl f
	lis 9,h@ha
	li 10,4
	stw 10,h@l(9)
	nop
	addi 11,31,16
	lwz 0,4(11)
	mtlr 0
	lwz 31,-4(11)
	.cfi_def_cfa 11, 0
	mr 1,11
	.cfi_restore 31
	.cfi_def_cfa_register 1
	blr
	.cfi_endproc
.LFE1:
	.size	_start,.-_start
	.ident	"GCC: (Ubuntu 10.3.0-1ubuntu1) 10.3.0"
	.section	.note.GNU-stack,"",@progbits
