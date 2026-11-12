#!/usr/bin/env python3
"""Turn a JATS XML article into an HTML page with annotated terms.

Two kinds of terms are highlighted in the text:

  green  a chemical structure in ChEBI: the text is the label or exact synonym
         of a ChEBI term that has a structure, or it is a chemical name (found
         by Oscar, see ner_chemicals.groovy) that OPSIN turned into a structure
         with the same InChIKey as a ChEBI term
  grey   the text is the label or exact synonym of another term in one of the
         ontologies on ExpoPortal

Hovering, focusing or clicking a term opens a popup with the matching
ontology terms, like the one of the exposome map in background/ExpoPortal.

Terms can be added by hand in a TSV file (--local, see local_terms.tsv), for
names and structures that ExpoPortal does not have.

The ontology terms come from the local cache made by cache_ontology_labels.py,
so this script does not use the network and several can run at the same time.

Usage: python3 annotate_jats.py article.xml --cache cache/ontology_labels.sqlite
                                [--chem article.chem.tsv] [--out article.html]
                                [--exclude NCIT,SWEET] [--local local_terms.tsv]
"""

import argparse
import csv
import html
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from cache_ontology_labels import MAX_LABEL, same_length_normalize

CHEBI_IRI = "http://purl.obolibrary.org/obo/CHEBI_"
PORTAL = "https://expoportal.biohackeu.bigcat-bioinformatics.org"
DEPICT = ("https://cdkdepict.toolforge.org/depict/cow/svg?abbr=off&hdisp=bridgehead"
          "&showtitle=false&zoom=1.6&annotate=none&smi=")
MAX_WORDS = 12      # longest term, in words
MAX_TERMS = 12      # most ontology terms shown in one popup
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

# Words that are labels or synonyms somewhere, but say nothing in running text.
STOPWORDS = frozenset("""
about above after again all also among and any are because been before being
below between both but can did does down during each few for from further had
has have here how however into its just may more most much not now off once
one only other our out over own per same she should some such than that the
their them then there these they this those through thus too two under until
upon use used using very via was were what when where which while who why will
with within without would you
""".split())

# ---------------------------------------------------------------- JATS to tokens
#
# The article is first turned into a list of tokens: HTML that is ready
# ("raw"), text that may be annotated ("text") and breaks. Text between two
# breaks is one run, also when it is cut in pieces by markup such as
# <italic> or <sub>, so that a term like "CO<sub>2</sub>" is found.

RAW, TEXT, BREAK = 0, 1, 2

INLINE = {"italic": "i", "bold": "b", "sub": "sub", "sup": "sup", "underline": "u",
          "monospace": "code", "sc": 'span class="sc"', "strike": "s",
          "named-content": "span", "styled-content": "span", "roman": "span",
          "abbrev": "span", "inline-formula": "span"}
BLOCK = {"p": "p", "list-item": "li", "def-list": "dl", "term": "dt", "def": "dd",
         "disp-quote": "blockquote", "boxed-text": 'div class="box"',
         "fig": 'figure class="fig"', "table-wrap": 'div class="table-wrap"',
         "caption": 'div class="caption"', "table-wrap-foot": 'div class="foot"',
         "fn": 'div class="fn"', "disp-formula": 'div class="formula"',
         "table": "table", "thead": "thead", "tbody": "tbody", "tfoot": "tfoot",
         "tr": "tr", "td": "td", "th": "th", "label": 'span class="label"',
         "supplementary-material": 'div class="supp"', "ack": "section",
         "app": "section", "glossary": "section", "notes": "section",
         "abstract": 'section class="abstract"', "kwd-group": 'p class="kwd"',
         "kwd": 'span class="kwd"', "statement": 'div class="box"',
         "verse-group": "blockquote", "preformat": "pre"}
SKIP = {"graphic", "inline-graphic", "media", "tex-math", "object-id", "col", "colgroup",
        "permissions", "ref-list", "journal-meta", "article-meta", "front", "fn-group-skip"}
PLAIN = {"math", "ext-link", "uri", "email", "xref", "inline-supplementary-material"}


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def esc(text):
    return html.escape(text, quote=True)


def flat(el):
    """All text in an element, with single spaces."""
    return " ".join("".join(el.itertext()).split())


class Tokens:
    def __init__(self):
        self.items = []

    def raw(self, markup):
        self.items.append((RAW, markup))

    def text(self, text):
        if text:
            self.items.append((TEXT, re.sub(r"\s+", " ", text)))

    def brk(self):
        self.items.append((BREAK, ""))

    def open(self, tag, attrs=""):
        self.raw(f"<{tag}{attrs}>")

    def close(self, tag):
        self.raw(f"</{tag.split()[0]}>")


def children(el, out, depth):
    out.text(el.text)
    for child in el:
        walk(child, out, depth)
        out.text(child.tail)


def walk(el, out, depth=1):
    """Add the tokens of one JATS element."""
    name = local(el.tag)
    if not name or name in SKIP:       # comments, processing instructions, skipped elements
        return
    ident = f' id="{esc(el.get("id"))}"' if el.get("id") else ""
    if name in INLINE:
        out.open(INLINE[name])
        children(el, out, depth)
        out.close(INLINE[name])
    elif name in PLAIN:
        # shown, but not annotated and not part of a term
        out.brk()
        text = esc(flat(el))
        href = el.get(XLINK_HREF) or (flat(el) if name == "uri" else "")
        if name == "xref" and el.get("rid"):
            out.raw(f'<a class="xref" href="#{esc(el.get("rid"))}">{text}</a>')
        elif name == "email":
            out.raw(text)
        elif href:
            out.raw(f'<a href="{esc(href)}" rel="noopener">{text}</a>')
        else:
            out.raw(f'<span class="{name}">{text}</span>')
        out.brk()
    elif name == "break":
        out.brk()
        out.raw("<br>")
    elif name == "sec":
        out.brk()
        out.open("section", ident)
        label = ""
        out.text(el.text)
        for child in el:
            kind = local(child.tag)
            if kind == "label":
                label = flat(child)
            elif kind == "title":
                level = min(depth + 1, 6)
                out.brk()
                out.open(f"h{level}")
                if label:
                    out.raw(esc(label) + " ")
                children(child, out, depth)
                out.close(f"h{level}")
                out.brk()
            else:
                walk(child, out, depth + 1)
        out.close("section")
        out.brk()
    elif name == "title":
        level = min(depth + 1, 6)
        out.brk()
        out.open(f"h{level}")
        children(el, out, depth)
        out.close(f"h{level}")
        out.brk()
    elif name == "list":
        tag = "ol" if el.get("list-type") in ("order", "alpha-lower", "alpha-upper",
                                              "roman-lower", "roman-upper") else "ul"
        out.brk()
        out.open(tag)
        for child in el:
            walk(child, out, depth)
        out.close(tag)
        out.brk()
    elif name == "def-item":
        for child in el:
            walk(child, out, depth)
    elif name in BLOCK:
        tag = BLOCK[name]
        attrs = ident
        if name in ("td", "th"):
            for attr in ("colspan", "rowspan"):
                if (el.get(attr) or "").isdigit():
                    attrs += f' {attr}="{el.get(attr)}"'
        out.brk()
        out.open(tag, attrs)
        children(el, out, depth)
        out.close(tag)
        out.brk()
        if name in ("label", "kwd"):
            out.raw(" ")
    else:                               # unknown element: keep its content
        out.brk()
        children(el, out, depth)
        out.brk()


# ---------------------------------------------------------------- matching

WORD = re.compile(r"\w+")


def candidates(norm):
    """(start, end) of every piece of text that starts and ends with a word."""
    words = [m.span() for m in WORD.finditer(norm)]
    for i, (start, _) in enumerate(words):
        for _, end in words[i:i + MAX_WORDS]:
            if end - start > MAX_LABEL:
                break
            yield i, start, end


def case_ok(surface, label):
    """Is the text in the article the same as a label, apart from harmless case?

    Short words and acronyms have to match exactly: "was" is not the gene WAS.
    """
    if same_length_normalize(surface) != same_length_normalize(label):
        return False
    same = "".join(same_length_normalize(c) if not c.isalpha() else c for c in surface) == \
           "".join(same_length_normalize(c) if not c.isalpha() else c for c in label)
    if same:
        return True
    if len(surface) <= 3:
        return False
    if label.isupper() and not surface.isupper():
        return False
    return True


class Matcher:
    """Looks up text in the label cache and the Oscar/OPSIN names of the article."""

    def __init__(self, cache, chem_names, exclude=(), local_terms=()):
        self.exclude = frozenset(exclude)
        self.local = {}         # key -> [(iri, label)] : terms added by hand
        self.local_smiles = {}  # iri -> SMILES given by hand
        local_keys = {}         # InChIKey -> iri
        for term in local_terms:
            iri = term["iri"]
            for name in [term["label"]] + term["synonyms"]:
                self.local.setdefault(same_length_normalize(name), []).append((iri, term["label"]))
            if term["smiles"]:
                self.local_smiles[iri] = term["smiles"]
            if term["inchikey"]:
                local_keys[term["inchikey"]] = iri
        self.db = sqlite3.connect(f"file:{cache}?mode=ro", uri=True)
        self.rows = {}          # key -> [(iri, ont, label, kind)]
        self.chem = {}          # key -> [(name, smiles, iri)] : OPSIN structure that is in ChEBI
        self.opsin_names = len(chem_names)
        for name, (smiles, inchikey) in chem_names.items():
            if not inchikey:
                continue
            iris = {iri for (iri,) in self.db.execute(
                "SELECT iri FROM structures WHERE inchikey = ?", (inchikey,))}
            if inchikey in local_keys:
                iris.add(local_keys[inchikey])
            for iri in sorted(iris):
                self.chem.setdefault(same_length_normalize(name), []).append((name, smiles, iri))
        self.annotations = []   # the data of the popups
        self.index = {}         # surface text -> index in annotations, or None

    def load(self, keys):
        """Fetch the cache rows of all keys in one go."""
        keys = sorted(k for k in keys if k not in self.rows)
        for i in range(0, len(keys), 500):
            batch = keys[i:i + 500]
            marks = ",".join("?" * len(batch))
            for key, iri, ont, label, kind in self.db.execute(
                    f"SELECT key, iri, ont, label, kind FROM labels WHERE key IN ({marks})", batch):
                self.rows.setdefault(key, []).append((iri, ont, label, kind))
        for key in keys:
            self.rows.setdefault(key, [])

    def main_label(self, iri, fallback):
        row = self.db.execute("SELECT label FROM labels WHERE iri = ? AND kind = 'label' "
                              "ORDER BY ont = 'CHEBI' DESC, ont LIMIT 1", (iri,)).fetchone()
        return row[0] if row else fallback

    def smiles(self, iri):
        if iri in self.local_smiles:
            return self.local_smiles[iri]
        if not iri.startswith(CHEBI_IRI):
            return None
        row = self.db.execute("SELECT smiles FROM structures WHERE iri = ?", (iri,)).fetchone()
        return row[0] if row else None

    def annotation(self, surface, key):
        """Index of the popup data for this text, or None when it is not a term."""
        if surface in self.index:
            return self.index[surface]
        result = None
        if len(key) >= 3 and key not in STOPWORDS and any(c.isalpha() for c in key):
            terms = {}
            for iri, ont, label, kind in self.rows.get(key, ()):
                if ont in self.exclude or not case_ok(surface, label):
                    continue
                term = terms.setdefault(iri, {"iri": iri, "how": kind, "onts": set()})
                term["onts"].add(ont)
                if kind == "label":
                    term["how"], term["label"] = "label", label
            for iri, label in self.local.get(key, ()):
                # a term added by hand; its ontologies are those that have the term
                onts = {ont for (ont,) in self.db.execute(
                    "SELECT DISTINCT ont FROM labels WHERE iri = ?", (iri,))} - self.exclude
                term = terms.setdefault(iri, {"iri": iri, "onts": set()})
                term["how"], term["label"] = "local", label
                term["onts"] |= onts
            for name, smiles, iri in self.chem.get(key, ()):
                term = terms.setdefault(iri, {"iri": iri, "how": "structure", "onts": {"CHEBI"}})
                term["opsin"] = smiles
            if terms:
                for term in terms.values():
                    if "label" not in term:
                        term["label"] = self.main_label(term["iri"], surface)
                    smiles = self.smiles(term["iri"])
                    if smiles:
                        term["smiles"] = smiles
                    term["onts"] = sorted(term["onts"])
                ranked = sorted(terms.values(), key=lambda t: (
                    t["how"] != "local", "smiles" not in t, t["how"] == "synonym", -len(t["onts"]), t["iri"]))
                result = len(self.annotations)
                self.annotations.append({
                    "text": surface,
                    "cls": "chem" if any("smiles" in t for t in ranked) else "ont",
                    "total": len(ranked),
                    "terms": ranked[:MAX_TERMS],
                })
        self.index[surface] = result
        return result


def annotate(tokens, matcher):
    """Return the HTML of the tokens, with the terms wrapped in <span class="ann">."""
    items = tokens.items
    # the runs: lists of indexes of text tokens between two breaks
    runs, run = [], []
    for i, (kind, _) in enumerate(items):
        if kind == TEXT:
            run.append(i)
        elif kind == BREAK and run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)

    prepared, keys = [], set()
    for run in runs:
        text = "".join(items[i][1] for i in run)
        norm = same_length_normalize(text)
        spans = list(candidates(norm))
        keys.update(norm[s:e] for _, s, e in spans)
        prepared.append((run, text, norm, spans))
    matcher.load(keys)

    counts = {"chem": 0, "ont": 0}
    rendered = {}       # index of text token -> HTML
    for run, text, norm, spans in prepared:
        # longest term first at every word; terms do not overlap
        by_word = {}
        for word, start, end in spans:
            by_word.setdefault(word, []).append((start, end))
        found, free = [], 0
        for word in sorted(by_word):
            start = by_word[word][0][0]
            if start < free:
                continue
            for start, end in reversed(by_word[word]):
                # not a part of a hyphenated word: no "quinone" in "6PPD-quinone"
                if norm[start - 1:start] == "-" or (
                        norm[end:end + 1] == "-" and norm[end + 1:end + 2].isalnum()):
                    continue
                k = matcher.annotation(text[start:end], norm[start:end])
                if k is not None:
                    found.append((start, end, k))
                    free = end
                    break
        for _, _, k in found:
            counts[matcher.annotations[k]["cls"]] += 1
        # write the pieces of the run, cutting a term where the markup cuts it
        offset = 0
        for i in run:
            piece = items[i][1]
            lo, hi = offset, offset + len(piece)
            out, pos = [], lo
            for start, end, k in found:
                if end <= lo or start >= hi:
                    continue
                a, b = max(start, lo), min(end, hi)
                out.append(esc(text[pos:a]))
                cls = matcher.annotations[k]["cls"]
                focus = ' tabindex="0"' if a == start else ""
                out.append(f'<span class="ann {cls}" data-k="{k}"{focus}>{esc(text[a:b])}</span>')
                pos = b
            out.append(esc(text[pos:hi]))
            rendered[i] = "".join(out)
            offset = hi
    parts = []
    for i, (kind, value) in enumerate(items):
        if kind == RAW:
            parts.append(value)
        elif kind == TEXT:
            parts.append(rendered[i])
    return "".join(parts), counts


# ---------------------------------------------------------------- the page

PAGE_CSS = """
:root{--bg:#fff;--fg:#1c1c1a;--muted:#6b6b66;--line:#dcdcd4;--panel:#fff;--accent:#1d5fa8;--hl:#fff4e6;
--ont:#e2e4e8;--ont-line:#9aa0a8;--chem:#c4ecca;--chem-line:#3f9a52}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif}
header{padding:16px 16px 4px}
header h1{font-size:24px;line-height:1.3;margin:0 0 8px}
header .authors{margin:0 0 4px}
header .meta{color:var(--muted);font-size:14px;margin:0}
header .meta a{color:var(--accent)}
#legend{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);
padding:8px 16px;font-size:14px;color:var(--muted);display:flex;gap:6px 18px;flex-wrap:wrap}
#legend .ann{cursor:default}
header,main{max-width:892px;margin:0 auto}
main{padding:8px 16px 80px;overflow-wrap:break-word}
h2{font-size:20px;margin:28px 0 8px}h3{font-size:17px;margin:22px 0 6px}h4,h5,h6{font-size:16px;margin:18px 0 4px}
p{margin:0 0 12px}
.abstract{border-left:3px solid var(--line);padding-left:14px;margin:16px 0}
.sc{font-variant:small-caps}
.label{font-weight:600}
.xref{color:var(--accent);text-decoration:none;font-size:.85em}
.fig,.table-wrap,.box,.supp{margin:16px 0;padding:10px 12px;border:1px solid var(--line);border-radius:8px}
.table-wrap{overflow-x:auto}
.caption p,.foot p,.fn p{margin:4px 0;font-size:14px}
table{border-collapse:collapse;font-size:14px;margin:8px 0}
td,th{border:1px solid var(--line);padding:3px 8px;text-align:left;vertical-align:top}
.kwd+.kwd::before{content:"· ";color:var(--muted)}
.refs{font-size:14px;color:var(--muted);padding-left:28px}
.refs li{margin-bottom:6px;overflow-wrap:anywhere}
.refs a{color:var(--accent)}
.ann{border-radius:3px;padding:0 2px;cursor:help;-webkit-box-decoration-break:clone;box-decoration-break:clone}
.ann.ont{background:var(--ont)}
.ann.chem{background:var(--chem)}
.ann.ont:hover,.ann.ont:focus,.ann.ont.active{outline:2px solid var(--ont-line);outline-offset:0}
.ann.chem:hover,.ann.chem:focus,.ann.chem.active{outline:2px solid var(--chem-line);outline-offset:0}
#pop{position:absolute;z-index:10;width:max-content;max-width:min(440px,calc(100vw - 16px));max-height:380px;overflow:auto;
background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;
box-shadow:0 6px 24px rgba(0,0,0,.25);font-size:14px;line-height:1.5}
#pop[hidden]{display:none}
#pop h3{margin:0;font-size:15px}
#pop .kind{color:var(--muted);font-size:12px}
#pop h4{margin:8px 0 2px;font-size:13px}
#pop .cand{padding:6px 0;border-top:1px solid var(--line)}
#pop .lbl{font-weight:600}
#pop .how{color:var(--muted);font-size:12px;font-weight:400;border:1px solid var(--line);border-radius:10px;padding:0 8px;margin-left:6px;white-space:nowrap}
#pop .structure .how{color:var(--fg);background:var(--chem)}
#pop .local .how{color:var(--fg);background:var(--hl)}
#pop .iri{color:var(--muted);font:12px ui-monospace,monospace;overflow-wrap:anywhere}
#pop .struct{display:block;height:130px;width:100%;object-fit:scale-down;margin:4px 0;background:#fff;border:1px solid var(--line);border-radius:6px}
#pop .links{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
#pop .links a{color:var(--accent);border:1px solid var(--line);border-radius:10px;padding:0 8px;font-size:12px;text-decoration:none}
#pop .links a:hover,#pop .links a:focus{background:var(--hl)}
#pop .more{color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:6px}
"""

PAGE_JS = r"""
const TERMS = JSON.parse(document.getElementById('term-data').textContent);
const PORTAL = document.body.dataset.portal, DEPICT = document.body.dataset.depict;
const page = document.getElementById('page'), pop = document.getElementById('pop');
const HOW = {local: 'added by hand', label: 'matched on label', synonym: 'matched on synonym', structure: 'same structure as the name (OPSIN)'};
let hideT, pinned = null, shown = [];
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text; return e; };
function unmark(){ shown.forEach(s => s.classList.remove('active')); shown = []; }
function show(span){
  clearTimeout(hideT);
  const d = TERMS[span.dataset.k]; if (!d) return;
  unmark();
  // a term that is cut by markup has several spans, one after the other
  shown = [span];
  for (let s = span.previousElementSibling; s && s.dataset && s.dataset.k === span.dataset.k && !shown[0].hasAttribute('tabindex'); s = s.previousElementSibling) shown.unshift(s);
  shown.forEach(s => s.classList.add('active'));
  pop.replaceChildren(el('h3', '', d.text),
    el('div', 'kind', d.cls === 'chem' ? 'chemical structure in ChEBI' : 'ontology term'));
  const n = d.total;
  pop.append(el('h4', '', n + ' ontology term' + (n > 1 ? 's' : '') + ' on ExpoPortal'));
  for (const m of d.terms) {
    const c = el('div', 'cand ' + m.how), k = el('div', 'links'), l = el('div', 'lbl', m.label);
    l.append(el('span', 'how', HOW[m.how]));
    for (const ont of m.onts) {
      const a = el('a', '', ont); a.target = '_blank'; a.rel = 'noopener';
      a.href = PORTAL + '/ontologies/' + ont + '?p=classes&conceptid=' + encodeURIComponent(m.iri);
      a.title = 'Open in ' + ont + ' on ExpoPortal'; k.append(a);
    }
    c.append(l, el('div', 'iri', m.iri));
    if (m.smiles) {          // 2D structure, drawn by CDK Depict from the SMILES
      const s = el('img', 'struct'); s.src = DEPICT + encodeURIComponent(m.smiles);
      s.alt = '2D structure of ' + m.label; s.title = 'SMILES: ' + m.smiles; c.append(s);
    }
    c.append(k); pop.append(c);
  }
  if (n > d.terms.length) pop.append(el('div', 'more', (n - d.terms.length) + ' more terms are not shown'));
  pop.hidden = false; pop.scrollTop = 0;
  const r = span.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  const x = Math.min(Math.max(8, r.left), innerWidth - w - 8);
  let y = r.bottom + 6;
  if (y + h + 8 > innerHeight && r.top > h + 14) y = r.top - h - 6;
  pop.style.left = (x + scrollX) + 'px'; pop.style.top = (y + scrollY) + 'px';
}
function hideNow(){ pop.hidden = true; pinned = null; unmark(); }
const hide = () => { if (!pinned) hideT = setTimeout(hideNow, 250); };
const target = e => e.target.closest ? e.target.closest('#page .ann') : null;
page.addEventListener('mouseover', e => { const s = target(e); if (s && !pinned) show(s); });
page.addEventListener('mouseout', e => { if (target(e)) hide(); });
page.addEventListener('focusin', e => { const s = target(e); if (s && !pinned) show(s); });
page.addEventListener('focusout', hide);
// a click keeps the popup open
page.addEventListener('click', e => { const s = target(e); if (!s) return; hideNow(); show(s); pinned = s; });
pop.addEventListener('mouseenter', () => clearTimeout(hideT));
pop.addEventListener('mouseleave', hide);
document.addEventListener('click', e => { if (!e.target.closest('#pop') && !target(e)) hideNow(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') hideNow(); });
"""


def find(root, path):
    found = root.find(path)
    return found if found is not None else None


def front_tokens(article, out):
    """Title, authors and identifiers of the article."""
    meta = find(article, "front/article-meta")
    info = {"title": "", "authors": [], "ids": {}}
    out.open("header")
    out.open("h1")
    title = find(meta, "title-group/article-title") if meta is not None else None
    if title is not None:
        info["title"] = flat(title)
        children(title, out, 1)
    out.close("h1")
    out.brk()
    if meta is not None:
        for contrib in meta.iter("contrib"):
            if contrib.get("contrib-type", "author") != "author":
                continue
            name = find(contrib, "name")
            if name is not None:
                parts = [flat(name.find(p)) for p in ("given-names", "surname") if name.find(p) is not None]
                info["authors"].append(" ".join(parts))
            elif find(contrib, "collab") is not None:
                info["authors"].append(flat(contrib.find("collab")))
        for ident in meta.findall("article-id"):
            info["ids"][ident.get("pub-id-type")] = flat(ident)
    if info["authors"]:
        out.raw(f'<p class="authors">{esc(", ".join(info["authors"]))}</p>')
    journal = find(article, "front/journal-meta/journal-title-group/journal-title")
    year = meta.find(".//pub-date/year") if meta is not None else None
    line = [esc(flat(x)) for x in (journal, year) if x is not None]
    ids = info["ids"]
    if ids.get("doi"):
        line.append(f'<a href="https://doi.org/{esc(ids["doi"])}" rel="noopener">doi:{esc(ids["doi"])}</a>')
    pmcid = ids.get("pmcid") or ids.get("pmc") or ids.get("pmcaid") or ""
    if pmcid:
        pmcid = pmcid if pmcid.upper().startswith("PMC") else "PMC" + pmcid
        line.append(f'<a href="https://europepmc.org/article/PMC/{esc(pmcid)}" rel="noopener">{esc(pmcid)}</a>')
    if line:
        out.raw(f'<p class="meta">{" · ".join(line)}</p>')
    out.close("header")
    out.brk()
    return info


def references(article):
    """The reference list, as it is (not annotated)."""
    refs = list(article.iter("ref"))
    if not refs:
        return ""
    rows = []
    for ref in refs:
        citation = next((c for c in ref if local(c.tag) != "label"), ref)
        text = esc(flat(citation))
        doi = next((flat(p) for p in ref.iter("pub-id") if p.get("pub-id-type") == "doi"), "")
        if doi:
            text += f' <a href="https://doi.org/{esc(doi)}" rel="noopener">doi</a>'
        ident = f' id="{esc(ref.get("id"))}"' if ref.get("id") else ""
        rows.append(f"<li{ident}>{text}</li>")
    return '<section><h2>References</h2><ol class="refs">' + "".join(rows) + "</ol></section>"


def read_local(path):
    """The terms added by hand: a list of dicts (see local_terms.tsv)."""
    if not path:
        return []
    with open(path, encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    terms = []
    for row in csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE):
        if not (row.get("iri") and row.get("label")):
            sys.exit(f"{path}: every line needs an iri and a label")
        terms.append({
            "iri": row["iri"].strip(), "label": row["label"].strip(),
            "synonyms": [s.strip() for s in (row.get("synonyms") or "").split("|") if s.strip()],
            "smiles": (row.get("smiles") or "").strip(),
            "inchikey": (row.get("inchikey") or "").strip(),
        })
    return terms


def read_chem(path):
    """name -> (smiles, inchikey) from the output of ner_chemicals.groovy."""
    if not path:
        return {}
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        return {row["name"]: (row["smiles"] or "", row["inchikey"] or "") for row in reader}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("jats", type=Path, help="JATS XML file")
    parser.add_argument("--cache", type=Path, required=True,
                        help="SQLite file made by cache_ontology_labels.py")
    parser.add_argument("--chem", type=Path, help="TSV file made by ner_chemicals.groovy")
    parser.add_argument("--out", type=Path, help="HTML file to write (default: next to the XML)")
    parser.add_argument("--local", type=Path, help="TSV file with terms added by hand")
    parser.add_argument("--exclude", default="", metavar="ACRONYMS",
                        help="ontologies whose terms are not used, e.g. NCIT,SWEET")
    args = parser.parse_args()
    out_path = args.out or args.jats.with_suffix(".html")
    if not args.cache.exists():
        sys.exit(f"{args.cache} not found: run cache_ontology_labels.py first")

    article = ET.parse(args.jats).getroot()
    if local(article.tag) != "article":
        article = next(article.iter("article"))
    matcher = Matcher(args.cache, read_chem(args.chem),
                      [a.strip().upper() for a in args.exclude.split(",") if a.strip()],
                      read_local(args.local))

    tokens = Tokens()
    info = front_tokens(article, tokens)
    tokens.open("main")
    meta = find(article, "front/article-meta")
    if meta is not None:
        for abstract in meta.findall("abstract"):
            if abstract.find("title") is None:
                tokens.raw("<h2>Abstract</h2>" if not abstract.get("abstract-type") else "")
            walk(abstract, tokens, 1)
        for keywords in meta.findall("kwd-group"):
            walk(keywords, tokens, 1)
    body = find(article, "body")
    if body is not None:
        children(body, tokens, 1)
    back = find(article, "back")
    if back is not None:
        for child in back:
            walk(child, tokens, 1)
    tokens.brk()
    content, counts = annotate(tokens, matcher)
    content += references(article) + "</main>"

    unique = {"chem": 0, "ont": 0}
    for annotation in matcher.annotations:
        unique[annotation["cls"]] += 1
    payload = json.dumps(matcher.annotations, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = info["title"] or args.jats.stem
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{PAGE_CSS}</style>
</head>
<body data-portal="{esc(PORTAL)}" data-depict="{esc(DEPICT)}">
<div id="legend">
<span><span class="ann chem">chemical structure in ChEBI</span> {counts["chem"]} in the text, {unique["chem"]} different</span>
<span><span class="ann ont">other ontology term on ExpoPortal</span> {counts["ont"]} in the text, {unique["ont"]} different</span>
<span>Hover or focus a term to see its ontology terms; click to keep the popup open.</span>
</div>
<div id="page">
{content}
</div>
<div id="pop" role="tooltip" hidden></div>
<script type="application/json" id="term-data">{payload}</script>
<script>{PAGE_JS}</script>
</body>
</html>
"""
    out_path.write_text(page, encoding="utf-8")
    via_opsin = sum(1 for a in matcher.annotations for t in a["terms"] if "opsin" in t)
    print(f"{args.jats}: {counts['chem']} chemical structures ({unique['chem']} different, "
          f"{via_opsin} found by OPSIN) and {counts['ont']} other ontology terms "
          f"({unique['ont']} different) -> {out_path}")


if __name__ == "__main__":
    main()
