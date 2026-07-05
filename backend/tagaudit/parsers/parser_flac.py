"""
parsers/parser_flac.py - ZimaTAG FLAC Parser
Vorbis Comments, StreamInfo, calcul durée via samples
"""
import struct
from pathlib import Path
from typing import Dict, Optional

class FLACParser:
    """Parser natif FLAC"""
    
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.tags: Dict[str, str] = {}
        self.audio_info: Dict[str, any] = {}
        self.cover_data: Optional[bytes] = None
    
    def parse(self) -> Dict:
        """Parse complet du fichier FLAC"""
        try:
            fsize = self.filepath.stat().st_size
            with open(self.filepath, 'rb') as f:
                if f.read(4) != b'fLaC':
                    return self._build_result()
                
                last = False
                while not last:
                    hdr = f.read(4)
                    if len(hdr) < 4:
                        break
                    last = (hdr[0] & 0x80) != 0
                    btype = hdr[0] & 0x7F
                    bsize = struct.unpack('>I', b'\x00' + hdr[1:4])[0]
                    data = f.read(bsize)
                    
                    if btype == 0:
                        self._parse_streaminfo(data)
                    elif btype == 4:
                        self._parse_vorbis(data)
                    elif btype == 6:
                        self._parse_picture(data)
            
            # Calcul bitrate
            if 'duration_seconds' in self.audio_info and self.audio_info['duration_seconds'] > 0:
                self.audio_info['bitrate'] = int((fsize * 8) / (self.audio_info['duration_seconds'] * 1000))
            
            return self._build_result()
        except Exception:
            return self._build_result()
    
    def _parse_streaminfo(self, data: bytes):
        """Parse bloc STREAMINFO"""
        if len(data) < 34:
            return
        try:
            # Samplerate (20 bits)
            sr = (data[10] << 12) | (data[11] << 4) | (data[12] >> 4)
            # Channels (3 bits)
            ch = ((data[12] >> 1) & 0x07) + 1
            # Bits per sample (5 bits)
            bps = (((data[12] & 0x01) << 4) | (data[13] >> 4)) + 1
            # Total samples (36 bits)
            samples = (
                ((data[13] & 0x0F) << 32) |
                (data[14] << 24) | (data[15] << 16) |
                (data[16] << 8) | data[17]
            )
            
            self.audio_info['samplerate'] = sr
            self.audio_info['channels'] = ch
            self.audio_info['bitdepth'] = bps
            
            if sr > 0 and samples > 0:
                self.audio_info['duration_seconds'] = samples / sr
        except:
            pass
    
    def _parse_vorbis(self, data: bytes):
        """Parse Vorbis Comment"""
        if len(data) < 8:
            return
        try:
            pos = 0
            vlen = struct.unpack('<I', data[pos:pos+4])[0]
            pos += 4
            if pos + vlen <= len(data):
                self.tags['ENCODER'] = data[pos:pos+vlen].decode('utf-8', errors='ignore').strip()
            pos += vlen
            
            if pos + 4 > len(data):
                return
            count = struct.unpack('<I', data[pos:pos+4])[0]
            pos += 4
            
            for _ in range(count):
                if pos + 4 > len(data):
                    break
                clen = struct.unpack('<I', data[pos:pos+4])[0]
                pos += 4
                if pos + clen > len(data):
                    break
                comment = data[pos:pos+clen].decode('utf-8', errors='ignore')
                if '=' in comment:
                    key, val = comment.split('=', 1)
                    key = key.upper().strip()
                    val = val.strip()
                    if key in self.tags:
                        self.tags[key] = f"{self.tags[key]}; {val}"
                    else:
                        self.tags[key] = val
                pos += clen
        except:
            pass
    
    def _parse_picture(self, data: bytes):
        """Parse bloc Picture"""
        if len(data) < 32:
            return
        try:
            pos = 0
            pos += 4  # type
            if pos + 4 > len(data):
                return  # garde robustesse flac (mime_len hors bornes)
            mime_len = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4 + mime_len
            if pos + 4 > len(data):
                return  # garde robustesse flac (desc_len hors bornes)
            desc_len = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4 + desc_len + 16  # +16 for dimensions
            if pos + 4 > len(data):
                return  # garde robustesse flac (pic_len hors bornes)
            pic_len = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4
            if pos + pic_len <= len(data):
                self.cover_data = data[pos:pos+pic_len]
        except:
            pass
    
    def _build_result(self) -> Dict:
        return {'tags': self.tags, 'audio_info': self.audio_info, 'cover_data': self.cover_data}
