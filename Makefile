# Seed articles about 6PPD and 6PPD-quinone, and their annotated HTML pages.
#
#   make literature   find the seed articles in Wikidata and download their
#                     Europe PMC JATS XML files to literature/ (also: make)
#   make annotate     make literature/<PMCID>.html for every JATS file, with
#                     the ontology terms (grey) and the chemical structures in
#                     ChEBI (green) highlighted. Runs one article per job:
#                     make -j4 annotate
#                     Terms in local_terms.tsv are added to those of ExpoPortal.
#                     Also writes index.html, the list of these pages.
#   make index        only write index.html again
#   make cache        download the labels of all ontology terms on ExpoPortal
#                     again (a few minutes). "make annotate" does this by
#                     itself only when there is no cache yet.
#   make clean-html   remove the HTML pages and the lists of chemical names
#   make clean        remove all generated files but the cache
#
# Needs python3, groovy and a Java 21 runtime (Debian: openjdk-21-jre-headless).

PYTHON     ?= python3
GROOVY     ?= groovy
# Bacting (Oscar, OPSIN, CDK) needs Java 21 or later.
JAVA_HOME  ?= /usr/lib/jvm/java-21-openjdk-amd64
# Ontologies whose terms are not highlighted, e.g. make annotate EXCLUDE=NCIT,SWEET
EXCLUDE    ?=
LITERATURE  = literature
CACHE       = cache/ontology_labels.sqlite
# Terms added by hand (6PPD and 6PPD-Q), used on top of the cache.
LOCAL       = local_terms.tsv

JATS := $(wildcard $(LITERATURE)/PMC*.xml)
HTML := $(JATS:.xml=.html)
CHEM := $(JATS:.xml=.chem.tsv)

.PHONY: all literature annotate index cache clean clean-html
.DELETE_ON_ERROR:
# The lists of chemical names are kept: they are slow to make.
.SECONDARY: $(CHEM)

all: literature

# Find the 6PPD / 6PPD-Q seed articles in Wikidata and download their
# Europe PMC JATS XML files. Already downloaded files are kept.
literature:
	$(PYTHON) -u fetch_seed_articles.py --outdir $(LITERATURE)

annotate: $(HTML) index.html

index: index.html

# The list of annotated pages, in the root folder.
index.html: $(HTML) make_index.py $(wildcard $(LITERATURE)/seeds.tsv)
	$(PYTHON) make_index.py $(HTML) --seeds $(LITERATURE)/seeds.tsv --out $@

# Labels of all ontology terms on ExpoPortal, and the ChEBI structures.
cache:
	$(PYTHON) -u cache_ontology_labels.py $(CACHE)

$(CACHE):
	$(PYTHON) -u cache_ontology_labels.py $@

# Chemical names in the article (Oscar) and their structures (OPSIN, CDK).
$(LITERATURE)/%.chem.tsv: $(LITERATURE)/%.xml ner_chemicals.groovy
	JAVA_HOME=$(JAVA_HOME) $(GROOVY) ner_chemicals.groovy $< $@

# The annotated page. The cache is an order-only prerequisite: it is made
# when missing, but a newer cache does not make the pages out of date.
$(LITERATURE)/%.html: $(LITERATURE)/%.xml $(LITERATURE)/%.chem.tsv annotate_jats.py cache_ontology_labels.py $(LOCAL) | $(CACHE)
	$(PYTHON) annotate_jats.py $< --chem $(LITERATURE)/$*.chem.tsv --cache $(CACHE) --local $(LOCAL) --exclude "$(EXCLUDE)" --out $@

clean-html:
	rm -f $(HTML) $(CHEM) index.html

clean: clean-html
	rm -f $(LITERATURE)/*.xml $(LITERATURE)/seeds.tsv
	-rmdir $(LITERATURE)
