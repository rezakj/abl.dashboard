# ABL publications dashboard

Static page: open `index.html` in a browser. No build step is required just to view it.

## Refresh charts after the catalog changes

```bash
python3 build_dashboard.py
```

That rewrites `data/derived.js` and the PCC lab sizes in `data/pcc_dashboard.js`. `index.html` reads `derived.js` for the year charts, NYU network node sizes, collaborator-node sizes, link weights, and the department-by-year series. The set of labs and which links exist stays in `index.html`; only the numbers are replaced.

Year window is 2015–2026, set as `YEAR_START` / `YEAR_END` in `build_dashboard.py`. Current catalog size is 563 papers.

## Where the data lives

| File | Role |
| --- | --- |
| `newdata/pubmed-*.txt` | PubMed MEDLINE export. The script uses the latest `pubmed-*.txt`. Summary and abstract files are not read. |
| `data/publications.js` | Catalog the page renders. Curated tags live here: themes, disease tags, ABL members, PIs, departments, PCC programs, collaboration institutions. |
| `data/publications.json` | Same catalog as JSON. |
| `data/derived.js` | Generated. Do not edit by hand. |
| `data/pcc_dashboard.js` | PCC program series and lab list. Lab `pubs` counts are refreshed by the script. Program-by-year series are not. |
| `data/network_dashboard.js` | Top co-author table and US map institution list. Not rewritten by the script. |
| `assets/repo-logos/logos.js` | Repository-tab names and links. Wordmarks are HTML text, not remote images. |

## Adding papers

`build_dashboard.py` does **not** insert new PMIDs. It keeps every paper already in `data/publications.js`, updates `year` from the PubMed `DP` field when that PMID is in the export, and prints any export PMID inside 2015–2026 that is missing from the catalog.

To add papers:

1. Put the new MEDLINE file in `newdata/`.
2. Append records to `data/publications.js` (and `publications.json`) for the new PMIDs. Copy the field shape of an existing record: `n`, `pmid`, `title`, `authors`, `journal`, `year`, `citation`, `pmcid`, `doi`, `themes`, `diseaseThemes`, `abl`, `pis`, `depts`, `pccPrograms`, `pcc`, `collabInsts`.
3. Keep existing PMIDs and their tags. Renumber `n` if the list is re-sorted.
4. Run `python3 build_dashboard.py`.

PI and department names on the network must match the `pis` / `depts` strings already used in the catalog. A new lab node has to be added in `index.html` (`nyuNodes`); the script will then fill its paper count.
