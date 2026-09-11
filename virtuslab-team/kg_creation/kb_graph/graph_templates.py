"""maplib OTTR templates, defined programmatically (maplib's confirmed Python
API — see https://datatreehouse.github.io/maplib/maplib.html). No repo
content schema assumed: generic templates only, matching transform.py's
DataFrames — Document, Property (dynamic predicate), Label, Entity (which
carries the document -> entity edge), and Relation (dynamic predicate,
entity -> entity, from kg-gen).

Relations are written twice, deliberately:

1. As the plain triple `subject predicate object`, so the graph stays
   traversable with an ordinary one-hop query.
2. As a reified `:Assertion` node (`rdf:subject`/`rdf:predicate`/
   `rdf:object`) carrying `:derivedFrom` (the source Document),
   `:extractionModel` and `:extractedAt`.

The plain triple alone cannot answer "which document produced this edge?" —
RDF has nowhere to hang that on a bare triple. The reification pays about
seven extra triples per edge for that answer. Consumers that only traverse
can ignore `:Assertion` entirely.
"""
from __future__ import annotations

from maplib import IRI, Parameter, Prefix, RDFType, Template, Triple, Variable

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDF_TYPE = IRI(f"{RDF}type")
RDF_SUBJECT = IRI(f"{RDF}subject")
RDF_PREDICATE = IRI(f"{RDF}predicate")
RDF_OBJECT = IRI(f"{RDF}object")

XSD = "http://www.w3.org/2001/XMLSchema#"
_STRING = RDFType.Literal(IRI(f"{XSD}string"))
_DATETIME = RDFType.Literal(IRI(f"{XSD}dateTime"))
_INTEGER = RDFType.Literal(IRI(f"{XSD}integer"))


def build_templates(base_uri: str) -> dict[str, Template]:
    kg = Prefix(base_uri)

    document_template = Template(
        iri=kg.suf("DocumentTemplate"),
        parameters=[
            Parameter(Variable("DocIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("SourceUri"), rdf_type=RDFType.Literal(IRI("http://www.w3.org/2001/XMLSchema#string"))),
            Parameter(Variable("ContentType"), rdf_type=RDFType.Literal(IRI("http://www.w3.org/2001/XMLSchema#string"))),
            Parameter(Variable("SizeBytes"), rdf_type=RDFType.Literal(IRI("http://www.w3.org/2001/XMLSchema#integer"))),
            Parameter(Variable("HasText"), rdf_type=RDFType.Literal(IRI("http://www.w3.org/2001/XMLSchema#boolean"))),
            Parameter(Variable("TextTruncated"), rdf_type=RDFType.Literal(IRI("http://www.w3.org/2001/XMLSchema#boolean"))),
        ],
        instances=[
            Triple(Variable("DocIri"), RDF_TYPE, kg.suf("Document")),
            Triple(Variable("DocIri"), kg.suf("sourceUri"), Variable("SourceUri")),
            Triple(Variable("DocIri"), kg.suf("contentType"), Variable("ContentType")),
            Triple(Variable("DocIri"), kg.suf("sizeBytes"), Variable("SizeBytes")),
            # The document body is deliberately NOT in the graph: Bedrock's KB
            # already stores and chunks it, the checkpoint keeps it for resume,
            # and nothing here queries it. These two flags are what survive of
            # it, and both carry information the body's absence would otherwise
            # hide — :hasText separates "no term matched this document" from
            # "this scanned PDF was never extracted", and :textTruncated says
            # PDF_TEXT_MAX_CHARS clipped the text, so the index is partial for
            # this document.
            Triple(Variable("DocIri"), kg.suf("hasText"), Variable("HasText")),
            Triple(Variable("DocIri"), kg.suf("textTruncated"), Variable("TextTruncated")),
        ],
    )

    property_template = Template(
        iri=kg.suf("PropertyTemplate"),
        parameters=[
            Parameter(Variable("DocIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("PredicateIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("Value"), rdf_type=RDFType.Literal(IRI("http://www.w3.org/2001/XMLSchema#string"))),
        ],
        instances=[
            Triple(Variable("DocIri"), Variable("PredicateIri"), Variable("Value")),
        ],
    )

    # One :Label node per normalized label form, shared corpus-wide. The
    # lexical grouping that document-scoped entities opt into — see
    # entity_extraction.py's "Identity model" docstring.
    label_template = Template(
        iri=kg.suf("LabelTemplate"),
        parameters=[
            Parameter(Variable("LabelIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("LabelText"), rdf_type=_STRING),
        ],
        instances=[
            Triple(Variable("LabelIri"), RDF_TYPE, kg.suf("Label")),
            Triple(Variable("LabelIri"), kg.suf("labelText"), Variable("LabelText")),
        ],
    )

    entity_template = Template(
        iri=kg.suf("EntityTemplate"),
        parameters=[
            Parameter(Variable("EntityIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("Label"), rdf_type=_STRING),
            Parameter(Variable("LabelIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("DocIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("MentionCount"), rdf_type=_INTEGER),
        ],
        instances=[
            Triple(Variable("EntityIri"), RDF_TYPE, kg.suf("Entity")),
            Triple(Variable("EntityIri"), kg.suf("label"), Variable("Label")),
            Triple(Variable("EntityIri"), kg.suf("hasLabelForm"), Variable("LabelIri")),
            # The document -> entity edge, emitted from this row because this
            # row *is* the posting: an entity IRI hashes its document id, so a
            # posting cannot exist without exactly one document, and the edge
            # cannot go missing. It used to come from a separate Mention
            # template with its own table, which iterated a narrower set and so
            # left a quarter of the postings with no edge at all. One table,
            # one edge. :derivedFrom is now Assertion's alone.
            Triple(Variable("DocIri"), kg.suf("mentions"), Variable("EntityIri")),
            # Occurrences of the label in this document — ranks documents for a
            # term without a second pass over the corpus.
            Triple(Variable("EntityIri"), kg.suf("mentionCount"), Variable("MentionCount")),
        ],
    )

    relation_template = Template(
        iri=kg.suf("RelationTemplate"),
        parameters=[
            Parameter(Variable("AssertionIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("SubjectIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("PredicateIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("ObjectIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("DocIri"), rdf_type=RDFType.IRI),
            Parameter(Variable("ExtractionModel"), rdf_type=_STRING),
            Parameter(Variable("ExtractedAt"), rdf_type=_DATETIME),
        ],
        instances=[
            Triple(Variable("SubjectIri"), Variable("PredicateIri"), Variable("ObjectIri")),
            Triple(Variable("AssertionIri"), RDF_TYPE, kg.suf("Assertion")),
            Triple(Variable("AssertionIri"), RDF_SUBJECT, Variable("SubjectIri")),
            Triple(Variable("AssertionIri"), RDF_PREDICATE, Variable("PredicateIri")),
            Triple(Variable("AssertionIri"), RDF_OBJECT, Variable("ObjectIri")),
            Triple(Variable("AssertionIri"), kg.suf("derivedFrom"), Variable("DocIri")),
            Triple(Variable("AssertionIri"), kg.suf("extractionModel"), Variable("ExtractionModel")),
            Triple(Variable("AssertionIri"), kg.suf("extractedAt"), Variable("ExtractedAt")),
        ],
    )

    return {
        "document": document_template,
        "property": property_template,
        "label": label_template,
        "entity": entity_template,
        "relation": relation_template,
    }
