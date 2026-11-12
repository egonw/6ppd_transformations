#!/usr/bin/env python3
"""Write index.html: a list of the annotated articles in literature/.

For every annotated page (made by annotate_jats.py) the list gives the title,
authors, journal, year, DOI and the number of highlighted terms. These are
read from the page itself. The main subjects (6PPD, 6PPD-Q) come from
literature/seeds.tsv when that file is there.

Usage: python3 make_index.py literature/PMC*.html --out index.html
"""

import argparse
import csv
import html
import os
import re
import sys
from pathlib import Path

PAGE_CSS = """
:root{--bg:#fff;--fg:#1c1c1a;--muted:#6b6b66;--line:#dcdcd4;--accent:#1d5fa8;--ont:#e2e4e8;--chem:#c4ecca}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif}
header,main{max-width:892px;margin:0 auto;padding:16px}
header{padding-bottom:0}
h1{font-size:24px;line-height:1.3;margin:0 0 8px}
header p{margin:0 0 8px;color:var(--muted);font-size:14px}
ol{list-style:none;margin:0;padding:0}
li{padding:14px 0;border-top:1px solid var(--line)}
li h2{font-size:17px;line-height:1.4;margin:0 0 2px}
li p{margin:0;font-size:14px;color:var(--muted);overflow-wrap:anywhere}
li p.authors{color:var(--fg)}
a{color:var(--accent)}
li h2 a{text-decoration:none}
li h2 a:hover,li h2 a:focus{text-decoration:underline}
.tag{border-radius:3px;padding:0 4px;color:var(--fg);white-space:nowrap}
.tag.chem{background:var(--chem)}
.tag.ont{background:var(--ont)}
.tag.subject{border:1px solid var(--line);border-radius:10px;padding:0 8px}
"""


def esc(text):
    return html.escape(text, quote=True)


def first(pattern, text):
    match = re.search(pattern, text, re.S)
    return match.group(1).strip() if match else ""


def read_page(path):
    """What the index shows about one annotated page."""
    page = path.read_text(encoding="utf-8")
    head = page.split('<script type="application/json"', 1)[0]
    legend = first(r'<div id="legend">(.*?)</div>', head)
    return {
        "path": path,
        "title": html.unescape(first(r"<title>(.*?)</title>", head)) or path.stem,
        "authors": html.unescape(first(r'<p class="authors">(.*?)</p>', head)),
        "meta": first(r'<p class="meta">(.*?)</p>', head),      # journal, year and links: already HTML
        "doi": html.unescape(first(r'href="https://doi\.org/([^"]+)"', first(r'<p class="meta">(.*?)</p>', head))),
        "chem": first(r'class="ann chem">[^<]*</span>\s*(\d+) in the text', legend),
        "ont": first(r'class="ann ont">[^<]*</span>\s*(\d+) in the text', legend),
    }


def read_subjects(path):
    """DOI (lower case) -> list of main subjects, from seeds.tsv."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as handle:
        return {row["doi"].lower(): [s for s in row["main_subjects"].split(";") if s]
                for row in csv.DictReader(handle, delimiter="\t")}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pages", nargs="*", type=Path, help="annotated HTML pages")
    parser.add_argument("--out", type=Path, default=Path("index.html"))
    parser.add_argument("--seeds", type=Path, default=Path("literature/seeds.tsv"),
                        help="overview written by fetch_seed_articles.py")
    args = parser.parse_args()

    subjects = read_subjects(args.seeds)
    articles = sorted((read_page(p) for p in args.pages), key=lambda a: a["title"].lower())
    out_dir = args.out.resolve().parent
    rows = []
    for a in articles:
        href = Path(os.path.relpath(a["path"].resolve(), out_dir)).as_posix()
        tags = [f'<span class="tag subject">{esc(s)}</span>' for s in subjects.get(a["doi"].lower(), [])]
        if a["chem"]:
            tags.append(f'<span class="tag chem">{a["chem"]} chemical structure{"" if a["chem"] == "1" else "s"}</span>')
        if a["ont"]:
            tags.append(f'<span class="tag ont">{a["ont"]} other ontology term{"" if a["ont"] == "1" else "s"}</span>')
        rows.append(
            f'<li><h2><a href="{esc(href)}">{esc(a["title"])}</a></h2>'
            + (f'<p class="authors">{esc(a["authors"])}</p>' if a["authors"] else "")
            + (f'<p>{a["meta"]}</p>' if a["meta"] else "")
            + (f'<p>{" ".join(tags)}</p>' if tags else "")
            + "</li>")
    listing = "<ol>\n" + "\n".join(rows) + "\n</ol>" if rows else \
        "<p>No annotated articles yet: run <code>make annotate</code>.</p>"
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>6PPD and 6PPD-quinone: annotated articles</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<header>
<h1>6PPD and 6PPD-quinone: annotated articles</h1>
<p>{len(articles)} seed article{"" if len(articles) == 1 else "s"} from Wikidata with full text in Europe PMC.
In each page the <span class="tag chem">chemical structures in ChEBI</span> and the
<span class="tag ont">other ontology terms on ExpoPortal</span> are highlighted.</p>
</header>
<main>
{listing}
</main>
</body>
</html>
"""
    args.out.write_text(page, encoding="utf-8")
    print(f"{len(articles)} articles listed in {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
