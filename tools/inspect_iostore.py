"""Read-only, targeted UE5 IoStore investigation. Not required by the editor.

Pass a trusted local Oodle DLL and a filename filter. Extracts selected package
chunks only into the requested workspace output directory. Never writes a game file.
"""
from pathlib import Path
import argparse
import ctypes
import json
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.gvas import Reader


class IoStore:
    def __init__(self, toc: Path, dll: Path):
        self.toc = toc
        self.data = toc.read_bytes()
        b = self.data
        if b[:16] != b'-==--==--==--==-':
            raise ValueError('Bad IoStore magic')
        version = b[16]
        if version != 8:
            raise ValueError(f'Unsupported toc version {version}')
        self.header, self.count, self.block_count, self.block_entry_size, methods, method_size, self.block_size, dir_size = struct.unpack_from('<8I', b, 20)
        flags = b[80]
        if flags & 6:
            raise ValueError('Encrypted/signed containers are unsupported')
        seeds = struct.unpack_from('<I', b, 84)[0]
        overflow = struct.unpack_from('<I', b, 96)[0]
        self.block_base = self.header + self.count * 22 + seeds*4 + overflow*4
        method_base = self.block_base + self.block_count*self.block_entry_size
        self.methods = [b[method_base+i*method_size:method_base+(i+1)*method_size].rstrip(b'\0').decode() for i in range(methods)]
        r = Reader(b)
        r.position = method_base + methods*method_size
        self.mount = r.string()
        directories = [struct.unpack('<4I', r.read(16)) for _ in range(r.unpack('i'))]
        files = [struct.unpack('<3I', r.read(12)) for _ in range(r.unpack('i'))]
        strings = [r.string() for _ in range(r.unpack('i'))]
        self.entries = {}
        def walk(i, parent):
            name, child, sibling, first = directories[i]
            path = parent + '/' + (strings[name] if name != 0xffffffff else '')
            k = first
            while k != 0xffffffff:
                n, nxt, user = files[k]
                self.entries[path+'/'+strings[n]] = user
                k = nxt
            if child != 0xffffffff: walk(child, path)
            if sibling != 0xffffffff: walk(sibling, parent)
        walk(0, self.mount)
        self.dll = ctypes.CDLL(str(dll.resolve()))
        self.decode = self.dll.OodleLZ_Decompress
        self.decode.restype = ctypes.c_ssize_t
        self.decode.argtypes = [ctypes.c_void_p,ctypes.c_ssize_t,ctypes.c_void_p,ctypes.c_ssize_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_ssize_t,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ssize_t,ctypes.c_int]

    def extract(self, key: str) -> bytes:
        index = self.entries[key]
        raw = self.data[self.header+12*self.count+index*10:self.header+12*self.count+index*10+10]
        offset, length = int.from_bytes(raw[:5], 'big'), int.from_bytes(raw[5:], 'big')
        result = bytearray()
        with self.toc.with_suffix('.ucas').open('rb') as f:
            for block in range(offset//self.block_size, (offset+length-1)//self.block_size+1):
                entry = self.data[self.block_base+block*self.block_entry_size:self.block_base+(block+1)*self.block_entry_size]
                physical = int.from_bytes(entry[:5],'little')
                compressed = int.from_bytes(entry[5:8],'little')
                uncompressed = int.from_bytes(entry[8:11],'little')
                method = entry[11]
                f.seek(physical)
                src = f.read(compressed)
                if method:
                    if self.methods[method-1] != 'Oodle': raise ValueError('Unsupported compression')
                    dst = ctypes.create_string_buffer(uncompressed)
                    got = self.decode(src,len(src),dst,uncompressed,1,0,0,None,0,None,None,None,0,3)
                    if got != uncompressed: raise ValueError(f'Oodle failed: {got}')
                    result.extend(dst.raw)
                else: result.extend(src)
        start = offset % self.block_size
        return bytes(result[start:start+length])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('toc', type=Path)
    parser.add_argument('dll', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('filters', nargs='+')
    args = parser.parse_args()
    store = IoStore(args.toc,args.dll)
    args.output.mkdir(parents=True,exist_ok=True)
    index = []
    for key in store.entries:
        if not any(term in key for term in args.filters):continue
        if not key.endswith('.uasset'):continue
        chunk = store.extract(key)
        basename = key.rsplit('/',1)[1]
        destination = args.output/basename
        destination.write_bytes(chunk)
        print(key, len(chunk), str(destination))
        index.append({'package':key,'file':str(destination),'size':len(chunk)})
    (args.output/'index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':main()
