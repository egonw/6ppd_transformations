# Seed articles about 6PPD and 6PPD-quinone, and their annotated HTML pages.
#
#   make literature   find the seed articles in Wikidata and download their
#                     Europe PMC JATS XML files to literature/ (also: make)
#   make annotate     make literature/<PMCID>.html for every JATS file, with
#                     the ontology terms (grey) and the chemical structures in
#                     ChEBI (green) highlighted. Runs one article per job:
#                     make -j4 annotate
#                     Terms in local_terms.tsv are added to those of ExpoPortal.
#                     Also writes index.html, with the list of these pages and
#                     the list of pathways.
#   make index        only write index.html again
#   make sbml         convert every GPML file in the root folder to SBML with
#                     the MINERVA conversion service (sends the file there)
#   make svg          draw every SBML file made by "make sbml" as SVG, also with
#                     the MINERVA service
#   make pathway      make <name>.html for every GPML file: the SVG with popups
#                     that show structures, species and the sentences in the
#                     articles that support each conversion (evidence.tsv)
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
# Articles added by hand (DOIs), downloaded with the seed articles from Wikidata.
ARTICLES    = local_articles.txt

JATS := $(wildcard $(LITERATURE)/PMC*.xml)
HTML := $(JATS:.xml=.html)
CHEM := $(JATS:.xml=.chem.tsv)
GPML := $(wildcard *.gpml)
SBML := $(GPML:.gpml=.sbml)
SVG  := $(GPML:.gpml=.svg)
PATHWAY := $(GPML:.gpml=.html)
# Sentences in the articles that support the conversions in the pathway.
EVIDENCE = evidence.tsv
MINERVA = https://minerva-service.lcsb.uni.lu/minerva/api/convert

.PHONY: all literature annotate index sbml svg pathway cache clean clean-html
.DELETE_ON_ERROR:
# The lists of chemical names are kept: they are slow to make.
.SECONDARY: $(CHEM) $(SBML)

all: literature

# Find the 6PPD / 6PPD-Q seed articles in Wikidata and download their
# Europe PMC JATS XML files, and those of the articles in local_articles.txt.
# Already downloaded files are kept.
literature:
	$(PYTHON) -u fetch_seed_articles.py --outdir $(LITERATURE) --extra $(ARTICLES)

annotate: $(HTML) index.html

index: index.html

# The list of annotated pages, in the root folder.
# It has two tabs: the articles and the pathways (one for every GPML file).
# The About column lists the resources of resources.tsv, with the ELIXIR logo
# when there is a file images/elixir-logo.svg (or .png, .jpg, .webp).
# An article in hidden_articles.tsv keeps its page but is not listed.
index.html: $(HTML) $(GPML) make_index.py resources.tsv hidden_articles.tsv $(wildcard $(LITERATURE)/seeds.tsv $(PATHWAY) $(SVG) $(SBML) images/elixir-logo.*)
	$(PYTHON) make_index.py $(HTML) --gpml $(GPML) --seeds $(LITERATURE)/seeds.tsv --out $@

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
$(LITERATURE)/%.html: $(LITERATURE)/%.xml $(LITERATURE)/%.chem.tsv annotate_jats.py cache_ontology_labels.py $(LOCAL) $(EVIDENCE) | $(CACHE)
	$(PYTHON) annotate_jats.py $< --chem $(LITERATURE)/$*.chem.tsv --cache $(CACHE) --local $(LOCAL) --evidence $(EVIDENCE) --pathway ../$(firstword $(PATHWAY)) --exclude "$(EXCLUDE)" --out $@

sbml: $(SBML)

# GPML to SBML, converted by the MINERVA service. --fail makes curl stop with
# an error when the service refuses the file, instead of saving the error page.
%.sbml: %.gpml
	curl --fail --silent --show-error -H "Content-Type: application/octet-stream" \
		-X POST --data-binary @$< $(MINERVA)/GPML:SBML > $@

svg: $(SVG)

# SBML to SVG, drawn by the MINERVA service.
%.svg: %.sbml
	curl --fail --silent --show-error -H "Content-Type: application/octet-stream" \
		-X POST --data-binary @$< $(MINERVA)/image/SBML:svg > $@

# The index links to the pathway page, so it is written again afterwards.
pathway: $(PATHWAY)
	@$(MAKE) --no-print-directory index.html

# The pathway page: the SVG with a popup for every node and interaction. The
# articles are order-only: their pages only have to be there for the links.
$(PATHWAY): %.html: %.gpml %.svg $(EVIDENCE) make_pathway_html.py $(wildcard $(LITERATURE)/seeds.tsv) | $(HTML)
	$(PYTHON) make_pathway_html.py $< --svg $*.svg --evidence $(EVIDENCE) --seeds $(LITERATURE)/seeds.tsv --out $@

clean-html:
	rm -f $(HTML) $(CHEM) $(PATHWAY) index.html

clean: clean-html
	rm -f $(SBML) $(SVG)
	rm -f $(LITERATURE)/*.xml $(LITERATURE)/seeds.tsv
	-rmdir $(LITERATURE)
