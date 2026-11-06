import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import median

from book import chapter_groups, compile_pdf, duration_words, pretty_time, render_book
from model import write_illustrations
from utils import QUOTES, ROOT, sentences, substitute, unit_for, unit_pattern, unquoted

MEASURE_NUMBER = r'\d[\d,]*(?:\.\d+)?'
MEASURE_AMOUNT = MEASURE_NUMBER + r'(?:\s*(?:[–−-]|to\b|and\b)\s*' + MEASURE_NUMBER + r')?'
IMPERIAL_UNITS = unit_pattern(system='imperial')
METRIC_UNITS = unit_pattern(system='metric')
MEASUREMENT_PAIR = re.compile(
    r'(?<![\w.,])(?P<imperial_value>' + MEASURE_AMOUNT + r')(?P<separator>[\s-]*)'
    + r'(?P<imperial_unit>' + IMPERIAL_UNITS + r')(?!\w)'
    + r'(?P<suffix>[ -](?:tall|high|long|deep|wide))?'
    + r'(?P<noun>\s+(?:climb|spike|drop|hill|tower|loop))?\s*'
    + r'\(\s*(?P<metric_value>' + MEASURE_AMOUNT + r')[\s-]*'
    + r'(?P<metric_unit>' + METRIC_UNITS + r')(?!\w)\s*\)', re.I)


def metric_first(text): # As it should be!!
    def reorder(match):
        unit = unit_for(match['imperial_unit'])
        metric_unit = unit_for(match['metric_unit'])
        if unit.dimension != metric_unit.dimension:
            return match[0]
        primary = match['metric_value'] + ' ' + match['metric_unit']
        # Keep forms like "30-metre drop" hyphenated
        if match['suffix'] or ('-' in match['separator'] and unit.dimension == 'length'):
            primary = (match['metric_value'] + '-'
                       + metric_unit.adjective
                       + (match['suffix'].replace(' ', '-') if match['suffix'] else ''))
        secondary = match['imperial_value'] + ' ' + unit.symbol
        return primary + ' (' + secondary + ')' + (match['noun'] or '')

    return MEASUREMENT_PAIR.sub(reorder, text)


INDEPENDENT = r'(?:the (?:train|track|car|ride|coaster)|trains|cars|riders|guests|it|they)\b'
OPENING = re.compile(
    r'^(?:(?:immediately )?(?:after|following|exiting|leaving|coming out of|out of) '
    r'(?:the|this|that) [^,;.!?]+,\s*)(?=' + INDEPENDENT + ')', re.I)


SENTENCE_CUTS = (
    (re.compile(r'\s*\([^()]*\b(?:prior to|used to|formerly|previously|'
                r'ranking|in no particular order|\w+ overall|'
                r'originally|original|homage|named (?:for|after)|before (?:19|20)\d{2})\b[^()]*\)', re.I), ''),
    (re.compile(r',\s*(?:the (?:fastest|tallest|longest|steepest) of any|'
                r'resulting in one of the (?:longest|largest|highest))[^.!?]*', re.I), ''),
    (re.compile(r'\s+(?:on|during) (?:the|a) '
                r'(?:\d+(?:\.\d+)?|one|two|three|four|five)[- ]'
                r'(?:minute|second|hour)(?:[- ]long)? ride\b', re.I), ''),
    (re.compile(r'\bclimax\s+(?=[A-Z])'), ''),
    (re.compile(r'\s+and spends? the rest of the ride\s+', re.I), ', '),
    (re.compile(r',?\s+(?:and )?repeating the (?:course|layout) in reverse\b', re.I), ''),
    (re.compile(r'\b(?:last|sole|only)\s+(?=(?:inversion|loop|drop|lift|launch|turn|roll)\b)', re.I), ''),
    (re.compile(r'^(?:(?:and|next|then|after that)\b[, ]+)+(?=' + INDEPENDENT + ')', re.I), ''),
)


def clean_sentence(text):
    def cut(part):
        if re.search(r'^Initially\b.*\b(?:whereas|now|lasted)\b', part, re.I):
            return ''
        if re.search(r'\b(?:ride length|duration)\b.*\b(?:minutes?|seconds?|hours?)\b', part, re.I):
            return ''
        return substitute(part, SENTENCE_CUTS)

    text = unquoted(text, cut).strip()
    if not re.search(r'\w', text):
        return ''
    return text[:1].upper() + text[1:]


HISTORY = re.compile(
    r'\b(?:originally|formerly|previously|historically|used to|no longer|early days|'
    r'opened|closed|replaced|renamed|rethemed|redesigned|refurbished|relocated|debuted|billed)\b|'
    r'\b(?:18|19|20)\d{2}\b(?!\s+(?:riders?|passengers?|people|m|metres?|meters?|ft|feet)\b)', re.I)
EDITORIAL = re.compile(
    r'\b(?:notorious|popularity|popular|ranking|reports?|accounts?|injuries|'
    r'assures?|encourages?|pessimistic|remarks?|wish|wishes)\b|'
    r'\bis a good roller\s?coaster\b', re.I)
PAST_AUXILIARY = re.compile(r"\b(?:was|were|had|did|would|wasn[’']t|weren[’']t|hadn[’']t|didn[’']t)\b", re.I)
PAST_VERB = (r'(?:\w+ed|went|came|rose|took|made|led|fell|'
             r'left(?=\s+(?:the|a|an|its|their)\b)|ran|began|gave|saw|got|'
             r'swung|swept|dove|bent|stood|sat|sped|sent|built|brought|caught|felt|lay)')
PAST_FINITE = re.compile(r'\b(?:went|came|rose|took|fell|ran|began|gave|got|dove|stood|sat|lay)\b', re.I)
PAST_ACTION = re.compile(
    r'\b(?:trains?|cars?|riders?|passengers?|guests?|vehicles?|bobsleds?|'
    r'coasters?|rides?|tracks?|it|they|which|that|hills?|drops?|turns?|dips?|'
    r'layouts?|courses?|sections?|models?|versions?|cameras?|sides?|railways?|'
    r'actress|actors?|elements?|figures?|scenes?|effects?|grotto|buildings?|tunnels?)\s+'
    r'(?:to (?:the )?(?:left|right)\s+)?'
    r'(?:(?:\w+ly|then|also|now|once|first)\s+)*' + PAST_VERB + r'\b'
    r'(?!\s+(?:by|to look)\b)|'
    r',\s*' + PAST_VERB + r'\b(?!\s+(?:by|turns?|curves?|drops?|hills?|track)\b)', re.I)
PAST_NAMED = re.compile(
    r"\b(?!(?:A|An|The|This|That|These|Those|After|Before|Upon|Once|When|While|"
    r"As|In|At|On|During|For|With|To|It|They|Both|Each)\b)"
    r"(?:[A-Z][\w’'-]*\s+){1,4}" + PAST_VERB + r'\b(?!\s+by\b)')
TERMINAL = re.compile(
    r'\b(?:station|platform|boarding|boards?|before departure|unloading|loading|restrain\w*|seat belts?|'
    r'front gate|floors? drops?|ride (?:ends?|finishes?|concludes?)|'
    r'(?:final|last) brake|comes? to (?:a |an? complete )?stop|'
    r'complete (?:stop|halt)|disembark|dispatch\w*|comes? to (?:a )?halt|'
    r'coming to (?:rest|a stop)|approach\w* the end|finale|ending with)\b', re.I)
RIDE_DURATION = re.compile(
    r'\bduration\b|\b(?:ride|coaster|cycle|experience)\s+'
    r'(?:on\s+[^.!?]+?\s+)?'
    r'(?:from station(?: dispatch)? to brakes\s+)?'
    r'(?:(?:was|is|will)\s+(?:(?:advertised|designed)\s+to\s+)?)?'
    r'(?:last(?:s|ed)?|takes?|took)\b', re.I)
TIME_UNIT = re.compile(r'\b(?:seconds?|minutes?|hours?)\b', re.I)
DEPENDENT = re.compile(
    r'^(?:(?:once|when|while|after|upon)\s+(?:(?:it|the train)\s+)?'
    r'(?:at|there|reaching|cresting|exiting|leaving|completing)|'
    r'(?:at|from|over)\s+the\s+(?:top|bottom|crest)|'
    r'(?:this|that|these|those)\s+(?:element|inversion|loop|turn|hill|section)|'
    r'(?:once|when)\s+(?:vehicles?|cars?|trains?|riders?|it|they|the train)\s+reach\w*\s+the top|'
    r'(?:at|from)\s+the\s+summit|(?:during|after|following)\s+(?:this|that|these|those)\b|'
    r'(?:as|when|once)\s+(?:the train|it)\s+crests?\b|'
    r'(?:it|the train|the car)\s+is\s+(?:then\s+)?released|'
    r'(?:immediately\s+)?(?:afterwards|afterward)|'
    r'.{0,90}\b(?:the|its)\s+["“]?[^.!?]{0,30}?["”’\']s\s+(?:mouth|tunnel))', re.I)
PHASE = re.compile(
    r'(?P<climb>\b(?:climb\w*|ascend\w*|lift[- ]hill|chain lift|ris(?:e[sd]?|ing)))|'
    r'(?P<drop>\b(?:drop\w*|descend\w*|plung\w*|plummet\w*|dive))|'
    r'(?P<invert>\b(?:loops?|rolls?|corkscrews?|inversions?|immelmann|batwing|sidewinder))|'
    r'(?P<turn>\b(?:turn\w*|curv\w*|helix|helices|bank\w*))|'
    r'(?P<hill>\b(?:airtime|hills?|camelback|bunny hop))|'
    r'(?P<launch>\b(?:launch\w*|accelerat\w*|propelled))|'
    r'(?P<slow>\b(?:brakes?|slow\w*|stop\w*|pause\w*|held|holding))', re.I)
# Mask references to already-completed elements
REFERENCE = re.compile(
    r'^(?:(?:after|upon|once|when)\b[^,]*,|'
    r'(?:at|from) the (?:top|bottom)\b[^,]*,)\s*', re.I)
COMPATIBLE = {
    'climb': {'drop': 5, 'turn': 4, 'slow': 4, 'hill': 3},
    'drop': {'invert': 5, 'turn': 5, 'hill': 5, 'climb': 3},
    'invert': {'turn': 5, 'hill': 5, 'drop': 4, 'invert': 4},
    'turn': {'drop': 4, 'invert': 4, 'hill': 4, 'climb': 4, 'launch': 3},
    'hill': {'hill': 5, 'turn': 4, 'invert': 4, 'climb': 3},
    'launch': {'climb': 5, 'invert': 4, 'hill': 4, 'turn': 3},
    'slow': {'launch': 5, 'climb': 5, 'turn': 3},
    'motion': {},
}


def phases(text):
    return [match.lastgroup for match in PHASE.finditer(REFERENCE.sub('', text))] or ['motion']


def narrative_sentence(text):
    # We're aiming for mostly present tense
    plain = QUOTES.sub('', text)
    return bool(re.search(r'\w', plain)
                and not any(pattern.search(plain) for pattern in
                            (HISTORY, EDITORIAL, PAST_AUXILIARY, PAST_FINITE,
                             PAST_ACTION, PAST_NAMED, TERMINAL))
                and not (RIDE_DURATION.search(plain) and TIME_UNIT.search(plain)))


TERMINAL_RULES = (
    (r'\b(underneath|under|over|through|past) (?:the )?station\b', r'\1 the covered section'),
    (r'^(?:immediately\s+)?(?:after|once|upon|when|following)\b[^,]*'
     r'\b(?:station|dispatch\w*|front gate|floor drops|floors drop)\b[^,]*,\s*', ''),
    (r'\b(?:leaves|departs|exits)\s+(?:from\s+)?(?:the\s+)?station\b', 'continues'),
    (r'\b(?:leave|depart|exit)\s+(?:from\s+)?(?:the\s+)?station\b', 'continue'),
    (r'\bis dispatched (?:from (?:the )?station|straight)\b', 'continues'),
    (r'\b(?:final|last) brake run\b', 'trim brakes'),
    (r'\b(?:the )?(?:ride.s )?finale features\s+', 'The train continues through '),
    (r'\b(?:ends?|finishes?|concludes?) with\b', 'continues with'),
)
ARRIVAL = re.compile(
    r'\s*,?\s+(?:before|and(?: then)?|finally)\s+'
    r'(?=(?:(?:it|the train|the ride)\s+)?'
    r'(?:return\w*|enter\w*|reach\w*|com\w*|stop\w*)\s+'
    r'[^.!?]{0,60}\b(?:station|final brake|complete stop)\b)', re.I)


def trim_terminals(text):
    text = substitute(text, TERMINAL_RULES, flags=re.I)
    text = ARRIVAL.split(text, maxsplit=1)[0]
    text = re.sub(r'^(?:when|as) the ride (?:starts|begins),\s*', '', text, flags=re.I)
    text = text.strip(' ,;')
    if text and text[-1] not in '.!?':
        text += '.'
    return text[:1].upper() + text[1:]


OPENING_RULES = (
    (r'^This is followed by\s+', 'The train continues into '),
    (r'^This leads\b', 'The track leads'),
    (r'\banother\s+(?=[aeiou])', 'an '),
    (r'\banother\s+', 'a '),
    (r'\b(?:this|that) (spike|hill|drop|turn|loop|tunnel)\b', r'a \1'),
)


def clean_opening(text):
    text = unquoted(text, lambda part: substitute(part, OPENING_RULES, flags=re.I))
    text = OPENING.sub('', text).strip()
    return text[:1].upper() + text[1:]


def cut_passages(text, rng, names=None):
    # Keep sentences together and note the movements at either end
    runs, run = [], []
    for sentence in sentences(text):
        if names is not None:
            sentence = unquoted(sentence, lambda part: names.sub('the ride', part))
        sentence = ' '.join(trim_terminals(clean_sentence(sentence)).split())
        if not narrative_sentence(sentence):
            if run:
                runs.append(run)
                run = []
            continue
        # Skip sentences whose setup was cut
        if not run and DEPENDENT.search(sentence):
            continue
        run.append(sentence)
    if run:
        runs.append(run)

    result = []
    for run in runs:
        start = 0
        while start < len(run):
            size = rng.choice((2, 3, 4, 4, 5))
            end = min(start + size, len(run))
            # Keep dependent sentences with their setup
            while end < len(run) and DEPENDENT.search(run[end]):
                end += 1
            if len(run) - end == 1:
                end += 1
            edited = [clean_opening(run[start]), *run[start + 1:end]]
            result.append({'narrative': ' '.join(edited),
                           'entry': phases(edited[0])[0],
                           'exit': phases(edited[-1])[-1]})
            start = end
    return result


def join_score(previous, candidate):
    score = COMPATIBLE[previous['exit']].get(candidate['entry'], 1)
    if previous['revision'] == candidate['revision']:
        score -= 2
    return score


def route(pool, target, rng):
    # Use each passage once, picking randomly among the best joins
    remaining = pool.copy()
    selected, words = [], 0
    previous = {'exit': 'slow', 'revision': None}
    while remaining and words < target:
        scores = [join_score(previous, p) for p in remaining]
        best = max(scores)
        candidates = [i for i, score in enumerate(scores) if score == best]
        index = rng.choice(candidates)
        passage = remaining.pop(index)
        selected.append(passage)
        words += len(passage['narrative'].split())
        previous = passage
    return selected, words


CONTINUITY_RULES = (
    (r'\bthe the ride\b', 'the ride'),
    (r'\b(?:ride|coaster) (?:begins?|starts?) (with|by)\b', r'ride continues \1'),
)
RIDE_SENTENCE_START = re.compile(r'(^|[.!?]\s+)the ride\b', re.M)
LOCAL_DESCRIPTION = re.compile(
    r'\b(?:the|this|that) (?:ride|(?:roller )?coaster)'
    r'(?:(?P<possessive>[\'’]s)(?=\s+\w)|(?=\s+(?:'
    r'(?:features?|has|contains?|includes?|consists?|uses?|offers?|provides?|operates?|runs?|measures?|'
    r'reaches? a (?:top|maximum) speed|can (?:accommodate|carry)|comes? to an end)\b|'
    r'is\s+(?:in|inside|within|located|situated|made|surrounded|enclosed|covered|themed)\b|'
    r'is\s+(?:[^.!?]|\.(?=\d))*?\b(?:long|length|tall|height|high|speed)\b)))', re.I)


def local_descriptions(text):
    # Each coaster becomes "this section" of the ride
    def replace(match):
        subject = 'This section' if match[0][0].isupper() else 'this section'
        return subject + (match['possessive'] or '')

    return unquoted(text, lambda part: LOCAL_DESCRIPTION.sub(replace, part))


def ride_names(rides):
    names = {re.sub(r'\s*\([^)]*\)$', '', ride['title']) for ride in rides}
    names |= {name.removeprefix('The ') for name in names}
    alternatives = [re.escape(name).replace("'", "['’]")
                    for name in sorted(names, key=lambda name: (-len(name), name)) if name]
    return re.compile(r'(?<!\w)(?:' + '|'.join(alternatives) + r')(?!\w)')


FINALITY_RULES = (
    # "third and final launch" becomes "third launch"
    (r'\b(first|second|third|fourth|fifth|sixth|seventh|eighth|'
     r'ninth|tenth|\d+(?:st|nd|rd|th)),?\s+and\s+final\b', r'\1'),
    (r'\bfinal(?:,\s*|\s+)(\w|$)', lambda m: m[1].upper() if m[0][0].isupper() else m[1]),
    (r'\bfinally\b', lambda m: 'Then' if m[0][0].isupper() else 'then'),
)


def continuous(text):
    text = ' '.join(sentence for sentence in sentences(text)
                    if not (RIDE_DURATION.search(sentence) and TIME_UNIT.search(sentence)))
    text = unquoted(text, lambda part: substitute(part, FINALITY_RULES, flags=re.I))
    text = metric_first(text)
    text = substitute(text, CONTINUITY_RULES, flags=re.I)
    text = RIDE_SENTENCE_START.sub(lambda match: match[1] + 'The ride', text)
    return local_descriptions(text)


def make_book(rides, target, rng):
    # Give each passage a share of its source ride's duration
    known = [ride['duration_seconds'] for ride in rides
             if ride['duration_seconds'] is not None]
    fallback = round(median(known)) if known else 120
    names = ride_names(rides)
    pool = []
    for ride in rides:
        duration = ride['duration_seconds']
        passages = cut_passages(ride['text'], rng, names)
        source_words = len(ride['text'].split())
        duration = duration if duration is not None else fallback
        elapsed_words, allocated = 0, 0
        for passage in passages:
            elapsed_words += len(passage['narrative'].split())
            cumulative = round(duration * elapsed_words / source_words)
            pool.append({**passage, 'title': ride['title'], 'revision': ride['revision'],
                         'narrative': continuous(passage['narrative']),
                         'seconds': cumulative - allocated})
            allocated = cumulative
    selected, edited_words = route(pool, target, rng)
    if edited_words < target:
        raise ValueError(f'Only {edited_words:,} edited narrative words available; '
                         f'cannot reach {target:,} without repetition')
    seconds = sum(ride['seconds'] for ride in selected)
    opening = {'title': None, 'revision': None, 'seconds': 0,
               'narrative': 'The train leaves the station.'}
    ending = {'title': None, 'revision': None, 'seconds': 0,
              'narrative': 'The train slows to a stop approximately '
                           + duration_words(seconds) + ' after it began.'}
    return chapter_groups([opening, *selected, ending], rng), seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='corpus.jsonl')
    parser.add_argument('--contact', default='https://github.com/nmifsud/scream',
                        help='Contact URL or email used when scraping a missing corpus')
    parser.add_argument('--words', type=int, default=50000,
                        help='Minimum edited narrative words (default: 50000)')
    args = parser.parse_args()
    source = Path(args.input)
    if not source.exists():
        print(f'Missing {source}; collecting ride descriptions from Wikipedia.', flush=True)
        subprocess.run([sys.executable, str(ROOT / 'scrape.py'),
                        '--output', str(source), '--contact', args.contact], check=True)
    rides = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()
             if line.strip()]
    seed = random.randrange(2**63)
    chapters, seconds = make_book(rides, args.words, random.Random(seed))
    stamp = time.strftime('%y%m%d-%H%M%S')
    out = ROOT / f'scream-{stamp}.pdf'
    tex = render_book(chapters, seconds, stamp, random.Random(seed))
    narratives = ['\n\n'.join(ride['narrative'] for ride in chapter) for chapter in chapters]
    with tempfile.TemporaryDirectory(prefix='scream-images-') as directory:
        cover, images = write_illustrations(narratives, seed, directory)
        compile_pdf(tex, out, cover, images)
    narrative_words = sum(len(ride['narrative'].split()) for chapter in chapters for ride in chapter)
    print(f'SCREAM! / {narrative_words:,} edited words / {len(chapters)} chapters / '
          f'{pretty_time(seconds)}')
    print(out)


if __name__ == '__main__':
    main()
