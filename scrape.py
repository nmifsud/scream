import argparse
import json
import re
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

API = 'https://en.wikipedia.org/w/api.php'
HEADINGS = ('ride experience', 'ride description', 'ride sequence', 'ride through',
            'ride-through', 'ride cycle', 'ride layout', 'layout', 'the layout',
            'course', 'ride course', 'the ride', 'ride', 'experience')
HEADING_TAGS = ('h2', 'h3', 'h4', 'h5', 'h6')
MOTION = re.compile(
    r'\b(?:depart|leave|enter|exit|ascend|descend|drop|turn|twist|'
    r'launch|climb|plunge|loop|roll|brake|followed by|then|after)\w*\b', re.I)
TERMS = re.compile(
    r'\b(?:train|rider|track|lift|launch|drop|loop|inversion|'
    r'corkscrew|helix|brake|station|hill|turn|airtime)\w*\b', re.I)


def api(session, **params):
    for attempt in range(5):
        try:
            response = session.get(
                API, params={'format': 'json', 'formatversion': 2, **params},
                timeout=40)
            response.raise_for_status()
            data = response.json()
            if 'error' in data:
                raise ValueError(data['error'])
            return data
        except (requests.RequestException, ValueError):
            if attempt == 4:
                raise
            time.sleep(2**attempt)


def discover(session, max_categories=0, max_articles=0):
    pending = deque(['Category:Roller coasters'])
    visited, pages = set(), set()
    while pending and (not max_categories or len(visited) < max_categories):
        category = pending.popleft()
        if category in visited:
            continue
        visited.add(category)
        print(f'Category {len(visited)}: {category}', file=sys.stderr)
        continuation = {}
        while True:
            data = api(session, action='query', list='categorymembers',
                       cmtitle=category, cmtype='page|subcat', cmlimit='max',
                       **continuation)
            for item in data['query']['categorymembers']:
                if item['ns'] == 14 and item['title'] not in visited:
                    pending.append(item['title'])
                elif item['ns'] == 0 and not item['title'].lower().startswith('list of '):
                    pages.add(item['title'])
                    if max_articles and len(pages) >= max_articles:
                        return sorted(pages)
            if 'continue' not in data:
                break
            continuation = data['continue']
        time.sleep(0.1)
    return sorted(pages)


def clean_text(node):
    soup = BeautifulSoup(str(node), 'html.parser')
    for element in soup.select('sup.reference, .mw-editsection, .reference, '
                               '.noprint, .thumb, figure, table, script, style'):
        element.decompose()
    return ' '.join(soup.get_text().split())


def ride_duration(soup):
    for row in soup.select('table.infobox tr'):
        key, value = row.find('th', recursive=False), row.find('td', recursive=False)
        if key is None or value is None:
            continue
        label = ' '.join(key.get_text(' ', strip=True).split()).lower()
        if label in ('duration', 'ride duration', 'ride time'):
            return parse_duration(' '.join(value.get_text(' ', strip=True).split()))
    return None


def parse_duration(value):
    text = value.lower().strip()
    if match := re.fullmatch(r'(\d+):([0-5]\d)', text):
        seconds = int(match.group(1)) * 60 + int(match.group(2))
    elif match := re.fullmatch(
            r'(\d+(?:\.\d+)?)\s*(?:minutes?|mins?)'
            r'(?:\s+(?:and\s+)?(\d+(?:\.\d+)?)\s*(?:seconds?|secs?))?', text):
        seconds = float(match.group(1)) * 60 + float(match.group(2) or 0)
    elif match := re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:seconds?|secs?)', text):
        seconds = float(match.group(1))
    else:
        return None
    return round(seconds) or None


def ride_section(soup, title):
    content = soup.select_one('.mw-parser-output') or soup
    infobox = soup.select_one('table.infobox')
    lead = ' '.join(clean_text(p) for p in content.find_all('p', recursive=False)[:4])
    summary = (infobox.get_text(' ', strip=True) if infobox else '') + lead
    if 'roller coaster' not in summary.lower():
        return None

    sections = []
    for heading in content.find_all(HEADING_TAGS):
        name = re.sub(r'\[edit\]$', '', clean_text(heading).lower()).strip()
        if name not in HEADINGS and name != title.lower():
            continue
        paragraphs = []
        for element in heading.find_all_next(('p', *HEADING_TAGS)):
            if element.name in HEADING_TAGS:
                break
            if element.find_parent(('table', 'figure', 'aside', 'nav')):
                continue
            paragraph = clean_text(element)
            if paragraph:
                paragraphs.append(paragraph)
        passage = '\n\n'.join(paragraphs)
        if (len(passage) >= 120 and len(MOTION.findall(passage)) >= 3
                and len(TERMS.findall(passage)) >= 2):
            sections.append({'section': name, 'text': passage})
    return max(sections, key=lambda section: len(section['text']), default=None)


def fetch_ride(session, title):
    result = api(session, action='parse', page=title, prop='text|revid')['parse']
    soup = BeautifulSoup(result['text'], 'html.parser')
    section = ride_section(soup, title)
    if section is None:
        return None
    return {
        'title': title,
        'source': 'https://en.wikipedia.org/wiki/'
                  + quote(title.replace(' ', '_'), safe='()_,-'),
        'revision': result['revid'], 'licence': 'CC BY-SA 4.0',
        'duration_seconds': ride_duration(soup), **section,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='corpus.jsonl')
    parser.add_argument('--max-articles', type=int, default=0,
                        help='Stop discovery after this many candidate articles (0: all)')
    parser.add_argument('--max-categories', type=int, default=0)
    parser.add_argument('--delay', type=float, default=0.2)
    parser.add_argument('--contact', required=True, help='Your contact URL or email')
    args = parser.parse_args()
    if min(args.max_articles, args.max_categories, args.delay) < 0:
        parser.error('Limits and delay must be non-negative')
    session = requests.Session()
    session.headers['User-Agent'] = f'SCREAM!/0.2 ({args.contact})'
    titles = discover(session, args.max_categories, args.max_articles)
    path = Path(args.output)
    found = 0
    with path.open('w', encoding='utf-8') as output:
        for i, title in enumerate(titles, 1):
            try:
                record = fetch_ride(session, title)
                if record:
                    output.write(json.dumps(record, ensure_ascii=False) + '\n')
                    found += 1
            except Exception as exc:
                print(f'Skipped {title}: {exc}', file=sys.stderr)
            if i % 25 == 0:
                print(f'{i}/{len(titles)} checked; {found} rides collected',
                      file=sys.stderr)
            time.sleep(args.delay)
    print(f'Saved {found} rides to {path}')


if __name__ == '__main__':
    main()
