"""
parsers/parser_m4a.py - ZimaTAG M4A Parser
Atomes iTunes, priorité tag texte ©gen, Album Artist
"""
import struct
from pathlib import Path
from typing import Dict, Optional, Tuple, BinaryIO

class M4AParser:
    """Parser natif M4A/MP4"""
    
    ATOMS = {
        b'\xa9nam': 'TITLE', b'\xa9ART': 'ARTIST', b'aART': 'ALBUMARTIST',
        b'\xa9alb': 'ALBUM', b'\xa9day': 'DATE', b'\xa9gen': 'GENRE',
        b'\xa9wrt': 'COMPOSER', b'\xa9cmt': 'COMMENT', b'\xa9too': 'ENCODER',
        b'trkn': 'trkn', b'disk': 'disk', b'covr': 'COVER'
    }
    
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.tags: Dict[str, str] = {}
        self.audio_info: Dict[str, any] = {}
        self.cover_data: Optional[bytes] = None
    
    def parse(self) -> Dict:
        """Parse complet M4A"""
        try:
            with open(self.filepath, 'rb') as f:
                fsize = f.seek(0, 2)
                f.seek(0)
                while f.tell() < fsize:
                    atom = self._read_atom(f)
                    if not atom:
                        break
                    size, atype, dstart = atom
                    if atype == b'moov':
                        self._parse_moov(f, dstart, size - 8)
                    else:
                        f.seek(dstart + size - 8)
            return self._build_result()
        except Exception:
            return self._build_result()
    
    def _read_atom(self, f: BinaryIO) -> Optional[Tuple[int, bytes, int]]:
        """Lit un atom MP4"""
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        size = struct.unpack('>I', hdr[:4])[0]
        atype = hdr[4:8]
        if size == 1:
            size = struct.unpack('>Q', f.read(8))[0]
        elif size == 0:
            size = f.seek(0, 2) - f.tell() + 8
            f.seek(-8, 1)
        if size < 8:
            return None  # atom corrompu (garde robustesse m4a)
        return (size, atype, f.tell())
    
    def _parse_moov(self, f: BinaryIO, start: int, size: int):
        """Parse atom moov"""
        end = start + size
        f.seek(start)
        while f.tell() < end:
            atom = self._read_atom(f)
            if not atom:
                break
            asize, atype, dstart = atom
            if atype == b'udta':
                self._parse_udta(f, dstart, asize - 8)
            elif atype == b'mvhd':
                self._parse_mvhd(f, dstart)
            elif atype == b'trak':
                self._parse_trak(f, dstart, asize - 8)
            f.seek(dstart + asize - 8)
    
    def _parse_udta(self, f: BinaryIO, start: int, size: int):
        """Parse atom udta"""
        end = start + size
        f.seek(start)
        while f.tell() < end:
            atom = self._read_atom(f)
            if not atom:
                break
            asize, atype, dstart = atom
            if atype == b'meta':
                f.read(4)  # version/flags
                self._parse_ilst_container(f, dstart + 4, asize - 12)
            f.seek(dstart + asize - 8)
    
    def _parse_ilst_container(self, f: BinaryIO, start: int, size: int):
        """Cherche et parse ilst"""
        end = start + size
        f.seek(start)
        while f.tell() < end:
            atom = self._read_atom(f)
            if not atom:
                break
            asize, atype, dstart = atom
            if atype == b'ilst':
                self._parse_ilst(f, dstart, asize - 8)
            f.seek(dstart + asize - 8)
    
    def _parse_ilst(self, f: BinaryIO, start: int, size: int):
        """Parse atom ilst"""
        end = start + size
        f.seek(start)
        while f.tell() < end:
            atom = self._read_atom(f)
            if not atom:
                break
            asize, atype, dstart = atom
            self._parse_ilst_item(f, atype, dstart, asize - 8)
            f.seek(dstart + asize - 8)
    
    def _parse_ilst_item(self, f: BinaryIO, atype: bytes, start: int, size: int):
        """Parse item ilst"""
        f.seek(start)
        while f.tell() < start + size:
            datom = self._read_atom(f)
            if not datom:
                break
            dsize, dtype, ddstart = datom
            if dtype == b'data':
                if dsize < 16:
                    break  # data atom tronque (garde robustesse m4a)
                f.read(8)  # type + locale
                val = f.read(dsize - 16)
                
                if atype == b'\xa9gen':
                    self.tags['GENRE'] = val.decode('utf-8', errors='ignore').strip()
                elif atype == b'gnre':
                    pass  # Ignore numeric genre
                elif atype == b'trkn' and len(val) >= 6:
                    self.tags['TRACKNUMBER'] = str(struct.unpack('>H', val[2:4])[0])
                    tot = struct.unpack('>H', val[4:6])[0]
                    if tot:
                        self.tags['TOTALTRACKS'] = str(tot)
                elif atype == b'disk' and len(val) >= 6:
                    self.tags['DISCNUMBER'] = str(struct.unpack('>H', val[2:4])[0])
                    tot = struct.unpack('>H', val[4:6])[0]
                    if tot:
                        self.tags['TOTALDISCS'] = str(tot)
                elif atype == b'covr':
                    self.cover_data = val
                else:
                    tag = self.ATOMS.get(atype)
                    if tag and tag not in ('trkn', 'disk', 'COVER'):
                        self.tags[tag] = val.decode('utf-8', errors='ignore').strip()
                break
            f.seek(ddstart + dsize - 8)
    
    def _parse_mvhd(self, f: BinaryIO, start: int):
        """Parse atom mvhd"""
        f.seek(start)
        ver = f.read(1)[0]
        f.read(3)
        if ver == 1:
            f.read(16)
            ts = struct.unpack('>I', f.read(4))[0]
            dur = struct.unpack('>Q', f.read(8))[0]
        else:
            f.read(8)
            ts = struct.unpack('>I', f.read(4))[0]
            dur = struct.unpack('>I', f.read(4))[0]
        if ts > 0:
            self.audio_info['duration_seconds'] = dur / ts
    
    def _parse_trak(self, f: BinaryIO, start: int, size: int):
        """Parse atom trak pour specs audio"""
        end = start + size
        f.seek(start)
        while f.tell() < end:
            atom = self._read_atom(f)
            if not atom:
                break
            asize, atype, dstart = atom
            if atype == b'mdia':
                self._parse_mdia(f, dstart, asize - 8)
            f.seek(dstart + asize - 8)
    
    def _parse_mdia(self, f: BinaryIO, start: int, size: int):
        """Parse atom mdia"""
        end = start + size
        f.seek(start)
        while f.tell() < end:
            atom = self._read_atom(f)
            if not atom:
                break
            asize, atype, dstart = atom
            if atype == b'minf':
                self._parse_minf(f, dstart, asize - 8)
            f.seek(dstart + asize - 8)
    
    def _parse_minf(self, f: BinaryIO, start: int, size: int):
        """Parse atom minf"""
        end = start + size
        f.seek(start)
        while f.tell() < end:
            atom = self._read_atom(f)
            if not atom:
                break
            asize, atype, dstart = atom
            if atype == b'stbl':
                self._parse_stbl(f, dstart, asize - 8)
            f.seek(dstart + asize - 8)
    
    def _parse_stbl(self, f: BinaryIO, start: int, size: int):
        """Parse atom stbl"""
        end = start + size
        f.seek(start)
        while f.tell() < end:
            atom = self._read_atom(f)
            if not atom:
                break
            asize, atype, dstart = atom
            if atype == b'stsd':
                self._parse_stsd(f, dstart)
            f.seek(dstart + asize - 8)
    
    def _parse_stsd(self, f: BinaryIO, start: int):
        """Parse atom stsd"""
        f.seek(start + 8)
        atom = self._read_atom(f)
        if atom and atom[1] == b'mp4a':
            f.seek(atom[2] + 16)
            ch = struct.unpack('>H', f.read(2))[0]
            f.read(6)
            sr = struct.unpack('>I', f.read(4))[0] >> 16
            self.audio_info['channels'] = ch
            self.audio_info['samplerate'] = sr
    
    def _build_result(self) -> Dict:
        return {'tags': self.tags, 'audio_info': self.audio_info, 'cover_data': self.cover_data}
