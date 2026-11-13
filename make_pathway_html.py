#!/usr/bin/env python3
"""Make a web page of a GPML pathway, with popups for nodes and interactions.

The picture is the SVG that MINERVA draws from the pathway (make svg). It
uses the coordinates of the GPML, so the page puts an invisible layer with
one shape for every node and line of the GPML on top of it. Hovering,
focusing or clicking a shape opens a popup, like in the article pages:

  metabolite   structure (from the SMILES or CXSMILES comment), notes, link to
               Wikidata, the species it was measured in, and the sentences
               that support the conversions that form it
  interaction  the conversion, its enzymes, the species it is found in, its
               notes, the supporting sentences and the references
  enzyme       the conversions it catalyses, with their sentences

What the GPML has to give:

  <Comment Source="Species">     on an interaction, one for every species
  <Comment Source="Evidence">ID  on an interaction, one for every sentence;
                                 the ID is a line of evidence.tsv
  <BiopaxRef>                    the articles (DOI) of an interaction

A supporting sentence is quoted and links to the place where it is marked in
the annotated article (literature/<PMCID>.html#ID). A reference links to the
annotated article when there is one, and to the DOI otherwise.

Usage: python3 make_pathway_html.py 6ppd.gpml --svg 6ppd.svg --evidence evidence.tsv
                                    [--seeds literature/seeds.tsv] [--out 6ppd.html]
"""

import argparse
import csv
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GPML = "{http://pathvisio.org/GPML/2013a}"
BP = "{http://www.biopax.org/release/biopax-level3.owl#}"
RDF_ID = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}id"
DEPICT = ("https://cdkdepict.toolforge.org/depict/cow/svg?abbr=off&hdisp=bridgehead"
          "&showtitle=false&zoom=1.6&annotate=none&smi=")
XREF_URL = {"Wikidata": "https://www.wikidata.org/wiki/{}",
            "ChEBI": "https://www.ebi.ac.uk/chebi/searchId.do?chebiId={}"}


def esc(text):
    return html.escape(text, quote=True)


def comments(element):
    """The comments of a GPML element: {source: [text]}; no source is ''."""
    found = {}
    for comment in element.findall(GPML + "Comment"):
        text = " ".join((comment.text or "").split())
        if text:
            found.setdefault(comment.get("Source") or "", []).append(text)
    return found


def read_tsv(path, key):
    """Lines of a TSV file (comment lines start with #) as {key column: row}."""
    if not path or not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    return {row[key]: row for row in csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE)}


def read_gpml(path):
    root = ET.parse(path).getroot()
    nodes, lines, anchors = {}, [], {}
    for el in root.findall(GPML + "DataNode"):
        g = el.find(GPML + "Graphics")
        xref = el.find(GPML + "Xref")
        nodes[el.get("GraphId")] = {
            "id": el.get("GraphId"), "label": el.get("TextLabel") or "", "type": el.get("Type") or "",
            "box": [float(g.get(a)) for a in ("CenterX", "CenterY", "Width", "Height")],
            "comments": comments(el),
            "xref": (xref.get("Database") or "", xref.get("ID") or "") if xref is not None else ("", ""),
        }
    for n, el in enumerate(root.findall(GPML + "Interaction")):
        g = el.find(GPML + "Graphics")
        points = g.findall(GPML + "Point")
        line = {
            "id": el.get("GraphId") or f"line{n}",
            "points": [(float(p.get("X")), float(p.get("Y"))) for p in points],
            "start": points[0].get("GraphRef"), "end": points[-1].get("GraphRef"),
            "arrow": points[-1].get("ArrowHead") or "",
            "comments": comments(el),
            "refs": [r.text.strip() for r in el.findall(GPML + "BiopaxRef") if r.text],
        }
        for anchor in g.findall(GPML + "Anchor"):
            anchors[anchor.get("GraphId")] = line
        lines.append(line)
    dois = {}
    for ref in root.iter(BP + "PublicationXref"):
        ident, db = ref.find(BP + "ID"), ref.find(BP + "DB")
        if ident is not None and ident.text:
            dois[ref.get(RDF_ID)] = (ident.text.strip(), (db.text or "").strip() if db is not None else "")
    return root.get("Name") or path.stem, nodes, lines, anchors, dois


def build(nodes, lines, anchors, dois, evidence, seeds, literature):
    """The data of the popups: {id of node or line: {...}}."""
    pages = {doi.lower(): row["file"] for doi, row in seeds.items() if row.get("file")}

    def article(pmcid):
        page = literature / f"{pmcid}.html"
        return page.as_posix() if page.exists() else ""

    def sentences(ids):
        out = []
        for ident in ids:
            row = evidence.get(ident)
            if row is None:
                sys.exit(f"evidence {ident} is in the GPML but not in the evidence file")
            page = article(row["pmcid"])
            out.append({"id": ident, "quote": row["quote"], "pmcid": row["pmcid"],
                        "section": row.get("section", ""),
                        "url": f"{page}#{ident}" if page else f"https://doi.org/{row['doi']}"})
        return out

    def references(ref_ids):
        out = []
        for ref in ref_ids:
            doi, db = dois.get(ref, (ref, ""))
            file = pages.get(doi.lower(), "")
            page = article(Path(file).stem) if file else ""
            out.append({"doi": doi, "pmcid": Path(file).stem if page else "",
                        "url": page or f"https://doi.org/{doi}"})
        return out

    def name(ident):
        return nodes[ident]["label"] if ident in nodes else "?"

    def merge(target, source):
        for item in source:
            if item not in target:
                target.append(item)

    # conversions: lines from node to node. Other lines hang on an anchor.
    data = {}
    conversions = [l for l in lines if l["start"] in nodes and l["end"] in nodes]
    for line in conversions:
        data[line["id"]] = {
            "title": f"{name(line['start'])} → {name(line['end'])}", "kind": "conversion",
            "species": line["comments"].get("Species", []), "notes": line["comments"].get("", []),
            "sentences": sentences(line["comments"].get("Evidence", [])),
            "refs": references(line["refs"]), "enzymes": [], "with": [],
        }
    for line in lines:
        if line in conversions:
            continue
        parent = anchors.get(line["end"]) or anchors.get(line["start"])
        if parent is None or parent["id"] not in data:
            data[line["id"]] = {"title": "interaction", "kind": "", "species": [], "notes": [],
                                "sentences": [], "refs": []}
            continue
        main = data[parent["id"]]
        own = sentences(line["comments"].get("Evidence", []))
        if line["arrow"] == "mim-catalysis":
            enzyme = name(line["start"])
            main["enzymes"].append(enzyme)
            data[line["id"]] = {
                "title": f"{enzyme} catalyses {main['title']}", "kind": "catalysis",
                "species": main["species"], "notes": line["comments"].get("", []),
                "sentences": own, "refs": references(line["refs"]) or main["refs"],
            }
            node = data.setdefault(line["start"], {"catalyses": [], "species": [], "sentences": [], "refs": []})
            node["catalyses"].append(main["title"])
            merge(node["species"], main["species"])
            merge(node["sentences"], own)
            merge(node["refs"], main["refs"])
        else:   # a second substrate or product of the conversion
            other = line["start"] if line["start"] in nodes else line["end"]
            role = "with" if line["start"] in nodes else "also gives"
            main["with"].append(f"{role} {name(other)}")
            data[line["id"]] = dict(main, part_of=True)

    for ident, node in nodes.items():
        entry = data.setdefault(ident, {"species": [], "sentences": [], "refs": []})
        is_enzyme = "catalyses" in entry
        formed = [l for l in conversions if l["end"] == ident]
        for line in formed:
            merge(entry["species"], data[line["id"]]["species"])
            merge(entry["sentences"], data[line["id"]]["sentences"])
            merge(entry["refs"], data[line["id"]]["refs"])
        structure = node["comments"].get("CXSMILES") or node["comments"].get("SMILES") or []
        database, xref = node["xref"]
        entry.update({
            "title": node["label"],
            "kind": "enzyme" if is_enzyme else node["type"].lower() or "node",
            "notes": node["comments"].get("", []),
            "smiles": structure[0] if structure else "",
            "markush": bool(node["comments"].get("CXSMILES")),
            "formed": [data[l["id"]]["title"] for l in formed],
            "xref": {"db": database, "id": xref, "url": XREF_URL.get(database, "").format(xref)}
                    if database and xref else None,
            "node": True,
        })
    return data


def overlay(nodes, lines, data):
    """The invisible shapes on top of the picture."""
    shapes = []
    for line in lines:
        (x1, y1), (x2, y2) = line["points"][0], line["points"][-1]
        shapes.append(f'<line class="hit" data-id="{esc(line["id"])}" tabindex="0" '
                      f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"><title>{esc(data[line["id"]]["title"])}</title></line>')
    for ident, node in nodes.items():
        cx, cy, w, h = node["box"]
        shapes.append(f'<rect class="hit" id="{esc(ident)}" data-id="{esc(ident)}" tabindex="0" '
                      f'x="{cx - w / 2}" y="{cy - h / 2}" width="{w}" height="{h}" rx="{h / 2}">'
                      f'<title>{esc(node["label"])}</title></rect>')
    return "\n".join(shapes)


PAGE_CSS = """
:root{--bg:#fff;--fg:#1c1c1a;--muted:#6b6b66;--line:#dcdcd4;--panel:#fff;--accent:#1d5fa8;--hl:#fff4e6;
--ev:rgba(255,214,0,.42);--chem:#c4ecca}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:6px 16px;align-items:baseline;flex-wrap:wrap}
header h1{font-size:18px;margin:0}header span{color:var(--muted);font-size:14px}
header a{color:var(--accent)}
#scroll{overflow:auto;padding:8px 16px}
#map{position:relative}
#map svg{display:block;position:absolute;top:0;left:0}
#map .hit{fill:transparent;stroke:transparent;cursor:help;outline:none}
#map line.hit{stroke-width:14;pointer-events:stroke;stroke-linecap:round}
#map rect.hit{stroke-width:3;pointer-events:all}
#map rect.hit:hover,#map rect.hit:focus,#map rect.hit.active{fill:rgba(255,170,0,.18);stroke:rgba(255,150,0,.9)}
#map line.hit:hover,#map line.hit:focus,#map line.hit.active{stroke:rgba(255,170,0,.45)}
#pop{position:absolute;z-index:10;width:max-content;max-width:min(480px,calc(100vw - 16px));max-height:440px;overflow:auto;
background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;
box-shadow:0 6px 24px rgba(0,0,0,.25);font-size:14px;line-height:1.5}
#pop[hidden]{display:none}
#pop h3{margin:0;font-size:15px}
#pop .kind{color:var(--muted);font-size:12px}
#pop h4{margin:10px 0 2px;font-size:13px}
#pop p{margin:4px 0}
#pop .note{color:var(--muted);font-size:13px}
#pop .struct{display:block;height:150px;width:100%;object-fit:scale-down;margin:6px 0 2px;background:#fff;border:1px solid var(--line);border-radius:6px}
#pop .smiles{color:var(--muted);font:11px ui-monospace,monospace;overflow-wrap:anywhere}
#pop .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:2px}
#pop .chips span,#pop .chips a{border:1px solid var(--line);border-radius:10px;padding:0 8px;font-size:12px;text-decoration:none}
#pop .chips .species{font-style:italic}
#pop .chips a{color:var(--accent)}
#pop .chips a:hover,#pop .chips a:focus{background:var(--hl)}
#pop blockquote{margin:6px 0;padding:4px 8px;border-left:3px solid #e6c200;background:var(--ev);border-radius:0 4px 4px 0}
#pop blockquote a{display:block;margin-top:2px;font-size:12px;color:var(--accent);text-decoration:none}
#pop blockquote a:hover,#pop blockquote a:focus{text-decoration:underline}
#pop .none{color:var(--muted);font-size:13px}
"""

PAGE_JS = r"""
const DATA = JSON.parse(document.getElementById('pathway-data').textContent);
const DEPICT = document.body.dataset.depict;
const map = document.getElementById('map'), pop = document.getElementById('pop');
// the page shows its own popup, so drop the plain SVG tooltips
map.querySelectorAll('.hit > title').forEach(t => t.remove());
let hideT, pinned = null, shown = null;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text; return e; };
const link = (cls, text, url) => { const a = el('a', cls, text); a.href = url;
  if (/^https?:/.test(url)) { a.target = '_blank'; a.rel = 'noopener'; } return a; };
function show(shape){
  clearTimeout(hideT);
  const d = DATA[shape.dataset.id]; if (!d) return;
  if (shown) shown.classList.remove('active');
  shown = shape; shape.classList.add('active');
  pop.replaceChildren(el('h3', '', d.title), el('div', 'kind', d.markush ? d.kind + ', Markush structure' : d.kind));
  if (d.smiles) {            // 2D structure, drawn by CDK Depict from the (CX)SMILES
    const s = el('img', 'struct'); s.src = DEPICT + encodeURIComponent(d.smiles);
    s.alt = '2D structure of ' + d.title; pop.append(s, el('div', 'smiles', d.smiles));
  }
  if (d.xref) { const c = el('div', 'chips'); c.append(link('', d.xref.db + ' ' + d.xref.id, d.xref.url)); pop.append(c); }
  for (const t of d.with || []) pop.append(el('p', '', t));
  if (d.enzymes && d.enzymes.length) pop.append(el('p', '', 'Catalysed by ' + d.enzymes.join(', ')));
  if (d.catalyses) for (const t of d.catalyses) pop.append(el('p', '', 'Catalyses ' + t));
  for (const t of d.notes || []) pop.append(el('p', 'note', t));
  if (d.formed && d.formed.length) pop.append(el('p', 'note', 'Formed by: ' + d.formed.join('; ')));
  const metabolite = d.node && !d.catalyses;
  if (d.species.length) {
    pop.append(el('h4', '', metabolite ? 'Measured in' : 'Found in'));
    const c = el('div', 'chips'); for (const s of d.species) c.append(el('span', 'species', s)); pop.append(c);
  }
  if (d.sentences.length) {
    pop.append(el('h4', '', 'Supporting sentence' + (d.sentences.length > 1 ? 's' : '')));
    for (const s of d.sentences) {
      const q = el('blockquote', '', s.quote);
      q.append(link('', s.pmcid + (s.section ? ', ' + s.section : '') + ' ↗', s.url));
      pop.append(q);
    }
  }
  if (d.refs.length) {
    pop.append(el('h4', '', 'Reference' + (d.refs.length > 1 ? 's' : '')));
    const c = el('div', 'chips');
    for (const r of d.refs) c.append(link('', r.pmcid ? r.pmcid + ' (annotated article)' : 'doi:' + r.doi, r.url));
    pop.append(c);
  }
  if (!d.species.length && !d.sentences.length && !d.refs.length && !d.smiles && !(d.notes || []).length)
    pop.append(el('p', 'none', 'No further information in the pathway.'));
  pop.hidden = false; pop.scrollTop = 0;
  const r = shape.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  const x = Math.min(Math.max(8, r.left), innerWidth - w - 8);
  let y = r.bottom + 6;
  if (y + h + 8 > innerHeight && r.top > h + 14) y = r.top - h - 6;
  pop.style.left = (x + scrollX) + 'px'; pop.style.top = (y + scrollY) + 'px';
}
function hideNow(){ pop.hidden = true; pinned = null; if (shown) shown.classList.remove('active'); shown = null; }
const hide = () => { if (!pinned) hideT = setTimeout(hideNow, 250); };
const target = e => e.target.closest ? e.target.closest('#map .hit') : null;
map.addEventListener('mouseover', e => { const s = target(e); if (s && !pinned) show(s); });
map.addEventListener('mouseout', e => { if (target(e)) hide(); });
map.addEventListener('focusin', e => { const s = target(e); if (s && !pinned) show(s); });
map.addEventListener('focusout', hide);
// a click keeps the popup open
map.addEventListener('click', e => { const s = target(e); if (!s) return; hideNow(); show(s); pinned = s; });
pop.addEventListener('mouseenter', () => clearTimeout(hideT));
pop.addEventListener('mouseleave', hide);
document.addEventListener('click', e => { if (!e.target.closest('#pop') && !target(e)) hideNow(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') hideNow(); });
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("gpml", type=Path)
    parser.add_argument("--svg", type=Path, help="picture of the pathway (default: next to the GPML)")
    parser.add_argument("--evidence", type=Path, default=Path("evidence.tsv"),
                        help="TSV file with the supporting sentences")
    parser.add_argument("--seeds", type=Path, default=Path("literature/seeds.tsv"),
                        help="overview of the articles (DOI and file), to link the references")
    parser.add_argument("--index", default="index.html", help="page with the list of articles")
    parser.add_argument("--out", type=Path, help="HTML file to write (default: next to the GPML)")
    args = parser.parse_args()
    svg_path = args.svg or args.gpml.with_suffix(".svg")
    out_path = args.out or args.gpml.with_suffix(".html")

    title, nodes, lines, anchors, dois = read_gpml(args.gpml)
    evidence = read_tsv(args.evidence, "id")
    seeds = read_tsv(args.seeds, "doi")
    data = build(nodes, lines, anchors, dois, evidence, seeds, args.seeds.parent)

    svg = svg_path.read_text(encoding="utf-8")
    svg = svg[svg.index("<svg"):]                       # no XML declaration and DOCTYPE
    box = re.search(r'viewBox="([\d.\s-]+)"', svg)
    if not box:
        sys.exit(f"{svg_path}: no viewBox")
    _, _, width, height = (float(v) for v in box.group(1).split())
    svg = svg.replace("<svg ", f'<svg width="{width:g}" height="{height:g}" role="img" '
                               f'aria-label="{esc(title)}" ', 1)
    n_sentences = len({s["id"] for d in data.values() for s in d["sentences"]})
    n_conversions = sum(1 for d in data.values() if d.get("kind") == "conversion" and not d.get("part_of"))
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{PAGE_CSS}</style>
</head>
<body data-depict="{esc(DEPICT)}">
<header><h1>{esc(title)}</h1>
<span><a href="{esc(args.index)}#pathways">All pathways</a></span>
<span>{n_conversions} conversions, supported by {n_sentences} sentences in the
<a href="{esc(args.index)}#literature">annotated articles</a>.
Hover or focus a node or a line to see its structure, species and supporting sentences; click to keep the popup open.</span></header>
<div id="scroll">
<div id="map" style="width:{width:g}px;height:{height:g}px">
{svg}
<svg width="{width:g}" height="{height:g}" viewBox="0 0 {width:g} {height:g}" xmlns="http://www.w3.org/2000/svg">
{overlay(nodes, lines, data)}
</svg>
</div>
</div>
<div id="pop" role="tooltip" hidden></div>
<script type="application/json" id="pathway-data">{payload}</script>
<script>{PAGE_JS}</script>
</body>
</html>
"""
    out_path.write_text(page, encoding="utf-8")
    print(f"{args.gpml}: {len(nodes)} nodes, {n_conversions} conversions, "
          f"{n_sentences} supporting sentences -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
