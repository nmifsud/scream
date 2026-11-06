import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from bisect import bisect_left
from collections import Counter
from itertools import accumulate, chain
from pathlib import Path
from statistics import geometric_mean

from num2words import num2words as number_words

from utils import ROOT, SENTENCE_BREAK

CHAPTER_WORDS = (2100, 4900)
TITLE_ELEMENT = re.compile(
    r'\b(?:(?:small|large|sharp|tight|twisted|banked|overbanked|inverted|'
    r'upward|downward|ascending|descending|interlocking|consecutive|'
    r'left[- ]hand(?:ed)?|right[- ]hand(?:ed)?)\s+){0,3}'
    r'(?:'
    r'(?:zero[- ](?:g|gravity)|heartline|barrel|inline|cobra|sea serpent|banana)\s+rolls?|'
    r'(?:vertical|dive|immelmann|pretzel|non[- ]inverting|Finnish|Norwegian)\s+loops?|'
    r'(?:zero[- ]g|top gun)\s+stalls?|'
    r'(?:chain|cable)\s+lift(?:[- ]hills?)?|'
    r'(?:airtime|air[- ]time|camelback|bunny hop|parabolic|speed)\s+hills?|'
    r'(?:wave|hammerhead|horseshoe|hairpin|banked|overbanked|left|right)\s+turns?|'
    r'(?:pretzel\s+knots?|diving\s+spirals?|raven\s+(?:turns?|drops?)|'
    r'fly[- ]to[- ]lie|lie[- ]to[- ]fly|double[- ]up|double[- ]down|top hats?|'
    r'corkscrews?|helix|helices|immelmann|batwing|sidewinder))\b', re.I)
TITLE_PHRASE = re.compile(
    r'\b(?:in|into|through|beneath|under|above|past|inside|around|between|beyond)\s+'
    r'(?:an?\s+|the\s+)?([^,;:.!?()]+)', re.I)
TITLE_ROUTINE = re.compile(
    r'\b(?:trains?|tracks?|coasters?|rides?|riders?|passengers?|brakes?|brake[- ]runs?|'
    r'inversions?|loops?|hills?|lifts?|launch(?:es)?|turns?|curves?|twists?|'
    r'turnarounds?|straights?|straightaways?|helix|helixes)\b', re.I)
TITLE_SMALL_WORDS = frozenset(
    'a an the and but or nor as at by for from in into of on onto per to via with'.split())
TITLE_WORD = re.compile(r"([^\W\d_]+(?:[’'][^\W\d_]+)*)")
LATEX_ESCAPES = str.maketrans({
    '\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$',
    '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}',
    '~': r'\textasciitilde{}', '^': r'\textasciicircum{}',
})

TEMPLATE = (ROOT / 'template.tex').read_text(encoding='utf-8')


def curly_quotes(text):
    text = re.sub(r"(?<!\w)'(?=[^\"“”\n]*'(?!\w))", '‘', text)
    text = text.replace("'", '’')
    text = re.sub(r'(^|[\s([{—–])"', r'\1“', text)
    return text.replace('"', '”')


def latex(text):
    return curly_quotes(text).translate(LATEX_ESCAPES)


def pretty_time(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:,} hours, {minutes} minutes and {seconds} seconds'


def duration_words(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = [number_words(value) + ' ' + unit + ('s' if value != 1 else '')
             for value, unit in ((hours, 'hour'), (minutes, 'minute'), (seconds, 'second'))
             if value]
    if not parts:
        return 'zero seconds'
    return ', '.join(parts[:-1]) + ' and ' + parts[-1] if len(parts) > 1 else parts[0]


def chapter_groups(selected, rng):
    # Vary chapter lengths without splitting passages
    if not selected:
        return []
    totals = list(accumulate(len(ride['narrative'].split()) for ride in selected))
    count = min(len(selected), max(1, round(totals[-1] / (sum(CHAPTER_WORDS) / 2))))
    weights = [rng.uniform(*CHAPTER_WORDS) for _ in range(count)]
    scale = totals[-1] / sum(weights)
    chapters, start = [], 0
    for chapter, target in enumerate(accumulate(weight * scale for weight in weights[:-1])):
        # Leave at least one passage for each remaining chapter
        last = len(selected) - (count - chapter - 1)
        boundary = bisect_left(totals, target, start, last)
        before = max(start + 1, boundary)
        after = min(last, boundary + 1)
        end = min((before, after),
                  key=lambda end: abs(totals[end - 1] - target))
        chapters.append(selected[start:end])
        start = end
    chapters.append(selected[start:])
    return chapters


def title_case(text):
    parts = TITLE_WORD.split(text)
    for i in range(1, len(parts), 2):
        word = parts[i]
        parts[i] = (word.lower() if 1 < i < len(parts) - 2 and word.casefold() in TITLE_SMALL_WORDS
                    else word[:1].upper() + word[1:])
    return ''.join(parts)


def title_candidates(text):
    elements = [title_case(' '.join(match[0].split())) for match in TITLE_ELEMENT.finditer(text)]
    phrases = []
    for candidate in chain(TITLE_PHRASE.findall(text), SENTENCE_BREAK.split(text)):
        candidate = re.sub(r'["“”]', '', candidate)
        candidate = ' '.join(candidate.split()).strip(' .!?;:,')
        if not (2 <= len(candidate.split()) <= 8 and len(candidate) <= 64):
            continue
        if not TITLE_WORD.search(candidate) or re.search(r'[\d()]', candidate):
            continue
        if TITLE_ELEMENT.search(candidate) or TITLE_ROUTINE.search(candidate):
            continue
        phrases.append(title_case(candidate))
    return list(dict.fromkeys(elements)), list(dict.fromkeys(phrases))


def chapter_titles(chapters, rng):
    texts = [' '.join(ride['narrative'] for ride in chapter) for chapter in chapters]
    frequencies = Counter(word for text in texts for word in TITLE_WORD.findall(text.casefold()))

    def weight(title):
        words = [word for word in TITLE_WORD.findall(title.casefold())
                 if word not in TITLE_SMALL_WORDS]
        if not words:
            return 1
        # Favour less common words
        average_frequency = geometric_mean(frequencies[word] or 1 for word in words)
        return 1 / average_frequency ** 0.5

    titles, used = [], set()
    for number, text in enumerate(texts, 1):
        elements, phrases = title_candidates(text)
        preferred, fallback = (elements, phrases) if rng.randrange(4) == 0 else (phrases, elements)
        choices = ([t for t in preferred if t.casefold() not in used]
                   or [t for t in fallback if t.casefold() not in used])
        title = f'Chapter {number}'
        if choices:
            title = rng.choices(choices, weights=[weight(t) for t in choices])[0]
        titles.append(title)
        used.add(title.casefold())
    return titles


def render_book(chapters, seconds, stamp, rng=None):
    headings = chapter_titles(chapters, rng if rng is not None else random.Random(stamp))
    tex = []
    for number, (rides, heading) in enumerate(zip(chapters, headings), 1):
        tex.append(rf'\ridechapter{{{latex(heading)}}}{{chapter-{number}.png}}')
        for ride in rides:
            narrative = ride['narrative']
            if narrative:
                tex.append(latex(narrative) + '\n\n')
    tex.extend([r'\backmatter\chapter*{Sources}', r'\small'])
    sources = {}
    for ride in chain.from_iterable(chapters):
        revision = ride['revision']
        if revision is None or revision in sources:
            continue
        title = re.sub(r'\s*\(roller coaster\)', '', ride['title'], flags=re.I)
        sources[revision] = rf'\href{{https://en.wikipedia.org/w/index.php?oldid={revision}}}{{{latex(title)}}}'
    tex.append(', '.join(sources.values()) + '.')
    return (TEMPLATE.replace('%%BODY%%', '\n'.join(tex))
            .replace('%%COVER_DATE%%', latex(time.strftime('%B %Y')))
            .replace('%%EDITION%%', stamp).replace('%%RIDES%%', f'{len(sources):,}')
            .replace('%%HOURS%%', latex(number_words((seconds + 1800) // 3600))))


def compile_pdf(document, path, cover=None, chapter_images=()):
    with tempfile.TemporaryDirectory(prefix='scream-tex-') as directory:
        build = Path(directory)
        (build / 'scream.tex').write_text(document, encoding='utf-8')
        if cover is not None:
            shutil.copyfile(cover, build / 'cover.png')
        for image in chapter_images:
            shutil.copyfile(image, build / image.name)
        # The second pass uses page numbers and references from the first
        for _ in range(2):
            try:
                subprocess.run(['xelatex', '-interaction=nonstopmode', '-halt-on-error', 'scream.tex'],
                               cwd=build, check=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
            except subprocess.CalledProcessError as exc:
                print(exc.stdout, file=sys.stderr, end='')
                raise
        shutil.copyfile(build / 'scream.pdf', path)
