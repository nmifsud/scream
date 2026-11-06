import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUOTES = re.compile(r'("[^"\n]*"|“[^”\n]*”)')
SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+(?=[A-Z“"])')


def substitute(text, rules, flags=0):
    for pattern, replacement in rules:
        text = re.sub(pattern, replacement, text, flags=flags)
    return text


def unquoted(text, operation):
    # QUOTES.split keeps quoted spans at odd indices
    return ''.join(operation(part) if i % 2 == 0 else part
                   for i, part in enumerate(QUOTES.split(text)))


def sentences(text):
    start, depth, quote = 0, 0, None
    for i, char in enumerate(text):
        if char in '“"' and quote is None:
            quote = '”' if char == '“' else '"'
        elif char == quote:
            quote = None
        elif not quote:
            depth += (char == '(') - (char == ')' and depth > 0)
        if char in '.!?' and not quote and not depth:
            if i + 1 == len(text) or SENTENCE_BREAK.match(text, i + 1):
                yield ' '.join(text[start:i + 1].split())
                start = i + 1
    if text[start:].strip():
        yield ' '.join(text[start:].split())


@dataclass(frozen=True)
class Unit:
    symbol: str
    system: str
    dimension: str
    si_factor: float
    adjective: str
    aliases: str


UNITS = (
    Unit('m', 'metric', 'length', 1, 'metre', 'm|metre|metres|meter|meters'),
    Unit('km', 'metric', 'length', 1000, 'kilometre', 'km|kilometre|kilometres|kilometer|kilometers'),
    Unit('cm', 'metric', 'length', .01, 'centimetre', 'cm|centimetre|centimetres|centimeter|centimeters'),
    Unit('mm', 'metric', 'length', .001, 'millimetre', 'mm|millimetre|millimetres|millimeter|millimeters'),
    Unit('ft', 'imperial', 'length', .3048, 'foot', "ft|foot|feet|'|’|′"),
    Unit('in', 'imperial', 'length', .0254, 'inch', 'in|inch|inches|"|”|″'),
    Unit('yd', 'imperial', 'length', .9144, 'yard', 'yd|yard|yards'),
    Unit('mi', 'imperial', 'length', 1609.344, 'mile', 'mi|mile|miles'),
    Unit('km/h', 'metric', 'speed', 1/3.6, 'kilometre-per-hour',
         'km/h|kph|kilometre per hour|kilometres per hour|kilometer per hour|kilometers per hour'),
    Unit('mph', 'imperial', 'speed', .44704, 'mile-per-hour', 'mph|mile per hour|miles per hour'),
)
BY_ALIAS = {alias: unit for unit in UNITS for alias in unit.aliases.split('|')}


def unit_for(spelling):
    return BY_ALIAS[' '.join(spelling.lower().split())]


def unit_pattern(system=None, dimension=None):
    aliases = [alias for alias, unit in BY_ALIAS.items()
               if (system is None or unit.system == system)
               and (dimension is None or unit.dimension == dimension)]
    # Try longer spellings first so "m" does not steal the start of "metres"
    return '|'.join(re.escape(alias).replace(r'\ ', r'\s+')
                    for alias in sorted(aliases, key=lambda alias: (-len(alias), alias)))

