"""Research-only, read-only UObject inspection of ArkPark2; no memory writes."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import struct

import psutil


class MODULEINFO(C.Structure):
    _fields_ = [('lpBaseOfDll', C.c_void_p), ('SizeOfImage', W.DWORD), ('EntryPoint', C.c_void_p)]


class Memory:
    def __init__(self):
        candidates = [p for p in psutil.process_iter(['name'])
                      if (p.info['name'] or '').lower() == 'arkpark2-win64-shipping.exe']
        if len(candidates) != 1:
            raise RuntimeError('需要恰好一个正在运行的 ArkPark2 主程序')
        self.pid = candidates[0].pid
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        self.k.ReadProcessMemory.restype = W.BOOL
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.handle = self.k.OpenProcess(0x0410, False, self.pid)  # query information + VM_READ only
        if not self.handle:
            raise C.WinError(C.get_last_error())
        modules = (C.c_void_p * 1024)()
        needed = W.DWORD()
        self.k.K32EnumProcessModulesEx.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), W.DWORD]
        self.k.K32GetModuleInformation.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MODULEINFO), W.DWORD]
        if not self.k.K32EnumProcessModulesEx(self.handle, modules, C.sizeof(modules), C.byref(needed), 3):
            raise C.WinError(C.get_last_error())
        info = MODULEINFO()
        if not self.k.K32GetModuleInformation(self.handle, modules[0], C.byref(info), C.sizeof(info)):
            raise C.WinError(C.get_last_error())
        if info.SizeOfImage != 0xAE54000:
            raise RuntimeError('游戏构建与本次已分析程序不一致，拒绝复用研究偏移')
        self.base = info.lpBaseOfDll
        self.names = {}
        self.classes = {}
        self.pool = self.base + 0xA1A5100

    def close(self):
        self.k.CloseHandle(self.handle)

    def read(self, address, size):
        if not 0 < size <= 16 * 1024 * 1024:
            raise ValueError('Read size outside research bounds')
        buf = C.create_string_buffer(size)
        n = C.c_size_t()
        if not self.k.ReadProcessMemory(self.handle, C.c_void_p(address), buf, size, C.byref(n)) or n.value != size:
            raise C.WinError(C.get_last_error())
        return buf.raw

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def i32(self, address):
        return struct.unpack('<i', self.read(address, 4))[0]

    def name(self, index, number=0):
        if index not in self.names:
            block, offset = index >> 16, index & 0xffff
            if block > 8191:
                raise ValueError('Name block outside bounds')
            pointer = self.u64(self.pool + 0x10 + block * 8) + offset * 2
            header = struct.unpack('<H', self.read(pointer, 2))[0]
            length, wide = header >> 6, header & 1
            if not 0 < length < 1024:
                raise ValueError('Invalid name length')
            value = self.read(pointer + 2, length * (2 if wide else 1)).decode('utf-16le' if wide else 'utf-8')
            self.names[index] = value
        return self.names[index] + (f'_{number-1}' if number else '')

    def object_name(self, address):
        index, number = struct.unpack('<II', self.read(address + 0x18, 8))
        return self.name(index, number)

    def object_path(self, address):
        parts = []
        seen = set()
        while address and address not in seen and len(parts) < 16:
            seen.add(address)
            parts.append(self.object_name(address))
            address = self.u64(address + 0x20)
        return list(reversed(parts))

    def ancestry(self, address):
        if address in self.classes:
            return self.classes[address]
        names = []
        pointer = address
        visited = set()
        while pointer and pointer not in visited and len(names) < 32:
            visited.add(pointer)
            names.append(self.object_name(pointer))
            pointer = self.u64(pointer + 0x40)
        self.classes[address] = names
        return names

    def properties(self, address):
        properties = []
        visited = set()
        cls = address
        while cls and cls not in visited and len(visited) < 32:
            visited.add(cls)
            field = self.u64(cls + 0x50)
            seen = set()
            while field and field not in seen and len(seen) < 10000:
                seen.add(field)
                data = self.read(field, 0x58)
                # This UE5.6 build stores FFieldVariant owner in one tagged
                # pointer, so Next/Name precede older UE layouts by 8 bytes.
                index, number = struct.unpack_from('<II', data, 0x20)
                properties.append({'name': self.name(index, number),
                                   'offset': struct.unpack_from('<i', data, 0x44)[0],
                                   'size': struct.unpack_from('<i', data, 0x34)[0]})
                field = struct.unpack_from('<Q', data, 0x18)[0]
            cls = self.u64(cls + 0x40)
        return properties

    def objects(self):
        # These globals were traced from the build's native UObject index lookup.
        pointer = self.u64(self.base + 0xA288C50)
        count = self.i32(self.base + 0xA288C64)
        if not 0 < count < 2_000_000 or self.name(0) != 'None':
            raise RuntimeError('Object/name pool verification failed')
        print('UObject count:', count)
        for start in range(0, count, 65536):
            chunk = self.u64(pointer + (start // 65536) * 8)
            data = self.read(chunk, min(65536, count-start)*24)
            for offset in range(0, len(data), 24):
                address = struct.unpack_from('<Q', data, offset)[0]
                if address:
                    yield address


def main():
    m = Memory()
    report = {'pid': m.pid, 'module_base': hex(m.base), 'furniture': [], 'controllers': [], 'failures': 0}
    try:
        props = {}
        for obj in m.objects():
            try:
                header = m.read(obj, 0x28)
                flags = struct.unpack_from('<I', header, 8)[0]
                cls = struct.unpack_from('<Q', header, 0x10)[0]
                chain = m.ancestry(cls)
                is_furniture = 'BP_拉面龙家具_C' in chain
                is_controller = chain[0] == 'PC_方舟乐园_C'
                if flags & 0x30 or not (is_furniture or is_controller):
                    continue
                if cls not in props:
                    props[cls] = m.properties(cls)
                comfort_fields = [p for p in props[cls] if '舒适' in p['name']]
                row = {'address': hex(obj), 'name': m.object_name(obj), 'class': chain[0],
                       'ancestry': chain, 'outer': m.object_name(struct.unpack_from('<Q', header, 0x20)[0]),
                       'object_path': m.object_path(obj), 'comfort': []}
                for p in comfort_fields:
                    if p['size'] in (4, 8):
                        value = struct.unpack('<f' if p['size'] == 4 else '<d', m.read(obj+p['offset'], p['size']))[0]
                        row['comfort'].append({**p, 'value': value})
                report['furniture' if is_furniture else 'controllers'].append(row)
            except (OSError, ValueError, UnicodeError, IndexError):
                report['failures'] += 1
        output = Path('build/research/runtime-comfort.json')
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'furniture_count': len(report['furniture']), 'controllers': report['controllers'],
                          'failures': report['failures'], 'output': str(output)}, ensure_ascii=False))
    finally:
        m.close()


if __name__ == '__main__':
    main()
