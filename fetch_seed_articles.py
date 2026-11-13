#!/usr/bin/env python3
"""Find seed articles about 6PPD and 6PPD-quinone in Wikidata and download
their Europe PMC JATS XML full texts.

Steps:
  1. Resolve the ChEBI IDs to Wikidata items (main Wikidata SPARQL endpoint).
  2. Find articles with a DOI that have one of these items as 'main subject'
     (scholarly Wikidata SPARQL endpoint; scholarly articles are no longer
     in the main graph).
  3. Look up each DOI in Europe PMC and, when full text is available there,
     save the JATS XML as <outdir>/<PMCID>.xml.

Articles that are not in Wikidata as such can be added by hand: give a text
file with their DOIs with --extra (see local_articles.txt).

An overview of all seed articles is written to <outdir>/seeds.tsv.
Only the Python standard library is used.
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ChEBI ID -> short name
COMPOUNDS = {
    "747835": "6PPD",
    "233238": "6PPD-Q",
}

WDQS_MAIN = "https://query.wikidata.org/sparql"
WDQS_SCHOLARLY = "https://query-scholarly.wikidata.org/sparql"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
USER_AGENT = "6ppd-seed-article-fetcher/1.0 (Python urllib)"
MAX_WAIT = 120  # longest pause (seconds) before retrying a rate-limited request


def http_get(url, params=None, accept=None, retries=4, timeout=120):
    """GET a URL and return the response body as bytes (None on HTTP 404)."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    for attempt in range(1, retries + 1):
        wait = 5 * attempt
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            failure = error
            if error.code == 429:
                # Rate limited: honour Retry-After, but wait at most 2 min.
                retry_after = error.headers.get("Retry-After", "")
                wait = int(retry_after) + 1 if retry_after.isdigit() else 65
                wait = min(wait, MAX_WAIT)
        except (urllib.error.URLError, TimeoutError) as error:
            failure = error
        if attempt < retries:
            print(f"  {failure}; retrying in {wait} s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"GET {url} failed: {failure}")


def sparql(endpoint, query):
    body = http_get(endpoint, {"query": query},
                    accept="application/sparql-results+json")
    return json.loads(body)["results"]["bindings"]


def find_compounds():
    """Return {QID: short name} for the ChEBI IDs in COMPOUNDS."""
    values = " ".join(f'"{chebi}"' for chebi in COMPOUNDS)
    query = f"""
        SELECT ?compound ?chebi WHERE {{
          VALUES ?chebi {{ {values} }}
          ?compound wdt:P683 ?chebi .
        }}"""
    compounds = {}
    for row in sparql(WDQS_MAIN, query):
        qid = row["compound"]["value"].rsplit("/", 1)[-1]
        compounds[qid] = COMPOUNDS[row["chebi"]["value"]]
    missing = set(COMPOUNDS.values()) - set(compounds.values())
    if missing:
        sys.exit(f"No Wikidata item found for: {', '.join(sorted(missing))}")
    return compounds


def find_seed_articles(compounds):
    """Return {DOI: article dict} for articles with a compound as main subject."""
    values = " ".join(f"wd:{qid}" for qid in compounds)
    query = f"""
        SELECT DISTINCT ?article ?doi ?compound ?title (LANG(?title) AS ?lang) WHERE {{
          VALUES ?compound {{ {values} }}
          ?article wdt:P921 ?compound ;
                   wdt:P356 ?doi .
          OPTIONAL {{ ?article rdfs:label ?title }}
        }}"""
    articles = {}
    for row in sparql(WDQS_SCHOLARLY, query):
        doi = row["doi"]["value"].lower()
        article = articles.setdefault(doi, {
            "doi": doi,
            "wikidata": row["article"]["value"].rsplit("/", 1)[-1],
            "subjects": set(),
            "title": "", "title_lang": None,
        })
        # the label of the item is the title: English first, then "mul", then any
        if "title" in row:
            lang = row.get("lang", {}).get("value", "")
            rank = {"en": 0, "mul": 1}.get(lang, 2)
            if article["title_lang"] is None or rank < article["title_lang"]:
                article["title"], article["title_lang"] = row["title"]["value"], rank
        article["subjects"].add(
            compounds[row["compound"]["value"].rsplit("/", 1)[-1]])
    return articles


def read_extra_dois(path):
    """DOIs added by hand: one per line, lines starting with # are comments."""
    dois = []
    for line in path.read_text(encoding="utf-8").splitlines():
        doi = line.split("#", 1)[0].strip().lower()
        doi = doi.removeprefix("https://doi.org/").removeprefix("doi:")
        if doi:
            dois.append(doi)
    return dois


def europepmc_lookup(doi):
    """Return the Europe PMC record for a DOI, or None when it is not there."""
    body = http_get(f"{EUROPEPMC}/search", {
        "query": f'DOI:"{doi}"',
        "resultType": "lite",
        "format": "json",
        "pageSize": 25,
    })
    results = json.loads(body).get("resultList", {}).get("result", [])
    matches = [r for r in results if r.get("doi", "").lower() == doi]
    # Prefer a record that has a PMCID (and thus possibly full text).
    matches.sort(key=lambda r: "pmcid" not in r)
    return matches[0] if matches else None


def download_jats(pmcid, target):
    """Save the JATS XML of a PMCID to target; return False if unavailable."""
    body = http_get(f"{EUROPEPMC}/{pmcid}/fullTextXML")
    if not body or not body.lstrip().startswith(b"<"):
        return False
    target.write_bytes(body)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-o", "--outdir", default="literature", type=Path,
                        help="folder for the JATS XML files (default: literature)")
    parser.add_argument("--both", action="store_true",
                        help="only keep articles that have both 6PPD and "
                             "6PPD-Q as main subject (default: either)")
    parser.add_argument("--extra", type=Path,
                        help="text file with DOIs of articles to add by hand")
    parser.add_argument("--delay", default=0.2, type=float,
                        help="seconds to wait between Europe PMC requests")
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    compounds = find_compounds()
    for qid, name in compounds.items():
        print(f"{name}: https://www.wikidata.org/entity/{qid}")

    articles = find_seed_articles(compounds)
    if args.both:
        articles = {doi: a for doi, a in articles.items()
                    if len(a["subjects"]) == len(COMPOUNDS)}
    print(f"Found {len(articles)} seed articles with a DOI in Wikidata")
    if args.extra and args.extra.exists():
        added = 0
        for doi in read_extra_dois(args.extra):
            if doi not in articles:
                articles[doi] = {"doi": doi, "wikidata": "", "subjects": set(), "title": ""}
                added += 1
        print(f"Added {added} articles from {args.extra}")

    counts = {"downloaded": 0, "exists": 0, "no full text": 0,
              "not open access": 0, "not in Europe PMC": 0, "error": 0}
    rows = []
    for doi in sorted(articles):
        article = articles[doi]
        pmid = pmcid = filename = ""
        title = article.get("title", "")
        try:
            record = europepmc_lookup(doi)
            if record and not title:
                title = record.get("title", "")
            if record is None:
                status = "not in Europe PMC"
            else:
                pmid = record.get("pmid", "")
                pmcid = record.get("pmcid", "")
                target = args.outdir / f"{pmcid}.xml"
                if not pmcid:
                    status = "no full text"
                elif record.get("isOpenAccess") != "Y":
                    # Europe PMC only serves XML for open access articles
                    # (it answers HTTP 500 for the others).
                    status = "not open access"
                elif target.exists() and target.stat().st_size > 0:
                    status = "exists"
                elif download_jats(pmcid, target):
                    status = "downloaded"
                else:
                    status = "no full text"
                if status in ("exists", "downloaded"):
                    filename = target.name
        except RuntimeError as error:
            print(f"  {error}", file=sys.stderr)
            status = "error"
        counts[status] += 1
        print(f"  {doi}: {status}{' ' + filename if filename else ''}")
        rows.append([doi, article["wikidata"],
                     ";".join(sorted(article["subjects"])),
                     pmid, pmcid, status, filename, " ".join(title.split())])
        time.sleep(args.delay)

    with open(args.outdir / "seeds.tsv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["doi", "wikidata", "main_subjects", "pmid", "pmcid",
                         "status", "file", "title"])
        writer.writerows(rows)

    print("Summary: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n))
    print(f"Overview written to {args.outdir / 'seeds.tsv'}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
