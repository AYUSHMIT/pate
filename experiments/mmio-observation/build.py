#!/usr/bin/env python3
"""Build auditable PowerPC32 fixtures and native differential-check libraries."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
UPSTREAM = "284879d8bb3865e0f96e430995b944af2fbf21a0"
MODELS = ("ram", "read_to_clear")
VARIANTS = ("original", "equivalent", "second_read", "xor1")
STATE_ADDRESS = 0x10030000
OUTPUT_ADDRESS = 0x1003F000


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(args, cwd=REPO):
    result = subprocess.run([str(a) for a in args], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {args!r}\n"
                           f"{result.stdout}\n{result.stderr}")
    return result.stdout


def resolve_tool(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required tool is unavailable: {name}")
    return str(Path(path).resolve())


def symbols(nm, binary):
    result = {}
    for line in command([nm, "-n", binary]).splitlines():
        fields = line.split()
        if len(fields) == 3 and re.fullmatch(r"[0-9a-fA-F]+", fields[0]):
            result[fields[2]] = int(fields[0], 16)
    return result


def inspect_accesses(disassembly, entry="audit_entry"):
    """Track literal address construction in these straight-line PPC32 fixtures.

    This is an instruction-shape check, not a general PowerPC emulator. Reject
    unexpected control flow and require exactly two resolved state loads and
    one output store. Preserve the complete disassembly for manual inspection.
    """
    registers = {}
    events = []
    active = False
    instructions = 0
    returned = False
    for line in disassembly.splitlines():
        label = re.match(r"^[0-9a-fA-F]+ <([^>]+)>:$", line.strip())
        if label:
            if active:
                break
            active = label.group(1) == entry
            continue
        if not active:
            continue
        match = re.match(r"^\s*([0-9a-fA-F]+):\s+(?:[0-9a-fA-F]{2}\s+){4}\s*(\S+)\s*(.*)$", line)
        if not match:
            continue
        pc, op, operands = match.groups()
        args = [x.strip() for x in operands.split("#", 1)[0].split(",")]
        instructions += 1
        if op == "blr":
            returned = True
            break
        if op.startswith("b") and op not in ("bctrl",):
            raise ValueError(f"Unexpected branch in straight-line fixture: {line}")
        if op in ("bctrl", "bl", "bcl"):
            raise ValueError(f"Unexpected call in straight-line fixture: {line}")
        if op in ("nop", "mflr", "mtlr", "mtctr"):
            if op == "mflr":
                registers.pop(args[0], None)
            continue
        if op in ("lis", "li"):
            registers[args[0]] = (int(args[1], 0) << (16 if op == "lis" else 0)) & 0xFFFFFFFF
            continue
        if op in ("addi", "addis", "ori", "oris"):
            rd, ra, immediate = args
            value = 0 if ra in ("0", "r0") and op.startswith("add") else registers.get(ra)
            if value is None:
                registers.pop(rd, None)
            else:
                imm = int(immediate, 0) << (16 if op in ("addis", "oris") else 0)
                registers[rd] = ((value | imm) if op.startswith("or") else value + imm) & 0xFFFFFFFF
            continue
        if op == "mr":
            if args[1] in registers:
                registers[args[0]] = registers[args[1]]
            else:
                registers.pop(args[0], None)
            continue
        if op in ("lwz", "stw", "stwu"):
            address = re.fullmatch(r"(-?(?:0x[0-9a-fA-F]+|[0-9]+))\((r?[0-9]+)\)", args[1])
            if not address:
                raise ValueError(f"Unrecognized load/store address: {line}")
            displacement, base = address.groups()
            if op == "stwu" and base in ("0", "r0"):
                raise ValueError(f"Invalid zero-base update instruction: {line}")
            value = 0 if base in ("0", "r0") else registers.get(base)
            absolute = None if value is None else (value + int(displacement, 0)) & 0xFFFFFFFF
            if absolute in (STATE_ADDRESS, OUTPUT_ADDRESS):
                events.append({"pc": int(pc, 16), "instruction": op,
                               "address": absolute, "width_bits": 32})
            if op == "lwz":
                registers.pop(args[0], None)
            if op == "stwu":
                registers.pop(base, None)
            continue
        # Other arithmetic can invalidate a tracked constant, but cannot create
        # a new known address. No branch or memory instruction may be ignored.
        if op.startswith(("l", "st")):
            raise ValueError(f"Unrecognized memory instruction: {line}")
        if args and re.fullmatch(r"r?[0-9]+", args[0]):
            registers.pop(args[0], None)
    loads = [e for e in events if e["instruction"] == "lwz" and e["address"] == STATE_ADDRESS]
    writes = [e for e in events if e["instruction"] in ("stw", "stwu") and e["address"] == OUTPUT_ADDRESS]
    if not returned or len(loads) != 2 or len(writes) != 1 or not all(e["pc"] < writes[0]["pc"] for e in loads):
        raise ValueError(f"Expected two state loads before one output store; observed {events!r}")
    return {"entry": entry, "instructions_checked": instructions,
            "state_loads": len(loads), "output_stores": len(writes), "accesses": events,
            "method": "straight-line constant-address instruction check"}


def build(output, cc, objdump, nm, readelf, host_cc):
    output.mkdir(parents=True, exist_ok=True)
    src = HERE / "fixtures/device.c"
    linker = HERE / "fixtures/link.ld"
    launcher = HERE / "fixtures/linux-entry.S"
    tools = {"cc": cc, "objdump": objdump, "nm": nm, "readelf": readelf, "host_cc": host_cc}
    manifest = {"schema_version": 1, "upstream_commit": UPSTREAM,
                "checkout_commit": command(["git", "rev-parse", "HEAD"]).strip(),
                "tools": {name: {"path": tool, "version": command([tool, "--version"]).splitlines()[0]}
                          for name, tool in tools.items()},
                "sources": {str(p.relative_to(REPO)): digest(p) for p in (src, linker, launcher, Path(__file__))},
                "binaries": [], "native_libraries": []}
    for model_index, model in enumerate(MODELS):
        config = output / f"{model}.toml"
        regions = [{"start-address": OUTPUT_ADDRESS, "length": 4}]
        if model == "ram":
            regions.insert(0, {"start-address": STATE_ADDRESS, "length": 4})
        config.write_text("observable-memory = [\n" + ",\n".join(
            f"  {{ start-address = 0x{r['start-address']:x}, length = {r['length']} }}" for r in regions) + "\n]\n")
        for optimization in (0, 2):
            for variant_index, variant in enumerate(VARIANTS):
                defines = [f"-DAUDIT_MODEL={model_index}", f"-DAUDIT_VARIANT={variant_index}"]
                library = output / f"{model}-o{optimization}-{variant}.so"
                native_command = [host_cc, "-std=c11", f"-O{optimization}", "-Wall", "-Wextra", "-Werror",
                                  "-fPIC", "-shared", "-DAUDIT_HOST=1", *defines, src, "-o", library]
                command(native_command)
                manifest["native_libraries"].append({"model": model, "variant": variant,
                    "optimization": optimization, "path": library.name, "sha256": digest(library),
                    "command": [str(x) for x in native_command]})
                for seed in (0, 1):
                    stem = f"{model}-o{optimization}-s{seed}-{variant}"
                    binary = output / f"{stem}.elf"
                    compile_command = [cc, "-std=c11", f"-O{optimization}", "-g", "-Wall", "-Wextra", "-Werror",
                        "-m32", "-mcpu=powerpc", "-msdata=none", "-ffreestanding", "-fno-stack-protector",
                        "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables",
                        "-nostdlib", "-static", "-no-pie", "-Wl,--build-id=none", "-Wl,-e,_start",
                        *defines, f"-DAUDIT_SEED={seed}u", src, launcher, linker, "-o", binary]
                    command(compile_command)
                    syms = symbols(nm, binary)
                    if syms.get("audit_state") != STATE_ADDRESS or syms.get("audit_output") != OUTPUT_ADDRESS:
                        raise ValueError(f"Unexpected symbol layout for {binary}: {syms}")
                    header = command([readelf, "-h", binary])
                    if not ("ELF32" in header and "PowerPC" in header and "big endian" in header):
                        raise ValueError(f"Wrong architecture: {header}")
                    disassembly = command([objdump, "-d", binary])
                    dis_path = output / f"{stem}.dis"
                    header_path = output / f"{stem}.elf-header.txt"
                    dis_path.write_text(disassembly)
                    header_path.write_text(header)
                    access_check = inspect_accesses(disassembly)
                    manifest["binaries"].append({"model": model, "variant": variant, "seed": seed,
                        "optimization": optimization, "path": binary.name, "sha256": digest(binary),
                        "config": config.name, "config_sha256": digest(config),
                        "disassembly": dis_path.name, "disassembly_sha256": digest(dis_path),
                        "elf_header": header_path.name, "elf_header_sha256": digest(header_path),
                        "symbols": {k: syms[k] for k in ("audit_entry", "audit_state", "audit_output")},
                        "command": [str(x) for x in compile_command], "access_check": access_check})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "build")
    parser.add_argument("--cc", default="powerpc-linux-gnu-gcc")
    parser.add_argument("--objdump", default="powerpc-linux-gnu-objdump")
    parser.add_argument("--nm", default="powerpc-linux-gnu-nm")
    parser.add_argument("--readelf", default="powerpc-linux-gnu-readelf")
    parser.add_argument("--host-cc", default="gcc")
    args = parser.parse_args()
    manifest = build(args.output.resolve(), *(resolve_tool(getattr(args, k)) for k in
                     ("cc", "objdump", "nm", "readelf", "host_cc")))
    print(f"Built and inspected {len(manifest['binaries'])} PowerPC32 binaries and "
          f"{len(manifest['native_libraries'])} native libraries.")


if __name__ == "__main__":
    main()
