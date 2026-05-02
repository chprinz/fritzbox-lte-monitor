#!/usr/bin/env python3
"""Erzeugt fritz_monitor.icns als Platzhalter-Icon."""
import os, struct, zlib, subprocess, shutil

def png(size, r, g, b):
    def chunk(tag, data):
        raw = tag + data
        return struct.pack('>I', len(data)) + raw + struct.pack('>I', zlib.crc32(raw) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)
    row  = b'\x00' + bytes([r, g, b]) * size
    idat = zlib.compress(row * size)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')

iconset = "fritz_monitor.iconset"
os.makedirs(iconset, exist_ok=True)

sizes = [16, 32, 64, 128, 256, 512, 1024]
for s in sizes:
    with open(f"{iconset}/icon_{s}x{s}.png", "wb") as f:
        f.write(png(s, 0, 102, 204))   # AVM-Blau
    if s <= 512:
        with open(f"{iconset}/icon_{s}x{s}@2x.png", "wb") as f:
            f.write(png(s * 2, 0, 102, 204))

subprocess.run(["iconutil", "-c", "icns", iconset], check=True)
shutil.rmtree(iconset)
print("fritz_monitor.icns erstellt.")
