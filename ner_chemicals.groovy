#!/usr/bin/env groovy

// Chemical named entity recognition on one JATS XML file, after
// background/iupac2/parseXmlGz.groovy:
//
//   Oscar finds chemical names in the text of the article,
//   OPSIN turns a name into a SMILES,
//   CDK reads the SMILES and gives the InChIKey of the structure.
//
// Usage: groovy ner_chemicals.groovy literature/PMC1234.xml literature/PMC1234.chem.tsv
//
// The output has one line per unique name that Oscar found, with the columns
// name, smiles and inchikey. The last two are empty when OPSIN could not
// convert the name or CDK could not read the SMILES.

// Bacting config
@Grab(group='io.github.egonw.bacting', module='managers-opsin', version='1.0.14')
@Grab(group='io.github.egonw.bacting', module='managers-oscar', version='1.0.14')
@Grab(group='io.github.egonw.bacting', module='managers-cdk', version='1.0.14')
@Grab(group='org.openscience.cdk', module='cdk-inchi', version='2.13')

import javax.xml.parsers.SAXParserFactory
import org.xml.sax.helpers.DefaultHandler
import org.xml.sax.Attributes
import java.nio.file.Files
import java.nio.file.Paths

import net.bioclipse.managers.OpsinManager
import net.bioclipse.managers.OscarManager
import net.bioclipse.managers.CDKManager
import org.openscience.cdk.inchi.InChIGeneratorFactory

/** Collects the text of the article, one string per paragraph, title or table cell. */
class TextHandler extends DefaultHandler {
    // elements whose text is given to Oscar as one piece
    static final Set<String> BLOCKS = ["p", "title", "article-title", "td", "th"] as Set
    // elements that are left out: the reference list and formulas
    static final Set<String> SKIP = ["ref-list", "tex-math", "math", "mml:math", "object-id", "journal-meta"] as Set

    List<String> texts = []
    StringBuilder current = new StringBuilder()
    int blockDepth = 0
    int skipDepth = 0

    private void flush() {
        String text = current.toString().replaceAll(/\s+/, " ").trim()
        if (text.length() >= 5) texts.add(text)
        current.setLength(0)
    }

    @Override
    void startElement(String uri, String localName, String qName, Attributes attributes) {
        if (SKIP.contains(qName)) skipDepth++
        if (BLOCKS.contains(qName)) { flush(); blockDepth++ }
    }

    @Override
    void endElement(String uri, String localName, String qName) {
        if (SKIP.contains(qName)) skipDepth--
        if (BLOCKS.contains(qName)) { flush(); blockDepth-- }
    }

    @Override
    void characters(char[] ch, int start, int length) {
        if (blockDepth > 0 && skipDepth == 0) current.append(ch, start, length)
    }
}

if (args.length != 2) {
    System.err.println("Usage: groovy ner_chemicals.groovy <article.xml> <output.tsv>")
    System.exit(1)
}
String input = args[0]
String output = args[1]

SAXParserFactory factory = SAXParserFactory.newInstance()
// the JATS DTD is not on disk and not needed
factory.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false)
def handler = new TextHandler()
Files.newInputStream(Paths.get(input)).withCloseable { factory.newSAXParser().parse(it, handler) }

String workspaceRoot = "."
def opsin = new OpsinManager(workspaceRoot)
def oscar = new OscarManager(workspaceRoot)
def cdk = new CDKManager(workspaceRoot)
def inchiFactory = InChIGeneratorFactory.getInstance()

// name -> [smiles, inchikey]; kept in the order in which the names are found
Map<String, List<String>> names = new LinkedHashMap<>()
for (String text : handler.texts) {
    for (String entity : oscar.findNamedEntities(text)) {
        String name = entity.replaceAll(/\s+/, " ").trim()
        if (name.length() <= 2 || names.containsKey(name)) continue
        String smiles = ""
        String inchikey = ""
        try {
            smiles = opsin.parseIUPACNameAsSMILES(name)
            def mol = cdk.fromSMILES(smiles)
            String key = inchiFactory.getInChIGenerator(mol.getAtomContainer()).getInchiKey()
            if (key != null) inchikey = key
        } catch (Exception e) {
            // not a name OPSIN knows, or not a SMILES that CDK can read
            if (inchikey.isEmpty() && !smiles.isEmpty() && !(e.message ?: "").isEmpty()) {
                System.err.println("${name}: ${e.message}")
            }
        }
        names.put(name, [smiles ?: "", inchikey])
    }
}

new File(output).withWriter("UTF-8") { out ->
    out.write("name\tsmiles\tinchikey\n")
    names.each { name, v -> out.write("${name}\t${v[0]}\t${v[1]}\n") }
}
int converted = names.values().count { !it[1].isEmpty() }
println("${input}: ${handler.texts.size()} text blocks, ${names.size()} Oscar names, ${converted} with a structure -> ${output}")
