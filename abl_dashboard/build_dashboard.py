#!/usr/bin/env python3
"""Rebuild the ABL publications dashboard from a PubMed export.

Next time papers are added:
  1. Save the new PubMed MEDLINE file in newdata/ (pubmed-*.txt).
  2. Run:  python3 build_dashboard.py

Papers already in data/publications.js keep their curated tags.
Chart inputs are rewritten to data/derived.js, which index.html reads.
PMIDs in the export that are not in the catalog yet are listed and left
for tagging; once they are in the catalog, the same command refreshes every plot.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
YEAR_START = 2015
YEAR_END = 2026
NAMED_DEPTS = [
    'Pathology', 'Medicine', 'Biochemistry', 'Cell Biology', 'Neuroscience',
    'Neurology', 'Neurosurgery', 'Cardiothoracic', 'Dermatology',
]

def parse_medline(text):
    records, cur, tag = [], None, None
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(line) >= 6 and line[:4].strip() and line[4] == '-':
            tag = line[:4].strip()
            val = line[6:].strip()
            if tag == 'PMID':
                if cur:
                    records.append(cur)
                cur = {}
            if cur is None:
                continue
            cur.setdefault(tag, []).append(val)
        elif cur is not None and tag:
            cur[tag][-1] = cur[tag][-1] + ' ' + line.strip()
    if cur:
        records.append(cur)
    return records

def year_of(dp):
    m = re.search(r'(19|20)\d{2}', dp or '')
    return int(m.group(0)) if m else None

def load_catalog(js_text):
    start = js_text.find('[')
    end = js_text.find(';\nconst publicationsMeta')
    if end < 0:
        raise SystemExit('publications.js is missing publicationsMeta')
    catalog = json.loads(js_text[start:end])
    meta = json.loads(js_text[end + len(';\nconst publicationsMeta = '):].rstrip().rstrip(';'))
    return catalog, meta

def latest_pubmed():
    files = sorted((ROOT / 'newdata').glob('pubmed-*.txt'))
    if not files:
        raise SystemExit('No newdata/pubmed-*.txt export found')
    return files[-1]

def author_last(au):
    parts = au.split()
    if len(parts) < 2 or not re.fullmatch(r'[A-Z]{1,4}', parts[-1]):
        return None
    return ' '.join(parts[:-1])

def dominant_author_pmids(label, catalog, authors):
    """Papers for one person: curated PI tag, otherwise the most frequent author with that surname."""
    pi_ids = [p['pmid'] for p in catalog if label in (p.get('pis') or [])]
    if pi_ids:
        return set(pi_ids)
    per_au = Counter()
    owned = defaultdict(set)
    for paper in catalog:
        for au in authors.get(paper['pmid'], []):
            last = author_last(au)
            if last and last.lower() == label.lower():
                per_au[au] += 1
                owned[au].add(paper['pmid'])
    if not per_au:
        return set()
    top = per_au.most_common(1)[0][0]
    return owned[top]

def dept_series(catalog, years):
    series = {name: [] for name in NAMED_DEPTS}
    series['Other'] = []
    for year in years:
        papers = [p for p in catalog if p['year'] == year]
        for name in NAMED_DEPTS:
            series[name].append(sum(1 for p in papers if name in (p.get('depts') or [])))
        series['Other'].append(sum(
            1 for p in papers
            if any(d not in NAMED_DEPTS for d in (p.get('depts') or []))
        ))
    return series

def main():
    js_path = ROOT / 'data' / 'publications.js'
    js_text = js_path.read_text()
    catalog, _meta = load_catalog(js_text)
    pubmed_path = latest_pubmed()
    records = parse_medline(pubmed_path.read_text(errors='replace'))
    by_pmid = {r['PMID'][0]: r for r in records}
    authors = {pmid: rec.get('AU', []) for pmid, rec in by_pmid.items()}

    # Keep the curated catalog inside the year window. New PMIDs are reported
    # so the next export is obvious; classification of brand-new papers uses
    # the author/PI fields already stored when this file was last built.
    in_window = []
    for paper in catalog:
        rec = by_pmid.get(paper['pmid'])
        if rec:
            paper = dict(paper)
            y = year_of(rec['DP'][0])
            if y is not None:
                paper['year'] = y
        if YEAR_START <= paper['year'] <= YEAR_END:
            in_window.append(paper)
    known = {p['pmid'] for p in in_window}
    missing = []
    for rec in records:
        y = year_of(rec['DP'][0])
        pmid = rec['PMID'][0]
        if y is None or not (YEAR_START <= y <= YEAR_END) or pmid in known:
            continue
        missing.append(pmid)
    if missing:
        print(f'{len(missing)} PMIDs in {pubmed_path.name} are not in the catalog yet.')
        print('They are skipped. Re-run after tags are added, or extend classify_new in this script.')
        print('First new PMIDs:', ', '.join(missing[:8]))

    catalog = in_window
    years = list(range(YEAR_START, YEAR_END + 1))
    year_counts = Counter(p['year'] for p in catalog)
    pubs = [year_counts[y] for y in years]
    cumulative, running = [], 0
    for value in pubs:
        running += value
        cumulative.append(running)

    pi_papers = defaultdict(set)
    for paper in catalog:
        for pi in paper.get('pis') or []:
            pi_papers[pi].add(paper['pmid'])

    html = (ROOT / 'index.html').read_text()
    node_block = html[html.find('const nyuNodes'):html.find('const nyuCrossLinks')]
    node_ids = re.findall(r"id:'([^']+)'", node_block)
    node_pubs = {}
    for node_id in node_ids:
        if node_id in pi_papers:
            node_pubs[node_id] = len(pi_papers[node_id])
        else:
            node_pubs[node_id] = len(dominant_author_pmids(node_id, catalog, authors))

    cross_block = html[html.find('const nyuCrossLinks'):html.find('const extNodes')]
    cross_pairs = re.findall(r"source:'([^']+)'\s*,\s*target:'([^']+)'", cross_block)
    cross_weights = {}
    for src, tgt in cross_pairs:
        left = pi_papers.get(src) or dominant_author_pmids(src, catalog, authors)
        right = pi_papers.get(tgt) or dominant_author_pmids(tgt, catalog, authors)
        cross_weights[f'{src}|{tgt}'] = len(left & right)

    ext_block = html[html.find('const extNodes'):html.find('const bridgeLinks')]
    ext_ids = re.findall(r"id:'([^']+)',label:'([^']+)'", ext_block)
    ext_counts = {}
    ext_sets = {}
    for node_id, label in ext_ids:
        pmids = dominant_author_pmids(label.replace('Papagian.', 'Papagiannakopoulos'), catalog, authors)
        if label.startswith('Papagian'):
            pmids = pi_papers.get('Papagiannakopoulos', pmids)
        ext_sets[node_id] = pmids
        ext_counts[node_id] = len(pmids)

    bridge_block = html[html.find('const bridgeLinks'):html.find('const deptYears')]
    bridge_pairs = re.findall(r"source:'([^']+)'\s*,\s*target:'([^']+)'", bridge_block)
    bridge_weights = {}
    for src, tgt in bridge_pairs:
        bridge_weights[f'{src}|{tgt}'] = len(ext_sets.get(src, set()) & ext_sets.get(tgt, set()))

    derived = {
        'yearStart': YEAR_START,
        'yearEnd': YEAR_END,
        'total': len(catalog),
        'years': years,
        'pubsByYear': pubs,
        'cumulativePubs': cumulative,
        'nodePubs': node_pubs,
        'crossWeights': cross_weights,
        'extCounts': ext_counts,
        'bridgeWeights': bridge_weights,
        'deptSeries': dept_series(catalog, years),
    }
    derived_path = ROOT / 'data' / 'derived.js'
    derived_path.write_text(
        '// Generated by build_dashboard.py — do not edit by hand.\n'
        'const DERIVED = ' + json.dumps(derived) + ';\n'
    )

    # PCC lab bubble sizes follow the same PI paper counts.
    pcc_path = ROOT / 'data' / 'pcc_dashboard.js'
    pcc_js = pcc_path.read_text()
    labs = json.loads(re.search(r'const pccLabData = (\[.*?\]);', pcc_js, re.S).group(1))
    for lab in labs:
        lab['pubs'] = len(pi_papers.get(lab['id'], ()))
    pcc_js, n = re.subn(
        r'const pccLabData = \[.*?\];',
        'const pccLabData = ' + json.dumps(labs) + ';',
        pcc_js,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise SystemExit('could not refresh pccLabData')
    pcc_path.write_text(pcc_js)

    wire_html(html)
    print(f'Catalog {len(catalog)} papers · {YEAR_START}–{YEAR_END}')
    print('pubsByYear', pubs)
    print('Aifantis node', node_pubs.get('Aifantis'), 'Moreira|Pass', cross_weights.get('Moreira|Pass'))
    print('ext Aifantis', ext_counts.get('Aifantis_ext'), 'ext Zhang', ext_counts.get('Zhang'))
    print('Pathology series', derived['deptSeries']['Pathology'])
    print('Wrote', derived_path)

def wire_html(html):
    path = ROOT / 'index.html'
    if 'data/derived.js' not in html:
        html = html.replace(
            '<script src="data/network_dashboard.js"></script>',
            '<script src="data/network_dashboard.js"></script>\n<script src="data/derived.js"></script>',
            1,
        )
    fallback_years = ','.join(str(y) for y in range(YEAR_START, YEAR_END + 1))
    html = re.sub(
        r'const years = \[[^\]]+\];',
        f'const years = (typeof DERIVED !== "undefined" && DERIVED.years) ? DERIVED.years.slice() : [{fallback_years}];',
        html,
    )
    html = re.sub(
        r'const deptYears = \[[^\]]+\];',
        f'const deptYears = (typeof DERIVED !== "undefined" && DERIVED.years) ? DERIVED.years.slice() : [{fallback_years}];',
        html,
    )
    html = re.sub(
        r'const pubsByYear = \[[^\]]+\];',
        'const pubsByYear = (typeof DERIVED !== "undefined" && DERIVED.pubsByYear) ? DERIVED.pubsByYear.slice() : [17,19,28,32,47,52,57,58,62,60,73,50];',
        html,
    )
    html = re.sub(
        r'const cumulativePubs = \[[^\]]+\];',
        'const cumulativePubs = (typeof DERIVED !== "undefined" && DERIVED.cumulativePubs) ? DERIVED.cumulativePubs.slice() : [17,36,64,96,143,195,252,310,372,432,505,555];',
        html,
    )
    apply_nodes = '''
if (typeof DERIVED !== "undefined" && DERIVED.nodePubs) {
  nyuNodes.forEach(n => { if (DERIVED.nodePubs[n.id] != null) n.pubs = DERIVED.nodePubs[n.id]; });
}
'''
    apply_cross = '''
if (typeof DERIVED !== "undefined" && DERIVED.crossWeights) {
  nyuCrossLinks.forEach(l => {
    const w = DERIVED.crossWeights[l.source + "|" + l.target];
    if (w != null) l.weight = w;
  });
}
'''
    apply_ext = '''
if (typeof DERIVED !== "undefined" && DERIVED.extCounts) {
  extNodes.forEach(n => { if (DERIVED.extCounts[n.id] != null) n.count = DERIVED.extCounts[n.id]; });
}
'''
    apply_bridge = '''
if (typeof DERIVED !== "undefined" && DERIVED.bridgeWeights) {
  bridgeLinks.forEach(l => {
    const w = DERIVED.bridgeWeights[l.source + "|" + l.target];
    if (w != null) l.weight = w;
  });
}
'''
    apply_dept = '''
if (typeof DERIVED !== "undefined" && DERIVED.deptSeries) {
  deptYearDatasets.forEach(ds => {
    const key = ds.label.split(" (")[0];
    if (DERIVED.deptSeries[key]) ds.data = DERIVED.deptSeries[key].slice();
  });
}
'''
    def insert_once(html, after_decl, snippet, marker):
        if marker in html:
            return html
        m = re.search(rf'const {after_decl} = \[.*?\];', html, re.S)
        if not m:
            raise SystemExit(f'missing {after_decl}')
        return html[:m.end()] + '\n' + snippet + html[m.end():]

    html = insert_once(html, 'nyuNodes', apply_nodes, 'DERIVED.nodePubs')
    html = insert_once(html, 'nyuCrossLinks', apply_cross, 'DERIVED.crossWeights')
    html = insert_once(html, 'extNodes', apply_ext, 'DERIVED.extCounts')
    html = insert_once(html, 'bridgeLinks', apply_bridge, 'DERIVED.bridgeWeights')
    html = insert_once(html, 'deptYearDatasets', apply_dept, 'DERIVED.deptSeries')
    path.write_text(html)

if __name__ == '__main__':
    main()
