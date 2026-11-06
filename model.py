import math
import random
import re
from itertools import accumulate, groupby
from pathlib import Path

from PIL import Image, ImageDraw

from utils import SENTENCE_BREAK, unit_for, unit_pattern

ACTION = re.compile(
    r'\b(?:trains?|riders?|cars?|coaster|ride|tracks?|course|layout|bobsleds?|vehicles?|it|this)\b.*'
    r'\b(?:climb|ascend|drop|plung|enter|pass|travel|turn|navigate|complete|dip|traverse|follow|'
    r'proceed|leave|dispatch|reach|exit|ris|rose|descend|pull|dive|encounter|'
    r'go|went|continue|run|roll|curv|bend|bank|head|make|made|lead|led|launch|sweep|weav|take)\w*\b|'
    r'\b(?:followed by|next|finally)\b', re.I)
TOKEN = re.compile(r'\b(?:' + '|'.join(
    f'(?P<{kind}>{pattern})' for kind, pattern in (
        # Match these whole so a diving loop does not become a plain loop
        ('unsupported', r'non[- ‑]inverting loops?|half(?: a)?[- ](?:loops?|corkscrews?)|'
         r'div(?:e|ing) loops?|cobra rolls?|sea serpent rolls?|batwings?|'
         r'immelmanns?(?: loops?)?|sidewinders?|zero[- ]g stalls?|double[- ]ups?|'
         r'double[- ]downs?|wave turns?'),
        ('lift', r'(?:chain|cable)\s+lift(?:[- ]hills?)?|lift[- ]hills?'),
        ('roll', r'(?:zero[- ]g|heartline|barrel|inline)\s+rolls?'),
        ('corkscrew', r'corkscrews?'),
        ('helix', r'helix|helices'),
        ('loop', r'(?:vertical\s+)?loops?'),
        ('hill', r'(?:(?:airtime|air.time|camelback|bunny|parabolic|speed)\s+)?hills?'),
        ('turn', r'turn(?:s|ed|ing|around)?|curv(?:e[sd]?|ing)|bend(?:s|ing)?'),
        ('climb', r'(?:climb|ascend)(?:s|ed|ing)?|ascent|ris(?:e[sd]?|ing)|rose'),
        ('drop', r'drop(?:s|ped|ping)?|dip(?:s|ped|ping)?|plung(?:e[sd]?|ing)|descend(?:s|ed|ing)?'),
        ('brakes', r'brake run|block brakes|brakes'),
        ('straight', r'straight(?: section)?|launch(?:es|ed)?'),
    )) + r')\b', re.I)
MEASURE = re.compile(r'(?<![\w.])(\d+(?:,\d{3})*(?:\.\d+)?)\s*[-–]?\s*'
                     + r'(' + unit_pattern(dimension='length')
                     + r')(?![\w/])(?![-\s]+per[-\s]+hour\b)', re.I)
ANGLE = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)\s*[-–]?\s*(?:degrees?|°)', re.I)
COUNTS = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
          'seven': 7, 'eight': 8, 'nine': 9, 'double': 2, 'triple': 3,
          **{str(n): n for n in range(2, 10)}}
COUNT = re.compile(r'\b(' + '|'.join(COUNTS) + r')[- ]+'
                   r'(?:(?:small|large|consecutive|successive|little|vertical|banked|'
                   r'downward|upward|left[- ]hand|right[- ]hand|\d+(?:\.\d+)?[- ]degrees?)\s+)*$', re.I)
CONNECTOR = re.compile(r'\b(?:followed by|then|and|before|after)\b', re.I)
DIRECTION = re.compile(r'\b(left|right)(?:[- ]hand(?:ed)?)?\b', re.I)
REFERENCE = re.compile(r'\b(?:after|upon|while|exiting|cresting|completing|'
                       r'top of|bottom of|partway through)\s+(?:(?:the|this|that|its|a|an|'
                       r'first|second|third|final|another)\s+)*$', re.I)


def nearest(pattern, text, position):
    return min(pattern.finditer(text), key=lambda m: abs((m.start() + m.end()) / 2 - position),
               default=None)


def element_params(kind, context, masked, at, rng):
    direction = nearest(DIRECTION, context, at)
    distance = nearest(MEASURE, masked, at)
    angle = nearest(ANGLE, context, at)
    params = {}
    if distance:
        value = float(distance.group(1).replace(',', ''))
        value *= unit_for(distance.group(2)).si_factor
        if kind in ('straight', 'brakes'):
            params['length'] = value
        elif kind in ('lift', 'climb', 'drop', 'hill', 'loop', 'corkscrew'):
            lo, hi = sorted((distance.start(), at))
            bridge = context[lo:hi]
            if (not re.search(r'\b(?:ride|track|course|tunnel|long|length|straight)\b', bridge, re.I)
                    or re.search(r'\b(?:height|tall|high)\b', bridge, re.I)):
                params['height'] = value
    if kind in ('turn', 'helix', 'corkscrew', 'roll', 'drop'):
        if direction:
            params['direction'] = 1 if direction[1].lower() == 'right' else -1
        else:
            params['direction'] = rng.choice((-1, 1))
    if angle:
        params['angle'] = float(angle.group(1))
    if kind == 'turn':
        params['bank'] = params.pop('angle', 110) if 'overbank' in context.lower() else 45
        if not angle and re.search(r'\b(?:horseshoe|hairpin|U[- ]turn|turnaround|turn around)\b',
                                   context, re.I):
            params['angle'] = 180
    if kind == 'drop' and direction and params.get('angle', 0) > 90:
        params['sweep'] = params.pop('angle')
    return params


def interpret(text, rng):
    elements = []
    for sentence in SENTENCE_BREAK.split(text):
        if not ACTION.search(sentence) or 'catch car' in sentence.lower():
            continue
        # Parenthetical explanations can repeat elements already mentioned
        masked = re.sub(r'\([^)]*\)', lambda m: ' ' * len(m.group()), sentence)
        matches = list(TOKEN.finditer(masked))
        for i, match in enumerate(matches):
            kind = match.lastgroup
            prefix = masked[:match.start()]
            if (kind == 'unsupported' or REFERENCE.search(prefix)
                    or re.search(r'\b(?:known as|called)\s+(?:an?|the)\s*$', prefix, re.I)):
                continue
            if kind == 'lift' and (
                    re.search(r'\b(?:under|underneath|over|through)\s+(?:the\s+)?$', prefix, re.I)
                    or re.match(r'\s+structure', masked[match.end():], re.I)):
                continue
            if kind == 'climb' and i + 1 < len(matches):
                # Climbing a hill describes one element, not two
                following = matches[i + 1]
                between = masked[match.end():following.start()]
                if following.lastgroup in ('lift', 'hill') and not re.search(
                        r'\b(?:then|followed by|and|before|after)\b', between):
                    continue
            if kind == 'drop' and i and matches[i-1].lastgroup in ('hill', 'drop'):
                if re.match(r'[, ]*(?:which|that)\b', masked[matches[i-1].end():match.start()], re.I):
                    continue
            left = matches[i - 1].end() if i else 0
            right = matches[i + 1].start() if i + 1 < len(matches) else len(sentence)
            # Keep measurements and directions with their own part of the sentence
            previous = list(CONNECTOR.finditer(masked[left:match.start()]))
            if previous:
                left += previous[-1].end()
            following = CONNECTOR.search(masked[match.end():right])
            if following:
                right = match.end() + following.start()
            context = sentence[left:right]
            at = match.start() - left
            params = element_params(kind, context, masked[left:right], at, rng)
            count = COUNT.search(prefix)
            repeats = COUNTS[count.group(1).lower()] if count else 1
            elements.extend({'kind': kind, 'params': params.copy()} for _ in range(repeats))
    return elements


def smooth(t):
    return t*t*t * (10 + t*(-15 + 6*t))


def bend(t):
    ramp = 0.2
    if t > 1-ramp:
        return 1-bend(1-t)
    if t < ramp:
        u = t/ramp
        return ramp * (u**3 - u**4/2) / (1-ramp)
    return (t-ramp/2) / (1-ramp)


def unit(v):
    length = math.sqrt(sum(x*x for x in v))
    return tuple(x/length for x in v) if length > 1e-12 else (0, 0, 1)


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def rotate(v, axis, angle):
    c, s = math.cos(angle), math.sin(angle)
    perpendicular = cross(axis, v)
    along = dot(axis, v) * (1-c)
    return tuple(v[i]*c + perpendicular[i]*s + axis[i]*along for i in range(3))


def segment(element, origin, heading):
    # Level entries and exits let movements join smoothly
    # Work in local coordinates, then place it at the previous piece's end and heading
    kind, params = element['kind'], element['params']
    height, direction = params.get('height'), params.get('direction', 1)
    rise = height if height is not None else 35
    sweep = min(params.get('angle', 90), 360) if kind == 'turn' else 360
    radius = max(18, min(70, (height or 40)*0.8))
    bank = math.radians(params.get('bank', 45))
    length, slope = params.get('length', 30 if kind == 'brakes' else 50), 0
    if kind in ('lift', 'climb', 'drop'):
        if kind == 'drop':
            rise = -rise
        slope = math.radians(min(max(params.get('angle', 55 if kind == 'drop' else 35), 5), 120))
        length = max(20, abs(rise)*2)
        sweep = params.get('sweep', 0)
    elif kind == 'hill':
        rise = max(4, rise) if height is not None else 10
        length = max(35, rise*4)
    elif kind in ('turn', 'helix'):
        length = radius * math.radians(sweep) / 0.8
    elif kind in ('loop', 'corkscrew'):
        radius = max(6, (height or 30)/2)
        length = math.tau*radius/0.8 if kind == 'loop' else radius*6
    elif kind == 'roll':
        length = 65

    steps = max(160 if kind in ('loop', 'corkscrew', 'roll', 'helix') else 40,
                min(600, math.ceil(length/1.5)))
    sweep_radians = math.radians(sweep)
    points, x, y, z, previous = [], 0, 0, 0, 0
    c, s = math.cos(heading), math.sin(heading)
    for i in range(steps+1):
        # Cluster samples near joins
        t = (1-math.cos(math.pi*i/steps))/2
        crest = 64*t**3*(1-t)**3
        roll = 0
        # Integrating direction instead of joining arcs avoids curvature jumps
        if kind in ('lift', 'climb', 'drop', 'turn', 'helix', 'loop'):
            if i:
                midpoint = (t+previous)/2
                pitch = math.copysign(slope, rise)*64*midpoint**3*(1-midpoint)**3 if rise else 0
                a = sweep_radians*bend(midpoint)
                distance = length*(t-previous)
                if kind == 'loop':
                    y += math.sin(a)*distance
                    z += math.cos(a)*distance
                else:
                    cos_pitch = math.cos(pitch)
                    y += math.sin(pitch)*distance
                    x += direction*math.sin(a)*cos_pitch*distance
                    z += math.cos(a)*cos_pitch*distance
            if kind in ('turn', 'helix'):
                change = rise if height is not None else (-8 if kind == 'helix' else 0)
                y = change*smooth(t)
            if kind in ('turn', 'helix') or (kind != 'loop' and sweep):
                roll = direction*bank*crest
        else:
            x, y, z = 0, 0, length*t
            if kind == 'hill':
                y = rise*crest
            elif kind == 'corkscrew':
                a = math.tau*smooth(t)
                x, y = direction*radius*(math.cos(a)-1)*crest, radius*math.sin(a)*crest
                roll = direction*a
            elif kind == 'roll':
                y, roll = 2*crest, direction*math.tau*smooth(t)
        points.append([origin[0]+c*x+s*z, origin[1]+y, origin[2]-s*x+c*z, roll])
        previous = t
    if kind in ('lift', 'climb', 'drop') and rise:
        # Scale all axes to match the height without changing the pitch
        factor = rise/(points[-1][1]-origin[1])
        for p in points:
            p[:3] = [base+(value-base)*factor for base, value in zip(origin, p)]
    elif kind == 'loop':
        peak = max(p[1]-origin[1] for p in points)
        for p in points:
            p[1] = origin[1] + (p[1]-origin[1]) * (height or 30)/peak
    if kind in ('turn', 'helix') or (kind == 'drop' and sweep):
        heading += direction*sweep_radians
    return points, heading


def build_sections(texts, seed):
    # The track continues across chapters; note where each one starts and ends
    rng = random.Random(seed)
    points, elements, sections = [], [], []
    origin, heading = [0, 0, 0], 0
    for text in texts:
        first = len(elements)
        for element in interpret(text, rng):
            samples, heading = segment(element, origin, heading)
            start = max(0, len(points)-1)
            points.extend(samples if not points else samples[1:])
            origin = points[-1][:3]
            elements.append({**element, 'start': start, 'end': len(points)-1})
        sections.append((first, len(elements)))
    if not elements:
        raise ValueError('No supported track movements found in the novel')
    return {'points': points, 'elements': elements, 'sections': sections}


def rail_frames(model):
    points = model['points']
    tangents = [unit(tuple(points[min(i+1, len(points)-1)][j]-points[max(0, i-1)][j]
                           for j in range(3))) for i in range(len(points))]
    rights = [(1, 0, 0)] * len(points)
    up = (0, 1, 0)
    for element in model['elements']:
        start, end = element['start'], element['end']
        ups, previous = [], tangents[start]
        # Carry the up direction through inversions without flips
        for i in range(start, end+1):
            tangent = tangents[i]
            axis = cross(previous, tangent)
            sine = math.sqrt(dot(axis, axis))
            if sine > 1e-10:
                axis = tuple(x/sine for x in axis)
                up = rotate(up, axis, math.atan2(sine, dot(previous, tangent)))
            ups.append(up)
            previous = tangent
        # Ease back to upright before applying bank or roll
        vertical = unit(tuple((0, 1, 0)[j]-tangents[end][j]*tangents[end][1] for j in range(3)))
        correction = math.atan2(dot(tangents[end], cross(up, vertical)), dot(up, vertical))
        for i, carried in enumerate(ups, start):
            recovered = rotate(carried, tangents[i], correction*smooth((i-start)/(end-start)))
            banked = rotate(recovered, tangents[i], -points[i][3])
            rights[i] = unit(cross(banked, tangents[i]))
        up = vertical
    return rights


def fit_view(rails, width, height):
    left = min(x for rail in rails for x, _ in rail)
    right = max(x for rail in rails for x, _ in rail)
    low = min(y for rail in rails for _, y in rail)
    high = max(y for rail in rails for _, y in rail)
    return ((left+right)/2, (low+high)/2,
            min(width/max(right-left, 1), height/max(high-low, 1)))


def chapter_view(rails, width, height):
    count = len(rails[0])
    centres = [(a[0]+b[0])/2 for a, b in zip(*rails)]
    left, right = min(centres), max(centres)
    span = max(right-left, 1)
    fallback = None
    for zoom in (3.6, 4.4, 5.6, 7.2, 9.6, 12.0):
        scale = width*zoom/span
        half = width/(2*scale)
        candidates = []
        for fraction in (0.5, 0.35, 0.65, 0.2, 0.8):
            cx = left+span*fraction
            runs = groupby(range(count), key=lambda i: cx-half <= centres[i] <= cx+half)
            for inside, run in runs:
                if not inside:
                    continue
                indices = list(run)
                first, last = max(0, indices[0]-1), min(count-1, indices[-1]+1)
                piece = [rail[first:last+1] for rail in rails]
                ys = [y for rail in piece for _, y in rail]
                low, high = min(ys), max(ys)
                if (high-low)*scale <= height*0.9:
                    crosses = (centres[first] < cx-half and centres[last] > cx+half
                               or centres[last] < cx-half and centres[first] > cx+half)
                    view = piece, cx, (low+high)/2, scale, first, last
                    candidates.append(((crosses, last-first), view))
        if candidates:
            # Prefer a continuous stretch that crosses the full width of the strip
            (crosses, _), view = max(candidates, key=lambda candidate: candidate[0])
            if crosses:
                return view
            if fallback is None:
                fallback = view
    return fallback or (rails, *fit_view(rails, width, height*0.9), 0, count-1)


def render_model(model, output, *, chapter=False):
    positions = [p[:3] for p in model['points']]
    rights = rail_frames(model)
    extent = max(max(p[j] for p in positions)-min(p[j] for p in positions)
                 for j in range(3))
    gauge = max(1.4, extent/330)
    rails = [[tuple(p[j]+side*gauge*r[j]/2 for j in range(3))
              for p, r in zip(positions, rights)] for side in (-1, 1)]
    width, height, aa = 2400, (851 if chapter else 3405), 2
    background, ink = ((0, 0, 0, 0), '#000000') if chapter else ('#FFD300', '#FFFFFF')

    # The cover looks along the main axis; chapter strips look across
    elevation = math.radians(25)
    sample = positions[::max(1, len(positions)//1000)]
    mx, mz = (sum(p[j] for p in sample)/len(sample) for j in (0, 2))
    xx = sum((p[0]-mx)**2 for p in sample)
    zz = sum((p[2]-mz)**2 for p in sample)
    xz = sum((p[0]-mx)*(p[2]-mz) for p in sample)
    azimuth = math.atan2(2*xz, xx-zz)/2
    if chapter:
        azimuth += math.pi/2
    # Put the camera at the starting end of that axis
    if ((positions[-1][0]-positions[0][0])*math.cos(azimuth)
            + (positions[-1][2]-positions[0][2])*math.sin(azimuth)) > 0:
        azimuth += math.pi
    eye = (math.cos(azimuth)*math.cos(elevation), math.sin(elevation),
           math.sin(azimuth)*math.cos(elevation))
    right = unit(cross((0, 1, 0), eye))
    up = cross(eye, right)
    projected = [[(dot(p, right), -dot(p, up)) for p in rail] for rail in rails]
    # Zoom into the rails, letting the outer sections bleed beyond the page
    if chapter:
        projected, cx, cy, scale, first, last = chapter_view(projected, width, height)
        positions = positions[first:last+1]
    else:
        cx, cy, scale = fit_view(projected, width*1.4, height*1.4)
    scale *= aa * (1.1 if chapter else 1)
    projected = [[((x-cx)*scale+width*aa/2, (y-cy)*scale+height*aa/2)
                  for x, y in rail] for rail in projected]

    image = Image.new('RGBA' if chapter else 'RGB', (width*aa, height*aa), background)
    draw = ImageDraw.Draw(image)
    distances = list(accumulate((math.dist(a, b) for a, b in zip(positions, positions[1:])), initial=0))
    spacing, next_tie = max(2.4, distances[-1]/1800), 0
    for i, travelled in enumerate(distances):
        if travelled >= next_tie:
            draw.line([rail[i] for rail in projected], fill=ink, width=2*aa)
            next_tie = travelled+spacing
    for rail in projected:
        draw.line(rail, fill=ink, width=(2 if chapter else 5)*aa, joint='curve')
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    image.save(output, dpi=(450, 450))


def write_illustrations(texts, seed, directory):
    directory = Path(directory)
    model = build_sections(texts, seed)
    cover = directory / 'cover.png'
    render_model(model, cover)
    images = []
    for number, (first, last) in enumerate(model['sections'], 1):
        output = directory / f'chapter-{number}.png'
        if first == last:
            Image.new('RGBA', (2400, 851)).save(output)
        else:
            elements = model['elements'][first:last]
            start, end = elements[0]['start'], elements[-1]['end']
            section = {'points': model['points'][start:end+1],
                       'elements': [{**e, 'start': e['start']-start,
                                     'end': e['end']-start} for e in elements]}
            render_model(section, output, chapter=True)
        images.append(output)
    return cover, images
