"""Bounded, page-scoped PDF colour inspection, run in a disposable process.

This reports source colour usage and declared profiles, not conversion settings.
It never changes the uploaded PDF or renders/decodes image pixel data.
"""
import io
import json
import logging
import math
import os
import sys
import unicodedata

from PIL import ImageCms
from pypdf import PdfReader, apply_configuration
from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject

MAX_BYTES = 8 * 1024 * 1024
UNKNOWN = {"rgbStatus": "unknown", "profilesStatus": "unknown",
           "outputProfiles": [], "outputConditions": [], "sourceProfiles": []}


def bound_memory():
    """Keep parser expansion inside a 512 MiB disposable-process budget."""
    budget = 512 * 1024 * 1024
    if os.name != 'nt':
        import resource as system_resource
        system_resource.setrlimit(system_resource.RLIMIT_AS, (budget, budget))
        return
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [('processTime', ctypes.c_longlong), ('jobTime', ctypes.c_longlong),
                    ('flags', wintypes.DWORD), ('minWorking', ctypes.c_size_t),
                    ('maxWorking', ctypes.c_size_t), ('active', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', Counters), ('processMemory', ctypes.c_size_t),
                    ('jobMemory', ctypes.c_size_t), ('peakProcess', ctypes.c_size_t), ('peakJob', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    settings = Extended(); settings.basic.flags = 0x100; settings.processMemory = budget
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(settings), ctypes.sizeof(settings)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise OSError('Cannot bound inspection memory')
    # Retain the job handle until process exit; no other process is assigned.


def clean_name(value):
    if not isinstance(value, str): return ""
    return " ".join("".join(c for c in value if not unicodedata.category(c).startswith("C")).split())[:128]


def resolved(value):
    return value.get_object() if hasattr(value, "get_object") else value


def resource(resources, category, name):
    return resolved(resolved(resources).get(category, {})).get(name)


class Inspector:
    def __init__(self, reader):
        self.reader = reader
        self.rgb = False
        self.complete = True
        self.profiles_complete = True
        self.source_profiles = []
        self.spot_colors = {}
        self.conflicting_spots = set()
        self.ink_names = ['Cyan', 'Magenta', 'Yellow', 'Black']
        self.bytes = 0
        self.nodes = 0
        self.operations = 0

    def data(self, stream):
        data = stream.get_data()
        self.bytes += len(data)
        if self.bytes > MAX_BYTES: raise ValueError("inspection budget")
        return data

    def profile(self, stream):
        raw = self.data(stream)
        if len(raw) > 2 * 1024 * 1024: raise ValueError("profile budget")
        profile = ImageCms.ImageCmsProfile(io.BytesIO(raw))
        name = clean_name(ImageCms.getProfileDescription(profile))
        return name, raw[16:20]

    def spot_rgb(self, space, resources):
        """Preserve direct RGB alternate colours without a CMYK gamut roundtrip.

        Other tint functions/spaces use the renderer's CMYK equivalents.
        Only simple, fully understood endpoint definitions are handled here.
        """
        try:
            if (space[0] != '/Separation' or len(space) != 4
                    or resolved(space[2]) != '/DeviceRGB'
                    or resource(resources, '/ColorSpace', '/DefaultRGB') is not None):
                return
            function = resolved(space[3])
            if function.get('/FunctionType') != 2 or list(resolved(function.get('/Domain', []))) != [0, 1]:
                return
            exponent = float(function.get('/N', 0))
            if not math.isfinite(exponent) or exponent <= 0:
                return
            values = [float(v) for v in resolved(function.get('/C1', []))]
            if len(values) != 3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values):
                return
            ranges = resolved(function.get('/Range', [0, 1] * 3))
            if list(ranges) != [0, 1] * 3:
                return
            name = str(resolved(space[1]))[1:]
            if not name or len(name) > 1024 or name in self.conflicting_spots:
                return
            color = '#' + ''.join(f'{round(v * 255):02x}' for v in values)
            if name in self.spot_colors and self.spot_colors[name] != color:
                del self.spot_colors[name]
                self.conflicting_spots.add(name)
            elif len(self.spot_colors) < 64:
                self.spot_colors[name] = color
        except (ValueError, TypeError, AttributeError, IndexError):
            pass

    def space(self, space, resources, seen=None):
        """Follow only colour spaces actually selected for painting."""
        seen = set() if seen is None else seen
        space = space.get_object() if hasattr(space, "get_object") else space
        key = str(space) if isinstance(space, str) else id(space)
        if key in seen or len(seen) >= 16:
            self.complete = False
            return
        seen.add(key)
        if isinstance(space, str) and space in ("/DeviceRGB", "/RGB", "/CalRGB"):
            default = resource(resources, "/ColorSpace", "/DefaultRGB")
            if default is not None and space in ("/DeviceRGB", "/RGB"):
                self.space(default, resources, seen)
            else: self.rgb = True
        elif isinstance(space, str):
            if space in ("/DeviceCMYK", "/CMYK", "/DeviceGray", "/G"):
                default = resource(resources, "/ColorSpace", "/DefaultCMYK" if space in ("/DeviceCMYK", "/CMYK") else "/DefaultGray")
                if default is not None: self.space(default, resources, seen)
                return
            if space in ("/CalGray", "/Lab", "/Pattern"):
                return
            value = resource(resources, "/ColorSpace", space)
            if value is None: self.complete = False
            else: self.space(value, resources, seen)
        elif isinstance(space, (list, ArrayObject)) and space:
            kind = space[0]
            if kind == "/ICCBased":
                stream = space[1].get_object()
                count = int(stream.get("/N", 0))
                if count not in (1, 3, 4): self.complete = False
                try:
                    name, signature = self.profile(stream)
                    if name and name not in self.source_profiles:
                        if len(self.source_profiles) < 16: self.source_profiles.append(name)
                        else: self.profiles_complete = False
                    if signature == b"RGB ": self.rgb = True
                    if {b"RGB ":3, b"GRAY":1, b"CMYK":4, b"Lab ":3, b"XYZ ":3}.get(signature) != count:
                        self.complete = False
                except Exception:
                    self.profiles_complete = False
                    self.complete = False
            elif kind in ("/Indexed", "/I", "/Pattern") and len(space) > 1:
                self.space(space[1], resources, seen)
            elif kind in ("/Separation", "/DeviceN") and len(space) > 2:
                names = [resolved(space[1])] if kind == '/Separation' else resolved(space[1])
                for raw_name in names:
                    name = str(raw_name)[1:]
                    if name in ('None', 'All'):
                        continue
                    if not name or len(name) > 1024 or (name not in self.ink_names and len(self.ink_names) >= 64):
                        self.complete = False
                    elif name not in self.ink_names:
                        self.ink_names.append(name)
                self.spot_rgb(space, resources)
                # Alternate RGB definitions may be used during conversion.
                self.space(space[2], resources, seen)
            elif kind == "/CalRGB": self.rgb = True
            elif kind not in ("/CalGray", "/Lab"): self.complete = False
        else: self.complete = False

    def scan(self, stream, resources, inherited=None, depth=0, active=None):
        if stream is None: return
        self.nodes += 1
        if depth > 16 or self.nodes > 128: raise ValueError("inspection budget")
        resources = resolved(resources)
        active = set() if active is None else active
        stream = stream.get_object()
        if id(stream) in active:
            self.complete = False
            return
        active = active | {id(stream)}
        if hasattr(stream, "get"):
            group = resolved(stream.get("/Group", {}))
            if group.get("/S") == "/Transparency" and group.get("/CS") is not None:
                self.space(group["/CS"], resources)
        if isinstance(stream, ArrayObject):
            data = b"\n".join(self.data(s.get_object()) for s in stream)
        else: data = self.data(stream)
        raw = DecodedStreamObject()
        raw.set_data(data)
        content = ContentStream(raw, self.reader)
        state = dict(inherited or {"fill": "/DeviceGray", "stroke": "/DeviceGray", "mode": 0, "font": None})
        stack = []
        for args, op in content.operations:
            self.operations += 1
            if self.operations > 200_000: raise ValueError("inspection budget")
            if op == b"q":
                if len(stack) >= 128: raise ValueError("graphics stack budget")
                stack.append(state.copy())
            elif op == b"Q":
                if stack: state = stack.pop()
                else: self.complete = False
            elif op in (b"rg", b"g", b"k", b"cs"):
                state["fill"] = {b"rg": "/DeviceRGB", b"g": "/DeviceGray", b"k": "/DeviceCMYK"}.get(op, args[0] if args else None)
            elif op in (b"RG", b"G", b"K", b"CS"):
                state["stroke"] = {b"RG": "/DeviceRGB", b"G": "/DeviceGray", b"K": "/DeviceCMYK"}.get(op, args[0] if args else None)
            elif op == b"Tr": state["mode"] = int(args[0])
            elif op == b"Tf": state["font"] = args[0]
            elif op in (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"S", b"s", b"Tj", b"TJ", b"'", b'"'):
                text = op in (b"Tj", b"TJ", b"'", b'"')
                if (text and state["mode"] in (0, 2, 4, 6)) or (not text and op not in (b"S", b"s")):
                    self.space(state["fill"], resources)
                if (text and state["mode"] in (1, 2, 5, 6)) or (not text and op in (b"B", b"B*", b"b", b"b*", b"S", b"s")):
                    self.space(state["stroke"], resources)
                if text and state["font"]:
                    font = resource(resources, "/Font", state["font"])
                    if font is not None and font.get_object().get("/Subtype") == "/Type3":
                        self.complete = False  # Glyph programs need a separate interpreter.
            elif op == b"Do":
                obj = resource(resources, "/XObject", args[0])
                if obj is None:
                    self.complete = False
                    continue
                obj = obj.get_object()
                if obj.get("/Subtype") == "/Form":
                    self.scan(obj, obj.get("/Resources", resources), state, depth + 1, active)
                elif obj.get("/Subtype") == "/Image":
                    self.space(state["fill"] if obj.get("/ImageMask") else obj.get("/ColorSpace"), resources)
                    mask = obj.get("/SMask")
                    if mask is not None and hasattr(mask.get_object(), "get"):
                        self.space(mask.get_object().get("/ColorSpace"), resources)
                else: self.complete = False
            elif op == b"INLINE IMAGE":
                settings = args["settings"]
                self.space(state["fill"] if settings.get("/IM", settings.get("/ImageMask")) else settings.get("/CS", settings.get("/ColorSpace")), resources)
            elif op == b"sh":
                shading = resource(resources, "/Shading", args[0])
                if shading is None: self.complete = False
                else: self.space(shading.get_object().get("/ColorSpace"), resources)
            elif op in (b"scn", b"SCN") and args and isinstance(args[-1], str):
                pattern = resource(resources, "/Pattern", args[-1])
                if pattern is None: self.complete = False
                else:
                    pattern = pattern.get_object()
                    if pattern.get("/PatternType") == 1:
                        self.scan(pattern, pattern.get("/Resources", resources), state, depth + 1, active)
                    else: self.space(resolved(pattern.get("/Shading", {})).get("/ColorSpace"), resources)
            elif op == b"gs":
                ext = resource(resources, "/ExtGState", args[0])
                if ext is not None:
                    mask = ext.get_object().get("/SMask")
                    if hasattr(mask, "get_object"):
                        mask = mask.get_object()
                        if hasattr(mask, "get") and mask.get("/G") is not None:
                            group = mask["/G"]
                            self.scan(group, group.get("/Resources", resources), state, depth + 1, active)

    def inspect(self, page):
        resources = page.get("/Resources", {})
        output_profiles, conditions = [], []
        intents = resolved(page.get("/OutputIntents", self.reader.root_object.get("/OutputIntents", [])))
        for intent in intents:
            if len(output_profiles) + len(conditions) >= 16:
                self.profiles_complete = False
                break
            intent = intent.get_object()
            try:
                stream = intent.get("/DestOutputProfile")
                name = self.profile(stream.get_object())[0] if stream is not None else ""
                declared = clean_name(intent.get("/OutputConditionIdentifier"))
                if not declared or declared == "Custom": declared = clean_name(intent.get("/Info"))
                target, label = (output_profiles, name or declared) if stream is not None else (conditions, declared)
                if label and label not in target: target.append(label)
                if stream is not None and not label: self.profiles_complete = False
            except Exception: self.profiles_complete = False
        try:
            group = resolved(page.get("/Group", {}))
            if group.get("/S") == "/Transparency" and group.get("/CS") is not None:
                self.space(group["/CS"], resources)
            self.scan(page.get("/Contents"), resources)
            # Annotation appearances can contribute artwork to the rendered page.
            for annotation in resolved(page.get("/Annots", [])):
                annotation = annotation.get_object()
                appearances = resolved(annotation.get("/AP", {})).get("/N")
                if appearances is None: continue
                appearances = appearances.get_object()
                if not hasattr(appearances, "get_data"):
                    appearances = appearances.get(annotation.get("/AS"))
                if appearances is not None:
                    appearances = appearances.get_object()
                    self.scan(appearances, appearances.get("/Resources", resources))
        except Exception: self.complete = False
        return {"rgbStatus": "detected" if self.rgb else "not_detected" if self.complete else "unknown",
                "profilesStatus": "complete" if self.profiles_complete and self.complete else "unknown",
                "outputProfiles": output_profiles, "outputConditions": conditions, "sourceProfiles": self.source_profiles,
                "spotColors": self.spot_colors,
                "inkChannelsStatus": 'complete' if self.complete else 'unknown',
                "inkChannels": [{'name': name, **({'displayColor': self.spot_colors[name]} if name in self.spot_colors else {})}
                                for name in self.ink_names]}


def inspect_pdf(path, page_number):
    with apply_configuration(maximum_declared_stream_length=MAX_BYTES,
            array_based_stream_maximum_output_length=MAX_BYTES,
            zlib_maximum_output_length=MAX_BYTES, lzw_maximum_output_length=MAX_BYTES,
            run_length_maximum_output_length=MAX_BYTES, stream_decoding_work_maximum_length=MAX_BYTES * 2,
            page_tree_maximum_entries=100, page_tree_maximum_depth=32):
        with open(path, "rb") as stream:
            reader = PdfReader(stream, strict=True)
            return Inspector(reader).inspect(reader.pages[page_number - 1])


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        bound_memory()
        result = inspect_pdf(sys.argv[1], int(sys.argv[2]))
    except Exception: result = UNKNOWN.copy()
    print(json.dumps(result, ensure_ascii=True))
