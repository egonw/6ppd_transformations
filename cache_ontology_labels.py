#!/usr/bin/env python3
"""Build a local cache of all ontology term labels on ExpoPortal.

The labels come from the public SPARQL endpoint of ExpoPortal (an OntoPortal),
the same backend that the ExpoPortal MCP server uses. For the current
submission of every ontology the script downloads:

  * the labels (rdfs:label, skos:prefLabel) and the exact synonyms
    (skos:altLabel, oboInOwl:hasExactSynonym) of all terms, and
  * for ChEBI, the SMILES and InChIKey of the terms with a chemical structure.

The result is one SQLite file with two tables:

  labels(key, iri, ont, label, kind)   key is the normalised label (see
                                       normalize()), ont the ontology acronym,
                                       kind 'label' or 'synonym'
  structures(iri, smiles, inchikey)    ChEBI terms with a structure

Only labels without a language tag or with an English one are kept, and
deprecated terms are left out. Only the Python standard library is used.

Usage: python3 cache_ontology_labels.py cache/ontology_labels.sqlite
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ENDPOINT = "https://sparql.expoportal.biohackeu.bigcat-bioinformatics.org/sparql"
USER_AGENT = "6ppd-jats-annotator/1.0 (Python urllib)"
PAGE = 10000        # the endpoint returns at most 10,000 rows per query
MAX_LABEL = 120     # longer labels are not kept
WORKERS = 4         # parallel requests to the endpoint

RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
LABEL_PREDICATES = {
    RDFS_LABEL: "label",
    "http://www.w3.org/2004/02/skos/core#prefLabel": "label",
    "http://www.w3.org/2004/02/skos/core#altLabel": "synonym",
    "http://www.geneontology.org/formats/oboInOwl#hasExactSynonym": "synonym",
}
DEPRECATED = "http://www.w3.org/2002/07/owl#deprecated"
SMILES = "https://w3id.org/chemrof/smiles_string"
INCHIKEY = "https://w3id.org/chemrof/inchi_key_string"
CHEBI = "CHEBI"

# The current submission of each ontology: parsed (RDF), not archived, and
# without a newer submission that is also parsed and not archived.
CURRENT_SUBMISSIONS = """
PREFIX meta: <http://data.bioontology.org/metadata/>
PREFIX omv: <http://omv.ontoware.org/2005/05/ontology#>
PREFIX status: <http://data.bioontology.org/submission_statuses/>
SELECT ?acronym ?sub WHERE {
  GRAPH meta:OntologySubmission {
    ?sub meta:ontology ?ont ; meta:submissionId ?id ; meta:submissionStatus status:RDF .
    FILTER NOT EXISTS { ?sub meta:submissionStatus status:ARCHIVED }
    FILTER NOT EXISTS {
      ?other meta:ontology ?ont ; meta:submissionId ?id2 ; meta:submissionStatus status:RDF .
      FILTER NOT EXISTS { ?other meta:submissionStatus status:ARCHIVED }
      FILTER (?id2 > ?id)
    }
  }
  GRAPH meta:Ontology { ?ont omv:acronym ?acronym }
} ORDER BY ?acronym"""

# Characters that are written in several ways in articles and ontologies.
_SAME = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "′": "'", "’": "'", "‘": "'", "ʹ": "'",
    " ": " ", " ": " ", " ": " ", " ": " ",
}


def same_length_normalize(text):
    """Lower case and unify dashes, primes and spaces; one character in, one out."""
    out = []
    for char in text:
        char = _SAME.get(char, char)
        low = char.lower()
        out.append(low if len(low) == 1 else char)
    return "".join(out)


def normalize(text):
    """The key under which a label is stored and looked up."""
    return " ".join(same_length_normalize(text).split())


def sparql(query, retries=4):
    data = urllib.parse.urlencode({"query": query}).encode()
    request = urllib.request.Request(ENDPOINT, data=data, headers={
        "User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read())["results"]["bindings"]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            failure = error
            if attempt < retries:
                time.sleep(10 * attempt)
    raise RuntimeError(f"SPARQL query failed: {failure}\n{query}")


def fetch(graph, predicate):
    """All (subject IRI, literal, language) of one predicate in one graph.

    The endpoint (Virtuoso) refuses ORDER BY with a large OFFSET, so the pages
    are read without ORDER BY. For one triple pattern on a graph that does not
    change this gives every row once; the row count is checked afterwards.
    """
    pattern = f"GRAPH <{graph}> {{ ?s <{predicate}> ?o }}"
    rows, offset = [], 0
    while True:
        page = sparql(f"SELECT ?s ?o WHERE {{ {pattern} }} LIMIT {PAGE} OFFSET {offset}")
        for b in page:
            if b["s"]["type"] == "uri" and b["o"]["type"] != "bnode":
                rows.append((b["s"]["value"], b["o"]["value"], b["o"].get("xml:lang", "")))
        if len(page) < PAGE:
            break
        offset += PAGE
    total = offset + len(page)
    if total >= PAGE:   # more than one page: check that paging saw every row
        count = int(sparql(f"SELECT (COUNT(*) AS ?n) WHERE {{ {pattern} }}")[0]["n"]["value"])
        if count != total:
            print(f"warning: {graph} <{predicate}>: read {total} rows, "
                  f"the endpoint counts {count}", file=sys.stderr)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("database", type=Path, help="SQLite file to write")
    args = parser.parse_args()

    submissions = {b["acronym"]["value"]: b["sub"]["value"] for b in sparql(CURRENT_SUBMISSIONS)}
    if CHEBI not in submissions:
        sys.exit("ChEBI is not on ExpoPortal: no chemical structures to cache")
    print(f"{len(submissions)} ontologies on {ENDPOINT}")

    args.database.parent.mkdir(parents=True, exist_ok=True)
    partial = args.database.with_name(args.database.name + ".partial")
    partial.unlink(missing_ok=True)
    db = sqlite3.connect(partial)
    db.executescript("""
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE labels (key TEXT NOT NULL, iri TEXT NOT NULL, ont TEXT NOT NULL,
                             label TEXT NOT NULL, kind TEXT NOT NULL,
                             PRIMARY KEY (key, iri, ont)) WITHOUT ROWID;
        CREATE TABLE deprecated (ont TEXT NOT NULL, iri TEXT NOT NULL);
        CREATE TABLE smiles (iri TEXT PRIMARY KEY, smiles TEXT NOT NULL);
        CREATE TABLE inchikeys (iri TEXT PRIMARY KEY, inchikey TEXT NOT NULL);
        CREATE TABLE info (name TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)

    tasks = [(ont, graph, predicate) for ont, graph in submissions.items()
             for predicate in list(LABEL_PREDICATES) + [DEPRECATED]]
    tasks += [(CHEBI, submissions[CHEBI], SMILES), (CHEBI, submissions[CHEBI], INCHIKEY)]
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(fetch, graph, predicate): (ont, predicate)
                   for ont, graph, predicate in tasks}
        for future in as_completed(futures):
            ont, predicate = futures[future]
            rows = future.result()
            if predicate in LABEL_PREDICATES:
                kind = LABEL_PREDICATES[predicate]
                keep = []
                for iri, label, lang in rows:
                    label = " ".join(label.split())
                    if lang[:2] not in ("", "en") or not 2 <= len(label) <= MAX_LABEL:
                        continue
                    keep.append((normalize(label), iri, ont, label, kind))
                # the same text can be both label and synonym: keep it as label
                db.executemany("""INSERT INTO labels VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT DO UPDATE SET kind = excluded.kind, label = excluded.label
                    WHERE excluded.kind = 'label'""", keep)
            elif predicate == DEPRECATED:
                db.executemany("INSERT INTO deprecated VALUES (?, ?)",
                               [(ont, iri) for iri, value, _ in rows if value in ("true", "1")])
            else:
                table = "smiles" if predicate == SMILES else "inchikeys"
                db.executemany(f"INSERT OR IGNORE INTO {table} VALUES (?, ?)",
                               [(iri, value) for iri, value, _ in rows])
            done += 1
            if rows:
                print(f"  [{done}/{len(tasks)}] {ont} {predicate.rsplit('/', 1)[-1]}: {len(rows)}")
            del rows

    db.executescript("""
        DELETE FROM labels WHERE (ont, iri) IN (SELECT ont, iri FROM deprecated);
        DROP TABLE deprecated;
        CREATE TABLE structures (iri TEXT PRIMARY KEY, smiles TEXT NOT NULL, inchikey TEXT);
        INSERT INTO structures
            SELECT s.iri, s.smiles, k.inchikey FROM smiles s LEFT JOIN inchikeys k USING (iri);
        DROP TABLE smiles;
        DROP TABLE inchikeys;
        CREATE INDEX structures_inchikey ON structures (inchikey);
        CREATE INDEX labels_iri ON labels (iri, kind);
    """)
    n_labels = db.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
    n_structures = db.execute("SELECT COUNT(*) FROM structures").fetchone()[0]
    db.executemany("INSERT INTO info VALUES (?, ?)", [
        ("endpoint", ENDPOINT),
        ("created", time.strftime("%Y-%m-%dT%H:%M:%S%z")),
        ("ontologies", json.dumps(submissions, sort_keys=True)),
        ("labels", str(n_labels)),
        ("structures", str(n_structures)),
    ])
    db.commit()
    db.execute("VACUUM")
    db.close()
    os.replace(partial, args.database)
    size = args.database.stat().st_size / 1e6
    print(f"{n_labels} labels and {n_structures} ChEBI structures written to "
          f"{args.database} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
