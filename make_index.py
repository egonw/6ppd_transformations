#!/usr/bin/env python3
"""Write index.html: the annotated articles and the pathways, in two tabs.

Literature  for every annotated page (made by annotate_jats.py): title,
            authors, journal, year, DOI and the number of highlighted terms.
            These are read from the page itself. The main subjects (6PPD,
            6PPD-Q) come from literature/seeds.tsv when that file is there.
            An article in hidden_articles.tsv keeps its page but is not
            listed. Below the list: the seed articles of seeds.tsv that have
            no page, because Europe PMC has no (open access) full text.
Pathways    for every GPML file: its name and a short summary (metabolites,
            enzymes, conversions, species, supporting sentences, references),
            read from the GPML. Links to the pathway page (made by
            make_pathway_html.py) and to the SBML and SVG files next to the
            GPML, when they are there.

About       a column on the right with the resources that were used
            (resources.tsv) and the ELIXIR logo, which links to the
            BioHackathon project. The logo is the first file images/elixir-logo.*
            that is there (svg, png, jpg or webp); without one, the link is text.

Usage: python3 make_index.py literature/PMC*.html --gpml *.gpml --out index.html
"""

import argparse
import csv
import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GPML = "{http://pathvisio.org/GPML/2013a}"
BP = "{http://www.biopax.org/release/biopax-level3.owl#}"

PROJECT_URL = "https://github.com/elixir-europe/biohackathon-projects-2026/blob/main/19.md"
PROJECT_NAME = "ELIXIR BioHackathon Europe 2026, project 19"

PAGE_CSS = """
:root{--bg:#fff;--fg:#1c1c1a;--muted:#6b6b66;--line:#dcdcd4;--accent:#1d5fa8;--ont:#e2e4e8;--chem:#c4ecca;--ev:rgba(255,214,0,.42)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif}
.layout{max-width:1240px;margin:0 auto;padding:16px;display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:0 40px}
header{grid-column:1/-1}
main{min-width:0;padding-top:16px}
aside{padding-top:16px;font-size:14px}
aside h2{font-size:17px;margin:0 0 8px}
aside h3{font-size:13px;margin:16px 0 4px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
aside ul{list-style:none;margin:0;padding:0}
aside ul li{padding:3px 0;border:0;color:var(--muted);line-height:1.4}
aside ul li a{font-weight:600}
aside .logo{display:block;margin:0 0 6px}
aside .logo img{display:block;max-width:150px;max-height:90px;height:auto}
aside .project{margin:0 0 4px;color:var(--muted)}
@media (max-width:900px){.layout{grid-template-columns:minmax(0,1fr)}aside{border-top:1px solid var(--line);margin-top:24px}}
h1{font-size:24px;line-height:1.3;margin:0 0 12px}
[role=tablist]{display:flex;gap:4px;border-bottom:1px solid var(--line)}
[role=tab]{font:inherit;color:var(--muted);background:none;border:0;border-bottom:3px solid transparent;
padding:6px 14px;margin-bottom:-1px;cursor:pointer}
[role=tab]:hover{color:var(--fg)}
[role=tab][aria-selected=true]{color:var(--fg);font-weight:600;border-bottom-color:var(--accent)}
[role=tab] .n{font-weight:400;color:var(--muted);font-size:13px;margin-left:4px}
[role=tabpanel][hidden]{display:none}
.intro{margin:0 0 12px;color:var(--muted);font-size:14px}
ol{list-style:none;margin:0;padding:0}
li{padding:14px 0;border-top:1px solid var(--line)}
li:first-child{border-top:0;padding-top:0}
li h2{font-size:17px;line-height:1.4;margin:0 0 2px}
li p{margin:0;font-size:14px;color:var(--muted);overflow-wrap:anywhere}
li p.authors,li p.summary{color:var(--fg)}
li p+p.tags{margin-top:4px}
a{color:var(--accent)}
li h2 a{text-decoration:none}
li h2 a:hover,li h2 a:focus{text-decoration:underline}
.tag{border-radius:3px;padding:0 4px;color:var(--fg);white-space:nowrap}
.tag.chem{background:var(--chem)}
.tag.ont{background:var(--ont)}
.tag.ev{background:var(--ev)}
.tag.subject,.tag.species{border:1px solid var(--line);border-radius:10px;padding:0 8px}
.tag.species{font-style:italic}
.notin{margin-top:32px;padding-top:16px;border-top:2px solid var(--line)}
.notin h2{font-size:17px;margin:0 0 4px}
.notin h3{font-size:14px;margin:14px 0 4px}
.notin .n{font-weight:400;color:var(--muted);font-size:13px}
ul.missing{list-style:none;margin:0;padding:0}
ul.missing li{padding:5px 0;border:0;font-size:14px;color:var(--muted);line-height:1.45;overflow-wrap:anywhere}
ul.missing .t{color:var(--fg)}
.thumb{display:block;margin:8px 0 0;width:100%;height:auto;border:1px solid var(--line);border-radius:8px;background:#fff}
"""

PAGE_JS = """
const tabs = [...document.querySelectorAll('[role=tab]')];
function select(tab, focus){
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute('aria-selected', on); t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
  }
  if (focus) tab.focus();
}
function fromHash(){ const t = tabs.find(t => '#' + t.dataset.name === location.hash); if (t) select(t); }
for (const t of tabs) t.addEventListener('click', () => { select(t); history.replaceState(null, '', '#' + t.dataset.name); });
document.querySelector('[role=tablist]').addEventListener('keydown', e => {
  const i = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
  const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
  if (step) { const t = tabs[(i + step + tabs.length) % tabs.length]; select(t, true); history.replaceState(null, '', '#' + t.dataset.name); }
});
addEventListener('hashchange', fromHash); fromHash();
"""


def esc(text):
    return html.escape(text, quote=True)


def plural(n, word, many=None):
    return f"{n} {word if n == 1 else many or word + 's'}"


def first(pattern, text):
    match = re.search(pattern, text, re.S)
    return match.group(1).strip() if match else ""


def read_page(path):
    """What the index shows about one annotated page."""
    page = path.read_text(encoding="utf-8")
    head = page.split('<script type="application/json"', 1)[0]
    legend = first(r'<div id="legend">(.*?)</div>', head)
    meta = first(r'<p class="meta">(.*?)</p>', head)
    return {
        "path": path,
        "title": html.unescape(first(r"<title>(.*?)</title>", head)) or path.stem,
        "authors": html.unescape(first(r'<p class="authors">(.*?)</p>', head)),
        "meta": meta,                                           # journal, year and links: already HTML
        "doi": html.unescape(first(r'href="https://doi\.org/([^"]+)"', meta)),
        "chem": first(r'class="ann chem">[^<]*</span>\s*(\d+) in the text', legend),
        "ont": first(r'class="ann ont">[^<]*</span>\s*(\d+) in the text', legend),
        "ev": first(r'<mark class="ev">.*?</mark>\s*(\d+)', legend),
    }


def read_tsv(path):
    """The lines of a TSV file as dicts; lines that start with # are comments."""
    if not path or not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE))


# status in seeds.tsv -> why the article has no page
NO_FULL_TEXT = {
    "no full text": "In Europe PMC, but without full text",
    "not open access": "Full text in Europe PMC, but not open access",
    "not in Europe PMC": "Not in Europe PMC",
}


def missing_panel(seeds):
    """The seed articles that have no page, grouped by the reason."""
    parts, total = [], 0
    for status, reason in NO_FULL_TEXT.items():
        rows = sorted((r for r in seeds if r["status"] == status),
                      key=lambda r: (r.get("title") or r["doi"]).lower())
        if not rows:
            continue
        total += len(rows)
        items = []
        for r in rows:
            title = esc(r.get("title") or r["doi"])
            links = [f'<a href="https://doi.org/{esc(r["doi"])}" rel="noopener">doi:{esc(r["doi"])}</a>']
            if r.get("pmid"):
                links.append(f'<a href="https://europepmc.org/article/MED/{esc(r["pmid"])}" rel="noopener">Europe PMC</a>')
            if r.get("wikidata"):
                links.append(f'<a href="https://www.wikidata.org/wiki/{esc(r["wikidata"])}" rel="noopener">Wikidata</a>')
            tags = " ".join(f'<span class="tag subject">{esc(s)}</span>'
                            for s in r["main_subjects"].split(";") if s)
            items.append(f'<li><span class="t">{title}</span> {tags}<br>{" · ".join(links)}</li>')
        parts.append(f'<h3>{esc(reason)} <span class="n">{len(rows)}</span></h3>'
                     f'<ul class="missing">{"".join(items)}</ul>')
    if not parts:
        return ""
    return (f'<section class="notin"><h2>Seed articles without a page <span class="n">{total}</span></h2>'
            '<p class="intro">These articles are in the seeds, but are not annotated here because '
            'Europe PMC does not offer their full text as open access XML.</p>'
            + "".join(parts) + "</section>")


def read_gpml(path):
    """Name and summary of one pathway."""
    root = ET.parse(path).getroot()
    nodes = {n.get("GraphId"): n for n in root.findall(GPML + "DataNode")}
    metabolites = {n.get("TextLabel") for n in nodes.values() if n.get("Type") == "Metabolite"}
    enzymes = {n.get("TextLabel") for n in nodes.values() if n.get("Type") != "Metabolite"}
    conversions, species, evidence = 0, [], set()
    for line in root.findall(GPML + "Interaction"):
        points = line.find(GPML + "Graphics").findall(GPML + "Point")
        if points[0].get("GraphRef") in nodes and points[-1].get("GraphRef") in nodes:
            conversions += 1
        for comment in line.findall(GPML + "Comment"):
            text = " ".join((comment.text or "").split())
            if comment.get("Source") == "Species" and text not in species:
                species.append(text)
            elif comment.get("Source") == "Evidence":
                evidence.add(text)
    return {
        "path": path, "name": root.get("Name") or path.stem,
        "metabolites": len(metabolites), "enzymes": sorted(enzymes), "conversions": conversions,
        "species": species, "evidence": len(evidence),
        "references": len(list(root.iter(BP + "PublicationXref"))),
    }


def literature_panel(articles, subjects, href, missing=""):
    rows = []
    for a in articles:
        tags = [f'<span class="tag subject">{esc(s)}</span>' for s in subjects.get(a["doi"].lower(), [])]
        if a["chem"]:
            tags.append(f'<span class="tag chem">{plural(int(a["chem"]), "chemical structure")}</span>')
        if a["ont"]:
            tags.append(f'<span class="tag ont">{plural(int(a["ont"]), "other ontology term")}</span>')
        if a["ev"]:
            tags.append(f'<span class="tag ev">{plural(int(a["ev"]), "supporting sentence")}</span>')
        rows.append(
            f'<li><h2><a href="{esc(href(a["path"]))}">{esc(a["title"])}</a></h2>'
            + (f'<p class="authors">{esc(a["authors"])}</p>' if a["authors"] else "")
            + (f'<p>{a["meta"]}</p>' if a["meta"] else "")
            + (f'<p class="tags">{" ".join(tags)}</p>' if tags else "")
            + "</li>")
    intro = (f'<p class="intro">{plural(len(articles), "article")} with full text in Europe PMC: '
             'seed articles from Wikidata and articles added by hand. In each page the '
             '<span class="tag chem">chemical structures in ChEBI</span>, the '
             '<span class="tag ont">other ontology terms on ExpoPortal</span> and the '
             '<span class="tag ev">sentences that support a pathway</span> are highlighted.</p>')
    if not rows:
        return intro + "<p>No annotated articles yet: run <code>make annotate</code>.</p>" + missing
    return intro + "<ol>\n" + "\n".join(rows) + "\n</ol>" + missing


def pathways_panel(pathways, href):
    rows = []
    for p in pathways:
        page, svg = p["path"].with_suffix(".html"), p["path"].with_suffix(".svg")
        title = esc(p["name"])
        if page.exists():
            title = f'<a href="{esc(href(page))}">{title}</a>'
        summary = [plural(p["metabolites"], "metabolite"), plural(p["conversions"], "conversion")]
        if p["enzymes"]:
            summary.append(f'{plural(len(p["enzymes"]), "enzyme family", "enzyme families")} '
                           f'({esc(", ".join(p["enzymes"]))})')
        summary.append(plural(p["references"], "reference"))
        tags = [f'<span class="tag species">{esc(s)}</span>' for s in p["species"]]
        if p["evidence"]:
            tags.insert(0, f'<span class="tag ev">{plural(p["evidence"], "supporting sentence")}</span>')
        files = [f'<a href="{esc(href(f))}">{label}</a>'
                 for f, label in ((p["path"], "GPML"), (p["path"].with_suffix(".sbml"), "SBML"), (svg, "SVG"))
                 if f.exists()]
        thumb = ""
        if svg.exists():
            link = esc(href(page if page.exists() else svg))
            thumb = (f'<a href="{link}"><img class="thumb" src="{esc(href(svg))}" '
                     f'alt="Picture of the pathway {esc(p["name"])}"></a>')
        rows.append(f'<li><h2>{title}</h2>'
                    f'<p class="summary">{", ".join(summary)}.</p>'
                    + (f'<p class="tags">{" ".join(tags)}</p>' if tags else "")
                    + f'<p>Files: {" · ".join(files)}</p>{thumb}</li>')
    intro = (f'<p class="intro">{plural(len(pathways), "pathway")}, one for every GPML file. '
             'The pathway page shows the structures, the species and the supporting sentences '
             'from the articles.</p>')
    if not rows:
        return intro + "<p>No GPML files found.</p>"
    return intro + "<ol>\n" + "\n".join(rows) + "\n</ol>"


def about_panel(resources, logo, href):
    """The column on the right: the project, its logo and the resources."""
    if logo:
        project = (f'<a class="logo" href="{PROJECT_URL}"><img src="{esc(href(logo))}" alt="ELIXIR"></a>'
                   f'<p class="project">Made in <a href="{PROJECT_URL}">{PROJECT_NAME}</a>.</p>')
    else:
        project = f'<p class="project">Made in <a href="{PROJECT_URL}">{PROJECT_NAME}</a>.</p>'
    groups = {}
    for row in resources:
        groups.setdefault(row["group"], []).append(row)
    parts = ["<h2>About</h2>", project]
    for group, rows in groups.items():
        items = "".join(f'<li><a href="{esc(r["url"])}">{esc(r["name"])}</a>: {esc(r["used_for"])}</li>'
                        for r in rows)
        parts.append(f"<h3>{esc(group)}</h3><ul>{items}</ul>")
    return "\n".join(parts)


def read_resources(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pages", nargs="*", type=Path, help="annotated HTML pages")
    parser.add_argument("--gpml", nargs="*", default=[], type=Path, help="GPML files of the pathways")
    parser.add_argument("--hidden", type=Path, default=Path("hidden_articles.tsv"),
                        help="TSV file with the articles (pmcid) that are not listed")
    parser.add_argument("--resources", type=Path, default=Path("resources.tsv"),
                        help="TSV file with the resources for the About column")
    parser.add_argument("--logo", type=Path,
                        help="ELIXIR logo (default: the first images/elixir-logo.* that is there)")
    parser.add_argument("--out", type=Path, default=Path("index.html"))
    parser.add_argument("--seeds", type=Path, default=Path("literature/seeds.tsv"),
                        help="overview written by fetch_seed_articles.py")
    args = parser.parse_args()

    out_dir = args.out.resolve().parent

    def href(path):
        return Path(os.path.relpath(path.resolve(), out_dir)).as_posix()

    logo = args.logo
    if logo is None:
        found = [p for ext in ("svg", "png", "jpg", "webp")
                 for p in [out_dir / "images" / f"elixir-logo.{ext}"] if p.exists()]
        logo = found[0] if found else None
    elif not logo.exists():
        sys.exit(f"{logo} not found")
    hidden = {row["pmcid"] for row in read_tsv(args.hidden)}
    seeds = read_tsv(args.seeds)
    subjects = {row["doi"].lower(): [s for s in row["main_subjects"].split(";") if s] for row in seeds}
    articles = sorted((read_page(p) for p in args.pages if p.stem not in hidden),
                      key=lambda a: a["title"].lower())
    # one pathway for every GPML file
    pathways = sorted((read_gpml(p) for p in {p.resolve(): p for p in args.gpml}.values()),
                      key=lambda p: p["name"].lower())
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>6PPD and 6PPD-quinone: literature and pathways</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="layout">
<header>
<h1>6PPD and 6PPD-quinone: literature and pathways</h1>
<div role="tablist" aria-label="Content">
<button role="tab" id="tab-literature" data-name="literature" aria-controls="literature" aria-selected="true">Literature<span class="n">{len(articles)}</span></button>
<button role="tab" id="tab-pathways" data-name="pathways" aria-controls="pathways" aria-selected="false" tabindex="-1">Pathways<span class="n">{len(pathways)}</span></button>
</div>
</header>
<main>
<section role="tabpanel" id="literature" aria-labelledby="tab-literature">
{literature_panel(articles, subjects, href, missing_panel(seeds))}
</section>
<section role="tabpanel" id="pathways" aria-labelledby="tab-pathways" hidden>
{pathways_panel(pathways, href)}
</section>
</main>
<aside aria-label="About">
{about_panel(read_resources(args.resources), logo, href)}
</aside>
</div>
<script>{PAGE_JS}</script>
</body>
</html>
"""
    args.out.write_text(page, encoding="utf-8")
    if hidden:
        print(f"not listed: {', '.join(sorted(hidden))}", file=sys.stderr)
    print(f"{plural(len(articles), 'article')} and {plural(len(pathways), 'pathway')} "
          f"listed in {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
