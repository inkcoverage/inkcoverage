"""Bounded, read-only vector inspection for the estimator's PDF helper.

Interpret a deliberately small subset of PDF graphics. Keep source colours:
never infer CMYK/spot recipes from rendered RGB. Text uses source font metrics.
Coordinates and bounds exclude stroke thickness.
"""
import json
import logging
import math
import sys

from pypdf import PdfReader, PdfWriter, apply_configuration
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject, NameObject
from pypdf.generic._font import Font
from pdf_colors import MAX_BYTES, bound_memory, clean_name, resolved, resource

IDENTITY = (1, 0, 0, 1, 0, 0)
# Outlined packaging text commonly contains thousands of small painted paths.
# Keep these limits independent of the much smaller renderer diagnostic budget.
MAX_OBJECTS = 5000
MAX_COMMANDS = 100000
MAX_PACKET_BYTES = 6 * 1024 * 1024
MAX_TEXT_GLYPHS = 100000


def numbers(values, count=None):
    result = [float(v) for v in values]
    if (count is not None and len(result) != count) or any(not math.isfinite(v) or abs(v) > 1e9 for v in result):
        raise ValueError('unsupported coordinates')
    return result


def compose(a, b):
    """Return a(b(point)), matching PDF's concatenation order."""
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def point(matrix, x, y):
    return numbers((matrix[0]*x+matrix[2]*y+matrix[4], matrix[1]*x+matrix[3]*y+matrix[5]), 2)


def cubic_at(a, b, c, d, t):
    return (1-t)**3*a + 3*(1-t)**2*t*b + 3*(1-t)*t*t*c + t**3*d


def extrema(a, b, c, d):
    # Derivative / 3 of the cubic Bézier polynomial.
    qa, qb, qc = -a+3*b-3*c+d, 2*(a-2*b+c), b-a
    if abs(qa) < 1e-12:
        return [-qc/qb] if abs(qb) > 1e-12 else []
    discriminant = qb*qb - 4*qa*qc
    if discriminant < 0:
        return []
    root = math.sqrt(discriminant)
    return [(-qb+root)/(2*qa), (-qb-root)/(2*qa)]


def bounds(commands):
    """Tight bounds of lines/curves, rather than their control-point box."""
    xs, ys = [], []
    current = start = None
    for command in commands:
        op, *args = command
        if op in ('M', 'L'):
            current = args
            if op == 'M': start = args
            xs.append(args[0]); ys.append(args[1])
        elif op == 'C' and current is not None:
            for axis, output in ((0, xs), (1, ys)):
                values = [current[axis], args[axis], args[axis+2], args[axis+4]]
                output.append(values[-1])
                output.extend(cubic_at(*values, t) for t in extrema(*values) if 0 < t < 1)
            current = args[-2:]
        elif op == 'Z':
            current = start
    if not xs: return None
    return [min(xs), min(ys), max(xs), max(ys)]


def intersect(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


class Inspector:
    def __init__(self, reader, page_number):
        self.reader = reader
        self.page_number = page_number
        self.objects, self.layers = [], {}
        self.byte_count = self.operations = self.nodes = self.command_count = 0
        self.limited = False
        self.text_present = False
        self.fonts = {}
        self.text_glyphs = 0
        self.hidden_layers = set()
        config = resolved(resolved(reader.root_object.get('/OCProperties', {})).get('/D', {}))
        self.base_off = config.get('/BaseState') == '/OFF'
        self.on = {self.key(o) for o in resolved(config.get('/ON', []))}
        self.off = {self.key(o) for o in resolved(config.get('/OFF', []))}
        for obj in resolved(resolved(reader.root_object.get('/OCProperties', {})).get('/OCGs', [])):
            self.layer(obj)

    @staticmethod
    def key(obj):
        obj = resolved(obj)
        ref = getattr(obj, 'indirect_reference', None)
        return str(ref.idnum) if ref else str(id(obj))

    def layer(self, obj):
        obj = resolved(obj)
        if obj.get('/Type') != '/OCG':
            self.limited = True
            return None, False  # Membership expressions are not guessed.
        key = self.key(obj)
        if key not in self.layers and len(self.layers) >= 128: raise ValueError('layer budget')
        visible = key not in self.off and (not self.base_off or key in self.on)
        self.layers[key] = {'id': key, 'name': clean_name(str(obj.get('/Name', 'Unnamed layer'))) or 'Unnamed layer', 'visible': visible}
        return key, visible

    def data(self, stream):
        if isinstance(stream, ArrayObject):
            return b'\n'.join(self.data(item) for item in stream)
        raw = resolved(stream).get_data()
        self.byte_count += len(raw)
        if self.byte_count > MAX_BYTES: raise ValueError('stream budget')
        return raw

    def color(self, space, values, resources):
        space = resolved(space)
        aliases = {'/DeviceCMYK': ('CMYK', 4), '/DeviceRGB': ('RGB', 3), '/DeviceGray': ('Gray', 1)}
        if isinstance(space, str):
            if space in aliases:
                kind, count = aliases[space]
                default = resource(resources, '/ColorSpace', '/Default'+kind)
                if default is not None: return {'kind': 'unavailable'}
                if len(values) == count and all(0 <= v <= 1 for v in values):
                    return {'kind': kind, 'values': values}
            else:
                definition = resource(resources, '/ColorSpace', space)
                if definition is not None: return self.color_definition(resolved(definition), values)
        return self.color_definition(space, values)

    def color_definition(self, space, values):
        if isinstance(space, (list, ArrayObject)) and space:
            kind = str(resolved(space[0]))
            if kind in ('/Separation', '/DeviceN'):
                names = [str(resolved(space[1]))[1:]] if kind == '/Separation' else [str(v)[1:] for v in resolved(space[1])]
                if (len(names) == len(values) and len(names) <= 32 and all(0 <= v <= 1 for v in values)
                        and not any(n in ('All', 'None') for n in names)):
                    return {'kind': 'Spot' if kind == '/Separation' else 'DeviceN',
                            'inks': [{'name': clean_name(n), 'tint': v} for n, v in zip(names, values)]}
            if kind == '/Pattern': return {'kind': 'unavailable'}
        return {'kind': 'unavailable'}

    def add(self, commands, state, resources, fill=False, stroke=False, even_odd=False, kind='Path'):
        if not state['visible']: return
        box = bounds(commands)
        if box is None or box[2] < box[0] or box[3] < box[1]: return
        clipped = intersect(box, state['clip'])
        if clipped[2] < clipped[0] or clipped[3] < clipped[1]: return
        if box[2] == box[0] and box[3] == box[1]: return
        if len(self.objects) >= MAX_OBJECTS: raise ValueError('object budget')
        unavailable = state['transparent'] or kind in ('Image', 'Gradient')
        def paint(which, enabled):
            if not enabled: return {'kind': 'None'}
            return {'kind': 'unavailable'} if unavailable else self.color(state[which+'Space'], state[which], resources)
        scale_x = math.hypot(state['matrix'][0], state['matrix'][1])
        scale_y = math.hypot(state['matrix'][2], state['matrix'][3])
        uniform = abs(scale_x-scale_y) < 1e-6 and abs(state['matrix'][0]*state['matrix'][2]+state['matrix'][1]*state['matrix'][3]) < 1e-6
        width = state['width']*scale_x*self.unit if uniform else None
        item = {'id': len(self.objects), 'type': kind, 'bounds': box, 'path': commands,
                'fill': paint('fill', fill), 'stroke': paint('stroke', stroke),
                'lineWidthPt': width if stroke else None, 'evenOdd': even_odd,
                'layer': state['layer'], 'layers': list(state['layers']), 'clip': state['clip'],
                'geometryWarning': state['complexClip'] or any(abs(a-b) > 1e-6 for a, b in zip(box, clipped)),
                'colorWarning': unavailable or (fill and self.color(state['fillSpace'], state['fill'], resources)['kind'] == 'unavailable')
                    or (stroke and self.color(state['strokeSpace'], state['stroke'], resources)['kind'] == 'unavailable')}
        self.objects.append(item)

    def rect(self, matrix, rectangle):
        x0, y0, x1, y1 = numbers(rectangle, 4)
        return [['M', *point(matrix, x0, y0)], ['L', *point(matrix, x1, y0)],
                ['L', *point(matrix, x1, y1)], ['L', *point(matrix, x0, y1)], ['Z']]

    def text_font(self, definition):
        """Read metrics once per source font, without exposing font properties.

        Character codes, rather than extracted Unicode, select the PDF's widths.
        Identity-H CID fonts use two-byte CIDs. Other CMaps/vertical fonts need
        their own displacement mapping; do not guess their dimensions.
        """
        if definition is None: return None
        key = self.key(definition)
        if key not in self.fonts:
            if len(self.fonts) >= 128: raise ValueError('font budget')
            self.fonts[key] = None
            try:
                definition = resolved(definition)
                subtype = definition.get('/Subtype')
                if subtype == '/Type0':
                    if definition.get('/Encoding') != '/Identity-H': return None
                    code_size = 2
                elif subtype in ('/Type1', '/MMType1', '/TrueType'):
                    code_size = 1
                else:
                    # Type3 glyphs may paint their own colours and arbitrary
                    # graphics; ordinary text fill/stroke rules do not apply.
                    return None
                font = Font.from_font_resource(definition)
                if code_size == 2:
                    descendant = resolved(resolved(definition['/DescendantFonts'])[0])
                    font.character_widths['default'] = numbers((descendant.get('/DW', 1000),), 1)[0]
                elif '/Widths' in definition:
                    descriptor = resolved(definition.get('/FontDescriptor', {}))
                    font.character_widths['default'] = numbers((descriptor.get('/MissingWidth', 0),), 1)[0]
                descent, ascent = numbers((font.font_descriptor.descent, font.font_descriptor.ascent), 2)
                if ascent <= descent: return None
                self.fonts[key] = (font, code_size, descent, ascent)
            except (ValueError, TypeError, KeyError, AttributeError, NotImplementedError, PdfReadError):
                pass
        return self.fonts[key]

    def text_string(self, value, state, text_matrix):
        """Return a positioned run rectangle and the advanced text matrix.

        This is a metric text box, not a glyph-outline measurement or a recovered
        editor text frame. Spaces advance the cursor but do not create objects.
        """
        font_info = self.text_font(state['font'])
        if font_info is None or text_matrix is None:
            self.limited = True
            return [], None
        font, code_size, descent, ascent = font_info
        raw = value.original_bytes
        if len(raw) % code_size:
            self.limited = True
            return [], None
        count = len(raw)//code_size
        self.text_glyphs += count
        if self.text_glyphs > MAX_TEXT_GLYPHS: raise ValueError('text budget')
        cursor, left, right = 0, None, None
        size, horizontal = state['fontSize'], state['horizontal']
        for index in range(0, len(raw), code_size):
            code = int.from_bytes(raw[index:index+code_size], 'big')
            width = numbers((font.character_widths.get(chr(code), font.character_widths['default']),), 1)[0]/1000*size
            space = chr(code) == font.space_char if code_size == 1 else font.character_map.get(chr(code)) == ' '
            if not space:
                a, b = sorted((cursor, cursor+width*horizontal))
                left = a if left is None else min(left, a)
                right = b if right is None else max(right, b)
            # PDF word spacing applies only to the single-byte code 32.
            cursor += (width+state['charSpace']+(state['wordSpace'] if code_size == 1 and code == 32 else 0))*horizontal
        commands = []
        if left is not None and size != 0 and horizontal != 0:
            bottom, top = sorted((descent/1000*size+state['rise'], ascent/1000*size+state['rise']))
            matrix = compose(self.page_matrix, compose(state['matrix'], text_matrix))
            commands = self.rect(matrix, (left, bottom, right, top))
            self.command_count += len(commands)
            if self.command_count > MAX_COMMANDS: raise ValueError('path budget')
        advanced = compose(text_matrix, (1, 0, 0, 1, cursor, 0))
        numbers(advanced, 6)
        return commands, advanced

    def scan(self, stream, resources, inherited, depth=0, active=None):
        if stream is None: return
        self.nodes += 1
        if depth > 16 or self.nodes > 128: raise ValueError('form budget')
        obj = resolved(stream)
        active = set() if active is None else active
        key = self.key(obj)
        if key in active: raise ValueError('recursive form')
        raw = DecodedStreamObject(); raw.set_data(self.data(obj))
        content = ContentStream(raw, self.reader)
        state = inherited.copy()
        stack, marked, path = [], [], []
        current = start = None
        clipping = None
        text_matrix = line_matrix = IDENTITY
        text_active = False
        text_commands, text_clip = [], []
        text_state = None
        resources = resolved(resources)
        def flush_text():
            nonlocal text_commands, text_state
            if text_commands:
                mode = text_state['textMode']
                self.add(text_commands, text_state, resources, fill=mode in (0, 2, 4, 6),
                         stroke=mode in (1, 2, 5, 6), kind='Text')
            text_commands, text_state = [], None
        def move_text(x, y):
            nonlocal text_matrix, line_matrix
            # A BT/ET pair can contain captions scattered across an entire
            # page. Do not turn large placement jumps into a box over artwork
            # between those captions. Nearby paragraph lines stay together.
            if max(abs(x), abs(y)) > 3*abs(state['fontSize']): flush_text()
            line_matrix = compose(line_matrix, (1, 0, 0, 1, x, y))
            text_matrix = line_matrix
        text_ops = {b'BT', b'ET', b'Tf', b'Tm', b'Td', b'TD', b'T*', b'Tc', b'Tw', b'Tz', b'TL', b'Ts', b'Tj', b'TJ', b"'", b'"'}
        def close_path():
            nonlocal current
            if start is not None:
                path.append(['Z']); current = start
        def finish_clip():
            nonlocal clipping
            if clipping is not None and path:
                box = bounds(path)
                if box is not None: state['clip'] = intersect(state['clip'], box)
                # Only axis-aligned rectangular clips can be understood exactly here.
                simple = len(path) == 5 and [p[0] for p in path] == ['M', 'L', 'L', 'L', 'Z']
                if simple:
                    pts = [p[1:] for p in path[:4]]
                    simple = all(abs(a[0]-b[0]) < 1e-6 or abs(a[1]-b[1]) < 1e-6 for a, b in zip(pts, pts[1:]+pts[:1]))
                state['complexClip'] |= not simple
            clipping = None
        for args, op in content.operations:
            self.operations += 1
            if self.operations > 200000: raise ValueError('operation budget')
            # Split text objects when inks, compositing, clipping, membership or
            # graphics transforms change, so a selected box has one real recipe.
            if op not in text_ops: flush_text()
            if op == b'BT':
                flush_text()
                if text_active: self.limited = True
                text_matrix = line_matrix = IDENTITY
                text_active = True; text_clip = []
            elif op == b'ET':
                flush_text()
                if text_clip:
                    state['clip'] = intersect(state['clip'], bounds(text_clip))
                    state['complexClip'] = True
                text_active = False; text_clip = []
            elif op == b'Tf':
                state['font'] = resource(resources, '/Font', args[0])
                state['fontSize'] = numbers(args[1:], 1)[0]
            elif op == b'Tm':
                flush_text()
                text_matrix = line_matrix = tuple(numbers(args, 6))
            elif op in (b'Td', b'TD'):
                x, y = numbers(args, 2)
                if op == b'TD': state['leading'] = -y
                move_text(x, y)
            elif op == b'T*': move_text(0, -state['leading'])
            elif op in (b'Tc', b'Tw', b'Tz', b'TL', b'Ts'):
                name = {b'Tc':'charSpace', b'Tw':'wordSpace', b'Tz':'horizontal', b'TL':'leading', b'Ts':'rise'}[op]
                state[name] = numbers(args, 1)[0]/(100 if op == b'Tz' else 1)
            elif op == b'Tr':
                mode = numbers(args, 1)[0]
                if mode not in range(8): raise ValueError('text rendering mode')
                state['textMode'] = int(mode)
            elif op in (b'Tj', b'TJ', b"'", b'"'):
                self.text_present = True
                if not text_active:
                    self.limited = True
                    continue
                if op == b'"':
                    state['wordSpace'], state['charSpace'] = numbers(args[:2], 2)
                if op in (b"'", b'"'): move_text(0, -state['leading'])
                values = args[0] if op == b'TJ' else [args[-1]]
                for value in values:
                    if isinstance(value, (int, float)):
                        adjust = -numbers((value,), 1)[0]/1000*state['fontSize']*state['horizontal']
                        if text_matrix is not None: text_matrix = compose(text_matrix, (1, 0, 0, 1, adjust, 0))
                        continue
                    commands, text_matrix = self.text_string(value, state, text_matrix)
                    mode = state['textMode']
                    if mode >= 4: text_clip.extend(commands)
                    if mode in (3, 7): continue  # Invisible/search and clip-only text.
                    if commands:
                        if text_state is None: text_state = state.copy()
                        text_commands.extend(commands)
            elif op == b'q':
                if len(stack) >= 128: raise ValueError('graphics stack budget')
                stack.append(state.copy())
            elif op == b'Q':
                if not stack: raise ValueError('unbalanced graphics state')
                state = stack.pop()
            elif op == b'cm': state['matrix'] = compose(state['matrix'], numbers(args, 6))
            elif op in (b'g', b'rg', b'k', b'G', b'RG', b'K'):
                which = 'stroke' if op in (b'G', b'RG', b'K') else 'fill'
                kind, count = {b'g': ('/DeviceGray', 1), b'rg': ('/DeviceRGB', 3), b'k': ('/DeviceCMYK', 4)}[op.lower()]
                state[which+'Space'], state[which] = kind, numbers(args, count)
            elif op in (b'cs', b'CS'):
                which = 'fill' if op == b'cs' else 'stroke'
                state[which+'Space'], state[which] = args[0], []
            elif op in (b'sc', b'SC', b'scn', b'SCN'):
                which = 'fill' if op in (b'sc', b'scn') else 'stroke'
                # A pattern name is deliberately not a numeric colour recipe.
                state[which] = [] if any(isinstance(v, str) for v in args) else numbers(args)
            elif op == b'w': state['width'] = abs(numbers(args, 1)[0])
            elif op == b'gs':
                ext = resolved(resource(resources, '/ExtGState', args[0]))
                if not isinstance(ext, dict): raise ValueError('unknown graphics state')
                if '/ca' in ext: state['fillAlpha'] = float(ext['/ca'])
                if '/CA' in ext: state['strokeAlpha'] = float(ext['/CA'])
                if '/SMask' in ext: state['mask'] = resolved(ext['/SMask']) != '/None'
                if '/BM' in ext: state['blend'] = resolved(ext['/BM']) not in ('/Normal', '/Compatible')
                if '/Font' in ext:
                    font = resolved(ext['/Font'])
                    state['font'], state['fontSize'] = font[0], numbers(font[1:], 1)[0]
                state['transparent'] = state['groupTransparency'] or state['fillAlpha'] != 1 or state['strokeAlpha'] != 1 or state['mask'] or state['blend']
            elif op in (b'm', b'l', b'c', b'v', b'y', b're'):
                self.command_count += 5 if op == b're' else 1
                if self.command_count > MAX_COMMANDS: raise ValueError('path budget')
                matrix = compose(self.page_matrix, state['matrix'])
                if op == b're':
                    x, y, w, h = numbers(args, 4)
                    rectangle = self.rect(matrix, [x, y, x+w, y+h]); path.extend(rectangle)
                    current = start = rectangle[0][1:]
                elif op in (b'm', b'l'):
                    current = point(matrix, *numbers(args, 2))
                    path.append(['M' if op == b'm' else 'L', *current])
                    if op == b'm': start = current
                else:
                    if current is None: raise ValueError('curve without start')
                    vals = numbers(args, 6 if op == b'c' else 4)
                    pts = [point(matrix, *vals[i:i+2]) for i in range(0, len(vals), 2)]
                    if op == b'v': pts.insert(0, current)
                    elif op == b'y': pts.append(pts[-1])
                    path.append(['C', *(v for p in pts for v in p)]); current = pts[-1]
            elif op == b'h': close_path()
            elif op in (b'W', b'W*'): clipping = op
            elif op in (b'S', b's', b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*', b'n'):
                if op in (b's', b'b', b'b*'): close_path()
                if op != b'n' and path:
                    fill = op not in (b'S', b's')
                    stroke = op in (b'S', b's', b'B', b'B*', b'b', b'b*')
                    if stroke and not fill:
                        # Disjoint stroke subpaths often represent separate labels.
                        parts = []
                        for command in path:
                            if command[0] == 'M': parts.append([])
                            if parts: parts[-1].append(command)
                        for part in parts: self.add(part, state, resources, stroke=True)
                    else: self.add(path, state, resources, fill, stroke, op.endswith(b'*'))
                finish_clip(); path = []; current = start = None
            elif op in (b'BMC', b'BDC'):
                marked.append((state['layer'], state['visible'], state['layers']))
                if len(marked) > 64: raise ValueError('marked content budget')
                if op == b'BDC' and args[0] == '/OC':
                    prop = resource(resources, '/Properties', args[1]) if isinstance(args[1], str) else args[1]
                    if prop is None: raise ValueError('unknown layer')
                    layer, visible = self.layer(prop)
                    state['layer'] = layer
                    state['layers'] = (*state['layers'], layer) if layer is not None else state['layers']
                    # Keep objects in initially hidden OCGs so showing the layer
                    # can make them selectable. Unsupported membership stays skipped.
                    state['visible'] &= layer is not None
            elif op == b'EMC':
                if not marked: raise ValueError('unbalanced marked content')
                state['layer'], state['visible'], state['layers'] = marked.pop()
            elif op == b'Do':
                child = resolved(resource(resources, '/XObject', args[0]))
                if not isinstance(child, dict): raise ValueError('unknown object')
                child_state = state.copy()
                if '/OC' in child:
                    layer, visible = self.layer(child['/OC'])
                    child_state['layer'] = layer
                    child_state['layers'] = (*state['layers'], layer) if layer is not None else state['layers']
                    child_state['visible'] &= layer is not None
                if child.get('/Subtype') == '/Form':
                    child_state['matrix'] = compose(state['matrix'], numbers(resolved(child.get('/Matrix', IDENTITY)), 6))
                    box = bounds(self.rect(compose(self.page_matrix, child_state['matrix']), child['/BBox']))
                    child_state['clip'] = intersect(state['clip'], box)
                    group = resolved(child.get('/Group', {}))
                    if group.get('/S') == '/Transparency':
                        # A transparency group can contain ordinary opaque shapes.
                        # Preserve actual inherited compositing effects, without
                        # treating the mere presence of a group as transparency.
                        child_state['groupTransparency'] = state['transparent']
                    self.scan(child, child.get('/Resources', resources), child_state, depth+1, active | {key})
                elif child.get('/Subtype') == '/Image':
                    self.add(self.rect(compose(self.page_matrix, state['matrix']), [0, 0, 1, 1]), child_state, resources, fill=True, kind='Image')
                else: self.limited = True
            elif op == b'INLINE IMAGE':
                self.add(self.rect(compose(self.page_matrix, state['matrix']), [0, 0, 1, 1]), state, resources, fill=True, kind='Image')
            elif op == b'sh':
                # A shading is painted over the clip; use its declared BBox if present.
                shading = resolved(resource(resources, '/Shading', args[0]))
                matrix = compose(self.page_matrix, state['matrix'])
                commands = self.rect(matrix, shading['/BBox']) if isinstance(shading, dict) and '/BBox' in shading else [
                    ['M', *state['clip'][:2]], ['L', state['clip'][2], state['clip'][1]],
                    ['L', *state['clip'][2:]], ['L', state['clip'][0], state['clip'][3]], ['Z']]
                self.add(commands, state, resources, fill=True, kind='Gradient')
        flush_text()
        if text_active: self.limited = True
        if stack or marked: self.limited = True

    def inspect(self):
        page = self.reader.pages[self.page_number-1]
        x0, y0, x1, y1 = numbers(page.mediabox, 4)
        self.unit = float(page.get('/UserUnit', 1))
        if not math.isfinite(self.unit) or self.unit <= 0 or x1 <= x0 or y1 <= y0: raise ValueError('page dimensions')
        rotation = int(page.get('/Rotate', 0)) % 360
        matrices = {0: (1, 0, 0, -1, -x0, y1), 90: (0, 1, 1, 0, -y0, -x0),
                    180: (-1, 0, 0, 1, x1, -y0), 270: (0, -1, -1, 0, y1, x1)}
        if rotation not in matrices: raise ValueError('rotation')
        self.page_matrix = compose((self.unit, 0, 0, self.unit, 0, 0), matrices[rotation])
        width, height = (x1-x0)*self.unit, (y1-y0)*self.unit
        if rotation in (90, 270): width, height = height, width
        numbers((width, height), 2)
        transparent = False
        state = {'matrix': IDENTITY, 'fillSpace': '/DeviceGray', 'strokeSpace': '/DeviceGray',
                 'fill': [0], 'stroke': [0], 'width': 1, 'fillAlpha': 1, 'strokeAlpha': 1,
                 'mask': False, 'blend': False, 'transparent': transparent, 'groupTransparency': transparent,
                 'font': None, 'fontSize': 0, 'charSpace': 0, 'wordSpace': 0, 'horizontal': 1,
                 'leading': 0, 'rise': 0, 'textMode': 0,
                 'clip': [0, 0, width, height], 'complexClip': False, 'layer': None, 'layers': (), 'visible': True}
        try:
            self.scan(page.get('/Contents'), page.get('/Resources', {}), state)
        except Exception:
            self.limited = True
        result = {'page': self.page_number, 'widthPt': width, 'heightPt': height, 'objects': self.objects,
                  'layers': list(self.layers.values()), 'limited': self.limited, 'textPresent': self.text_present}
        # The worker has bounded stdout capture. Return an explicitly partial
        # packet rather than exceeding it or returning no data on dense artwork.
        while len(json.dumps(result, ensure_ascii=True, separators=(',', ':'))) > MAX_PACKET_BYTES:
            result['objects'] = result['objects'][:len(result['objects'])//2]
            result['limited'] = True
            if not result['objects']: break
        return result


def inspect_pdf(path, page_number):
    with apply_configuration(maximum_declared_stream_length=MAX_BYTES,
            array_based_stream_maximum_output_length=MAX_BYTES, zlib_maximum_output_length=MAX_BYTES,
            lzw_maximum_output_length=MAX_BYTES, run_length_maximum_output_length=MAX_BYTES,
            stream_decoding_work_maximum_length=MAX_BYTES*2,
            page_tree_maximum_entries=100, page_tree_maximum_depth=32):
        with open(path, 'rb') as source:
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted or not 1 <= page_number <= len(reader.pages): raise ValueError('page unavailable')
            return Inspector(reader, page_number).inspect()


def write_layer_view(path, page_number, hidden_layers, output):
    """Write a temporary single-page view with explicit OCG visibility.

    Clone original drawing/text/image content. Never paint over hidden layers or
    rebuild artwork from the inspector's deliberately incomplete path metadata.
    """
    with apply_configuration(maximum_declared_stream_length=MAX_BYTES,
            array_based_stream_maximum_output_length=MAX_BYTES, zlib_maximum_output_length=MAX_BYTES,
            lzw_maximum_output_length=MAX_BYTES, run_length_maximum_output_length=MAX_BYTES,
            stream_decoding_work_maximum_length=MAX_BYTES*2,
            page_tree_maximum_entries=100, page_tree_maximum_depth=32):
        with open(path, 'rb') as source:
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted or not 1 <= page_number <= len(reader.pages): raise ValueError('page unavailable')
            props = resolved(reader.root_object.get('/OCProperties', {}))
            groups = list(resolved(props.get('/OCGs', [])))
            if len(groups) > 128: raise ValueError('layer budget')
            ids = [Inspector.key(obj) for obj in groups]
            if not set(hidden_layers) <= set(ids): raise ValueError('unknown layer')
            writer = PdfWriter()
            writer.add_page(reader.pages[page_number-1])
            if groups:
                copied = props.clone(writer)
                on, off = ArrayObject(), ArrayObject()
                for original, clone in zip(groups, copied['/OCGs']):
                    # IDs sent by the browser identify the original PDF, before
                    # PdfWriter renumbers its disposable copy's objects.
                    (off if Inspector.key(original) in hidden_layers else on).append(clone)
                    resolved(clone).pop('/Usage', None)
                copied[NameObject('/D')] = DictionaryObject({
                    NameObject('/BaseState'): NameObject('/OFF'), NameObject('/ON'): on,
                    NameObject('/OFF'): off, NameObject('/Intent'): NameObject('/View')})
                writer.root_object[NameObject('/OCProperties')] = copied
            writer.write(output)
            return sorted(hidden_layers)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        bound_memory()
        if len(sys.argv) > 3 and sys.argv[3] == '--layer-view':
            hidden = json.loads(sys.argv[4])
            result = {'hiddenLayers': write_layer_view(sys.argv[1], int(sys.argv[2]), hidden, sys.argv[5])}
        else:
            result = inspect_pdf(sys.argv[1], int(sys.argv[2]))
        print(json.dumps(result, ensure_ascii=True, separators=(',', ':'), allow_nan=False))
    except Exception:
        sys.exit(1)
