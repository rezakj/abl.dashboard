#!/usr/bin/env python3
"""Tag brand-new PubMed records so they match the curated catalog shape.

Used when build_dashboard.py reports PMIDs that are not in data/publications.js.
Rules were reverse-engineered from the existing 555-paper catalog:

  abl          ABL member key when "Surname Initials" appears in the author list
  pis          PI key when an author string was (>=80%) tagged with that PI in the catalog
  pcc          True when any matched PI is a PCC lab (pccLabData)
  pccPrograms  union of PCC lab programs for matched PIs + PCC member name matches
  depts        departments of matched NYU PIs (nyuNodes / pccLabData) + NYU AD hints
  collabInsts  keyword match on all affiliation strings
  themes /     keyword rules on title + abstract (+ MeSH terms), tuned against catalog
  diseaseThemes

Usage:  python3 classify_new.py          # dry-run: prints tags for new PMIDs
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import build_dashboard as bd

ROOT = Path(__file__).resolve().parent

INST = {
    'mdanderson': r'MD Anderson|M\.?D\.? Anderson',
    'msk': r'Memorial Sloan',
    'nih': r'National Institutes of Health|National Cancer Institute|\bNIH\b|National Human Genome',
    'cornell': r'Weill Cornell|Cornell',
    'harvard': r'Harvard|Dana-Farber|Dana Farber|Brigham|Massachusetts General',
    'mit': r'Massachusetts Institute of Technology|\bMIT\b|Broad Institute',
    'ucsf': r'UCSF|University of California,? San Francisco',
    'mountsinai': r'Mount Sinai|Icahn',
    'upenn': r'University of Pennsylvania|Perelman|Penn Medicine|Abramson',
    'columbia': r'Columbia University|Columbia Univ|College of Physicians and Surgeons|Vagelos',
    'hopkins': r'Johns Hopkins',
    'stanford': r'Stanford',
}

NYU_AD = r'NYU|New York University|Langone'
DEPT_AD = {
    'Pathology': r'Pathology', 'Medicine': r'Department of Medicine|Division of [^,.]*Medicine',
    'Biochemistry': r'Biochemistry', 'Neuroscience': r'Neuroscience|Neural Science',
    'Neurosurgery': r'Neurosurg', 'Cardiothoracic': r'Cardiothoracic|Cardiac Surg',
    'Dermatology': r'Dermatolog', 'Cell Biology': r'Cell Biology',
    'Ophthalmology': r'Ophthalm', 'Microbiology': r'Microbiology',
    'Neurology': r'Neurology', 'Psychiatry': r'Psychiatry',
}

# (theme, regex on title/abstract/MeSH)
THEME_RULES = [
    ('Cancer/Oncology', r'cancer|tumou?r|oncolog|carcinoma|leuk[a]?emia|lymphoma|myeloma|melanoma|glioma|glioblastoma|sarcoma|neoplas|metasta|malignan'),
    ('Genomics/Epigenomics', r'genom|epigenom|sequenc|transcriptom|methylation|chromatin|single-cell|RNA-seq|mutation|variant|piRNA|proteom|multi-?omic'),
    ('Immunology', r'immun|T[- ]cell|B[- ]cell|macrophage|inflamm|cytokine|lymphocyte|antibod'),
    ('Neuroscience/Neuropathol.', r'neuro|brain|glio|cerebr|CNS|astrocyt|neuron'),
    ('Stem Cells/Dev.', r'stem cell|hematopoie|differentiation|development|embryo|progenitor|germline'),
    ('Pathology/Imaging', r'patholog|histolog|histopatholog|imaging|whole[- ]slide|radiolog|microscop'),
    ('Computational/AI', r'artificial intelligence|\bAI\b|machine learning|deep learning|neural network|computational|algorithm|benchmark|LLM|language model|foundation model'),
]
DISEASE_RULES = [
    ('Glioma / Brain Tumor', r'glioma|glioblastoma|neuroepithelial|brain tumou?r|meningioma|medulloblastoma|glioneuronal|ependymoma'),
    ('Acute Leukemia', r'acute (myeloid|lymphoblastic|lymphoid) leuk|\bAML\b|T-ALL|\bB-ALL\b'),
    ('Leukemia', r'leuk[a]?emia'),
    ('Lung Cancer', r'lung (cancer|adenocarcinoma|carcinoma)|NSCLC|SCLC|non-small cell|mesothelioma'),
    ('Melanoma', r'melanoma'),
    ('Pancreatic Cancer', r'pancrea[a-z]* (ductal|cancer|adeno)|\bPDAC\b'),
    ('Multiple Myeloma', r'myeloma'),
    ('Infectious Disease', r'infection|viral|virus|bacteri|SARS|COVID|HIV|sepsis|microbiome|pathogen'),
    ('Breast Cancer', r'breast (cancer|carcinoma)'),
    ('Sarcoma', r'sarcoma'),
    ('Cardiovascular', r'cardiac|cardio|vasoplegia|heart|vascular'),
    ('Lymphoma', r'lymphoma'),
    ('Ovarian Cancer', r'ovarian'),
    ('Prostate Cancer', r'prostate'),
    ('Colorectal Cancer', r'colorectal|colon cancer'),
    ('Renal Cancer', r'renal (cell )?carcinoma|kidney cancer'),
    ('Gastric Cancer', r'gastric'),
    ('Neurodegenerative', r'alzheimer|parkinson|neurodegenerat|huntington'),
    ('Autoimmune / Inflammatory', r'autoimmun|lupus|arthritis|psoriasis'),
]
GENERAL_ONC = r'cancer|tumou?r|oncolog|carcinoma|neoplas|metasta|malignan'


def norm(s):
    return re.sub(r'[\s\-]+', '', s).lower()


class Classifier:
    def __init__(self):
        js_text = (ROOT / 'data' / 'publications.js').read_text()
        self.catalog, self.meta = bd.load_catalog(js_text)
        pcc_js = (ROOT / 'data' / 'pcc_dashboard.js').read_text()
        self.labs = json.loads(re.search(r'const pccLabData = (\[.*?\]);', pcc_js, re.S).group(1))
        self.lab_prog = {l['id']: l['programs'] for l in self.labs}
        members = json.loads(re.search(r'const pccMembersByProgram = (\{.*?\});\s*\n', pcc_js, re.S).group(1))
        self.members = defaultdict(set)
        for prog, lst in members.items():
            for m in lst:
                self.members[(m['last'].lower(), m['first'][:1].lower())].add(prog)
        html = (ROOT / 'index.html').read_text()
        blk = html[html.find('const nyuNodes'):html.find('const nyuCrossLinks')]
        self.pi_dept = dict(re.findall(r"\{id:'([^']+)'[^}]*?dept:'([^']*)'", blk))
        for l in self.labs:
            self.pi_dept.setdefault(l['id'], l['dept'])
        self._build_author_maps()
        self.theme_names = {t['key'] for t in self.meta['themes']}
        self.disease_names = {t['key'] for t in self.meta['diseaseThemes']}

    def _build_author_maps(self):
        astr = defaultdict(Counter)
        acnt = Counter()
        for p in self.catalog:
            for a in set(x.strip() for x in p['authors'].rstrip('.').split(', ')):
                acnt[a] += 1
                for pi in p['pis']:
                    astr[a][pi] += 1
        self.author_pi = {}
        for a, cnt in astr.items():
            sn = a.rsplit(' ', 1)[0].lower()
            cands = [(n, pi) for pi, n in cnt.items()
                     if sn == pi.lower() or sn.startswith(pi.lower() + ' ')]
            if not cands:
                continue
            n, pi = max(cands)
            # >=80% of this author string's catalog papers carry that PI tag
            if n / acnt[a] >= 0.8:
                self.author_pi[a] = pi
        # author string -> programs (learned): program present in >=80% of >=2 papers
        prog_cnt = defaultdict(Counter)
        self.author_progs = {}
        for p in self.catalog:
            for a in set(x.strip() for x in p['authors'].rstrip('.').split(', ')):
                for g in p['pccPrograms']:
                    prog_cnt[a][g] += 1
        for a, cnt in prog_cnt.items():
            self.author_progs[a] = {g for g, n in cnt.items() if n >= 2 and n / acnt[a] >= 0.8}
        # PI -> departments (learned): dept on >=50% of that PI's catalog papers
        d = defaultdict(Counter)
        npi = Counter()
        for p in self.catalog:
            for pi in p['pis']:
                npi[pi] += 1
                for x in p['depts']:
                    d[pi][x] += 1
        self.pi_learned_dept = {pi: [x for x, n in c.most_common() if n / npi[pi] >= 0.5] for pi, c in d.items()}
        # ABL members: key like "TsirigosA" / "KawalerEA"
        self.abl_match = []
        for m in self.meta['ablMembers']:
            key = m['key']
            sur, ini = re.match(r'^(.*?)([A-Z]{1,3})$', key).groups()
            # key "KawalerEA": greedy split leaves "Kawaler" + "EA"
            self.abl_match.append((key, norm(sur), ini))

    # ---- field classifiers -------------------------------------------------
    def abl(self, authors):
        out = []
        for key, sur, ini in self.abl_match:
            for a in authors:
                parts = a.rsplit(' ', 1)
                if len(parts) == 2 and norm(parts[0]) == sur and parts[1][:1] == ini[:1]:
                    out.append(key)
                    break
        return out

    def pis(self, authors):
        out = []
        for a in authors:
            pi = self.author_pi.get(a)
            if pi and pi not in out:
                out.append(pi)
        return out

    def pcc(self, pis, authors):
        pcc = any(pi in self.lab_prog for pi in pis)
        progs = set()
        if pcc:
            for pi in pis:
                progs |= set(self.lab_prog.get(pi, []))
            for a in authors:
                progs |= self.author_progs.get(a, set())
                parts = a.rsplit(' ', 1)
                if len(parts) == 2 and a in self.author_pi:
                    progs |= self.members.get((parts[0].lower(), parts[1][:1].lower()), set())
        order = ['CGD', 'CCB', 'ECC', 'TI', 'MEL']
        return pcc, [p for p in sorted(progs, key=lambda x: order.index(x))]

    def depts(self, pis, ads):
        """Catalog convention: departments follow the matched NYU PIs (none without a PI)."""
        out = []
        for pi in pis:
            for d in self.pi_learned_dept.get(pi, []):
                if d not in out:
                    out.append(d)
        return out

    def collab(self, ads):
        txt = ' | '.join(ads)
        return [k for k, pat in INST.items() if re.search(pat, txt)]

    @staticmethod
    def _score(rules, title, abstract, thr, tw):
        out = []
        for name, pat in rules:
            sc = tw * len(re.findall(pat, title, re.I)) + len(re.findall(pat, abstract, re.I))
            if sc >= thr:
                out.append(name)
        return out

    def themes(self, title, abstract):
        out = self._score(THEME_RULES, title, abstract, 4, 5)
        return out or ['Other/General']

    def diseases(self, title, abstract):
        out = self._score(DISEASE_RULES, title, abstract, 3, 3)
        if 'Leukemia' in out and 'Acute Leukemia' in out:
            out.remove('Leukemia')
        if not out and self._score([('x', GENERAL_ONC)], title, abstract, 3, 3):
            out = ['General Oncology']
        return out


def record_to_paper(rec, clf):
    au = rec.get('AU', [])
    cn = [re.sub(r'\. Electronic address:.*$', '', c).strip() for c in rec.get('CN', [])]
    ads = rec.get('AD', [])
    title = rec['TI'][0].rstrip('.')
    abstract = ' '.join(rec.get('AB', []) + rec.get('MH', []) + rec.get('OT', []))
    doi = ''
    for v in rec.get('LID', []) + rec.get('AID', []):
        if v.endswith('[doi]'):
            doi = v.replace('[doi]', '').strip()
            break
    pis = clf.pis(au)
    pcc, progs = clf.pcc(pis, au)
    return {
        'pmid': rec['PMID'][0],
        'title': title,
        'authors': (', '.join(au) if au else '; '.join(dict.fromkeys(cn))) + '.',
        'journal': (rec.get('TA') or [''])[0],
        'year': bd.year_of(rec['DP'][0]),
        'citation': (rec.get('SO') or [''])[0].rstrip('.'),
        'pmcid': (rec.get('PMC') or [''])[0],
        'doi': doi,
        'themes': clf.themes(title, abstract),
        'diseaseThemes': clf.diseases(title, abstract),
        'abl': clf.abl(au),
        'pis': pis,
        'depts': clf.depts(pis, ads),
        'pccPrograms': progs,
        'pcc': pcc,
        'collabInsts': clf.collab(ads),
    }


def evaluate(clf):
    """Compare the rules with curated tags on papers already in the old export."""
    old_path = sorted((ROOT / 'newdata').glob('pubmed-*.txt'))[0]
    old = {r['PMID'][0]: r for r in bd.parse_medline(old_path.read_text(errors='replace'))}
    stats = defaultdict(lambda: [0, 0, 0])
    for p in clf.catalog:
        r = old.get(p['pmid'])
        if not r:
            continue
        q = record_to_paper(r, clf)
        for f in ['abl', 'pis', 'depts', 'collabInsts', 'themes', 'diseaseThemes', 'pccPrograms']:
            a, b = set(p[f]), set(q[f])
            s = stats[f]
            s[0] += len(a & b)
            s[1] += len(b - a)
            s[2] += len(a - b)
        stats['pcc'][0] += p['pcc'] == q['pcc']
    for f, (tp, fp, fn) in stats.items():
        print(f'{f:14s} tp={tp} fp={fp} fn={fn}')


if __name__ == '__main__':
    clf = Classifier()
    evaluate(clf)
