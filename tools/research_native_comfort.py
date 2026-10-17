"""Read-only local PE disassembly for comfort research; not shipped with editor."""
from pathlib import Path
import argparse
import struct

import capstone
import pefile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('addresses', nargs='+', type=lambda s: int(s, 16))
    args = parser.parse_args()
    raw = Path('D:/Tools/Steam/steamapps/common/ArkPark2/ArkPark2/Binaries/Win64/ArkPark2-Win64-Shipping.exe').read_bytes()
    pe = pefile.PE(data=raw, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    pdata = next(s for s in pe.sections if s.Name.rstrip(b'\0') == b'.pdata')
    entries = [struct.unpack_from('<III', raw, o) for o in range(pdata.PointerToRawData, pdata.PointerToRawData + pdata.SizeOfRawData - 11, 12)]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    for address in args.addresses:
        entry = next((e for e in entries if e[0] <= address - base < e[1]), None)
        if not entry:
            print(hex(address), 'not found')
            continue
        start, end, _ = entry
        # MSVC may split one function into adjacent unwind regions.
        while next_entry := next((e for e in entries if e[0] == end), None):
            end = next_entry[1]
        offset = pe.get_offset_from_rva(start)
        lines = []
        for instruction in md.disasm(raw[offset:offset + end - start], base + start):
            comment = []
            for operand in instruction.operands:
                if operand.type == capstone.x86.X86_OP_MEM and operand.mem.base == capstone.x86.X86_REG_RIP:
                    target = instruction.address + instruction.size + operand.mem.disp
                    try:
                        target_offset = pe.get_offset_from_rva(target - base)
                        data = raw[target_offset:target_offset + 160]
                        ascii_text = data.split(b'\0', 1)[0].decode('utf-8')
                        if len(ascii_text) > 3 and all(c.isprintable() for c in ascii_text):
                            comment.append(repr(ascii_text))
                        else:
                            utf = data.decode('utf-16le', errors='ignore').split('\0', 1)[0]
                            if len(utf) > 3 and all(c.isprintable() for c in utf):
                                comment.append(repr(utf))
                    except (pefile.PEFormatError, UnicodeDecodeError):
                        pass
            lines.append(f'{instruction.address:x}: {instruction.mnemonic} {instruction.op_str}' + (' ; ' + ', '.join(comment) if comment else ''))
        text = '\n'.join(lines)
        destination = Path(f'build/research/native-{base + start:x}.txt')
        destination.write_text(text, encoding='utf-8')
        print(destination, len(lines), 'instructions')
        print(text)


if __name__ == '__main__':
    main()
