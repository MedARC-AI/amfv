"""Per-document and per-publisher keep/drop overrides for the MAGICapp scraper.

Data, not logic: every table is keyed on a guideline's ``shortCode`` or a publisher's
``institutionName``, and consumed by :mod:`amfv_datasets.scraping.magic`. Built by
reading the in-corpus guidelines end to end.

Tables, in order:

* ``_INSTITUTION_SKIP_HEADINGS`` - headings that are paperwork for one publisher only
* ``_GUIDELINE_KEEP_HEADINGS`` - sections exempt from a corpus-wide drop rule, per guideline
* ``_GUIDELINE_SKIP_HEADINGS`` - headings that are paperwork in one named guideline
* ``_GUIDELINE_INLINE_SKIP_HEADINGS`` - paperwork headings written inside section bodies
* ``_GUIDELINE_DROP_BLOCKS`` - hand-written text blocks to remove, per guideline
* ``_GUIDELINE_PAPERWORK_DROP_BLOCKS`` - paperwork text blocks from the corpus read-through
"""

from __future__ import annotations

# Headings that are paperwork for one publisher and content for everyone else: a publisher is
# internally consistent in a way the corpus as a whole is not. Keyed on `institutionName` as the
# catalogue spells it, which is what `ref.institution` carries.
_INSTITUTION_SKIP_HEADINGS: dict[str, frozenset[str]] = {
    "ANZ Hearing Health Collaborative": frozenset(
        {"overview of methodology", "patient hearing journey", "what are living guidelines?"}
    ),
    "AWMF - Arbeitsgemeinschaft der Wissenschaftlichen Medizinischen Fachgesellschaften": frozenset(
        {
            "approval",
            "citation",
            "comments on the consultation version",
            "composition of the guideline group",
            "external review",
            "external review and approval",
            "formulation and grading of recommendations and statements",
            "implementation manual",
            "information about the guideline",
            "list of abbreviations",
            "methodological approach",
            "methodological support",
            "participating professional societies and organizations",
            "scope and purpose of the guideline",
        }
    ),
    "Alzheimer's Association": frozenset(
        {"guideline panel composition", "guideline scope", "guideline update process and external review process"}
    ),
    "American Dental Association and the University of Pennsylvania School of Dental Medicine": frozenset(
        {"appendix 3. interest-holders", "chairside guides", "conversation aids", "summary"}
    ),
    "Australia & New Zealand Musculoskeletal Clinical Trials Network": frozenset(
        {"glossary, abbreviations and acronyms"}
    ),
    "Australian Living Evidence Collaboration": frozenset(
        {"methods for the 2020 australian clinical practice guidelines"}
    ),
    "BE-SAFE": frozenset(
        {
            "adaptation and implementation of the guideline",
            "guideline development process",
            "navigate the guideline and related content",
        }
    ),
    "CARI Guidelines: Caring for Australians and New Zealanders with Kidney impairment": frozenset(
        {"guideline development methodology", "guideline development methods", "implementation and audit"}
    ),
    "Canadian Rheumatology Association": frozenset({"methods"}),
    "Cancer Council Australia": frozenset(
        {
            "applicability to the australian setting",
            "barriers to implementation",
            "citation",
            "consideration of priority groups",
            "consultation",
            "dissemination and implementation",
            "feedback received during the consultation and how it was addressed",
            "foreword",
            "glossary of technical terms and abbreviations",
            "glossary of terms and abbreviations",
            "guideline development process",
            "guidelines development process",
            "introduction and guideline development",
            "key implementation considerations",
            "literature search",
            "methodological issues",
            "post-public consultation draft revisions",
            "process of developing aya clinical guidance",
            "public consultation and independent expert review",
            "publication approval",
            "purpose",
            "purpose and scope",
            "purpose of this guidance",
            "remit (scope)",
            "review process",
            "scope",
            "scope of this guideline",
            "screening of literature results against pre-defined inclusion and exclusion criteria",
            "statement of intent/disclaimer",
            "writing the content",
        }
    ),
    "Deakin Lifespan Institute": frozenset(
        {"scope and equity considerations of the guidelines", "the purpose of the guidelines"}
    ),
    "Deutsche Gesellschaft für Psychiatrie und Psychotherapie, Psychosomatik und Nervenheilkunde e. V. "
    "(DGPPN)": frozenset({"preface", "scope and purpose"}),
    "European Association for Endoscopic Surgery and other Interventional Techniques": frozenset(
        {"purpose, scope and target users"}
    ),
    "European Mosquito Control Association (EMCA)": frozenset({"governance – stakeholders", "references to chapter 4"}),
    "GELA": frozenset(
        {
            "dissemination and updating of the guideline and recommendations",
            "dissemination, applicability and updating of the guideline and recommendations",
            "implementation considerations",
            "purpose",
            "scope",
        }
    ),
    "MAGIC Evidence Ecosystem Foundation": frozenset(
        {
            "about the guideline and how to navigate it",
            "coi management",
            "guideline panel",
            "implementation",
            "implementation and adaptation of this guideline",
            "methodology for development of primary care rapid recommendations",
            "methods to inform values and preferences discussions for recommendations",
            "navigate the guideline and related content",
            "navigating the guideline and related content",
        }
    ),
    "Malawi Ministry of Health": frozenset({"acronyms & abbreviations"}),
    "McMaster Centre for Transfusion": frozenset({"scope and objectives of guidelines"}),
    "Monash University, Faculty of Pharmacy and Pharmaceutical Sciences": frozenset(
        {"about this guideline", "governance and stakeholder involvement", "purpose of guideline"}
    ),
    "National Blood Authority": frozenset({"governance", "governance and process"}),
    "National Health and Medical Research Council (NHMRC)": frozenset({"index", "scope"}),
    "National Neonatology Forum of India": frozenset({"literature search strategies", "scope of the guidelines"}),
    "National Pain Centre": frozenset({"scope", "scope of the guideline and how to use the guideline"}),
    "Nordic Federation of Obstetrics and Gynecology": frozenset({"contributions of authors"}),
    "Norwegian Orthopaedic Association - The Norwegian Medical Association": frozenset({"method and background"}),
    "Stroke Foundation": frozenset({"introduction"}),
    "Sundhedsstyrelsen": frozenset({"implementation"}),
    "The CI Task Force": frozenset({"overview of methodology", "what are living guidelines?"}),
    "The George Institute for Global Health India": frozenset({"endorsement of the guideline", "scope of guideline"}),
    "The Scandinavian Society of Anaesthesiology and Intensive Care Medicine (SSAI)": frozenset(
        {
            "about this guideline from the scandinavian society for anesthesiology and intensive care (ssai)",
            "citation",
            "methods",
        }
    ),
    "WHO maternal and perinatal health recommendations": frozenset(
        {
            "activities prioritized by the gdg for dissemination and implementation of the guideline",
            "changes from the approved scope of this guideline",
            "dissemination adaptation and implementation of the recommendation",
            "dissemination and implementation",
            "dissemination and implementation of recommendations",
            "dissemination and implementation of the recommendations",
            "dissemination of the recommendation",
            "dissemination of the recommendations",
            "dissemination, applicability and updating of the guideline and recommendations",
            "foreword",
            "implementation of the guidelines",
            "implementation of the recommendations",
            "list of participants in the scoping and two final panel meetings",
            "participants in the guideline development process",
            "review and updating of the recommendations",
            "updating of the guideline",
            "updating the guideline",
        }
    ),
    "World Health Organization (WHO)": frozenset(
        {
            "abbreviations, acronyms and symbols",
            "applicability",
            "decision-making during the consultation",
            "dissemination, applicability and updating of the guideline and recommendations",
            "dissemination, evaluation and plans for updating",
            "dissemination, implementation and future updates",
            "external peer review",
            "foreword",
            "gdg topic-specific working groups",
            "guideline development and compilation process",
            "guideline development and implementation",
            "guideline development process",
            "guideline development process and methods",
            "how to use this guideline?",
            "implementation tools",
            "methodology",
            "methods of guideline development",
            "methods: how this guideline was created",
            "monitoring and evaluation of guideline implementation",
            "peer review and approval of the guidelines",
            "preface",
            "publication, dissemination, implementation, monitoring and evaluation",
            "publication, dissemination, monitoring and evaluation",
            "risk factors for severe disease and prognosis methodology",
            "scope",
            "scope and formulation of picos",
            "scope and target audience",
            "search strategy and terminology for reported routes of mpxv infection",
            "step-wise approach - application of grade methodology",
            "technical consultation",
            "updating recommendations",
            "updating the guideline",
        }
    ),
}


# Headings a general rule would drop, in the one guideline where it must not. This exempts those
# sections rather than deleting the general entries, deliberately: each entry earns its place on a
# measurement, and what is recorded here is the specific guidelines where the general rule is wrong.
_GUIDELINE_KEEP_HEADINGS: dict[str, frozenset[str]] = {
    "6nYJxE": frozenset({"glossary and abbreviations", "introduction", "methodology"}),
    "8L0RME": frozenset({"glossary and abbreviations", "introduction", "methodology"}),
    "8nyb0E": frozenset({"scope of the guideline and how to use the guideline"}),
    "BjOM9n": frozenset({"method and background"}),
    "E52Obj": frozenset({"administrative report", "glossary"}),
    "E80D0E": frozenset({"dissemination and implementation of the recommendations", "executive summary", "methods"}),
    "E80ezE": frozenset({"applicability issues"}),
    "E83abn": frozenset(
        {"glossary", "guidelines development process", "key implementation considerations", "purpose and scope"}
    ),
    "EK0DDj": frozenset({"preface", "scope and purpose", "what's new?"}),
    "EK0ldj": frozenset({"methodology"}),
    "EKKOyE": frozenset({"dissemination and implementation of the recommendations"}),
    "EKeJyL": frozenset({"applicability issues", "prioritization of the outcomes", "research implications"}),
    "EPY83j": frozenset({"methods"}),
    "EQ3m3L": frozenset({"methods"}),
    "ERWMXj": frozenset({"methods"}),
    "ERWdzj": frozenset({"background to deliberations"}),
    "EZVOaE": frozenset({"glossary"}),
    "EZYMwn": frozenset({"methods"}),
    "EZvY8E": frozenset({"methods", "research implications"}),
    "Ea7gOL": frozenset({"methods: how this guideline was created"}),
    "EaG1dL": frozenset({"methods", "patient version"}),
    "Edr04L": frozenset({"development of the guidelines"}),
    "Ee438n": frozenset({"executive summary", "glossary", "methods", "purpose of guideline"}),
    "Ee4Orn": frozenset({"methods", "scope and objectives of guidelines"}),
    "Eea27E": frozenset({"methods", "search strategy"}),
    "Eez2Kj": frozenset({"glossary of terms and abbreviations", "guideline development process"}),
    "Eez3Kj": frozenset({"executive summary", "introduction and guideline development"}),
    "EezrQj": frozenset({"methods: how this guideline was created"}),
    "Eg9eVL": frozenset({"applicability issues", "research implications"}),
    "EgJmpn": frozenset({"guideline development methodology"}),
    "EgXyej": frozenset({"applicability issues"}),
    "EvqB0n": frozenset(
        {
            "breastfeeding technical working group pre-gdg discussion",
            "gdg topic-specific working groups",
            "hiv antiretroviral technical working group pre-gdg discussion",
            "infection prevention and control technical working group",
            "methods: how this guideline was created",
            "search strategy and terminology for reported routes of mpxv infection",
        }
    ),
    "Evqmmn": frozenset(
        {"challenges", "evidence gaps and potential research priorities", "methodology", "supply considerations"}
    ),
    "Jn37kn": frozenset({"glossary", "research gaps"}),
    "Kj2R8j": frozenset({"glossary and abbreviations", "introduction", "methodology"}),
    "L4Q5An": frozenset({"methods and processes"}),
    "L6RxYL": frozenset({"methods: how this guideline was created"}),
    "L6zBvL": frozenset({"methods", "protocol"}),
    "LAR07n": frozenset({"glossary", "key areas for future development"}),
    "LAag6L": frozenset({"methods", "patient version"}),
    "LAkxVE": frozenset({"methods", "research implications"}),
    "LGm87E": frozenset({"purpose, scope and target users"}),
    "Lkk3pL": frozenset({"foreword", "guideline development process"}),
    "Lpmozn": frozenset({"guideline development methods"}),
    "Lpv2kE": frozenset({"protocol", "purpose, scope and target users"}),
    "LpvNkE": frozenset({"methods"}),
    "Lq0orj": frozenset({"executive summary", "methods"}),
    "LqGR0E": frozenset({"dissemination and implementation of the recommendation", "methods", "research implications"}),
    "LqgJ3E": frozenset({"executive summary"}),
    "LrRxrL": frozenset({"glossary", "scope and target audience"}),
    "LwRMXj": frozenset({"glossary"}),
    "Lwq0oE": frozenset({"research implications"}),
    "LwqRXE": frozenset({"dissemination and implementation of recommendations"}),
    "LwqZeE": frozenset(
        {"applicability issues", "dissemination adaptation and implementation of the recommendation", "methods"}
    ),
    "LwvKej": frozenset({"patient version"}),
    "LwvpGj": frozenset({"methods"}),
    "NnV76E": frozenset({"glossary and abbreviations", "introduction", "methodology"}),
    "QnoKGn": frozenset(
        {
            "brief summary of grade",
            "cost effectiveness summaries",
            "explanation of absolute effect estimates used",
            "glossary and abbreviations",
            "introduction",
            "methodology",
            "strength of recommendations",
        }
    ),
    "VLpK8j": frozenset(
        {
            "brief summary of grade",
            "cost effectiveness summaries",
            "explanation of absolute effect estimates used",
            "glossary and abbreviations",
            "introduction",
            "methodology",
            "strength of recommendations",
        }
    ),
    "WE8wOn": frozenset({"glossary and abbreviations", "introduction", "methodology"}),
    "aEeKpL": frozenset({"background and methods for bmj-rapidrecs"}),
    "j1O57n": frozenset({"monitoring"}),
    "j1Q1Xj": frozenset({"applicability to the australian setting", "glossary"}),
    "j1QPrj": frozenset({"executive summary", "research implication"}),
    "j1WYVn": frozenset({"methods"}),
    "j1Wqrn": frozenset(
        {
            "how to use these recommendations/understanding the recommendations",
            "methods to inform values and preferences discussions for recommendations",
        }
    ),
    "j1k9Jn": frozenset({"dissemination and implementation of the recommendations"}),
    "j1kmYn": frozenset({"applicability issues", "methods", "research priorities"}),
    "j20X4n": frozenset({"patient version", "protocol"}),
    "j2QPrE": frozenset({"research implications"}),
    "j2QQ4E": frozenset({"applicability issues", "research implications"}),
    "j2QZZE": frozenset({"executive summary", "methods", "updating the recommendations"}),
    "j2bBrj": frozenset({"glossary", "glossary and abbreviations"}),
    "j7mQNn": frozenset({"dissemination and implementation of the recommendations"}),
    "j7q7Gn": frozenset({"patient version", "protocol"}),
    "j98OoE": frozenset({"glossary", "suggestions for future research"}),
    "jDReJn": frozenset({"applicability issues", "executive summary"}),
    "jDRvgn": frozenset({"executive summary", "governance – stakeholders", "methods"}),
    "jDePyL": frozenset({"glossary", "other information"}),
    "jDeeDL": frozenset({"executive summary", "methods: how this guideline was created"}),
    "jMMeqj": frozenset({"about this guideline", "executive summary", "glossary"}),
    "jNW0VL": frozenset({"evidence summary"}),
    "jNxJmn": frozenset({"methods", "scope"}),
    "jO0lNL": frozenset({"executive summary", "implementation considerations"}),
    "jO3B7j": frozenset({"protocol"}),
    "jOK05j": frozenset({"methods"}),
    "jOKYGj": frozenset({"how the guideline was made"}),
    "jW0ZbL": frozenset({"glossary"}),
    "jW9Gpn": frozenset({"methods"}),
    "jWN6oE": frozenset({"applicability issues", "executive summary"}),
    "jXXAdj": frozenset({"scope of this guideline"}),
    "jXXBBj": frozenset({"scope of guideline"}),
    "jXXZNj": frozenset({"executive summary"}),
    "jbXYZn": frozenset({"dissemination, adaptation and implementation of the recommendation"}),
    "jlAbxL": frozenset({"guideline development process"}),
    "jlPRdj": frozenset({"executive summary", "guideline development process"}),
    "jxxdwj": frozenset({"methods"}),
    "jz5DdE": frozenset({"barriers to implementation", "glossary"}),
    "jz7rXL": frozenset({"how these recommendations were created"}),
    "jzQAlE": frozenset({"dissemination and implementation of the recommendations"}),
    "mL6yYj": frozenset({"bmj rapid recommendations: background and methods"}),
    "n303gE": frozenset(
        {"executive summary", "guideline development and implementation", "guideline development process"}
    ),
    "n3QAOj": frozenset({"clinical question list", "foreword", "glossary"}),
    "n3QGej": frozenset({"methodology"}),
    "n3QxOj": frozenset({"foreword", "glossary of technical terms and abbreviations"}),
    "nBAZDL": frozenset({"methodology", "scope of the guidelines"}),
    "nBAezL": frozenset(
        {
            "how this guideline was created",
            "implementation",
            "implementation and adaptation of this guideline",
            "methodology",
            "navigate the guideline and related content",
        }
    ),
    "nBRK8n": frozenset({"methods: how this guideline was created"}),
    "nBkO1E": frozenset({"methods: how this guideline was created"}),
    "nBkgRE": frozenset({"methods"}),
    "nBpo1j": frozenset({"executive summary", "scope"}),
    "nJ5zyL": frozenset({"methods", "patient version"}),
    "nV6X3n": frozenset({"glossary", "glossary and abbreviations", "guideline amendments"}),
    "nV6zvn": frozenset({"methods: how this guideline was created"}),
    "nVY73L": frozenset({"scope of the guidelines"}),
    "nYYb4n": frozenset({"foreword"}),
    "noPKwE": frozenset(
        {"barriers to implementation", "glossary and abbreviations", "methodological issues", "target populations"}
    ),
    "noPQkE": frozenset(
        {
            "evidence retrieval, synthesis, and assessment",
            "glossary",
            "guideline development process and methods",
            "scope",
        }
    ),
    "noaRMj": frozenset({"how this guideline was made"}),
    "ny70vj": frozenset({"patient version"}),
    "ny74yj": frozenset({"methods"}),
    "ny76yj": frozenset({"methods"}),
    "nyO1Yj": frozenset({"guideline scope"}),
    "nyONYj": frozenset({"about the guidelines", "methods and processes"}),
    "nyX5xL": frozenset(
        {
            "facilitating and hindering factors for the application of the guideline and quality indicators",
            "information about the guideline",
        }
    ),
    "nyXKVL": frozenset({"changes from the approved scope of this guideline", "methods"}),
    "nyXP0L": frozenset({"dissemination and implementation of the recommendations", "executive summary", "methods"}),
    "nyXxZL": frozenset({"methods", "research implications"}),
    "ojmKvn": frozenset(
        {
            "brief summary of grade",
            "cost effectiveness summaries",
            "explanation of absolute effect estimates used",
            "glossary and abbreviations",
            "introduction",
            "methodology",
            "strength of recommendations",
        }
    ),
    "pEQmQE": frozenset({"methods"}),
}


# Headings that are paperwork in ONE guideline, keyed on its `shortCode`. The publisher table above
# cannot express these: the same heading is paperwork in one of a publisher's guidelines and content
# in another. Document-scoped even where it could be a publisher rule, to keep the blast radius
# small.
_GUIDELINE_SKIP_HEADINGS: dict[str, frozenset[str]] = {
    "6nYJxE": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "clinical questions",
            "cost effectiveness summaries",
            "explanation of absolute effect estimates used",
            "strength of recommendations",
        }
    ),
    "8L0RME": frozenset({"abbreviations", "clinical questions"}),
    "BjOM9n": frozenset({"hearings"}),
    "E52Obj": frozenset(
        {"aim", "capturing new evidence", "how the evidence was used", "target audience", "the guideline format"}
    ),
    "E5mWbE": frozenset({"conclusion"}),
    "E80D0E": frozenset({"executive summary"}),
    "E83abn": frozenset({"health care settings in which the guidelines will be applied", "intended users"}),
    "EK0DDj": frozenset({"german version", "publisher", "wording of recommendations"}),
    "EKeJyL": frozenset({"results"}),
    "EPY83j": frozenset(
        {"contextualizing guidance", "contextualizing the guidelines – workbook", "the scoping questions"}
    ),
    "ERWdzj": frozenset({"background to deliberations"}),
    "ERx1yL": frozenset({"early detection of cancer in ayas"}),
    "EZVOaE": frozenset({"purpose and target audience"}),
    "EZVlYE": frozenset({"acronyms and abrebiations"}),
    "EZYMwn": frozenset({"discussion"}),
    "EZvY8E": frozenset({}),
    "EaG1dL": frozenset({"conclusion"}),
    "EaKvXL": frozenset({"grade assessment and evidence-to-decision tables"}),
    "Ee438n": frozenset({"executive summary", "supporting materials to implement the guideline"}),
    "Ee4Orn": frozenset({"guideline recommendation questions"}),
    "Ee4mAn": frozenset(
        {
            "abbreviations",
            "global evidence, local adaptation (gela) project information",
            "monitoring and evaluation",
            "organisation, planning, budget and training",
            "overview",
            "target audience",
        }
    ),
    "Eez2Kj": frozenset({"context", "development of these guidelines", "safety monitoring", "target readership"}),
    "Eez3Kj": frozenset({"executive summary"}),
    "EezrQj": frozenset({"visual summary"}),
    "EgJmpn": frozenset({"guideline information"}),
    "EgXyej": frozenset({"priority outcomes for decision-making"}),
    "EvqB0n": frozenset({"a cknowledgements", "collection of standardized data and the who clinical platform"}),
    "Evqmmn": frozenset(
        {"challenges", "implementing, evaluation and maintaining the guideline", "major haemorrhage protocol (mhp)"}
    ),
    "Jn37kn": frozenset({"process report"}),
    "Kj2R8j": frozenset({"abbreviations", "clinical questions"}),
    "Kj2WZL": frozenset(
        {
            "assessment of evidence",
            "description of the method used",
            "description of the strength and implications of recommendations",
            "monitoring",
            "tables concerning focused question 1-3",
        }
    ),
    "L6RxYL": frozenset({"who made this guideline?"}),
    "L6zBvL": frozenset({"conclusion"}),
    "LAR07n": frozenset({"what the guidelines include", "who the guidelines are for"}),
    "LAag6L": frozenset({"conclusion"}),
    "LG45vn": frozenset({"introduction"}),
    "LGm87E": frozenset({"conclusion", "figures"}),
    "Lkk3pL": frozenset(
        {
            "drug therapies in patients with advanced melanoma",
            "note on recommendations based on this evidence",
            "table 4. nhmrc approved recommendation types and definitions",
        }
    ),
    "Lpv2kE": frozenset({"conclusion"}),
    "Lq0orj": frozenset({"executive summary"}),
    "LqGR0E": frozenset({"dissemination and implementation of the recommendation"}),
    "LqgJ3E": frozenset(
        {
            "coronavirus (covid-19) infection in pregnancy – summary of updates",
            "executive summary",
            "identification and assessment of evidence",
            "ii: summary of key studies and meta-analysis on maternal and pregnancy outcomes",
        }
    ),
    "Lr21gL": frozenset(
        {
            "centring human rights and equity in self-care interventions",
            "foreward",
            "knowledge translation for self-care interventions",
            "living guideline approach",
            "objectives",
            "research on self-care and self-care interventions contributing to world health organization's "
            "triple-billion goals",
            "target audience",
            "towards an appropriate approach to research on self-care interventions",
        }
    ),
    "Lr2a8L": frozenset(
        {
            "risk assessment and management of exposure",
            "systematic review for prevention, identification, management of covid-19 in health and care workers",
        }
    ),
    "LrRxrL": frozenset({"guiding principles", "purpose"}),
    "LwRK5j": frozenset({"write section name here"}),
    "LwRMXj": frozenset({"research needs"}),
    "Lwq0oE": frozenset({"priority outcomes used in decision-making"}),
    "LwqRXE": frozenset({}),
    "LwqZeE": frozenset({"priority outcomes used in decision-making"}),
    "LwvKej": frozenset({"conclusion"}),
    "LwvpGj": frozenset({"other who guidelines with recommendations relevant to routine anc"}),
    "NnV76E": frozenset({"clinical question"}),
    "QnoKGn": frozenset({"abbreviations", "clinical questions"}),
    "VLpK8j": frozenset({"abbreviations", "clinical questions"}),
    "WE8wOn": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "clinical questions",
            "cost effectiveness summaries",
            "explanation of absolute effect estimates used",
            "strength of recommendations",
        }
    ),
    "bEvGJj": frozenset({"conclusion"}),
    "j1O57n": frozenset(
        {
            "description of the method used",
            "description of the strength and implications of the recommendations",
            "monitoring",
            "radiological measuring of the radial - angle and length",
            "search description",
            "treatment algorithm for distal radial fracture with dorsal angulation",
        }
    ),
    "j1Q1Xj": frozenset(
        {
            "clinical questions",
            "health care settings in which the guidelines will be applied",
            "intended users",
            "resourcing",
        }
    ),
    "j1QPrj": frozenset({"executive summary", "reference table for summary of findings"}),
    "j1WBYn": frozenset(
        {
            "guideline pruning",
            "guideline pruning (restructuring)",
            "immediate implementation of appropriate infection prevention and control measures",
            "resources for supporting clinical management of covid-19",
        }
    ),
    "j1WYVn": frozenset({"supplemental material"}),
    "j1k9Jn": frozenset({"priority outcomes for decision-making"}),
    "j1kmYn": frozenset({"priority outcomes used in decision-making", "research priorities"}),
    "j20X4n": frozenset({"ranking per outcome table"}),
    "j2QPrE": frozenset({}),
    "j2QZZE": frozenset({"executive summary"}),
    "j7m4dn": frozenset({"priority outcomes for decision-making"}),
    "j7q7Gn": frozenset({"conclusion"}),
    "j98OoE": frozenset(
        {
            "community consultation",
            "flow & thrive",
            "historical context for these guidelines",
            "objective",
            "what do the other guidelines say?",
        }
    ),
    "jDReJn": frozenset({"executive summary"}),
    "jDRvgn": frozenset(
        {"executive summary", "glossary", "objectives of the document and expected outcomes", "references to chapter 6"}
    ),
    "jDePyL": frozenset({"abbreviations used", "what is new in this version and what is coming next?"}),
    "jDeeDL": frozenset({"copyright", "executive summary"}),
    "jMMGZj": frozenset({"introduction"}),
    "jMMeqj": frozenset({"executive summary", "supporting materials to implement the guideline"}),
    "jNW0VL": frozenset({"evidence summary"}),
    "jO0lNL": frozenset(
        {
            "abbreviations",
            "executive summary",
            "global evidence, local adaptation (gela) project information",
            "research gaps",
            "target audience",
        }
    ),
    "jO0qrL": frozenset({"bibliography", "who national oxygen scale-up framework meeting in-person attendees"}),
    "jO3B7j": frozenset({"conclusion", "figures"}),
    "jOKYGj": frozenset({"cadx recommendation", "timeline and key changes"}),
    "jW0ZbL": frozenset({"executive summary", "methodological terms"}),
    "jW9PJn": frozenset({"method", "objectives"}),
    "jWN6oE": frozenset(
        {"executive summary", "results", "scoping and prioritization of the topics covered in the guidelines"}
    ),
    "jXXAdj": frozenset({"the need for an australian guideline", "who this guideline is intended for"}),
    "jXXZNj": frozenset({"executive summary"}),
    "jbXYZn": frozenset({"dissemination, adaptation and implementation of the recommendation"}),
    "jlAbxL": frozenset({"resources for patients and health professionals"}),
    "jlPRdj": frozenset({"executive summary", "financial support", "tools associated with the guideline"}),
    "jm83RE": frozenset(
        {
            "executive summary",
            "health professional summary sheets",
            "methodological terms",
            "methods for the 2020 australian clinical practice guidelines: pregnancy care",
        }
    ),
    "jxBJyn": frozenset({"barcodes"}),
    "jxxdwj": frozenset({"supplemental material"}),
    "jz7rXL": frozenset({"how these recommendations were created"}),
    "n303gE": frozenset({"executive summary"}),
    "n3QGej": frozenset(
        {
            "care pathway",
            "definition of the strength of recommendations",
            "implementing, evaluating and maintaining the guideline",
        }
    ),
    "n3QxOj": frozenset(
        {
            "abbreviations",
            "evidence based recommendation grades",
            "guideline scope",
            "healthcare settings in which the guidelines will be applied",
            "intended users",
            "nhmrc approved recommendation types and definitions",
        }
    ),
    "nBAezL": frozenset({"get in touch"}),
    "nBkO1E": frozenset({"a", "what triggered this update"}),
    "nBpo1j": frozenset(
        {
            "executive summary",
            "global evidence, local adaptation (gela) project information",
            "monitoring and evaluation",
            "target audience",
        }
    ),
    "nJ5zyL": frozenset({"conclusion"}),
    "nV6X3n": frozenset({"abbreviations and acronyms"}),
    "nYvlZE": frozenset({"priority guideline questions and outcomes"}),
    "noPKwE": frozenset(
        {
            "barriers to implentation",
            "healthcare settings in which the guideline will be applied",
            "intended users",
            "target populations",
        }
    ),
    "noPQkE": frozenset({"guideline questions", "purpose", "target audience"}),
    "noaRMj": frozenset({"timeline and key changes"}),
    "ny70vj": frozenset({"conclusion"}),
    "ny74yj": frozenset({}),
    "nyO1Yj": frozenset({"target audience"}),
    "nyONYj": frozenset({"monitoring and auditing criteria", "who developed the guideline"}),
    "nyX5xL": frozenset(
        {
            "cost-benefit analysis",
            "dissemination and implementation",
            "facilitating and hindering factors for the application of the guideline and quality indicators",
            "validity and update procedure",
        }
    ),
    "nyXKVL": frozenset(
        {
            "monitoring and evaluating the impact of the guideline",
            "other who guidelines with recommendations relevant to routine postnatal care",
        }
    ),
    "nyXP0L": frozenset({"executive summary"}),
    "nyXxZL": frozenset({}),
    "ojmKvn": frozenset({"abbreviations", "clinical questions"}),
    "pEQmQE": frozenset({"description of studies", "objectives"}),
}


# Headings a publisher wrote INSIDE a section body, keyed on the guideline's `shortCode` - paperwork
# placed where no rule about sections can see it. Guideline-scoped like `_GUIDELINE_SKIP_HEADINGS`,
# only more so: these words are paperwork in one guideline and content in the next.
_GUIDELINE_INLINE_SKIP_HEADINGS: dict[str, frozenset[str]] = {
    "6nYJxE": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "citation",
            "clinical expert review",
            "cost effectiveness summaries",
            "data extraction, updating evidence summary and grade profile",
            "explanation of absolute effect estimates used",
            "literature identification",
            "strength of recommendations",
        }
    ),
    "8L0RME": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "citation",
            "clinical expert review",
            "cost effectiveness summaries",
            "data extraction, updating evidence summary and grade profile",
            "explanation of absolute effect estimates used",
            "literature identification",
            "strength of recommendations",
        }
    ),
    "8nyb0E": frozenset(
        {
            "external review",
            "guideline format",
            "methodology",
            "panel composition and conflict of interest management",
            "selection and prioritization of questions and outcomes",
            "update of the guideline",
        }
    ),
    "E52Obj": frozenset({"additional evidence", "additional scientific evidence", "how the guidelines were developed"}),
    "EK0ldj": frozenset({"purpose", "scope"}),
    "ERWdzj": frozenset(
        {"public consultation", "purpose", "scientific publications", "scope", "updating and public consultation"}
    ),
    "EZVlYE": frozenset(
        {
            "adaptation",
            "persons affected by the recommendation",
            "rationale and objectives",
            "recommendation dissemination",
            "scope of the recommendation",
            "target audience",
        }
    ),
    "EZvY8E": frozenset({"guideline development methods", "target audience"}),
    "Ea7gOL": frozenset({"broader context", "what are the guideline's objectives?"}),
    "EaKvXL": frozenset({"intervention and comparator", "population"}),
    "Edr04L": frozenset({"citation"}),
    "Ee438n": frozenset({"target audience", "updating the guideline", "what the guideline does not address"}),
    "Ee4mAn": frozenset(
        {
            "abbreviations",
            "acknowledgements",
            "data collection and analysis",
            "decision-making process to reach a recommendation",
            "qualitative evidence",
            "selection criteria",
        }
    ),
    "Eg9eVL": frozenset({"target audience"}),
    "EvqB0n": frozenset({"methods questions"}),
    "Evqmmn": frozenset({"acknowledgements and endorsements", "clinical need for this guideline", "related material"}),
    "Jn37kn": frozenset({"chlorhexidine resistance"}),
    "Kj2R8j": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "citation",
            "clinical expert review",
            "cost effectiveness summaries",
            "data extraction, updating evidence summary and grade profile",
            "explanation of absolute effect estimates used",
            "literature identification",
            "strength of recommendations",
        }
    ),
    "L4Q5An": frozenset({"recommendations"}),
    "Lkk3pL": frozenset({"table 4. nhmrc approved recommendation types and definitions"}),
    "Lq0orj": frozenset({"other who documents"}),
    "LwRMXj": frozenset({"core principles", "objectives", "target audience"}),
    "NnV76E": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "citation",
            "clinical expert review",
            "cost effectiveness summaries",
            "data extraction, updating evidence summary and grade profile",
            "explanation of absolute effect estimates used",
            "literature identification",
            "strength of recommendations",
        }
    ),
    "QnoKGn": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "citation",
            "clinical expert review",
            "cost effectiveness summaries",
            "data extraction, updating evidence summary and grade profile",
            "explanation of absolute effect estimates used",
            "literature identification",
            "strength of recommendations",
        }
    ),
    "VLpK8j": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "citation",
            "clinical expert review",
            "cost effectiveness summaries",
            "data extraction, updating evidence summary and grade profile",
            "explanation of absolute effect estimates used",
            "literature identification",
            "strength of recommendations",
        }
    ),
    "WE8wOn": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "citation",
            "clinical expert review",
            "cost effectiveness summaries",
            "data extraction, updating evidence summary and grade profile",
            "explanation of absolute effect estimates used",
            "literature identification",
            "strength of recommendations",
        }
    ),
    "j2bBrj": frozenset({"citation", "purpose", "scope", "target audience", "use"}),
    "j7mQNn": frozenset({"aim", "target audience"}),
    "jDRvgn": frozenset({"glossary"}),
    "jDeeDL": frozenset(
        {"areas of future research", "optimized supportive care", "rapid diagnostic tests", "therapeutics"}
    ),
    "jMMeqj": frozenset(
        {
            "lodging complaints about medication management",
            "purpose of guideline",
            "target audience",
            "updating the guideline",
        }
    ),
    "jO0lNL": frozenset(
        {
            "abbreviations",
            "contributors to the guideline development process",
            "decision-making process to reach a recommendation",
            "health system",
            "table 3: description of the interpretation of the grade four levels of certainty of evidence",
            "table 4: grade etd criteria and considerations that link to the strength of recommendations",
        }
    ),
    "jOKYGj": frozenset({"what is coming next?"}),
    "jm83RE": frozenset({"resources"}),
    "n303gE": frozenset(
        {
            "how does this survey work?",
            "one more small problem!",
            "the aim",
            "the issues",
            "the solution",
            "this survey asks you to make a decision about what baseline rates we should use",
        }
    ),
    "n3QGej": frozenset(
        {
            "acknowledgements and endorsements",
            "clinical need for this guideline",
            "definition of the strength of recommendations",
            "figure 8.2 international units (ius) of rh d immunoglobulin issued since 2003–04",
            "intent of the guideline",
            "related material",
        }
    ),
    "n3QxOj": frozenset(
        {
            "abbreviations",
            "common concerns raised by patients (section lead: helena rosengren)",
            "early detection (section lead: david whiteman)",
            "metastatic disease and systematic therapies (section lead: alex guminski)[",
            "radiotherapy (section lead: gerald fogarty)",
            "surgical treatment (section lead: peter callan)",
        }
    ),
    "nBpo1j": frozenset(
        {
            "abbreviations",
            "contributors to the guideline development process",
            "decision-making process to reach a recommendation",
            "declarations and management of interests",
            "dr. binyerem c. ukaire, fwacs",
            "formulating questions and selecting outcomes",
            "organization, budget, planning and training",
        }
    ),
    "nV6X3n": frozenset({"abbreviations and acronyms", "citation", "purpose", "scope", "use"}),
    "noPKwE": frozenset(
        {
            "chapter subsections",
            "figure 1.10 trends in number of new cases and age-standardised incidence rates(a) for colorectal cancer "
            "in australian females, 1982 to 2007, projected to 2020",
            "figure 1.4 age-standardised mortality rates for colorectal cancer, australia, 1968–2014",
            "figure 1.7 crude participation in the national bowel cancer screening program, by remoteness area, "
            "2013–2014",
            "nhmrc approved recommendation types and definitions",
        }
    ),
    "noVdWL": frozenset({"formation of pico questions"}),
    "ny70vj": frozenset({"conclusion", "objectives"}),
    "nyONYj": frozenset(
        {
            "as detailed in the methods (section 2.4), these updated recommendations are based on the best available "
            "evidence and a grade assessment of the certainty of evidence, with explicit consideration of benefits "
            "and harms, values and preferences, and system-level implementation considerations",
            "note: detailed information on dosing, treatment duration, and formulations by age and weight is "
            "provided in the relevant sections (5.3.1, 5.4.1) of the guideline and should be consulted when "
            "prescribing",
            "note: detailed information on dosing, treatment duration, and formulations by age and weight is "
            "provided in the relevant sections (6.2) of the guideline and should be consulted when prescribing",
        }
    ),
    "nyXxZL": frozenset({"guideline development methods", "rationale and objectives", "target audience"}),
    "nyxpZL": frozenset({"purpose", "scope", "target population and audience"}),
    "ojmKvn": frozenset(
        {
            "abbreviations",
            "brief summary of grade",
            "citation",
            "clinical expert review",
            "cost effectiveness summaries",
            "data extraction, updating evidence summary and grade profile",
            "explanation of absolute effect estimates used",
            "literature identification",
            "strength of recommendations",
        }
    ),
}


# Blocks a named guideline writes into its body that no rule about headings can reach, keyed on
# `shortCode`. Each entry is the opening of the block to remove and, where the run is longer than
# one block, the opening of the first block that must be KEPT. Matched against the block reduced to
# lowercase with its emphasis stripped, and removal fails closed: if the stop phrase is not found,
# only the opening block goes. Applied to the assembled document, so it reaches text rendered from a
# recommendation as well as from a section body.
_GUIDELINE_DROP_BLOCKS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "jm83RE": (
        ("download health professional summary sheet", ""),
        ("these guidelines are regularly being updated and expanded", ""),
    ),
    "nV6X3n": (("for more detailed information on the development of this recommendation", ""),),
    "nyX5xL": (("appendix", None),),
    "nyxpZL": (
        (
            "an australian living guideline for the management of juvenile idiopathic arthritis is being produced by",
            "",
        ),
    ),
}


# Paperwork blocks to remove, named one guideline at a time - a list rather than logic. Same entry
# shape as `_GUIDELINE_DROP_BLOCKS` above: an empty second element removes the opening block alone,
# and `None` runs to the end of the document, only ever written out deliberately. Matched with
# emphasis and heading marks stripped, tolerant of a leading run of footnote markers or table
# wreckage.
_GUIDELINE_PAPERWORK_DROP_BLOCKS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "6nY0Lg": (("for more information, we refer to the specific recommendations for each type of surgery.", ""),),
    "6nYJxE": (
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        ("implementation consideration clinical indicators are collected as part of the national stroke audit", ""),
        (
            (
                "implementation consideration there is an organisational indicator collected in the national "
                "stroke audit"
            ),
            "",
        ),
        (
            (
                "implementation considerations there is also a clinical indicator collected to determine "
                "whether patients"
            ),
            "",
        ),
        (
            (
                "in conjunction with this chapter, healthcare professionals should also consider discharge "
                "planning, home-based"
            ),
            "",
        ),
        (
            (
                "there is a clinical indicator collected in the national stroke audit to determine whether "
                "patients with stroke"
            ),
            "",
        ),
        ("there is also a clinical indicator collected to determine whether patients were made aware", ""),
    ),
    "8L0RME": (
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        ("antiplatelet use along with af is collected as part of the national stroke audit.", ""),
        ("data are collected against a clinical indicator on early antiplatelet therapy and long term", ""),
        ("there is a clinical indicator collected on anticoagulation therapy in the national stroke audit", ""),
        ("there is a clinical indicator collected on cholesterol-lowering therapy in the national stroke audit", ""),
        ("there is a clinical indicator collected on early antiplatelet therapy and long term (secondary)", ""),
        ("there is a clinical indicator collected on provision of education regarding risk factor modification", ""),
        ("this recommendation will be superseded by the update above once it is approved.", ""),
    ),
    "8nyb0E": (
        (
            (
                "assessment of heterogeneity and subgroup analyses for pooled effect estimates from rcts, we "
                "examined heterogeneity"
            ),
            "",
        ),
        ("cadth has compiled the best available evidence to inform decisions on non-opioid therapies", ""),
        (
            (
                "data abstraction teams of reviewers abstracted data, independently and in duplicate, from "
                "each eligible study"
            ),
            "",
        ),
        ("evaluating risk of bias in individual studies reviewers assessed the risk of bias from eligible", ""),
        ("for observational studies, we pooled adjusted odds ratios using random effects models.", ""),
        ("identifying the evidence each pico question was informed by one or more systematic reviews", ""),
        ("in 2014, the canadian federal government expanded the focus of the national anti-drug strategy", ""),
        ("to optimize interpretation of the wmd, we calculated the proportion of patients in the intervention", ""),
        ("using standardized forms, reviewers screened, independently and in duplicate, titles and abstracts", ""),
        (
            "we applied the grade system to move from evidence to recommendations.",
            "our systematic reviews either identified sufficient evidence to justify making a formal clinical",
        ),
    ),
    "BjOM9n": (
        ("alle hearing responses are published on www.håndleddsbrudd.no.", ""),
        ("we thank norwegian orthopaedic association for the mandate and trust we were given", ""),
    ),
    "E52Obj": (
        ("for more advice on alcohol and pregnancy, please visit the alcohol and drug foundation", ""),
        ("source: australian institute of health and welfare, 2017.", ""),
        ("source: healthlink bc, 2019 and international alliance for responsible drinking, 2019.", ""),
        ("where can i get help? more information about alcohol and young people can be found", ""),
        ("where can i get help? seek professional advice if you have questions about this information.", ""),
        ("where can i get help? the australian government department of health recommends the services", ""),
    ),
    "E5AbPE": (
        ("a summary of the network meta-analysis on which this recommendation is based can be found", ""),
        ("basic characteristics, list here", "what are the main results?"),
        ("details regarding the clinical question (pico), search strategy and other methods can be found", ""),
        ("for details of the evidence used to develop the recommendations, please see the summaries", ""),
        ("for more details on the clinical questions see section 5.4.", ""),
        (
            (
                "subject demarcation this resource contains information specific to select areas of diabetes "
                "treatment, as defined"
            ),
            (
                "the populations, interventions, comparators and outcomes of interest (pico) and included "
                "study designs are"
            ),
        ),
        ("the consortium will seek nhmrc approval of the guideline under section 14a of", ""),
        (
            "the perspective of people living with diabetes the consortium believes that forming and maintaining",
            "updating and public consultation a considerable volume of research related to the care",
        ),
        (
            (
                "the recommendations contained within this resource were generated as a result of "
                "collaboration between the"
            ),
            "",
        ),
        ("the summary of changes from previous versions can be found here.", ""),
        ("this recommendation was updated on 01 december 2023.", ""),
        ("updating and public consultation a considerable volume of research related to the care", ""),
    ),
    "E5W6bL": (
        ("existing global models such as those for who antenatal and intrapartum care guidelines can", ""),
        (
            (
                "international human rights law includes fundamental commitments of states to enable women "
                "and adolescent"
            ),
            "",
        ),
        (
            ("national and subnational subgroups may be established to adapt and implement this recommendation based"),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        (
            ("the recommendation document will be translated into the six united nations languages and disseminated"),
            "",
        ),
        (
            (
                "the successful introduction of evidence-based policies (relating to updated "
                "recommendations) depends on well-planned and"
            ),
            "",
        ),
        ("this recommendation will also be of interest to women giving birth in a range", ""),
        ("1.2 rationale and objectives", "1.3 target audience"),
        ("carbetocin solution vs oxytocin solution", ""),
        ("oxytocin solution vs ergometrine solution", ""),
        ("prostaglandin solution vs oxytocin solution", ""),
        (
            "the dissemination and the implementation of this recommendation are to be considered by all",
            "4.1 recommendation dissemination",
        ),
        (
            (
                "the following section outlines the recommendation, the corresponding evidence for the "
                "prioritized question, and"
            ),
            "the following recommendation was adopted by the gdg. evidence on the effectiveness of this",
        ),
        (
            (
                "to ensure that the recommendation is correctly understood and appropriately implemented in "
                "practice, additional"
            ),
            "",
        ),
        ("uvi of oxytocin solution vs uvi of plasma expander", ""),
        ("uvi of oxytocin solution vs uvi of saline solution", ""),
        ("¹ these outcomes reflect the prioritized outcomes used in the development of this recommendation", ""),
    ),
    "E5mWbE": (
        ("a third trainee (elisa calabrese) who has completed the sages guideline curriculum", ""),
        ("all members from each guideline development group declared conflicts of interest and", ""),
        ("all members of the guideline development group will declare financial, personal, or", ""),
        ("eaes members will have the opportunity to provide feedback on this protocol", ""),
        ("funding body had no influence on the development of this clinical practice", ""),
        ("given that only two trials have been performed in this space in", ""),
        ("our guideline development process consisted of collaboration between a steering group, systematic", ""),
        ("our systematic review is reported separately in detail", ""),
        ("patient-centered decision-making is crucial for healthcare providers managing patients with tokyo", ""),
        ("the consensus meeting began with a detailed overview of the guideline development", ""),
        ("the grade external advisor participated in discussions but was not permitted to", ""),
        ("the guideline panel consisted of a multidisciplinary group of stakeholders with a", ""),
        ("the multidisciplinary, expert guideline panel will include four gastrointestinal surgeons, members of", ""),
        ("the steering group will consist of three co-chairs. these members include a", ""),
        (
            (
                "this clinical practice guideline will provide rigorous, evidence-informed recommendations "
                "to guide healthcare"
            ),
            "",
        ),
        ("this guide was developed by a group of surgeons, doctors, and patients", ""),
        ("this guideline protocol adheres to best applicable reporting standards from the preferred", ""),
        ("this guideline topic was set as high priority by the eaes membership via", ""),
        ("this team will be comprised of systematic reviewers, statisticians, methodologists, and the", ""),
        ("we did not involve a health sciences librarian in the development of", ""),
        ("we will develop a patient-friendly version of the guideline to support future", ""),
        ("we will develop this clinical practice guideline according to robust methodological standards", ""),
        ("we will monitor the use of this clinical practice guideline by eaes", ""),
        ("we will publish this protocol in the guidelines international network (gin) journal.", ""),
        ("we will search clinicaltrials.gov for ongoing studies on the topic and we", ""),
    ),
    "E80D0E": (
        ("detailed evidence-to-decision judgements in the corresponding section.", ""),
        ("in march 2023, who convened a group of over 130 stakeholders for the first", ""),
        ("in the context of poor identification of pph, slow treatment, and inconsistent use", ""),
        ("since 2017, the who department of sexual and reproductive health and research (srh) has applied", ""),
        ("the components of the pph treatment bundle, and the overall effects of the intervention", ""),
        (
            "the development of these recommendations was guided by standardized operating procedures in accordance",
            "",
        ),
        ("the primary audience for this document includes health-care professionals responsible for developing", ""),
        ("the tables summarizing the findings and the etd frameworks", ""),
        ("updated and de novo systematic reviews were used to prepare evidence profiles", ""),
    ),
    "E80ezE": (
        (
            (
                "existing world health organization (who) guidance on maternal interventions for preterm "
                "labour is available"
            ),
            "",
        ),
        (
            (
                "the implementation and impact of these recommendations will be monitored at the "
                "healthservice, regional and"
            ),
            "",
        ),
        ("the participants at the who technical consultation on this guideline in may 2014 adopted", ""),
        (
            (
                "the purpose of this guideline is to provide evidencebased recommendations for interventions "
                "during pregnancy"
            ),
            "",
        ),
        (
            (
                "the target audience for this guideline includes health-care professionals responsible for "
                "developing national and local"
            ),
            "",
        ),
    ),
    "E83abn": (
        (
            ("for each recommendation, the corresponding chapter in the guidelines provides more detailed information"),
            "",
        ),
        ("in each instance, the guideline development working group was able to reach a decision about", ""),
        ("in these guidelines, implications of the recommendations for clinical practice and the health system", ""),
        ("where to find information about liver cancer and liver cancer treatment", ""),
    ),
    "EK0DDj": (
        (
            "as part of the first living guideline cycle, new evidence was reviewed and",
            "the guideline group would like to explicitly point out that the term",
        ),
        ("as part of the living cycle, further comparison with the s3 guideline", ""),
        ("before the next revision cycle in q2 2027, the guideline group will", ""),
        ("errors and misprints in the publication of guidelines cannot be completely ruled out,", ""),
        (
            (
                "following the publication of new studies and meta-analyses, the recommendations on "
                "repetitive transcranial"
            ),
            "",
        ),
        (
            (
                "for persistent auditory hallucinations, the existing recommendation was reformulated "
                "(conditional recommendation):"
            ),
            "",
        ),
        (
            (
                "guidelines issued by scientific medical associations are systematically developed aids for "
                "physicians to assist"
            ),
            "",
        ),
        ("in this guideline, registered trademarks are not specifically identified in most cases. the", ""),
        ("medicine is subject to a continuous process of development, so that all information, especially", ""),
        (
            ("rtms/tbs applied over the left dorsolateral prefrontal cortex may be offered as an adjunctive treatment"),
            "",
        ),
        ("rtms/tbs should be offered as a treatment option within a comprehensive treatment plan in cases", ""),
        ("the available antipsychotics (on-label) and antipsychotics with proven efficacy in this population", ""),
        (
            (
                "the grade methodology was added during this transformation into magicapp. however, the "
                "transformation will"
            ),
            "",
        ),
        ("the guideline group would like to explicitly point out that the term", ""),
        ("the masculine form used in this guideline naturally includes the feminine form and the", ""),
        ("the quality indicators were taken from the 2019 guideline, as a review", ""),
        ("the recommendation level for occupational therapy (recommendation 76) was increased from 0", ""),
        (
            (
                "the recommendation on addressing suicidality openly and empathically (recommendation 96; "
                "new numbering: recommendation"
            ),
            "",
        ),
        ("the recommendation on psychoeducation was re-evaluated and divided into two parts. psychoeducation", ""),
        (
            (
                "the recommendation on the continuous assessment of suicidal thoughts, plans, and behaviours "
                "(recommendation"
            ),
            "",
        ),
        ("the recommendation on the treatment of pregnant women with schizophrenia (recommendation 108) was", ""),
        ("the recommendation regarding persistent negative symptoms was also revised:", ""),
        ("the therapeutic recommendation from the 2019 guideline was divided into three recommendations", ""),
        ("this recommendation is to be revised in the next revision in q2/2027,", ""),
        ("this recommendation will be revisited in q2 2027, as coordination with the", ""),
        ("this update completes the preparations for the transition of the s3 guideline on", ""),
        ("with a few exceptions (recommendations on rtms), the guideline group reviewed and agreed on all", ""),
    ),
    "EK0ldj": (
        ("community dental health coordinators and policy makers also can use the recommendation statements", ""),
        ("the target user for this guideline includes the following: general and specialty dentists", ""),
    ),
    "EKKOyE": (
        ("corticosteroids vs placebo or no treatment- pregnant women at risk", ""),
        ("corticosteroids vs placebo or no treatment-pregnant women at risk of imminent preterm birth - intact", ""),
        (
            ("corticosteroids vs placebo or no treatment-pregnant women at risk of imminent preterm birth - interval:"),
            "",
        ),
        (
            (
                "corticosteroids vs placebo or no treatment-pregnant women at risk of imminent preterm birth "
                "- population"
            ),
            "",
        ),
        (
            (
                "corticosteroids vs placebo or no treatment-pregnant women at risk of imminent preterm birth "
                "- undergoing"
            ),
            "",
        ),
        ("corticosteroids vs placebo or no treatment-pregnant women at risk of imminent preterm birth - with", ""),
        ("corticosteroids vs placebo or no treatment-with or without hypertensive disorders:", ""),
        ("corticosteroids vs placebo or not treatment- women with pregestational diabetes", ""),
        ("detailed evidence-to-decision judgement in the corresponding section", ""),
        ("detailed evidence-to-decision judgements in the corresponding section", ""),
        ("dexamethasone vs betamethasone - type and regimen of antenatal corticosteroids:", ""),
        ("repeat corticosteroids vs single course of corticosteroids- interval between repeat", ""),
        ("repeat corticosteroids vs single course of corticosteroids- subgroup analysis: number", ""),
        ("repeat corticosteroids vs single course of corticosteroids-repeat course compared to", ""),
        ("since 2017, the department of sexual and reproductive health and research", ""),
        ("the priority questions for updating these recommendations were identified by the who executive", ""),
        ("these updated recommendations were developed in accordance with the standards and procedures in the", ""),
        ("what is the effect of oxytocin for pph prevention on the priority outcomes?", ""),
    ),
    "EKeJyL": (
        ("ideally, implementation of the recommendations should be monitored at the health-service level.", ""),
        ("monitoring and evaluating the guideline implementation", ""),
        (
            (
                "note: systematic reviews identified with an asterisk have been updated during the "
                "preparation of this guideline."
            ),
            "",
        ),
        ("proportion of women with eclampsia receiving magnesium sulfate as the first option method", ""),
        (
            ("template for the summary of considerations related to the strength of recommendations with explanations"),
            "",
        ),
        (
            (
                "the successful introduction of these recommendations into national programmes and "
                "healthcare services depends"
            ),
            "",
        ),
    ),
    "EKevdL": (
        (
            ("evidence profiles for oral anticoagulants versus antiplatelet therapy for preventing stroke in patients"),
            "",
        ),
    ),
    "EPY83j": (
        ("authors: daniels k, lewin s, glenton c", ""),
        ("chapter 3 describes the methods used for this analysis. the full findings of this", ""),
        ("christopher j. colvin1, jodie de heer1, laura winterton1, claire glenton2,3, simon lewin2,4,", ""),
        ("citation: lewin s, munabi-babigumira s, glenton c, daniels k, bosch-capblanch x, van wyk be,", ""),
        ("citations of reviews contributing to the guidance", "grade working group grades of evidence"),
        ("claire glenton1,2, rajesh khanna3, chris morgan4,5, elin strømme nilsen1", ""),
        (
            (
                "dovlo d. using mid-level cadres as substitutes for internationally mobile health "
                "professionals in africa."
            ),
            "",
        ),
        (
            "lewin sa, dick j, pond p, zwarenstein m, aja g, van wyk b,",
            "annex 4: the criteria used in moving from evidence to recommendations (the decide",
        ),
        ("lobis s, mbaruku g, kamwendo f, mcauliffe e, austin j, de pinho h.", ""),
        (
            (
                "norwegian knowledge centre for the health services, norway; 2norwegian branch of the nordic "
                "cochrane centre, norway;"
            ),
            "",
        ),
        ("school of public health and family medicine, university of cape town, south africa;", ""),
        ("simon lewin1,2, claire glenton1,3, christopher j. colvin4, arash rashidian5, benedicte carlsen6,", ""),
        ("the following papers were reviewed:", ""),
        ("this project forms part of a comprehensive knowledge-to-action framework implemented by the who", ""),
        ("unni gopinathan1 simon lewin1,2 claire glenton1,3", ""),
        ("we searched medline, pubmed (1950-january week2) (searched january 17); popline (searched february", ""),
        (
            (
                "yarnall j, swica y, winikoff b. non-physician clinicians can safely provide first trimester "
                "medical abortion."
            ),
            "",
        ),
    ),
    "EQ3k5L": (
        ("this is a high priority recommendation and we do not expect to update", ""),
        ("this is a high priority recommendation and will be updated as soon", ""),
        ("this is a low priority recommendation and we do not expect", ""),
        ("this is a moderate priority recommendation and we do not expect to update", ""),
        ("this is a moderate priority recommendation and will be updated when new evidence", ""),
    ),
    "EQ3m3L": (
        ("existing global models such as those for who antenatal and intrapartum care guidelines can", ""),
        ("framed using the population (p), intervention (i), comparison (c), outcome (o) (pico) format, the", ""),
        (
            ("national and subnational subgroups may be established to adapt and implement this recommendation based"),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        (
            ("the recommendation document will be translated into the six united nations languages and disseminated"),
            "",
        ),
        (
            (
                "the successful introduction of evidence-based policies (relating to updated "
                "recommendations) depends on well-planned and"
            ),
            "",
        ),
        ("1.2 rationale and objectives", "1.3 target audience"),
        (
            (
                "international human rights law includes fundamental commitments of states to enable women "
                "and adolescent girls"
            ),
            "",
        ),
        ("the dissemination and implementation of this recommendation are to be considered by all stakeholders", ""),
        ("the following section outlines the recommendation and the corresponding narrative summary of evidence", ""),
        ("to ensure that the recommendation is correctly understood and appropriately implemented in practice", ""),
    ),
    "EQNVKj": (
        ("find evidence summaries, decision aids and practical issues in user-friendly formats here:", ""),
        ("practical issues see the full evidence summaries and practical issues relevant to", ""),
    ),
    "ERWMXj": (
        ("future guidelines should assess interventions for the common clinical presentations of svd including", ""),
        ("stroke presentations of svd such as ‘lacunar’ stroke or ich, are included within current regional", ""),
    ),
    "ERWQ1j": (
        ("1. kumar d. c4 article: implications of covid-19 in transplantation. am j transplant 2020.", ""),
        ("development of recommendations recommendations were developed using a rapid response", ""),
        ("for each clinical question, teams of reviewers screened the titles and abstracts independently and", ""),
        (
            (
                "guideline panel members were selected through an informal process, using purposeful "
                "sampling. through the"
            ),
            "",
        ),
        (
            (
                "in response, canadian blood services, the canadian donation and transplantation research "
                "program, the canadian"
            ),
            "",
        ),
        (
            ("the goal of this collaboration is to create rigorously developed clinical practice recommendations that"),
            "",
        ),
    ),
    "ERWdzj": (
        (
            "clinical guidelines are integral to ensuring that healthcare decisions are based on the best available",
            "",
        ),
        (
            (
                "recommendations within this guideline were developed in collaboration with iceg and the "
                "organisations listed below"
            ),
            "",
        ),
        ("when making decisions about ways to eliminate or minimise infection risks in the workplace", ""),
    ),
    "ERx1yL": (
        ("algorithms for the assessment of individual cancers are detailed in the", ""),
        ("cosa convened a working group of multidisciplinary health professionals", ""),
        ("if you are an aya cancer patient, survivor or family member seeking advice", ""),
        ("the information in this section draws significantly from the excellent resource", ""),
        ("this guidance has been produced by the clinical oncological society of australia", ""),
        ("this information should be read in conjunction with recommendations and text under", ""),
    ),
    "EZ1w8n": (
        ("authors: francois lamontagne, chair, critical care clinician ; bram rochwerg, critical care clinician", ""),
    ),
    "EZVOaE": (
        ("ebola virus disease (evd): key questions and answers concerning health-care waste", ""),
        (
            (
                "for details on appropriate treatment and disposal methods of health-care associated waste, "
                "including infectious waste"
            ),
            "",
        ),
        (
            "see summary in above evidence profile hcws providing direct/indirect care during evd/marburg outbreaks",
            "",
        ),
        ("see summary in health workers in direct contact and/or indirect contact to patients with ebola", ""),
        (
            (
                'see summary in pico onopen in new window"> hand hygiene (including glove disinfection) '
                "between patients."
            ),
            "",
        ),
        (
            (
                "since the 2016 publication, several ebola and marburg disease outbreaks have occurred, "
                "providing health and care"
            ),
            "",
        ),
        (
            ("since the large and protracted 2014-2016 west african ebod outbreak, who has produced several documents"),
            "",
        ),
        (
            (
                "this section addresses general considerations for waste management for items or wastewater "
                "generated during the"
            ),
            "",
        ),
        ("this updated, consolidated guideline will be developed in three phases: phase 1 focuses on ipc", ""),
    ),
    "EZVlYE": (
        (
            (
                "international human rights law includes fundamental commitments of states to enable women "
                "and adolescent"
            ),
            "",
        ),
        (
            ("the following recommendation was adopted by the gdg. evidence on the effectiveness of this intervention"),
            "",
        ),
        ("the following section outlines the recommendation and the corresponding narrative summary of evidence", ""),
        ("the implementation and impact of this recommendation will be monitored at the health service", ""),
        (
            (
                "to ensure that the recommendation is correctly understood and appropriately implemented in "
                "practice, addition remarks"
            ),
            "",
        ),
        (
            (
                "various strategies for addressing these barriers and facilitating implementation are "
                "provided under implementation considerations"
            ),
            "",
        ),
    ),
    "EZvY8E": (
        ("as part of the who’s normative work on supporting evidence-informed policies and practices", ""),
        ("evidence on these interventions was evaluated by a guideline development group (gdg) composed", ""),
        ("this section provides the who recommendation adopted by the gdg on antenatal mms,", ""),
    ),
    "Ea7gOL": (
        ("a planned update is already ongoing to address clinical questions related to the prevention", ""),
        ("blue boxes (right side of diagram) represent the probability tree where dat is given", ""),
        ("full summary of the evidence synthesis is available here.", ""),
        ("red boxes (left side of diagram) represent the probability tree where allergy testing", ""),
        ("update and access: the living guideline is written, disseminated, and updated on an online platform", ""),
    ),
    "EaG1dL": (
        ("10. funding and conflict of interest", "11. conclusion"),
        ("5. link to full guideline", ""),
        ("detailed statistical analyses are available in the appendix", ""),
        (
            (
                "the guidelines subcommittee of the european association for endoscopic surgery (eaes) "
                "decided to develop"
            ),
            "",
        ),
        ("the panel reached unanimous consensus after 2 delphi rounds", ""),
        ("this guideline is planned to be updated within 2030 unless substantial new evidence is published", ""),
        (
            "this is a patient version of the complete mesocolic excision for right-sided colon cancer",
            "2. key points",
        ),
        ("where can i find more information about cme?", ""),
        ("you can find more information about cme on the websites of the following organizations:", ""),
    ),
    "EaKBdL": (
        ("table of evidence provides details regarding the safety and efficacy of continuing", ""),
        ("the evidence table provides details regarding the safety and efficacy of continuing versus", ""),
        ("this guideline document was developed using the grade methodology and aims to assist", ""),
        (
            "this guideline was initiated by the eso and prepared according to eso standard",
            "the mwg undertook the following steps:",
        ),
    ),
    "EaKvXL": (
        ("details of standard precautions and best practices for prevention and control of filovirus", ""),
        ("extracted from the rapid advice guideline: personal protective equipment for use in a filovirus", ""),
        ("pubmed, google and google scholar were searched for the key words (compliance, attitudes, beliefs,", ""),
        ("this topic is addressed in more detail elsewhere", ""),
        ("who should develop specifications and training materials on use of ppe and disposal protocols.", ""),
    ),
    "Edr04L": (
        ("a search was conducted in central (cochrane), medline, psycinfo and pilots databases", ""),
        ("appendix 2: picos and selection criteria", "appendix 3: detailed search strategies"),
        ("evidence profiles reporting relative and absolute effects, the grade of evidence for each outcome,", ""),
        ("for the treatment delivery modalities question the following criteria were used:", ""),
        (
            "for the treatment modalities research question asearch was conducted in medline and cochrane databases",
            "",
        ),
        ("magicapp works in ‘layers’, with the first layer outlining the recommendation", ""),
        (
            (
                "note: there is further discussion of pharmacological treatments for children in the "
                "'pharmacotherapy for"
            ),
            "",
        ),
        ("rationale: description of how the guideline development group synthesised the above elements", ""),
        (
            "table 1: clinical questions",
            "studies were screened on title and abstract by two independent reviewers against the eligibility",
        ),
        ("the following information has been extracted from the istss guidelines methodology and development", ""),
        ("the guidelines were developed by a team of australia’s leading trauma experts, a methodologist", ""),
        ("the istss guidelines recommendations were developed through a rigorous process that was overseen", ""),
        ("the special populations covered in chapter 9 are:", "guidance for use of magicapp"),
        ("these guideline recommendations were first approved by the chief executive officer of the national", ""),
        ("these guidelines are under continual review and updating of recommendations in response to new", ""),
    ),
    "Ee438n": (
        ("figure 1. treatment process of mdma-ap based on phase 3 clinical", ""),
        ("how will the guideline be kept up to date? we plan to review", ""),
        (
            (
                "national strategic framework for aboriginal and torres strait islander peoples’ mental "
                "health and social"
            ),
            "",
        ),
        ("this guideline should be used in tandem with other published resources and tools", ""),
    ),
    "Ee4mAn": (
        ("justifications and remarks are developed by the gdg with support from the methodologists.", ""),
        ("the gela guideline development process aimed to reduce duplication of efforts and improve efficiency", ""),
        ("the global evidence local adaptation (gela) project team at kamuzu university of health sciences in", ""),
        ("the global evidence, local adaptation (gela) project aims to maximize the impact of research on", ""),
        ("the grade etd framework was used to guide the decision-making process. evidence from the", ""),
        (
            (
                "the methodologists populated the evidence profile in gradepro. the systematic review "
                "reports were shared"
            ),
            "",
        ),
        (
            (
                "this guideline has been developed according to global who, guidelines international "
                "network, cochrane and grade"
            ),
            "",
        ),
        ("this study was motivated by the findings of a scoping review of economic evidence", ""),
        ("to put it briefly, we developed and adapted the rules by adhering to multiple", ""),
        (
            (
                "we identified primary studies through systematic searching using a detailed search strategy "
                "in pubmed and"
            ),
            "",
        ),
        ("we searched for economic evidence on pubmed, scopus, google scholar, embase and the nhs", ""),
    ),
    "EeGaAL": (("please access the full guideline and associated visual summary of the recommendations", ""),),
    "Eea27E": (
        ("recently, the european stroke organisation (eso) updated its policy on preparation and publication", ""),
        ("the eso guidelines committee invited the lead author (md) to form and chair", ""),
    ),
    "Eea3zE": (
        ("the european stroke initiative (eusi) last published recommendations on management of ich in 2006.", ""),
        ("the working group formulated 20 pico questions, each one examining two outcomes", ""),
    ),
    "Eez2Kj": (
        ("supporting documents", ""),
        (
            (
                "cervical screening pathways according to primary human papillomavirus (hpv) screening "
                "result are summarised in flowchart"
            ),
            "",
        ),
        ("colposcopy data can be entered into the ncsr using the healthcare provider portal (this requires", ""),
        (
            (
                "high-quality colposcopic management requires meticulous documentation of the patient’s "
                "medical record. the results of consultations,"
            ),
            "",
        ),
        ("in the meantime, queries about recertification and accreditation should be referred to ranzcog", ""),
        ("ncsp summary guide for healthcare providers", ""),
        ("performance reports for individual colposcopists are not available at present.", ""),
        ("quick reference guide for clinician-collected sample", ""),
        ("quick reference guide for self-collected vaginal sample", ""),
        ("resources to support these changes are available below:", "overview of the most significant changes"),
        ("systematic review report (peco 1 - comparison of risk associated with prevalent vs incident detection", ""),
        ("systematic review report (peco 1 – comparison of risk associated with prevalent vs incident detection", ""),
        ("systematic review report 1", ""),
        ("systematic review report 2", ""),
        ("the following ncsp documents support healthcare providers in understanding the cst", ""),
        ("under the renewed ncsp, all colposcopists are required to report a minimum data set", ""),
        ("understanding the national cervical screening program management pathway", ""),
    ),
    "Eez3Kj": (
        ("key clinical questions have been developed for the stages of the nutrition care process", ""),
        ("the evidence based guidelines for the nutritional management of patients with head and neck cancer", ""),
        ("the purpose of these guidelines is to provide the multidisciplinary team of health professionals with", ""),
        (
            (
                "these evidence based guidelines (ebg) were originally developed and maintained according to "
                "methods outlined by"
            ),
            "",
        ),
        ("these guidelines have undergone rigorous peer and expert review by the committee members and they", ""),
    ),
    "EezrQj": (
        (
            "who was involved?",
            "what is the approach to prognosis and risk prediction for cardiovascular and kidney outcomes?",
        ),
        ("guidance was drafted by the methods chair with input from the clinical chair", ""),
        ("panel meetings were facilitated by methods and clinical co-chairs, and were conducted in july 2023", ""),
        ("this bmj rapid recommendation was developed in accordance with standards for trustworthy guidance", ""),
    ),
    "Eg947L": (
        (
            (
                "in the context of humanitarian emergencies, the adaptation of the current recommendation "
                "should consider"
            ),
            "",
        ),
        (
            ("national and subnational subgroups may be established to adapt and implement this recommendation based"),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        (
            (
                "the successful introduction of evidence-based policies (relating to updated "
                "recommendations) depends on well-planned and"
            ),
            "",
        ),
        ("this recommendation will also be of interest to women giving birth, as well as", ""),
        ("1.2 rationale and objectives", "1.3 target audience"),
        ("a. overview of the subclasses of cephalosporins and penicillins given to women", ""),
        ("b. detailed information on specific drugs, doses, and routes of administration", ""),
        ("the dissemination and implementation of this recommendation are to be considered by all stakeholders", ""),
        ("the following recommendation was adopted by the gdg. evidence on the effectiveness", ""),
        ("the following section outlines the recommendation and the corresponding narrative summary of evidence", ""),
        ("these outcomes reflect the prioritized outcomes used in the development of this recommendation", ""),
        ("to ensure that the recommendation is correctly understood and appropriately implemented in practice", ""),
    ),
    "Eg9eVL": (
        (
            (
                "stakeholders from all who regions participated in the preliminary online survey. feedback "
                "from the survey"
            ),
            "",
        ),
    ),
    "EgXyej": (
        ("the following section outlines the recommendation and the corresponding narrative summary of evidence", ""),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        ("the recommendation document will be translated into the six un languages and disseminated", ""),
        (
            "the recommendation will be disseminated through who regional and country offices, ministries of health",
            "",
        ),
        ("these outcomes reflect the prioritized outcomes used for this recommendation", ""),
        ("to ensure that the recommendation is correctly understood and appropriately implemented in practice", ""),
        (
            (
                "who has established a novel process for prioritizing and updating maternal and perinatal "
                "health recommendations"
            ),
            "",
        ),
    ),
    "EvqB0n": (
        ("about this guideline: this living guideline from who incorporates new evidence to dynamically", ""),
        ("for further details of systematic evaluation, see.", ""),
        ("for more information on implementation in resource-limited settings refer to the", ""),
        ("further research needs for ipc focusing on understanding transmission routes and ipc measures", ""),
        (
            (
                "note: this recommendation is based on existing who recommendations from clinical management "
                "and infection"
            ),
            "",
        ),
        ("statements in the 2022 interim guidance about clinical care and ipc measures for patients", ""),
        (
            (
                "target audience: this document is for public health specialists, health emergency "
                "responders, clinicians"
            ),
            "",
        ),
        ("the antiviral and therapeutics section of this guideline will be updated following the systematic", ""),
        ("the following section of the guideline provides recommendations for screening, triage and testing", ""),
        ("the latest guidance from who on hiv prevention, testing, treatment, service delivery and monitoring", ""),
        ("the need for evidence-based clinical guidance has become apparent as cases of mpox", ""),
        ("the next section will show the results of a recent systematic review", ""),
        ("there is one updated ipc (infection prevention and control in health facilities) and two", ""),
        ("there is one updated ipc recommendation and one new best practice recommendation in this section.", ""),
        ("this section provides an overview of the major mpox outbreaks by mpxv subclade.", ""),
    ),
    "Evqmmn": (
        (
            "blood components and blood products are a critical aspect of health care",
            (
                "the reference group did not explicitly include search strategies to identify evidence "
                "related to cost-effectiveness"
            ),
        ),
        (
            ("for detailed information on adverse event management and reporting visit the nba adverse events webpage"),
            "",
        ),
        (
            (
                "the recommendations and good practice statements were reviewed by the reference group "
                "between november 2021"
            ),
            "",
        ),
        ("the scope of this guideline will be updated according to the results of ongoing", ""),
        ("this question was retired in march 2021 as research in this area", ""),
    ),
    "GnJ7bE": (
        (
            (
                "below you will find the recommendations with evidence summaries (grade summary of "
                "findings-tables), practical information"
            ),
            "",
        ),
        ("chair: reed siemieniuk, md", ""),
        ("department of clinical epidemiology & biostatistics, mcmaster university, 1280 main st west, hamilton", ""),
        ("methods editor: annette kristiansen, md phd", ""),
        ("reed a.c. siemieniuk, ¹,²; ian a. harris, professor of orthopaedic surgery ³,⁴; thomas agoritsas,", ""),
    ),
    "Jn37kn": (
        (
            (
                "a useful resource is the national safety and quality health service measurement for "
                "improvement toolkit,"
            ),
            "",
        ),
        (
            "for further information on epps and bbvs, including compliance requirements and occupational exposure,",
            "",
        ),
        (
            (
                "for further information on furniture and fittings, see the current australasian health "
                "facility guidelines"
            ),
            "",
        ),
        ("for further information on surveillance, refer to the australian commission on safety and quality in", ""),
        ("for more information on the handling and disposal of sharps, see standard as 3825: 2020,", ""),
        (
            (
                "for more information on traceability, refer to as 5369:2023 section 2.5.3 identification "
                "and traceability"
            ),
            "",
        ),
        (
            (
                "for more information, refer to the centers for disease control and prevention guidelines "
                "for environmental"
            ),
            "",
        ),
        ("for more information, see standard as 5369:2023.", ""),
        (
            (
                "for more information, see the royal australian college of general practitioners' infection "
                "prevention and control"
            ),
            "",
        ),
        ("source: table adapted from:", ""),
        ("the guidelines are based on the best available evidence and knowledge of the practicalities", ""),
        ("the sections of the guidelines are based on these core principles and are organised", ""),
    ),
    "Kj2R8j": (
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        ("a clinical indicator is collected in the national stroke audit to determine if a", ""),
        (
            (
                "amount of rehabilitation, cardiorespiratory fitness and specific physical activities "
                "(sitting, standing up, standing balance,"
            ),
            "",
        ),
        ("evidence is currently being reviewed for this topic.", ""),
        (
            (
                "implementation considerations there are clinical indicators collected in the national "
                "stroke audit on whether"
            ),
            "",
        ),
        (
            (
                "implementation considerations there are clinical indicators collected on the types of "
                "management that patients"
            ),
            "",
        ),
        (
            (
                "implementation considerations there are no clinical indicators collected in the national "
                "stroke audit on"
            ),
            "",
        ),
        ("implementation considerations there is a clinical indicator collected in the national stroke audit on", ""),
        (
            (
                "implementation considerations there is an organisational indicator collected on whether "
                "hospitals have locally agreed"
            ),
            "",
        ),
        (
            (
                "implementation considerations there is currently no clinical indicator for sitting balance "
                "collected in the"
            ),
            "",
        ),
        ("information previously included on trunk restraint during therapy has been moved to the practical", ""),
        (
            (
                "note: additional information about amount of rehabilitation for communication (specifically "
                "aphasia) has been developed."
            ),
            "",
        ),
        (
            (
                "note: additional information regarding commencement of therapy specifically for aphasia is "
                "also available. please"
            ),
            "",
        ),
        ("there are many practical guides and resources on telehealth collated during the", ""),
        ("there is a clinical indicator collected in the national stroke audit on the type", ""),
        ("there is an organisational indicator collected in the national stroke audit to determine whether", ""),
        ("this section should be read in conjunction with weakness and cardiorespiratory fitness; see also", ""),
        ("weakness, loss of sensation and vision are discussed separately below.", ""),
    ),
    "Kj2WZL": (
        ("for a more detailed literature review, please see appendix 1d under references.", ""),
        ("pregnant/parturient women and others interested in information on dystocia are also welcome to read", ""),
        ("rationale not to update in 2017 based on feedback from professional companies,", ""),
        ("the primary target group for the national clinical guideline are healthcare professionals", ""),
        ("the purpose of the national clinical guideline is to ensure the use of evidencebased procedures", ""),
        ("the relevant patient organisations were represented in the established reference group and had", ""),
        ("updating the recommendation is not considered necessary in 2017", ""),
    ),
    "L4Q5An": (
        (
            "clinical guidelines are integral to ensuring that healthcare decisions are based on the best",
            (
                "version 63 of these guideline recommendations were approved without exception by the chief "
                "executive officer"
            ),
        ),
        ("definitions are subsequently reviewed by the guidelines leadership group and approved by the steering", ""),
        (
            (
                "nhmrc is satisfied that the guideline recommendations are systematically derived, based on "
                "the identification and"
            ),
            "",
        ),
        ("numerous therapies are being evaluated to determine their effectiveness and safety in treating people", ""),
        (
            (
                "our understanding of effective management approaches is still emerging. as such, "
                "recommendations for the"
            ),
            "",
        ),
        ("panels responsible for the recommendations in this section", ""),
        (
            "recommendations are reviewed by the guidelines leadership group and approved by the steering committee",
            "",
        ),
        (
            (
                "recommendations are subsequently reviewed by the guidelines leadership group and approved "
                "by the steering"
            ),
            "",
        ),
        (
            ("the acute and critical care panel is responsible for developing recommendations specific to respiratory"),
            "",
        ),
        ("the comparisons and outcomes referred to below were developed in consultation with members of", ""),
        ("the drug treatment panel is responsible for developing recommendations specific to", ""),
        ("the paediatric and adolescent care panel is responsible for developing recommendations specific to", ""),
        (
            (
                "the pregnancy and perinatal care panel is responsible for developing recommendations "
                "specific to pregnancy"
            ),
            "",
        ),
        ("the primary and chronic care panel and acute and critical care panel are responsible", ""),
        ("the primary and chronic care panel is responsible for developing recommendations specific to", ""),
        ("the primary panels for the recommendations in this section are the primary and chronic", ""),
        ("the taskforce has reviewed high-quality evidence for several therapies used to prevent or treat", ""),
        ("the taskforce will seek nhmrc approval of the guideline under section 14a of the", ""),
        ("this is a high priority recommendation and we do not expect to update it in the", ""),
        ("this is a high priority recommendation and will be updated as soon as new evidence becomes", ""),
        ("this is a low priority recommendation and we do not expect to update it in the", ""),
        ("this is a moderate priority recommendation and we do not expect to update it in the", ""),
        ("this is a moderate priority recommendation and will be updated when new evidence becomes available", ""),
        ("this publication reflects the views of the authors and not necessarily the views of the", ""),
        ("updating and public consultation a considerable volume of research related to the care", ""),
        (
            (
                "version 63 of these guideline recommendations were approved without exception by the chief "
                "executive officer"
            ),
            "",
        ),
        ("versions 20, 28, 36, 42, 48 and 57 of these guideline recommendations have all been", ""),
        (
            (
                "we are continually monitoring new research for randomised trials that evaluate any "
                "disease-modifying treatments"
            ),
            "",
        ),
        ("when sufficient evidence emerges that changes the recommendation from ‘research only’ we will", ""),
    ),
    "L6RxYL": (
        ("each dot represents a week of time. in deciding which drugs to cover, the who", ""),
        ("how this guideline was created: this living guideline is from the world health organization (who)", ""),
        ("this guideline is related to two other who living guidelines for covid-19:", ""),
        ("updates and access: this is a living guideline; therefore, recommendations may be updated", ""),
    ),
    "L6zBvL": (
        (
            (
                "we will develop this clinical practice guideline according to robust methodological "
                "standards outlined by"
            ),
            "",
        ),
        ("a systematic review as well as a clinical practice guideline will be published separately", ""),
        (
            (
                "all members of the guideline development group will declare financial, personal, or "
                "intellectual conflicts"
            ),
            "",
        ),
        (
            "given the absence of structured guidance for guideline protocols, this document is informed by best",
            "outcomes",
        ),
        ("once data is collected, the expert systematic reviewer will hold a synchronous meeting", ""),
        (
            (
                "panel ratings and considerations pertaining to outcome prioritization are available in the "
                "online appendix."
            ),
            "",
        ),
        ("pubmed (1911-oct 16 2024)", "embase <1974 to 2024 october 16>"),
        ("the junior co-chair developed a systematic literature search with the guidance of an academic", ""),
        ("the junior co-chair will query online databases including pubmed, embase via elsevier, & cochrane", ""),
        ("the methodology applied in the development of the systematic review and meta-analysis is reported", ""),
        (
            "this guideline was developed in alignment with various standards including the appraisal of guidelines",
            "outcome selection & decision thresholds",
        ),
        (
            ("to enhance our panel with the highest-level expertise and foster intersociety collaboration, we invited"),
            "",
        ),
        ("to improve the uptake of the recommendations from this rapid update, we will ensure", "monitoring"),
        ("validity period and updates", "limitations"),
        ("we will upload articles identified by the systematic literature search to covidence", ""),
    ),
    "LAKO4E": (
        (
            "clinical guidelines are integral to ensuring that healthcare decisions are based on the best available",
            "consumer-centred care in the context of mpx consumer-centred care is the provision of health care",
        ),
        (
            ("individuals such as policymakers, practice managers, researchers and students may elect to use or adopt"),
            "",
        ),
        ("note on the language in the pregnancy and perinatal care recommendations the taskforce recognises", ""),
        (
            (
                "recommendations will be published once they are reviewed by the guidelines leadership group "
                "and approved"
            ),
            "",
        ),
        (
            "target audience these recommendations are applicable to individuals responsible for the care of people",
            "",
        ),
        ("the taskforce continues to review evidence on drug treatments for people with mpx, and will", ""),
        ("we continually monitor new research for randomised trials that evaluate any treatments for mpx", ""),
    ),
    "LAR07n": (
        ("if you work with boys or men in health or education settings, these guidelines", ""),
        ("note: effect sizes reported are unadjusted for consistency. for effects adjusted for potential", ""),
    ),
    "LAag6L": (
        ("use of the guideline by eaes members will be monitored through an online survey", ""),
        ("an update of this rapid guideline is planned to take place in 2028", ""),
        ("the guideline was sponsored and funded by the european association for endoscopic surgery", ""),
        ("the guideline will be published in surgical endoscopy & other interventional techniques", ""),
        ("there was unanimous consensus on the direction, the strength, and the wording of the recommendations", ""),
    ),
    "LAkxVE": (
        ("2.13 declarations of interests by external contributors", "3. evidence and recommendations"),
        ("2.4 technical working group", "2.6 identifying priority questions and outcomes"),
        ("at the face-to-face meeting (held in september 2017 at the who headquarters in geneva,", ""),
        ("the gdg meeting was held in september 2017 at the who headquarters in geneva,", ""),
        (
            (
                "using the decide framework, the guideline methodologists in collaboration with the steering "
                "group prepared"
            ),
            "",
        ),
    ),
    "LG45vn": (
        ("figure 1: partner types for clinical practice(estcourt cs et al 2022)", ""),
        ("sample letter to be sent to contacts. for documents required by law in your country", ""),
        ("sample letter to be sent to family doctor. for documents required by law in your", ""),
    ),
    "LGm87E": (
        ("an update of this rapid guideline is planned to take place in 2025.", ""),
        ("first and second level screening was carried out by two investigators, instead of one", ""),
        ("first-level and second level screening were performed by two reviewers independently (saa, mm) using", ""),
        ("outcome data were extracted from one author (mm) and cross checked by a second author", ""),
        ("risk of bias summary tables and graphs were constructed using the", ""),
        (
            (
                "the development of this document complied with the reporting checklist for public versions "
                "of guidelines"
            ),
            "",
        ),
        (
            (
                "the guideline will be published in surgical endoscopy & other interventional techniques, "
                "official journal"
            ),
            "",
        ),
        (
            (
                "the objective of this rapid guideline was to develop reliable, trustworthy, pertinent, "
                "evidence-informed recommendations"
            ),
            "",
        ),
        ("this rapid guideline was sponsored and funded by the european association for endoscopic surgery;", ""),
        ("use of the guideline by eaes members will be monitored through an online survey", ""),
    ),
    "Lkk3pL": (
        ("| | | | --- | --- | | type of recommendation | definition", ""),
        ("a non-systematic literature search review was performed to answer the following question:", ""),
        ("a systematic review was performed to answer the following question:", ""),
        (
            (
                "after systematic review of the literature (2012-2016) including previous melanoma "
                "guidelines, we considered the evidence"
            ),
            "",
        ),
        ("each ebr was assigned a grade by the expert working group, taking into account", ""),
        ("evidence included from outside the systematic review is identified with an asterisk", ""),
        ("figure 1. systemic drug therapy flowchart (click to enlarge)", ""),
        ("next section: summary of all recommendations: immunotherapy chapter", ""),
        ("next section: targeted therapies (mek and braf inhibitors)", ""),
        ("no direct recommendations were formulated based on this evidence because it serves to describe", ""),
        ("note: the options in the flowchart are not listed in order of preference.", ""),
        ("see the summary of all recommendations section for all recommendations and practice points.", ""),
        (
            (
                "this guideline includes evidence-based recommendations (ebr), consensus-based "
                "recommendations (cbr) and practice points (pp) as"
            ),
            "",
        ),
        ("this section addresses the following clinical questions:", ""),
        ("this section covers the following questions:", ""),
        ("this section covers the following:", ""),
        ("this section includes all recommendations and practice points from the systemic therapies section of", ""),
        ("this section of the guideline is supported by evidence from a systematic review undertaken", ""),
        ("topics in this section cover:", ""),
    ),
    "Lpmozn": (
        (
            (
                "cari guidelines previously published a clinical practice guideline on the pharmacological "
                "management of adpkd."
            ),
            "",
        ),
        ("please note that the guideline development methods are available in appendix 1. we have updated", ""),
        ("the objective of this guideline is to review the updated evidence of urate-lowering therapy", ""),
    ),
    "Lpv2kE": (
        ("an update of this guideline is planned to take place in 2027, if", ""),
        ("competing interests statement the authors disclose no conflicts of interest", ""),
        ("conclusion this rapid guideline will address the surgical management of obesity within an", ""),
        (
            (
                "ethics and dissemination as a eaes research committee/guideline subcommittee project, this "
                "guideline will"
            ),
            "",
        ),
        ("ethics and dissemination the funding body will not be involved in the development", ""),
        ("funding statement this project is funded by the european association for endoscopic surgery", ""),
        ("guideline development methodology the guideline development process will adhere to agree ii and", ""),
        ("guideline methodologist the senior author (saa) fulfills the criteria of a grade methodologist", ""),
        ("implications for practice and research stringent criteria defined by grade and agree ii", ""),
        ("material and methods the present protocol adheres to agree ii and applicable prisma-p", ""),
        ("patient and public involvement statement as member of the guideline panel, a patient", ""),
        ("strengths and limitations the strengths and limitations of rapid guidelines have been previously", ""),
        ("target users this guideline is intended to be used by general and bariatric", ""),
        ("the clinical question was formulated by the steering group and thresholds for clinical", ""),
        ("the development of this document complied with the reporting checklist for public versions", ""),
        (
            (
                "the guideline panel will consist of bariatric surgeons, obesity physicians, nutritional "
                "experts, psychologists"
            ),
            "",
        ),
        ("the project is funded by the european association for endoscopic surgery. the funding", ""),
        ("the steering group consists of general surgeons, members of the eaes research committee/guideline", ""),
        ("the steering group will consider constructive feedback received during the conduct of the", ""),
        ("use of the guideline by eaes members will be monitored through an online", ""),
    ),
    "LpvNkE": (("a pragmatic flow chart (figure 1), guiding on how to treat according to the principles", ""),),
    "Lq0orj": (
        ("a more detailed algorithm is in development.", ""),
        ("a priori voting rules were in place if the gdg failed to reach consensus", ""),
        ("as described in the methods section, priority questions were identified to define the guideline scope", ""),
        ("draft recommendations underwent internal and external peer review and final approval by the who", ""),
        ("methods these guidelines were developed in accordance with the who handbook for guideline development", ""),
    ),
    "LqGR0E": (
        ("1.2 rationale and objectives", ""),
        ("as part of the who’s normative work on supporting evidence-informed policies and practices", ""),
        ("in january 2021, a who-convened gdg comprising most of the 2016 gdg members re-evaluated", ""),
        (
            "the recommendation in this global guideline is intended to inform the development of relevant national",
            "",
        ),
        (
            (
                "the target audience of this updated recommendation includes national and local public "
                "health policymakers,"
            ),
            "",
        ),
        ("the updating of this recommendation was guided by the standardized operating procedures described", ""),
        ("this section provides the who recommendation adopted by the gdg on antenatal zinc supplementation", ""),
    ),
    "LqR80n": (("the objective of this guideline is to review the updated evidence of urate-lowering therapy", ""),),
    "LqRV3n": (
        (
            ("the recommendations in this living guideline apply to all healthcare settings in australia including"),
            "",
        ),
        ("australia and new zealand musculoskeletal (anzmusc) clinical trials network (2018 onwards)", ""),
        ("n.b. it is unlikely that new evidence will emerge relating to this recommendation but", ""),
        ("n.b. it is unlikely that new evidence will emerge that will impact the direction", ""),
        ("n.b. please see the ‘appendices’ (pico 1) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 10) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 11) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 2) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 3) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 4) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 5) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 6) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 8) for evidence tables, systematic search-related criteria", ""),
        ("n.b. please see the ‘appendices’ (pico 9) for evidence tables, systematic search-related criteria", ""),
        (
            (
                "the australian rheumatology association provides patient information sheets for those "
                "commencing dmards, including the"
            ),
            "",
        ),
        ("we anticipate that data relevant to this recommendation will continue to emerge.", ""),
    ),
    "LqgJ3E": (
        ("a useful summary on supportive care for adults diagnosed with covid-19 has been published by", ""),
        ("appendix iii shows meta-analyses on maternal and pregnancy outcomes from studies published between", ""),
        (
            (
                "specific recommendations on minimising the risk of covid-19 transmission when feeding "
                "babies has been developed"
            ),
            "",
        ),
        ("studies on the risk of severe disease from covid-19 in pregnancy are summarised in appendix", ""),
        ("the bapm has also published guidelines on the neonatal care of babies born to women", ""),
        ("the rcpch/bapm and the rcm have provided separate guidance on this topic,", ""),
        (
            ("this document aims to provide clinical guidance to healthcare professionals who care for pregnant women"),
            "",
        ),
        ("this section aims to summarise, in a format useful for maternity care, the evidence presented", ""),
        ("within this document we use the terms ‘pregnant woman’ and women's health.", ""),
    ),
    "Lr21gL": (
        ("fig 4.2. self-care within the healthcare pyramid", ""),
        ("fig. 1.1. improved outcomes associated with self-care interventions", ""),
        (
            (
                "fig. 1.2. who strategic priorities and the triple-billion goals in the thirteenth general "
                "programme of work"
            ),
            "",
        ),
        ("fig. 1.3. health-promotion tips for self-care practices", ""),
        ("fig. 1.4. example of a who-recommended self-care practice during the covid-19 pandemic", ""),
        ("fig. 1.6. self-care in the context of interventions linked to health systems", ""),
        ("fig. 1.7. continuum of care for self-care", ""),
        ("fig. 2.1. conceptual framework for self-care interventions", ""),
        ("fig. 2.2. who health system framework", ""),
        ("fig. 2.3. processes to guarantee access to appropriate, safe and quality self-care interventions", ""),
        ("fig. 2.4. characteristics of the enabling environment affecting self-care interventions", ""),
        ("fig. 2.5. places of access to self-care interventions", ""),
        ("fig. 4.1. kleinman’s healthcare sectors", ""),
        ("fig. 4.4. factors affecting health workforce readiness during covid-19", ""),
        ("fig. 4.5. role of self-care interventions in public health and social measures during pandemics", ""),
        ("figure 4.3. who community health worker guideline recommendations, using a life-course approach", ""),
        ("illustrative research questions are provided in table 5.1 in relation to the enabling environment", ""),
        ("table 5.1 lists questions to address the research gaps identified by the gdg, organized by topic", ""),
        (
            "the self-care sdg logo (fig. 1.5) was developed to promote this who guideline and related",
            "box 1.1. relevant sustainable development goals and targets",
        ),
        ("this chapter also includes two new recommendations (38 and 39), presented in the same", ""),
        ("this chapter is not intended to be an implementation guide. key aspects of implementation", ""),
        (
            "this chapter presents all of the good-practice statements developed for this guideline. for existing",
            "this chapter also includes two new recommendations (38 and 39), presented in the same",
        ),
        ("to assist countries in making progress towards uhc, who has developed the uhc compendium", ""),
    ),
    "Lr2a8L": (
        ("a qualitative synthesis was also performed on this topic; for results, see the annex.", ""),
        ("figure 1. chain of infection", ""),
        ("figure 2. hierarchy of occupational safety and health controls", ""),
        ("for more information on the care of persons with covid-19 in the home", ""),
        ("the covid-19 pandemic has been at the centre of who’s research endeavours. the global", ""),
        ("the methods used to develop the following technical specifications involved a review of infection", ""),
        (
            ("this update supersedes the previous guidance on the prevention, identification and management of health"),
            "",
        ),
        ("who and unicef jointly developed this guideline. a guideline development group, the who-unicef gdg", ""),
        ("who guidance on the use of masks by children in the community was first published", ""),
    ),
    "LrRxrL": (
        (
            (
                "a guideline development group (gdg) of researchers, clinicians, ethicists and people living "
                "with obesity"
            ),
            "",
        ),
        ("donnan j, huang r, twells l. patient preferences for attributes of health canada approved", ""),
        ("franco j, meza n, guo y, bracchiglione j, escobar liquitay cm, veroniki a, et al.", ""),
        ("meza n, bracchiglione j, escobar liquitay cm, madrid e, varerla l, guo y, et al.", ""),
        ("reference for decision making", ""),
        ("references for decision making", ""),
        ("the key stakeholders that comprise the target audience of this guideline are national policy-makers", ""),
        ("the process involved synthesis and grade assessments of efficacy, effectiveness and safety evidence", ""),
        (
            (
                "there were two guideline questions that were initially prioritized for this guideline with "
                "a supplementary"
            ),
            "",
        ),
        (
            (
                "this guideline includes two conditional recommendations and two good practice statements. "
                "importantly, all"
            ),
            "",
        ),
    ),
    "LwRK5j": (
        ("akl ea, meerpohl jj, elliott j, kahale la, schünemann hj, living systematic", ""),
        ("alvarez pm, mckeon jf, spitzer ai, krueger ca, pigott m, li m,", ""),
        ("baraliakos x, østergaard m, poddubnyy d, van der heijde d, deodhar a,", ""),
        ("braun j, baraliakos x, kiltz u. treat-to-target in axial spondyloarthritis - what", ""),
        ("burgos-vargas r, loyola-sanchez a, ramiro s, reding-bernal a, alvarez-hernandez e, van der", ""),
        ("burgos-vargas r, tse sm, horneff g, pangan al, kalabic j, goss s,", ""),
        ("burgos-vargas r, vazquez-mellado j, pacheco-tena c, hernandez-garduno a, goycochea-robles mv. a 26", ""),
        ('burmester, gerd-rűdiger, et al. "efficacy and safety of ascending methotrexate dose in', ""),
        ("chamlati, r., connolly, b., laxer, r., stimec, j., panwar, j., tse, s.,", ""),
        ("constantin, t., foeldvari, i., vojinovic, j., horneff, g., burgos-vargas, r., nikishina, i.,", ""),
        ("csa provides programs centered on four strategic pillars:", ""),
        ("curbelo rodríguez r, zarco montejo p, almodóvar gonzález r, flórez garcía m,", ""),
        ("deodhar a, gensler ls, sieper j, clark m, calderon c, wang y,", ""),
        ("deodhar a, machado pm, mørup m, taieb v, willems d, orme m,", ""),
        ("deodhar a, poddubnyy d, pacheco-tena c, et al. efficacy and safety of", ""),
        ('deodhar, a., et al. "pos0939 bimekizumab in patients with active non-radiographic axial', ""),
        ("dick ad, et al. guidance on noncorticosteroid systemic immunomodulatory therapy in noninfectious", ""),
        ("donmez u. et al. ege journal of medicine / ege tıp dergisi", ""),
        ("ducourau e et al. methotrexate effect on immunogenicity and long-term maintenance of", ""),
        ("elliott jh, synnot a, turner t, simmonds m, akl ea, mcdonald s,", ""),
        ("fischer t, biedermann t, hermann kg, diekmann f, braun j, hamm b,", ""),
        ("fitzgerald g, anachebe t, mccarroll k, o'shea f> measuring bone density in", ""),
        ("fritz j, tzaribachev n, thomas c, carrino ja, claussen cd, lewin js,", ""),
        ("giardina a r et al. a 2-year comparative open label randomized study", ""),
        ("gronning k, skomsvoll jf, rannestad t, steinsbekk a. the effect of an", ""),
        ("gunaydin i, pereira pl, fritz j, konig c, kotter i. magnetic resonance", ""),
        ("hammond a, bryan j, hardy a. effects of a modular behavioural arthritis", ""),
        ("horneff g, burgos-vargas r, constantin t, foeldvari i, vojinovic j, chasnyk vg,", ""),
        ("hu mx, turner d, generaal e, et al. exercise interventions for the", ""),
        ("hurley vb, wang y, rodriguez hp, shortell sm, kearing s, savitz la.", ""),
        ("jabs da, et al. am j ophthalmol 2000; 130: 492-513.", ""),
        ("karberg k, zochling j, sieper j, felsenberg d, braun j. bone loss", ""),
        ("khan mn, ali mu, bhambani l, prashanth n, tross s. outcomes of", ""),
        ("li, e k et al. short-term efficacy of combination methotrexate and infliximab", ""),
        ("liu wy, li hm, jiang h, zhang wk. effect of exercise training", ""),
        ("lubrano e, helliwell p, parsons w, emery p, veale d. patient education", ""),
        ("luukkainen r, nissila m, asikainen e, sanila m, lehtinen k, alanaatu a,", ""),
        ("maugars y, mathis c, berthelot j-m, charlier c, prost a. assessment of", ""),
        ("migliore a, bizzi e, massafra u, vacca f, martin-martin ls, granata m,", ""),
        ("minden k, niewerth m, zink a, seipelt e, foeldvari i, girschick h,", ""),
        ("molto a, gossec l, poiraudeau s, claudepierre p, soubrier m, fayet f,", ""),
        ("musso-daury l, pascual fernández t, lópez-ortiz s, pico de las heras m,", ""),
        ("nayak s, roberts m, greenspan s. osteoporosis screening preferences of older adults.", ""),
        ("o'dwyer t, mcgowan e, o'shea f, wilson f. physical activity and exercise:", ""),
        ("passalent l, cyr a, jurisica i, mathur s, inman rd, haroon n.", ""),
        ("posadzki p, pieper d, bajpai r, makaruk h, könsgen n, neuhaus al,", ""),
        ("rohekar s, chan j, tse sml, haroon n, chandran v, bessette l,", ""),
        ("sieper j, listing j, poddubnyy d, song ih, hermann kg, callhoff j,", ""),
        ("solarino, giuseppe, davide bizzoca, anna maria moretti, rocco d’apolito, biagio moretti, and", ""),
        ("sudre a, figuereido it, lukas c, combe b, morel j. on the", ""),
        ("summary of the panel's discussions on this recommendation:", ""),
        ("supplement e: evidence report, available on the arthritis & rheumatology web site", ""),
        ("sweeney s, gupta r, taylor g, calin a. total hip arthroplasty in", ""),
        ("talk more about patients and clinician's values and preferences -", ""),
        (
            (
                "the canadian spondyloarthritis association (csa) is a registered charitable organization, "
                "the only patient focused"
            ),
            "",
        ),
        ("ulu m, cevik r, dilek b. comparison of pa spine, lateral spine,", ""),
        ("van der heijde d, cheng-chung wei j, dougados m, et al. ixekizumab,", ""),
        ("van der heijde d, gensler ls, deodhar a, et al. dual neutralisation", ""),
        ('van der heijde, d., et al. "op0019 bimekizumab in patients with active', ""),
        ("van der weijden m, van der horst-bruinsma, van denderen j, dijkmans b,", ""),
        ("wanders a, heijde dv, landewé r, béhier jm, calin a, olivieri i,", ""),
        ("wang d, zeng q, chen s, gong y, hou z, xiao z.", ""),
        ("ward mm, deodhar a, gensler ls, dubreuil m, yu d, khan ma,", ""),
        ("weiss pf, xiao r, brandon tg, pagnini i, wright tb, beukelman t,", ""),
        ("windschall d, muller t, becker i, horneff g. safety and efficacy of", ""),
        ("xu y et al. am health drug benefits. 2018 nov;11(8):408-417. pmid: 30647828;", ""),
    ),
    "LwRMXj": (
        ("a guide to support implementation of iptsc will be developed in due course", ""),
        ("a guide to support implementation of pdmc will be developed in due course", ""),
        ("data on costed activities from the rts,s/as01 pilot introductions are available in", ""),
        ("expert input is important for the interpretation of the evidence, and the development", ""),
        ("further detailed information is provided in the who publication", ""),
        ("methods and techniques for clinical trials on antimalarial drug efficacy: genotyping to identify", ""),
        (
            (
                "note: recommendations on deployment of pyrethroid-chlorfenapyr nets were separated into two "
                "distinct recommendations"
            ),
            "",
        ),
        (
            (
                "note: recommendations on deployment of pyrethroid-pyriproxyfen nets were separated into two "
                "distinct recommendations"
            ),
            "",
        ),
        ("recommendation development was informed by a systematic review (gutman", ""),
        ("recommendation development was informed by a systematic review (phiri", ""),
        (
            (
                "recommendation development was informed by a systematic review, independently evaluated "
                "using the amstar-2 checklist"
            ),
            "",
        ),
        ("who commissioned a systematic review to inform this guidance on smc (thwing", ""),
    ),
    "Lwq0oE": (
        (
            (
                "national and subnational subgroups may be established to adapt and implement these "
                "recommendations based"
            ),
            "",
        ),
        ("external partners and observers", "identification of priority questions and outcomes"),
        ("in 2019, the executive guideline steering group (gsg) for the world health organization (who)", ""),
        ("the executive gsg prioritized updating the existing who recommendations on the use of aspirin", ""),
        ("the external review group was also asked to determine if the recommendations made were", ""),
        ("the following recommendations were adopted by the gdg. the evidence on the effectiveness of", ""),
        ("the following section outlines the recommendation and the corresponding narrative summary of evidence", ""),
        (
            (
                "the primary audience for these recommendations includes health professionals who are "
                "responsible for developing"
            ),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        (
            (
                "the recommendations were developed using standardized operating procedures in accordance "
                "with the process described"
            ),
            "evidence synthesis group (esg)",
        ),
        ("these recommendations will also be of interest to pregnant women, as well as members", ""),
        (
            (
                "to ensure that the recommendations are correctly understood and appropriately implemented "
                "in practice, additional"
            ),
            "",
        ),
    ),
    "LwqRXE": (
        (
            (
                "recognizing that anc provides a strategic platform for important health-care functions, "
                "including health promotion"
            ),
            (
                "the updated who recommendation on antenatal oral vitamin d supplements for a positive "
                "pregnancy experience"
            ),
        ),
        ("selected forest plots for effects of vitamin d plus calcium supplements vs no vitamin d", ""),
        ("the updated recommendation in the context of the who anc guideline", ""),
        (
            (
                "this section provides the who recommendation on antenatal vitamin d supplementation, with "
                "its corresponding"
            ),
            "",
        ),
    ),
    "LwqZeE": (
        ("1.2 rationale and objectives", "1.4 scope of the recommendations"),
        ("murano m, chou d, costa do nascimento ml, turner t. using the", ""),
        ("note: the etd table – which summarizes the balance between the desirable and undesirable", ""),
        ("the gdg acknowledges that there is planned or ongoing research relevant to some of", ""),
        ("the updated recommendations on induction of labour at term or beyond and on the", ""),
        (
            "the world health organization (who) envisions a world where",
            "to provide good-quality care and maximize maternal and perinatal outcomes, once a",
        ),
        (
            (
                "this section presents the updated recommendation on outpatient settings for induction of "
                "labour that was"
            ),
            "",
        ),
    ),
    "LwvKej": (
        ("a protocol was developed a priori by the steering group", ""),
        ("an ad hoc evidence outreach team (at, bh) performed record screening using the platform rayyan", ""),
        ("guideline registration number: prepare-2023cn045", ""),
        ("one panel member (kf) could not participate in the consensus meeting, hence he was not", ""),
        ("outcome data were extracted by 2 reviewers (bh, at), and cross-checked in detail by", ""),
        ("purpose, scope and target users", "recommendations"),
        ("this guideline is planned to be updated within 2031, unless substantial new evidence will be", ""),
        ("this is a cooperative project between eaes, sages and escp. eaes, building upon the growing", ""),
        ("this is a patient version of the eaes, sages, escp and escmid rapid guideline", "key points"),
    ),
    "LwvYKj": (
        (
            ("2.3.2 evidence on values, resource use and cost–effectiveness, equity, acceptability and feasibility"),
            "",
        ),
        (
            (
                "in the context of humanitarian emergencies, the adaptation of the current recommendation "
                "should consider"
            ),
            "",
        ),
        (
            ("national and subnational subgroups may be established to adapt and implement this recommendation based"),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        (
            (
                "the successful introduction of evidence-based policies (relating to updated "
                "recommendations) depends on well-planned and"
            ),
            "",
        ),
        ("this recommendation will also be of interest to women giving birth, as well as", ""),
        ("2.1 contributors to the guideline", "2.2 identification of priority questions and outcomes"),
        ("2.6 management of declarations of interests", "2.7 decision-making during the gdg meetings"),
        ("the dissemination and implementation of this recommendation are to be considered by all stakeholders", ""),
        (
            ("the following recommendation was adopted by the gdg. evidence on the effectiveness of this intervention"),
            "",
        ),
        ("the following section outlines the recommendation and the corresponding narrative summary of evidence", ""),
        (
            (
                "the recommendation was developed using standardized operating procedures in accordance with "
                "the process described"
            ),
            "2.1 contributors to the guideline",
        ),
        ("to ensure that the recommendation is correctly understood and appropriately implemented in practice", ""),
        ("who has established a new process for prioritizing and updating maternal and perinatal health", ""),
    ),
    "LwvpGj": (
        ("2.12 decision-making during the gdg meetings", "2.15 presentation of guideline content"),
        ("2.3 external review group (erg)", "2.6 identifying priority questions and outcomes"),
        (
            "c. who guidelines for the identification and management of substance use and substance",
            "annex 5: priority questions and outcomes for the antenatal care (anc) interventions identified",
        ),
        (
            (
                "in accordance with who guideline development standards, these recommendations will be "
                "reviewed and updated"
            ),
            "",
        ),
        ("the corresponding grade tables for recommendations are referred to in this chapter as", ""),
        (
            "the priority questions and outcomes guiding the evidence review and synthesis for the recommendations",
            "outcomes of interest",
        ),
        (
            (
                "these anc recommendations are intended to inform the development of relevant health-care "
                "policies and clinical"
            ),
            "",
        ),
    ),
    "NnV76E": (
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        (
            (
                "development of questions questions have been extensively developed and reviewed over the "
                "four iterations"
            ),
            "brief summary of grade the guidelines were developed following the grade methodology",
        ),
    ),
    "QnoKGn": (
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        ("add benefits, talk to skye about wso stroke unit certification about practical information", ""),
        ("for this section we are focused on reviewing new agents that have been or are", ""),
        ("note: this section was previously called neuroprotection.", ""),
        ("note: this topic was previously named neurointervention.", ""),
        ("recommendation on m2 segment has been moved and merged with the main recommendation.", ""),
        ("recommendation to be superseded by above updated recommendation when approved.", ""),
        ("recommendations for 0-24h ica/ma/basilar artery consolidated into one recommendation.", ""),
        ("update approve by nhmrc 3 september 2025.", ""),
    ),
    "VLpK8j": (
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        (
            ("implementation consideration there is a clinical indicator collected in the national stoke audit on the"),
            "",
        ),
        (
            (
                "implementation consideration there is a clinical indicator collected in the national stroke "
                "audit to determine"
            ),
            "",
        ),
        (
            (
                "implementation considerations there is a clinical indicator collected in the national "
                "stroke audit to determine"
            ),
            "",
        ),
        ("there is a clinical indicator collected in the national stroke audit on the provision of", ""),
        ("there is a clinical indicator for the provision of care plans (outlining post-discharge care in", ""),
    ),
    "WE8wOn": (
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        (
            ("also refer to the assessment and treatment protocol from original trial publication: harari et al. 2004"),
            "",
        ),
        ("post-stroke pain is currently not monitored in any national stroke data collection process", ""),
        (
            (
                "there are clinical indicator collected in the national stroke audit to determine whether "
                "urinary catherisation"
            ),
            "",
        ),
        (
            ("there are clinical indicators collected in the national stroke audit to determine whether patients with"),
            "",
        ),
        ("there is a clinical indicator collected in the national stroke audit on the provision of heparin", ""),
        ("there is a clinical indicator collected in the national stroke audit on the type of management", ""),
        (
            (
                "there is an organisational indicator collected in the national stroke audit to determine "
                "whether hospitals"
            ),
            "",
        ),
        ("there were no economic evaluations for oral care in stroke. oral care is not currently", ""),
    ),
    "ZjbDgn": (("due to copyright issues the rationale for this recommendation can not be published", ""),),
    "anBg0E": (
        ("due to copyright issues the rationale for this recommendation can not be published in magicapp.", ""),
        ("| member | role/representing | professional specialty | conflict of interest ", "|"),
    ),
    "anBmDL": (
        (
            (
                "1. department of intensive care, rigshospitalet, copenhagen university hospital, "
                "copenhagen, denmark. 2 department of anaesthesiology"
            ),
            "",
        ),
        (
            (
                "as part of the scandinavian society of anaesthesiology and intensive care medicine’s (ssai) "
                "efforts to improve perioperative"
            ),
            "",
        ),
        ("authors and affiliations: a. perner1, e. junttila2, m. haney3, k. hreinsson4", ""),
        ("the clinical practice committee of ssai appointed national members of the guideline task force", ""),
        (
            (
                "the task force identified key clinical questions for fluid resuscitation, vasopressor "
                "therapy, inotropic therapy and diagnostics"
            ),
            "",
        ),
        (
            (
                "this clinical practice guideline -available here with recommendations in multilayered "
                "formats available on all devices"
            ),
            "",
        ),
    ),
    "bEvGJj": (
        ("a limitation of this work is that we have relied heavily on use of the", ""),
        (
            (
                "further limitations of this paper results from lack of stakeholder involvement, i.e. "
                "patient-groups and relatives"
            ),
            "",
        ),
        (
            "in adopting the grade-system for guideline development, the ssai has emphasized that guidelines should",
            "",
        ),
        (
            (
                "the clinical practice committee of the scandinavian society of anaesthesia and intensive "
                "care medicine (ssai)"
            ),
            "",
        ),
        ("the guideline process serves to inform us that, despite advances, many areas of our practice", ""),
        (
            (
                "why develop nordic guidelines for intensive care medicine? across the nordic societies "
                "there is considerable"
            ),
            "",
        ),
    ),
    "eEZlLD": (
        (
            ("anne: could we provide contact information to obstetric care teams (eg. oslo university hospital) here?"),
            "",
        ),
        ("anne: should we add when asa should be discontinued? 1 week before delivery?", ""),
        ("chapter editor: anne flem jacobsen, oslo university hospital dept. of obstetrics and gynecology.", ""),
        ("conflicts of interest: coi are reported only for excluded, modified or new recommendations.", ""),
        ("consider moving information in the remark to this section.", ""),
        ("here we need information about apla criteria:", ""),
    ),
    "j1O57n": (
        ("delimitation of the subject matter the national clinical guideline contains instructions on how", ""),
        ("rationale not to update in 2017 based on feedback from professional companies, the danish", ""),
        ("target group/users the primary target group for this guideline are doctors specialising in", ""),
        ("the guideline may also be relevant for patients or relatives wishing to find information", ""),
        ("the patient organisations of relevance for this guideline were represented in the established", ""),
        ("the various methods were assessed against each other as per focused questions", ""),
        ("the working group weighted the various outcomes as critical (c), important (i)", ""),
        ("updating the recommendation is not considered necessary in 2017", ""),
    ),
    "j1Q1Xj": (
        ("a guide for community members. frequently asked questions about bowel cancer screening from:", ""),
        ("a guide for health professionals. frequently asked questions about bowel cancer screening from:", ""),
        (
            ("a strength was not assigned (n/a) to recommendations based on mathematical modelling evaluation because"),
            "",
        ),
        ("information for aboriginal and torres strait islander peoples on free bowel cancer screening:", ""),
        ("information for gps. bowel screening and aboriginal and torres strait islander peoples from:", ""),
        ("national bowel cancer screening program – clinical resources:", ""),
        ("resources for families and communities – indigenous bowel screening:", ""),
        (
            ("the clinical question and population, intervention, comparator and outcome (pico) question are shown in"),
            "",
        ),
        (
            "these guidelines include evidence-based recommendations (ebr) and practice points. for each ebr except",
            "",
        ),
        ("this guideline chapter on population screening for crc has been updated from that", ""),
        ("understanding the bowel cancer screening test in your language:", ""),
        ("where to find information about bowel cancer, bowel cancer screening, and bowel cancer treatment", ""),
    ),
    "j1Q1rj": (
        (
            (
                "national and subnational subgroups may be established to adapt and implement these "
                "recommendations based"
            ),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        ("1.2 rationale and objectives", "1.3 target audience"),
        (
            "the dissemination and implementation of these recommendations are to be considered by all stakeholders",
            "",
        ),
        (
            (
                "the following recommendations were adopted by the gdg. evidence on the effectiveness of "
                "this intervention"
            ),
            "",
        ),
        (
            "the following section outlines the recommendations and the corresponding narrative summary of evidence",
            "",
        ),
        ("these outcomes reflect the prioritized outcomes used in the development of these recommendations", ""),
        ("to ensure that the recommendations are correctly understood and appropriately implemented in practice", ""),
    ),
    "j1QPrj": (
        ("1.2 rationale and objectives", ""),
        ("considerations for implementation of tocolytic therapy are outlined in table 1.", ""),
        (
            (
                "cumulative rankograms: ranking indicates the cumulative probability of being the best "
                "agent, the second best,"
            ),
            "",
        ),
        ("detailed evidence-to-decision judgements in the corresponding section.", ""),
        (
            (
                "interrupted time series, clinical audits or criterionbased clinical audits could be used to "
                "obtain relevant"
            ),
            "",
        ),
        ("network diagram: the nodes represent an intervention and their size is proportional to the number", ""),
        ("since 2017, the department of sexual and reproductive health and research (srh) at who has", ""),
        (
            (
                "the dissemination and implementation of this recommendation is to be considered by all "
                "stakeholders involved"
            ),
            "4.2 implementation considerations",
        ),
        ("the effects of different classes of tocolytic agents are presented in the summary of findings", ""),
        (
            (
                "the executive gsg prioritized the updating of who’s recommendation on tocolytic therapy to "
                "improve preterm"
            ),
            "",
        ),
        (
            (
                "the implementation and impact of this recommendation will be monitored at the "
                "healthservice, regional and"
            ),
            "",
        ),
        (
            (
                "the primary audiences for this document are healthcare professionals responsible for "
                "developing national and local"
            ),
            "",
        ),
        (
            (
                "the update of this recommendation was guided by standardized operating procedures in "
                "accordance with the"
            ),
            "",
        ),
        (
            (
                "this recommendation is primarily for health-care professionals who are responsible for "
                "developing national and local"
            ),
            "",
        ),
        ("this recommendation will also be of interest to women giving birth in a range of", ""),
        (
            (
                "updated systematic reviews were used to prepare evidence profiles for the prioritized "
                "questions. the quality"
            ),
            "",
        ),
    ),
    "j1WBYn": (
        ("a living mapping and systematic review of covid-19 therapeutic studies is available", ""),
        (
            (
                "complementary guidelines: for the most up to date clinical practice guideline on "
                "therapeutics and covid-19"
            ),
            "",
        ),
        ("each recommendation in this document is identified by a coloured box (which describes the strength", ""),
        ("for general advice in navigating the challenges of reducing unnecessary antibiotic use, please see", ""),
        ("for the most up to date clinical practice guideline on therapeutics and covid-19 see", ""),
        ("for version 8, a review of the scope of the entire guideline was undertaken in", ""),
        ("icons used where grade methodology does not apply", ""),
        ("in this case (version 8), a new systematic review and meta-analysis became available, and", ""),
        ("infographic co-produced by the bmj and magic; designer will stahl-timmins", ""),
        ("related guidelines this living who guideline for the clinical management of covid-19 is published", ""),
        ("step 1: evidence monitoring and mapping and triggering of evidence synthesis", ""),
        ("step 2: convening the guideline development group (gdg) - see also", ""),
        (
            (
                "step 3: evidence synthesis the new evidence synthesis for antibiotic use, including methods "
                "is available"
            ),
            "",
        ),
        (
            ("step 4: development of recommendations the gdg panel members are responsible for the following critical"),
            "",
        ),
        ("step 5: external and internal review", ""),
        (
            (
                "the approach to guideline production is described below, although for efficiency various "
                "processes occurred"
            ),
            "",
        ),
        ("the following key factors were used to formulate transparent and trustworthy recommendations:", ""),
        ("the gdg convened on 18 june 2024, to address the use of antibiotics", ""),
        (
            (
                "the grade approach provided the framework for establishing evidence certainty and "
                "generating both the direction"
            ),
            "",
        ),
        ("the green symbol denotes a non-grade-based strong recommendation of a best practice statement", ""),
        ("the guideline creation process:", ""),
        ("the infographic illustrates these three disease severity groups and key characteristics to apply", ""),
        (
            ("this guidance brings together diagnostic technical guidance developed and published since the beginning"),
            "",
        ),
        (
            "timing this guideline aims to be trustworthy and living; dynamically updated and globally disseminated",
            "",
        ),
        ("to advise on the priority questions and scope of the guideline;", ""),
        ("who guidelines for the management of patients with post-covid-19 condition are in preparation.", ""),
        (
            (
                "who selected guideline development group (gdg) members providing balanced representation by "
                "global region, gender,"
            ),
            "",
        ),
    ),
    "j1WRkn": (("the aim of this guideline document is to assist physicians treating patients with ischaemic", ""),),
    "j1WYVn": (
        ("in this document, we outline the current state of the evidence on the effect of ivt", ""),
        ("one group member (ww) did not vote or comment on this chapter because he", ""),
        ("this guideline document was developed following the grade methodology and aims to assist physicians", ""),
    ),
    "j1Wqrn": (
        ("reproduced with permission from bmj, designed by will stahl-timmins", ""),
        ("the match-it decision support tool allows you to interact with the evidence and your patients", ""),
        ("we encourage professional societies and other actors in the evidence ecosystem to re-use", ""),
        ("what is my patient’s risk?", ""),
    ),
    "j1k9Jn": (
        (
            (
                "the following section outlines the recommendations and the corresponding narrative summary "
                "of evidence for"
            ),
            "",
        ),
        ("these outcomes reflect the prioritized outcomes used for this recommendation, in the", ""),
    ),
    "j1kmYn": (
        ("high-quality health care is essential for the prevention of morbidity and mortality in pregnancy", ""),
        (
            (
                "national and subnational subgroups may be established to adapt and implement these "
                "recommendations based"
            ),
            "",
        ),
        (
            ("the dissemination and implementation of these recommendations are to be considered by all stakeholders"),
            "",
        ),
        (
            (
                "the successful introduction of evidencebased policies (relating to the updated "
                "recommendations) depends on well"
            ),
            "",
        ),
        (
            (
                "the successful introduction of these recommendations into national programmes and health "
                "services depends on"
            ),
            "",
        ),
        ("• apgar score less than 7 at 5 minutes • admission to a neonatal", ""),
        ("1.2 rationale and objectives", "1.4 scope of the recommendations"),
        ("murano m, chou d, costa do nascimento ml, turner t. using the who-integrate evidence to", ""),
        ("the evidence is summarized in grade tables:", ""),
        ("the implementation and impact of these recommendations will be monitored at the health service,", ""),
        ("the updated recommendations on induction of labour at term or beyond, and outpatient settings for", ""),
        ("the world health organization (who) envisions a world where", ""),
        ("this section presents the two updated recommendations on the timing of induction of labour that", ""),
    ),
    "j20X4n": (
        ("5 link to the source guideline", ""),
        ("a protocol was developed a priori by the steering group. the protocol draft", ""),
        ("an update of this rapid guideline is planned to take place in 2028. an update", ""),
        (
            (
                "as a eaes research committee/guideline subcommittee project, this guideline will be "
                "submitted for publication"
            ),
            "",
        ),
        ("ethics and dissemination the funding body will not be involved in the development", ""),
        ("fig. 1. a. total posterior; b. partial posterior; c. anterior 90°; d. anterior >90°", ""),
        ("network plots and risk of bias contribution charts per outcome or group of outcomes", ""),
        (
            "the development of this document complied with the reporting checklist for public versions",
            "2 key points",
        ),
        ("the first author is a certified guideline methodologist, has participated in the development", ""),
        ("the guideline panel will consist of three general surgeons, two gastroenterologists, an anesthetist", ""),
        ("the guideline was sponsored and funded by the european association for endoscopic surgery", ""),
        ("the guideline will be published in the united european gastroenterology journal, official journal", ""),
        ("the present protocol adheres to agree-s and prisma-p reporting standards. it will be available", ""),
        ("the steering group consisted of two general surgeons who perform laparoscopic antireflux surgery", ""),
        (
            (
                "the steering group consists of general surgeons, members of the eaes research "
                "committee/guideline subcommittee"
            ),
            "",
        ),
        ("the steering group will consider constructive feedback received during the conduct of the project", ""),
        (
            ("this guideline is intended to be used by general surgeons, gastroenterologists, primary care physicians"),
            "",
        ),
        ("this is a patient version of the ueg and eaes rapid guideline", ""),
        ("total posterior wrap partial posterior wrap", ""),
        ("use of the guideline by eaes members will be monitored through an online survey", ""),
        ("we will apply stringent criteria defined by g-i-n, grade and agree-s to collate", ""),
        ("we will collect conflicts of interest statements from all participants before and upon completion", ""),
        ("we will monitor use of the guideline by eaes members through an online survey", ""),
    ),
    "j2QPrE": (
        (
            (
                "in accordance with who guideline development procedures, this recommendation will be "
                "regularly reviewed and updated"
            ),
            "",
        ),
        ("the evidence base is summarized in one grade table below, the evidence to decision framework", ""),
        (
            (
                "the primary audience for this guideline is health-care professionals, particularly fistula "
                "surgeons and nurses"
            ),
            "",
        ),
        (
            (
                "the primary target audience for this guideline is health-care professionals, particularly "
                "fistula surgeons and nurses"
            ),
            "",
        ),
        ("the table below summarizes the quality of the evidence, values and preferences, the balance between", ""),
        ("this guideline includes one recommendation adopted by the guideline development group (gdg).", ""),
        (
            (
                "this guideline was developed following standardized operating procedures in accordance with "
                "the process described in the"
            ),
            "",
        ),
    ),
    "j2QQ4E": (
        (
            "monitoring and evaluating the guideline implementation",
            "annex 2. critical and important outcomes for decision-making",
        ),
        ("the quality of the supporting evidence rated as", ""),
    ),
    "j2QZZE": (
        (
            (
                "in the context of humanitarian emergencies, the adaptation of the current recommendation "
                "should consider"
            ),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        ("this recommendation will also be of interest to women giving birth, as well as", ""),
        ("1.2 rationale and objectives", ""),
        ("2.3 evidence identification and retrieval", "2.4 certainty assessment and grading of the evidence"),
        (
            ("the executive summary and recommendation from this publication will be translated into the six official"),
            "",
        ),
        (
            (
                "the following recommendation was adopted by the gdg. evidence on the effectiveness of this "
                "intervention was derived"
            ),
            "",
        ),
        (
            (
                "the following section outlines the recommendation and the corresponding narrative summary "
                "of evidence for the"
            ),
            "",
        ),
        (
            (
                "the guideline was developed using standard operating procedures in accordance with the "
                "process described in"
            ),
            "",
        ),
        (
            (
                "the recommendation will be disseminated through who regional and country offices, "
                "ministries of health, professional"
            ),
            "",
        ),
        (
            (
                "the scientific evidence supporting the recommendation was synthesized using the grading of "
                "recommendations, assessment, development"
            ),
            "",
        ),
        ("this recommendation was developed in accordance with the standards and procedures in the", ""),
        (
            (
                "to ensure that the recommendation is correctly understood and appropriately implemented in "
                "practice, additional remarks"
            ),
            "",
        ),
    ),
    "j2b9Wj": (
        ("figure 10: literature search results", ""),
        ("figure 11: literature search results", ""),
        ("figure 2: literature search results", ""),
        ("figure 3: literature search results", ""),
        ("figure 4: literature search results", ""),
        ("figure 5: literature search results", ""),
        ("figure 6: literature search results", ""),
        ("figure 7: literature search results", ""),
        ("figure 8: literature search results", ""),
        ("figure 9: literature search results", ""),
    ),
    "j2bBrj": (
        ("abbreviations: ci, cochlear implant", ""),
        ("access to rehabilitation and support services is well referenced in current guidelines/guidance.", ""),
        ("an overview of the elements considered within the living guidelines across a person's journey", ""),
        ("an overview of the patient journey from hearing loss screening, to support following", ""),
        ("based on task force and external stakeholder feedback, future updates to recommendation 1/2 and", ""),
        ("for further information on prescribing and fitting hearing aids. the ci task force reviewed", ""),
        ("for further information on the development of the recommendation, please see the technical report.", ""),
        (
            (
                "for more detailed information on the development of this recommendation, please see the "
                "technical report."
            ),
            "",
        ),
        ("please follow the links below to find the nciq in its available languages:", ""),
        ("please note, updates are currently being made to the link below. all versions and", ""),
        (
            (
                "recommendations for objective and subjective assessment of hearing intervention performance "
                "are included in current"
            ),
            "",
        ),
        (
            "recommendations regarding quality of life (qol) including health-related (hr) qol, are provided in few",
            "",
        ),
        ("suggestions for goal setting and achievement in people using hearing interventions are presented in", ""),
        (
            (
                "the role of technology throughout the hearing health continuum is referenced in most "
                "guidelines/guidance."
            ),
            "",
        ),
        (
            ("to inform the management, monitoring and support of individuals accessing hearing interventions as part"),
            "",
        ),
        (
            "what constitutes best practice for the consideration and assessment of cognitive functioning in people",
            "",
        ),
    ),
    "j7m4dn": (
        (
            (
                "a shorter document containing the recommendation, remarks, implementation considerations "
                "and research priorities will be"
            ),
            "",
        ),
        ("recommendation dissemination and evaluation", ""),
        (
            (
                "see “calcium supplementation-high-dose calcium supplementation (>1 g/day) with or without "
                "co-supplements vs no"
            ),
            "",
        ),
        ("see “low-dose calcium supplementation (< 1 g/day) with or without co-supplements vs placebo”", ""),
        ("the dissemination and implementation of this recommendation is to be considered by all actors", ""),
        (
            (
                "the following section outlines the recommendation and the corresponding narrative summary "
                "of evidence for the"
            ),
            "",
        ),
        (
            (
                "the primary audience includes healthcare professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        ("the primary goal of this recommendation is to improve the quality of care and outcomes", ""),
        ("the recommendation document will be translated into the six un languages and disseminated through", ""),
        (
            "the recommendation will be disseminated through who regional and country offices, ministries of health",
            "",
        ),
        ("this recommendation may be of interest to professional societies involved in the care of pregnant", ""),
        (
            (
                "to ensure that the recommendation is correctly understood and appropriately implemented in "
                "practice, additional"
            ),
            "",
        ),
        (
            (
                "who has established a novel process for prioritizing and updating maternal and perinatal "
                "health recommendations"
            ),
            "",
        ),
    ),
    "j7mQNn": (
        ("what is the effect of oxytocin for pph prevention on the priority outcomes?", ""),
        ("detailed evidence-to-decision judgements can be found in the corresponding section.", ""),
        ("detailed evidence-to-decision judgements in the corresponding section.", ""),
        (
            "the guideline development group (gdg) adopted four main recommendations and six sub-recommendations at",
            "",
        ),
        (
            "the summary of findings (sof) tables and evidence to decision (etd) frameworks, presenting the balance",
            "",
        ),
        ("these updated recommendations were developed in accordance with the standards and procedures in the", ""),
        ("who has established a new process for prioritizing and updating maternal and perinatal health", ""),
    ),
    "j7q7Gn": (
        (
            (
                "((paraesophageal or paraoesophageal or para-esophageal or para-oesophageal or hiatal or "
                "hiatus) and hernia and (mesh"
            ),
            "",
        ),
        (
            (
                "(paraesophageal or paraoesophageal or para-esophageal or para-oesophageal or hiatal or "
                "hiatus) and hernia and (surgery"
            ),
            "",
        ),
        ("5 link to the source guideline", ""),
        (
            (
                "a certified guideline methodologist (inguide certificate number 2021-l2-v1-00001) who has "
                "participated in the development"
            ),
            "",
        ),
        (
            (
                "all members of the guideline development group will declare any direct (financial) or "
                "indirect (intellectual) conflicts"
            ),
            "",
        ),
        ("an average of 1.6 reports per year was published on q2. one trial", ""),
        (
            (
                "decision aids available on magicapp (https://app.magicapp.org/#/guideline/j7q7gn) and the "
                "evidence tables"
            ),
            "",
        ),
        ("guideline panel and external advisors", ""),
        ("guideline registration number: prepare-2023cn018", ""),
        ("monitoring, update and future steps", ""),
        ("please see the accompanying evidence-to-decision table.", ""),
        ("publication and dissemination strategy", ""),
        ("seven out of 9 panel members agreed with the recommendation on mesh versus", ""),
        ("six out of 9 panel members agreed with the recommendation on surgery versus", ""),
        ("stavros a. antoniou, md phd mph febs chair eaes guidelines subcommittee email:", ""),
        (
            (
                "the development of this document complied with the reporting checklist for public versions "
                "of guidelines: right-pvg."
            ),
            "",
        ),
        ("the steering group consists of a surgeon with expertise in upper gastrointestinal surgery and a", ""),
        ("the use of the guideline will be reviewed by eaes members at 2", ""),
        (
            (
                "there was unanimous consensus with regards to the recommendation on antireflux surgery "
                "versus gastropexy."
            ),
            "",
        ),
        (
            (
                "there was unanimous consensus with regards to the recommendation on surgery versus "
                "conservative management in"
            ),
            "",
        ),
        ("this guideline is funded and sponsored by eaes and will be submitted for publication", ""),
        (
            (
                "this is a patient version of the eaes multidisciplinary rapid guideline: systematic review, "
                "meta-analysis,"
            ),
            "",
        ),
        ("this protocol follows applicable agree-s and prisma-p reporting standards [3,4]. eaes members will", ""),
        ("wang x, chen y, akl ea, tokalić r, marušić a, qaseem a,", ""),
        ("we plan to update this guideline within 2030, unless substantial new evidence will become available.", ""),
        ("we will include 5 surgeons, one gastroenterologist and one patient representative as panel members.", ""),
    ),
    "j98OoE": (
        ("a summary infographic on the guideline recommendations and ungraded statements is provided", ""),
        ("figure 1. cultural safety for healthcare among first nations australians", ""),
        ("figure 5. self-management of chronic kidney disease among first nations australians", ""),
        ("figure 6. components of models of care for pre-dialysis", ""),
    ),
    "j9QY4j": (
        ("the wg formulated three pico questions. because these questions are closely intertwined", ""),
        ("the wg has formulated two pico questions. because these questions are closely related, an", ""),
        ("the working group formulated 6 pico questions. because these questions are closely related,", ""),
        ("the working group formulated one pico question.", ""),
        ("the working-group formulated one pico question.", ""),
        ("the working-group formulated one pico-question.", ""),
        ("the working-group formulated two pico-questions.", ""),
    ),
    "jDReJn": (
        (". hofmeyr gj, abdel-aleem h, abdel-aleem ma. uterine massage for preventing postpartum haemorrhage.", ""),
        (". mcdonald sj, middleton p. effect of timing of umbilical cord clamping of", ""),
        (". mshweshwe nt, hofmeyr gj, gülmezoglu am. controlled cord traction for the third", ""),
        (". rabe h, reynolds gj, diaz-rosello jl, mcdonald sj, middleton p. early versus", ""),
        ("the development of these recommendations involved 130 stakeholders who participated in the online", ""),
        ("the procedures used in the development of this guideline are outlined in the", ""),
        ("the scientific evidence for the recommendations was synthesized using the grading of", ""),
        ("the who technical consultation adopted 32 recommendations and these are shown in", ""),
    ),
    "jDRvgn": (
        ("(adapted from who 2017 and ecdc 2014)", ""),
        ("achee nl, grieco jp, vatandoost h, seixas g, pinto j, ching-ng l,", ""),
        ("allen t, crouch a, topp sm (2021). community participation and empowerment approaches", ""),
        ("baldacchino f, caputo b, chandre f, drago a, della torre a, montarsi", ""),
        ("becker n, lüthy p (2017). chapter 26 - mosquito control with entomopathogenic", ""),
        ("becker n, zgomba m (2007). chapter 21 - mosquito control in europe.", ""),
        ("becker, n. (2010). the rhine larviciding program and its application to vector", ""),
        ("bellini r, michaelakis a, petrić d, schaffner f, alten b, et al.", ""),
        ("bellini r, zeller h, van bortel w (2014). a review of the", ""),
        ("chaskopoulou a, l'ambert g, petric d, bellini r, zgomba m, groen ta,", ""),
        ("flacio e, engeler l, tonolla m, lüthy p, patocchi n (2015). strategies", ""),
        ("giunti g, becker n, benelli g (2023). invasive mosquito vectors in europe:", ""),
        (
            (
                "in order to provide good practices for the surveillance and monitoring of mosquitoes, we "
                "summarize below"
            ),
            "",
        ),
        ("la ruche g, souarès y, armengaud a, peloux-petiot f, delaunay p, et", ""),
        ("references to chapter 7.4", ""),
        ("who: fifth meeting of the vector control advisory group, geneva, switzerland, 2–4", ""),
        ("who: handbook for integrated vector management. geneva: world health organization; 2012.", ""),
        ("who: third meeting of the vector control advisory group. geneva, switzerland,12-14 november", ""),
        ("wilson al, boelaert m, kleinschmidt i, pinder m, scott tw, tusting ls,", ""),
    ),
    "jDe23L": (
        ("an enabling environment should be created for the use of txa", ""),
        (
            "as part of who´s normative work on supporting evidence-informed policies and practices, the department",
            "",
        ),
        ("dissemination and implementation of the recommendation is to be considered by all actors involved", ""),
        ("in 2012, who published 32 recommendations for the prevention and treatment of pph, including", ""),
        (
            (
                "in 2017, the executive gsg on who maternal and perinatal health recommendations prioritized "
                "the updating"
            ),
            "",
        ),
        ("recommendation dissemination and evaluation", ""),
        (
            (
                "the following section outlines the recommendation and the corresponding grade tables and "
                "narrative summary"
            ),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        ("the recommendation should be adapted into locally appropriate documents that are able to meet", ""),
        ("the recommendation will be disseminated through who regional and country offices, ministries", ""),
        ("this recommendation will also be of interest to professional societies involved in the care", ""),
    ),
    "jDePyL": (
        ("part of these questions will be further addressed in next stages of be-safe project", ""),
        ("this guideline is developed as a part of the be-safe project, funded by the", ""),
        ("two pairs of authors extracted epoc factors data in duplicate. the factors reported per arm", ""),
        ("for any queries related to the clinical aspects of the guidelines, please contact", ""),
        ("for methodological queries, please contact the guideline methods chair", ""),
        ("for any other queries or comments, please contact", ""),
    ),
    "jDeeDL": (
        (
            (
                "about this guideline: this guideline from the world health organization (who) incorporates "
                "the latest high-quality"
            ),
            "",
        ),
        ("fig. 1. network plot of indirect comparison of mab114 compared with standard care via zmapp", ""),
        ("fig. 2. network plot of direct comparison of regn-eb3 with standard care via zmapp", ""),
        ("fig. 3. network plot of direct comparison of regn-eb3 to mab114", ""),
        ("fig. 5. network plot of direct comparison between zmapp and standard care", ""),
        ("network plot of direct comparison between mab114 and remdesivir", ""),
        ("network plot of direct comparison between regn-eb3 to zmapp", ""),
        ("network plot of direct comparison of mab114 to zmapp", ""),
        ("network plot of direct comparison of regn-eb3 and remdesivir", ""),
        ("network plot of direct comparison of remdesivir to zmapp", ""),
        ("the indirect comparison of remdesivir to standard care via zmapp is shown below.", ""),
        ("to note, to access the document offline, please download it for personal use.", ""),
    ),
    "jMKQ9n": (("may want to fill this part out", ""),),
    "jMMYPj": (
        (
            (
                "our international panel included general practitioners, internists, paediatricians, "
                "pharmacists, physicians specialising in pain management"
            ),
            "",
        ),
    ),
    "jMMeqj": (
        ("australia government - guidance and resources for provider to support the aged care quality standards", ""),
        ("federal legislation relating to quality of care principles", ""),
        ("for further information about antidepressant continuation, please refer the to benefits and harms", ""),
        ("for more information about consent, please refer to the consent section.", ""),
        ("for more information about the evidence review, please refer to the technical report.", ""),
        ("for more information about the evidence update, please refer to the technical report.", ""),
        ("the evidence update for this guideline used the", ""),
    ),
    "jNW0VL": (
        ("conclusion: recommendations for jia-associated uveitis were adapted to the canadian context by a", ""),
        ("gaudo, ocular immunology and inflammation, volume 12, 2004 -", ""),
        (
            (
                "methods: recommendations were developed using the grading of recommendations assessment, "
                "development and evaluation"
            ),
            "",
        ),
        ("please see references 37-39 in the acr guidelines for uveitis.", ""),
        ("results from the web survey identified agreement to adopt 13 of the source recommendations", ""),
        ("results: the survey identified that 7 of the nineteen recommendations required rigorous discussion", ""),
        (
            (
                "the acr/af guidelines used the rigorous grade (grading of recommendations, assessment, "
                "development, and evaluations) methodology"
            ),
            "",
        ),
        (
            (
                "the grade-adolopment approach provides a structured approach to selectively combining "
                "adoption, adaptation and"
            ),
            "",
        ),
        ("this work represents the first set of canadian jia-associated uveitis guidelines, and the first", ""),
        ("wakefield, arch ophthalmol. 1986;104(6):847-851", ""),
    ),
    "jNxJmn": (
        ("at the time when results of 4 trials were available,", ""),
        ("before the first panel meeting, the steering committee met 5 times to discuss issues of", ""),
        ("group composition and process", ""),
        ("in the third panel meeting, the panel reviewed and discussed the updated grade summary of", ""),
        ("management of competing interests", ""),
        ("our competing interest procedures adhered to guidelines international network principles.", ""),
        ("the guideline panel comprised 24 members from 6 countries (canada, china, india,", ""),
        ("the guideline steering committee comprised 7 members: the guideline clinical chair (ees),", ""),
        ("to introduce grade and optimize their participation, we conducted a training session with our patient", ""),
    ),
    "jNxw7n": (
        ("evidence up to date as of january 31, 2023.", ""),
        ("monitoring and evaluation it will be important to monitor this recommendation in real-world practice", ""),
        (
            (
                "n.b., please see the ‘appendices’ (pico 10) for evidence tables, systematic search-related "
                "criteria and results"
            ),
            "",
        ),
        (
            (
                "n.b., please see the ‘appendices’ (pico 8) for evidence tables, systematic search-related "
                "criteria and results"
            ),
            "",
        ),
        (
            (
                "n.b., please see the ‘appendices’ (pico 9) for evidence tables, systematic search-related "
                "criteria and results"
            ),
            "",
        ),
        ("see equity considerations summarized across other etd domains.", ""),
    ),
    "jO0lNL": (
        ("an online survey was then implemented, where diverse national stakeholders were asked to vote", ""),
        ("priority topics on poverty-related diseases in the field of newborn and child health were", ""),
        ("the gela project focuses on newborn and child health. to identify priority topics within", ""),
        ("figure 1: gela-adolopment algorithm (adapted from the grade-adolopment algorithm", ""),
        (
            (
                "given the enormous work put into producing the guideline recommendation including "
                "infographics and virtual mode"
            ),
            "",
        ),
        ("honourable minister for health", ""),
        ("justifications and remarks are developed by the gdg with support from the methodologists", ""),
        (
            "organisation, budget, planning and training",
            "the gela project focuses on newborn and child health. to identify priority topics within this area",
        ),
        ("see appendix d (section 6) for full evidence profiles and reviews", ""),
        ("see appendix e (section 6) for full evidence profiles and reviews", ""),
        ("see appendix f (section 6) for full evidence profiles and reviews", ""),
        (
            "table 2. timeline of guideline-development activities",
            (
                "the three topics identified were ’interventions for identification and early management of "
                "pre-eclampsia"
            ),
        ),
        (
            "the global evidence, local adaptation (gela) project aimed to enhance decision makers’ capacity to use",
            "",
        ),
        ("ᵃ we synthesised findings addressing similar themes from the mini-qes and the chatfield review", ""),
        ("ᵇ these assessments are based on those for the individual contributing findings from the mini-qes", ""),
    ),
    "jO3B7j": (
        ("an update of this rapid guideline is planned to take place in 2025, if further", ""),
        (
            (
                "conclusion this rapid guideline will address the diagnosis and management of acute "
                "appendicitis in elderly"
            ),
            "",
        ),
        ("implications for practice and research stringent criteria defined by grade and agree ii will be", ""),
        ("methods the present protocol adheres to agree ii and prisma reporting standards. it will be", ""),
        (
            (
                "publication and dissemination strategy as a eaes research committee/guideline subcommittee "
                "project, this guideline will be"
            ),
            "",
        ),
        ("research ethics eaes, as the funder, will not be involved in the development of this", ""),
        (
            (
                "strengths and limitations the strengths and limitations of rapid guidelines have been "
                "previously reported. the"
            ),
            "",
        ),
        (
            (
                "the development of this document complied with the reporting checklist for public versions "
                "of guidelines:"
            ),
            "",
        ),
        ("the development of this guidance was sponsored and funded by the european association for endoscopic", ""),
        (
            (
                "the european association for endoscopic surgery (eaes) therefore sponsored the development "
                "of this rapid guideline"
            ),
            "",
        ),
        ("the guideline will be made available on the website of surgical endoscopy & other interventional", ""),
        (
            "the project is funded by the european association for endoscopic surgery. the funding bodies",
            "the pico questions have been formulated by the steering group and thresholds for clinical",
        ),
        ("the steering group will consider constructive feedback received during the conduct of the project via", ""),
        (
            (
                "this rapid guideline was sponsored and funded by the european association for endoscopic "
                "surgery, however"
            ),
            "",
        ),
        ("use of the guideline by eaes members will be monitored through an online survey", ""),
        ("we did not identify any registered studies planned to address any of the guideline questions", ""),
    ),
    "jOKYGj": (
        ("as a living guideline, our team will update this recommendation when more evidence becomes available.", ""),
        (
            "box 1: linked resources in this bmj rapid recommendations",
            "what triggered this guideline, what is new in this version, and what is coming next?",
        ),
        (
            "how did the panel formulate the recommendation?",
            "how were values and preferences of patients incorporated?",
        ),
        (
            (
                "how this guideline was created: an international panel, including three patient partners, "
                "eleven healthcare providers,"
            ),
            "",
        ),
        (
            "standards, methods, and process for trustworthy guidance",
            "what research did the guideline panel request and review?",
        ),
        ("updates: here we present the first version of our living practice guideline,", ""),
    ),
    "jOKZ9j": (
        ("figure 10: network geometry, sucra ranking and network meta-analysis forest plot with ‘placebo’", ""),
        ("figure 11: prisma flow of the literature search", ""),
        ("figure 12: forest plot depicting the change in mean arterial blood pressure after dopamine", ""),
        ("figure 13: prisma flow of the literature search", ""),
        ("figure 14: forest plots depicting the change in systolic bp, diastolic bp, mean arterial", ""),
        ("figure 15: prisma flow of the literature search", ""),
        ("figure 2: prisma flow of the literature search", ""),
        ("figure 3: agreement between nibp vs ibp from the included studies in this", ""),
        ("figure 4: prisma flow of the literature search", ""),
        ("figure 5: prisma flow of the literature search", ""),
        ("figure 6: forest plots depicting the effect estimates for different outcomes from rcts", ""),
        ("figure 7: prisma flow for the literature search", ""),
        ("figure 8: prisma flow of the literature search", ""),
        ("figure 9: network geometry, sucra ranking and network meta-analysis forest plot with ‘placebo’", ""),
    ),
    "jW0ZbL": (
        ("(note that these are some examples of commercial services, but the list is not exhaustive", ""),
        ("additional screening tools used in australian states and territories", ""),
        ("these guidelines are regularly being updated and expanded. the following topics are currently", ""),
    ),
    "jW9PJn": (
        ("study eligibility the titles and abstracts of the identified citations were reviewed for relevance", ""),
        ("study identification we systematically searched medline (accessed via pubmed) and the cochrane", ""),
    ),
    "jWN6oE": (
        ("ideally, implementation of the recommendations should be monitored at the health-service level.", ""),
        ("monitoring and evaluating the guideline implementation", ""),
        ("the first indicator provides an overall assessment of the use of induction of labour", ""),
    ),
    "jXX6xj": (
        (
            (
                "figure 1. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 10. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 11. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 12. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 13. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 14. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 15. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 16. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 17. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 18. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 19. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 2. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 20. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 21. sensitivity (excluding non-interventional, non-randomised studies) "
                "random-effects meta-analysis comparing mobile stroke"
            ),
            "",
        ),
        (
            (
                "figure 22. sensitivity (excluding non-interventional, non-randomised studies) "
                "random-effects meta-analysis comparing mobile stroke"
            ),
            "",
        ),
        (
            (
                "figure 23. sensitivity (excluding non-interventional, non-randomised studies) "
                "random-effects meta-analysis comparing mobile stroke"
            ),
            "",
        ),
        (
            (
                "figure 24. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 25. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 26. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 5. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 6. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        (
            (
                "figure 7. random-effects meta-analysis comparing mobile stroke units and conventional "
                "management in patients with"
            ),
            "",
        ),
        ("figure 9. random-effects meta-analysis comparing mobile stroke units and conventional management in", ""),
        ("six mwg members (sw, ha, kl, ts, ss, g.turc) independently screened the titles and abstracts", ""),
        ("the guideline document was subsequently reviewed several times by all mwg members and revised", ""),
        ("the numerical results of the votes for this expert consensus statement are provided in supplemental", ""),
        (
            (
                "this guideline was initiated by the european stroke organisation (eso) and prepared "
                "according to eso standard"
            ),
            "",
        ),
    ),
    "jXXAdj": (
        ("includes information, resources (e.g. publications and helplines), a pain measurement scale", ""),
        ("safe storage and disposal of pain medication", ""),
        ("source: abernethy et al for the australia-modified karnofsky performance status (akps) scale", ""),
        ("source: eastern cooperative oncology group 1982", ""),
    ),
    "jXXBBj": (
        (
            (
                "this section has 6 recommendations for different hif-phis for treatment of anaemia in "
                "dialysis depeendent"
            ),
            "",
        ),
        (
            (
                "this section has 6 recommendations for different hif-phis for treatment of anaemia in "
                "non-dialysis depeendent"
            ),
            "",
        ),
        ("this section has one recommendation.", ""),
    ),
    "jbXKAn": (
        ("figure 10: summary roc curve - diagnostic accuracy of cranial ultrasound - neurodevelopmental delay", ""),
        ("figure 3: paired forest plot - diagnostic accuracy of mri brain", ""),
        ("figure 4: summary roc curve - diagnostic accuracy of mri brain", ""),
        ("figure 5: paired forest plot - diagnostic accuracy of cranial ultrasound", ""),
        ("figure 6: summary roc curve - diagnostic accuracy of cranial ultrasound", ""),
        ("figure 7: paired forest plot - diagnostic accuracy of cranial ultrasound - post-neonatal epilepsy", ""),
        ("figure 8: summary roc curve - diagnostic accuracy of cranial ultrasound - post-neonatal epilepsy", ""),
        ("figure 9: paired forest plot - diagnostic accuracy of cranial ultrasound - neurodevelopmental delay", ""),
        ("mesh: discontinuation or continuation or drug tapering", ""),
        ("search engines: medline, embase, cinahl, central, cross references of selected articles", ""),
    ),
    "jbXYZn": (
        (
            (
                "international human rights law includes fundamental commitments of states to enable women "
                "and adolescent"
            ),
            "",
        ),
        (
            (
                "see latex balloon–loaded nelson catheter intrauterine tamponade (air filled) plus stitch "
                "and standard care"
            ),
            "",
        ),
        ("the following section outlines the recommendation and the corresponding narrative summary of evidence", ""),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local health-care"
            ),
            "",
        ),
        ("this recommendation will also be of interest to women giving birth in a range of", ""),
        (
            (
                "to ensure that the recommendation is correctly understood and appropriately implemented in "
                "practice, additional remarks"
            ),
            "",
        ),
        (
            (
                "who has established a new process for prioritizing and updating maternal and perinatal "
                "health recommendations"
            ),
            "",
        ),
        ("¹ these outcomes reflect the prioritized outcomes used in the development of this recommendation", ""),
    ),
    "jboXZL": (
        ("1. mazari fak, khan ja, samuel n, smith g, carradice d, mccollum pc et al.", ""),
        (
            (
                "1. methods editor, systematic reviewer. academic centre for general practice, department of "
                "public health"
            ),
            "",
        ),
        ("2. van reijen, bekkering ge, frans f, koelemaij mjw. management of intermittent claudication:", ""),
        ("3. siemieniuk ra, agoritsas t, macdonald h, guyatt gh, brandt l, vandvik po. introduction to", ""),
        ("4. hageman d, van den houten mm, spruijt s, gommans ln, scheltinga mr, teijink ja.", ""),
        ("5. harwood ae, smith ge, cayton t, broadbent e, chetter ic. a systematic review of", ""),
        ("6. fokkenrood hj, scheltinga mr, koelemay mj, breek jc, hasaart f, vahl ac et al.", ""),
        ("absolute benefit and harms the infographic explains the recommendations and provides an overview", ""),
        ("all panel members were pre-screened for conflicts of interest prior to the guideline process", ""),
        ("bekkering ge¹, kuijpers t², adegas a³, burgers j⁴, crockett k⁵, forjaz c⁶, forneau i⁷", ""),
        ("competing interests all authors have completed the icmje interests disclosure form and a detailed", ""),
        ("data supplements appendix 1: full list of panel members and declarations of interests", ""),
        ("dr leicht received personal fees and other support from exercise and sports science australia", ""),
        (
            (
                "financial disclosures: dr forjaz received scholarships from the national council for "
                "scientific and technological development"
            ),
            "",
        ),
        ("how the recommendation was created methodology was in accordance of the bmj rapidrecs series.", ""),
        (
            (
                "intellectual disclosures: drs. bekkering and van reijen participated in writing the "
                "complementary systematic review"
            ),
            "",
        ),
        ("no panel member has disclosed any other relationships that could influence the work.", ""),
        ("professional disclosures: drs. forneau and nordanstig perform revascularizations.", ""),
        ("this guideline was not funded.", ""),
    ),
    "jbzG8j": (
        ("our guideline also has limitations. first, the grade approach only allows for the strength", ""),
        ("the strengths of this guideline include its systematic approach to searching the literature", ""),
        ("the working group identified five areas for which pico questions were formulated", ""),
    ),
    "jlAbxL": (
        ("additional trials can be reviewed on clinicaltrials.gov", ""),
        ("authors: anas alawawdeh, timothy price", ""),
        ("authors: catherine mitchell, william k. murray", ""),
        ("authors: david chan, veenoo agrawal, bryan chan", ""),
        ("authors: david wyld, mark nalder", ""),
        ("authors: erin laing, caley schnaid", ""),
        ("authors: gabrielle cehic, nick pavlakis, bill macdonald, grace kong, thuan tzen koh", ""),
        ("authors: grace kong, david pattison, hyun ko", ""),
        ("authors: jane turner, kate wakelin", ""),
        ("authors: john burgess, roderick clifton-bligh", ""),
        ("authors: melainie cameron, holly evans, camille short", ""),
        ("authors: michael kitchener, phil chan, richard maher", ""),
        ("christopher b nahm, mehrdad nikfarjam, andrew barbour, ben thomson, jaswinder samra", ""),
        ("figure 1. a proposed algorithm for management of pc and tapgl (adapted from)", ""),
        ("figure 1: suggested algorithm for chemotherapy choice based on the ki-67 index", ""),
        ("future revisions of the cosa nens guidelines will update these advances as the data matures.", ""),
        ("picture from frilling a, clift a, “therapeutic strategies for neuroendocrine liver metastases”.", ""),
        ("selected substances which may interfere with measured ur 5hiaa (adapted from maton pn.", ""),
        ("table 1: selected trials in grade 3 gep nens", ""),
        ("working group chair: david chan", ""),
    ),
    "jlPRdj": (
        (
            (
                "about these guidelines: this updated guidelines from the world health organization (who) "
                "incorporate available new"
            ),
            "",
        ),
        (
            ("adjunctive immunomodulatory therapy vs no immunomodulatory therapy. for systematic review details, see."),
            "",
        ),
        ("note: green bars indicate influenza patients who received treatment, striped green bars are", ""),
        ("target audience: the guidelines are designed primarily for health care providers who manage patients", ""),
        ("the guidelines will also serve as a reference source for policy-makers, health managers and health", ""),
        ("updates and access: this publication is the update of the document published in 2022 entitled", ""),
    ),
    "jm83RE": (
        ("additional screening tools used in australian states and territories", ""),
        ("approved by nhmrc in december 2011; this recommendation is currently considered stable and no update", ""),
        ("approved by nhmrc in june 2014; this recommendation is currently considered stable and no update", ""),
        ("australian preterm birth prevention alliance", ""),
        (
            "brown et al (2020) evidence-based physical activity guidelines for pregnant women. report prepared for",
            "",
        ),
        ("carroll d (2004) pre-travel preparation of the pregnant traveller", ""),
        ("decision aid for prenatal testing for fetal abnormalities", ""),
        ("health on the net foundation", ""),
        ("information on how to calculate dietary intake of calcium can be found in the practical", ""),
        ("instructions: enter the number of servings per day in the green column", ""),
        ("maternity care in the bush", ""),
        (
            ("more details on the cervical screening pathway, including management and follow-up for hpv test results"),
            "",
        ),
        ("murra mullangari: introduction to cultural safety", ""),
        ("national gestational diabetes register", ""),
        ("nhmrc/doha (2015) healthy eating when you’re pregnant or breastfeeding. accessed 5 august 2020", ""),
        ("nuchal translucency online learning program", ""),
        ("ottawa personal decision aid", ""),
        ("queensland health aboriginal and torres strait islander cultural capability framework 2010 to 2033", ""),
        ("ranzcog (2015) prenatal screening and diagnosi of chromosomal and genetic abnormalities in the fetus", ""),
        (
            (
                "resources available to health professionals include websites and professional "
                "organisations, seminars, courses and printed materials"
            ),
            "",
        ),
        ("sources of reliable online health information", ""),
        ("table h1 presents a summary of advice on common conditions during pregnancy considered a priority", ""),
        (
            "these guidelines include recommendations on baseline clinical care for women with low-risk pregnancies",
            "",
        ),
        ("this is a stable recommendation and no updates are currently planned.", ""),
        ("walker r & reibel t (2009) developing cultural competence for health services and practitioners", ""),
        ("walker r (2010) improving communications with aboriginal families", ""),
        ("who interactive disease maps", ""),
        ("¹¹ see part 3 of the australian immunisation handbook 10ᵗʰ edition for discussion", ""),
    ),
    "jxBJyn": (
        ("consolidated guidelines on hiv testing services. geneva: world health organization; 2015", ""),
        ("further information regarding recommendations 1 and 2 can be found in the who publications", ""),
        ("further information regarding recommendations 5, 6, and 7 can be found in the who", ""),
        ("guidance on couples hiv testing and counselling, including antiretroviral therapy for treatment and", ""),
        ("the following factors were taken into consideration during the deliberations.", ""),
        ("the guideline development group took into consideration standard points as presented in the", ""),
        ("the guideline development group took into consideration the factors listed next during the", ""),
        ("the remarks in this section are intended to give some considerations for implementation", ""),
        ("the remarks in this section are points to consider regarding implementation of the recommendations", ""),
    ),
    "jxxdwj": (
        (
            (
                "accordingly, the clinical practice committee of the scandinavian society of anesthesiology "
                "and intensive care medicine"
            ),
            "",
        ),
        ("the results and recommendations based on the picos are presented below (a-d)", ""),
        ("this guideline will be updated if new potentially practice changing trials are published.", ""),
    ),
    "jz5DdE": (
        ("a guide for community members. frequently asked questions about bowel cancer screening from:", ""),
        ("a guide for health professionals. frequently asked questions about bowel cancer screening from:", ""),
        ("information for aboriginal and torres strait islander peoples on free bowel cancer screening:", ""),
        ("information for gps. bowel screening and aboriginal and torres strait islander people from:", ""),
        ("national bowel cancer screening program – clinical resources:", ""),
        ("resources for families and communities – indigenous bowel screening", ""),
        ("screening colonoscopies for people in categories 2 and 3 are currently subsidised under the", ""),
        ("the 2023 guideline chapter includes evidence-based recommendations (ebr) and practice points.", ""),
        ("the development and update of these questions was guided by current evidence and practice", ""),
        ("understanding the bowel cancer screening test in your language:", ""),
        ("where to find information about bowel cancer, bowel cancer screening and bowel cancer treatment", ""),
    ),
    "jz7rXL": (
        (
            ("8 vandvik et al. primary and secondary prevention of cardiovascular disease. antithrombotic therapy and"),
            "",
        ),
        ("any new evidence that emerges after the initial publication of these recommendations", ""),
        ("as depicted in infographic 1 the risk stratified recommendations warrant doctors to identify", ""),
        ("box 2. linked resources in this bmj rapid recommendations cluster", ""),
        (
            (
                "expanded version of results with multi-layered recommendations, evidence summaries and "
                "decision aids for use"
            ),
            "",
        ),
        ("hao q, aertgeerts b, guyatt g, et al. pcsk9 inhibitors and ezetimibe for the reduction", ""),
        ("khan su, yedlapati sh, lone an, et al. anti-pcsk9 agents and ezetimibe for cardiovascular risk", ""),
        ("li j, du h, wang y, et al. safety of proprotein convertase subtilisin/kexin 9 inhibitors", ""),
        ("review and network meta-analysis of all available randomised trials that assessed effects of pcsk9", ""),
        ("summary of the results from the rapid recommendation process", ""),
        (
            (
                "the infographic provides an overview of the risk-stratified recommendations, with evidence "
                "summaries of the"
            ),
            "",
        ),
        ("wang y, zhan s, du h, et al. safety of ezetimibe in lipid-lowering treatment: systematic", ""),
        ("what is the risk of my patient?", ""),
    ),
    "jz7xeL": (
        (
            "a representative of safe reviewed the phrasing of the recommendations and expert suggestions to ensure",
            "",
        ),
        ("a systematic review of literature was done to collect evidence to answer the pico questions.", ""),
        ("due to space constraints, the print version of this guideline incorporates the abstract", ""),
        ("in this manuscript, the analysis of each pico question was addressed in distinct sections.", ""),
        ("the guidelines document was reviewed several times by all mwg members, and modified using a", ""),
        (
            (
                "the guidelines for management of transient ischaemic attacks (tia) follow the standard "
                "operations procedure"
            ),
            "",
        ),
        ("the pico questions were reviewed and approved by the eso guidelines committee.", ""),
        (
            (
                "the working group selected eight population, intervention, comparator, outcome (pico) "
                "questions that were considered"
            ),
            "",
        ),
        ("these guidelines focus on issues specific to early tia management. therefore, aspects such as", ""),
        ("two chairpersons (am and acf) were selected by the eso guidelines committee to assemble", ""),
    ),
    "jzQAlE": (
        ("| | | --- | | priority outcomes | | maternal outcomes - pre-eclampsia", ""),
        (
            (
                "in 2011, the world health organization (who) published 22 recommendations for the "
                "prevention and treatment"
            ),
            "",
        ),
        ("in 2017, who established a new process for prioritizing and updating maternal and perinatal", ""),
        (
            (
                "the following section outlines the recommendations and the corresponding narrative summary "
                "of evidence for"
            ),
            "",
        ),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        ("the primary goal of these recommendations is to improve the quality of care and outcomes", ""),
        ("the recommendations will also be of interest to professional societies involved in the care", ""),
        ("these outcomes reflect the prioritized outcomes used for this recommendation, in the", ""),
    ),
    "jzb7Xj": (
        ("abbreviation key cpg: clinical practice guidelines. fda: us food and drug administration.", ""),
        ("add a summary of evidence here and link ref", ""),
        ("external review and updating process once the panel produced the first draft", ""),
        ("in developing this guideline, the american dental association science and research institute,", ""),
        ("see the practical info tab for recommendations footnotes", ""),
        ("target audience these recommendations are intended primarily for general dentists.", ""),
    ),
    "mL6yYj": (("for more detailed information see practical issues below the evidence profile in magicapp", ""),),
    "n303gE": (
        ("a short survey to determine which baseline absolute rates of clinical outcomes should be used", ""),
        ("existing guidelines on laboratory diagnosis are found at:", ""),
        ("figure 1. the course of dengue illness by days from onset.", ""),
        ("laboratory guidance for the diagnosis of dengue in outbreak settings is under development", ""),
        ("many thanks for taking the time to help us.", ""),
        ("many thanks in advance for taking the time to complete this short survey.", ""),
        ("this guideline will be updated according to emerging evidence.", ""),
        ("you have completed the survey.", ""),
    ),
    "n3QAOj": (
        ("co-authors: aisling kelly; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: ben stevenson; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: carmel o'kane; cancer therapy medication safety guidelines working group", ""),
        (
            (
                "co-authors: cassandra o'brien; hayley vasileff; cancer therapy medication safety guidelines "
                "working group"
            ),
            "",
        ),
        ("co-authors: connie diakos; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: gail rowan; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: ganessan kichenadasse; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: hayley vasileff; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: jim siderov; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: kate cameron; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: leisa brown-west; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: michael powell; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: rachael lawson; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: tina griffiths; cancer therapy medication safety guidelines working group", ""),
        ("co-authors: winston liauw; cancer therapy medication safety guidelines working group", ""),
        ("note: additional recommendations on information to be provided to patients are included in", ""),
        ("note: further information on oral cancer therapy is provided under the clinical verification", ""),
        (
            "note: further information on prescribing, dispensing and administering oral cancer therapy is provided",
            "",
        ),
        ("note: further information on the medication order can be found in the prescribing", ""),
        (
            (
                "note: further recommendations on competencies and skills relevant to individual disciplines "
                "are provided"
            ),
            "",
        ),
        ('note: the section of these guidelines on role of the pharmacist, "dispensing intrathecal', ""),
    ),
    "n3QGej": (
        ("figure 8.1 vials of rh d immunoglobulin issued since 2003–04", ""),
        ("note: issues of rhophylac are too small to appear on the graph.", ""),
        ("printable guideline summary for health professionals", ""),
        ("the demand for products over recent years does not correlate with the change", ""),
    ),
    "n3QxOj": (
        ("a systematic review was undertaken to answer this clinical question.", ""),
        ("background chapter based on general literature summary. the 2008 content was reviewed and updated", ""),
        ("each ebr was assigned a grade by the expert working group, taking into account", ""),
        ("figure 1. a, mohs micrographic surgery technique. b, standard technique for wide local excision", ""),
        ("no relevant clinical trials are known to be underway.", ""),
        ("recommendations and practice points were developed by working party members and subcommittee members.", ""),
        ("the cancer australia-endorsed optimal care pathway for people with basal cell carcinoma or squamous", ""),
        ("the search strategy, inclusion and exclusion criteria, and quality assessment are described in detail", ""),
        ("there are no studies currently underway which, when published, may provide more information", ""),
        ("there are no unresolved issues about this topic.", ""),
        ("there are no unresolved questions in regard to this topic nor suggestions of research", ""),
        ("this is a summary of all recommendations in these guidelines, please note that some chapters", ""),
        ("this page lists the questions answered by systematic review and modelling. for full details", ""),
        ("this position statement is approved by the australian and new zealand bone and mineral society,", ""),
        ("this section addresses questions and objections that may arise during the skin cancer consultation", ""),
    ),
    "nBAZDL": (
        (
            "a medline and cochrane search was performed using the words",
            "we couldn’t identify studies to pool the data to predict the need",
        ),
        ("a medline search was performed using the words", ""),
        ("figure 1: steps in formulation of clinical practice guidelines", ""),
        ("search strategies: we searched three databases ovid medline (r) from 1946 to october 27, 2022", ""),
        ('search strategy: (("infant, newborn"[mesh terms] or ("infant"[all fields] and "newborn"[all fields])', ""),
        (
            "search strategy: a search strategy was conducted comprehensive literature search on medline via pubmed",
            "",
        ),
        ('search terms:("infant, newborn"[mesh terms] or ("infant"[all fields] and "newborn"[all fields])', ""),
        ("the steps followed in writing the cpg are summarized below and in figure 1.", ""),
    ),
    "nBAezL": (
        ("algorithm used in the nor-drum trial as an", ""),
        (
            (
                "an international panel that included patients, healthcare professionals, and methodologists "
                "created these recommendations following"
            ),
            "",
        ),
        ("for more information click “how the guideline was created”.", ""),
        (
            (
                "the guideline includes the evidence supporting the decisions, the rationale behind the "
                "decisions, and practical"
            ),
            "",
        ),
        (
            (
                "this guideline contributes to the bmj rapid recommendations series - a collaborative effort "
                "between magic"
            ),
            "",
        ),
    ),
    "nBMa0L": (
        ("similarly, estimates on minimally important differences (mids) for pain, function and quality of life", ""),
        ("the evidence summary displayed as a grade summary of findings table represents the primary comparison", ""),
        ("this is a bmj rapid recommendation produced by magic", ""),
    ),
    "nBRK8n": (
        ("additional details are available in the network meta-analysis", ""),
        ("four people living with chronic spine pain, including two military veterans, were full", ""),
        ("given the lack of trustworthy guidelines in this area of high unmet clinical", ""),
        ("how this guideline was created an international guideline development panel including four people", ""),
        (
            (
                "our international panel—including physiatrists (also called physical medicine and "
                "rehabilitation physicians), anesthesiologists"
            ),
            "",
        ),
        ("patient and public involvement", ""),
        ("we required 80% consensus among panel members for strong recommendations and a majority", ""),
    ),
    "nBkO1E": (
        (
            (
                "about this guideline: this living guideline from the world health organization (who) "
                "dynamically incorporates"
            ),
            "",
        ),
        ("ci: confidence interval, rr: relative risk.", ""),
        ("infographic co-produced by the bmj and magic; designer will stahl-timmins", ""),
        (
            (
                "the infographic illustrates these three disease severity groups and key characteristics to "
                "apply in practice."
            ),
            "",
        ),
        ("updates and access: this is the 15th version (14th update), and replaces all earlier versions.", ""),
    ),
    "nBkgRE": (
        ("for each pico question, search terms were identified, tested, refined, and agreed by the mwg", ""),
        ("the guidelines document was reviewed by all mwg members, and modified using a delphi approach", ""),
        ("the guidelines for management of ead and iad follow the standard operations procedure (sop)", ""),
        (
            (
                "the mwg formulated six main pico (population, intervention, comparator, outcome) questions "
                "relevant for ead"
            ),
            "",
        ),
    ),
    "nBpo1j": (
        ("justifications and remarks are developed by the gdg with support from the methodologists. these", ""),
        ("research gaps were identified during the guideline meeting when the evidence based on the", ""),
        ("table 2. outline of research gaps that arose during the guideline meeting", ""),
        ("director, family health department", ""),
        ("figure 1. gela-adolopment algorithm (adapted from the grade-adolopment algorithm)", ""),
        ("finally, we wish to express our special thanks to the", ""),
        ("given the enormous work put into producing the guideline recommendation including infographics", ""),
        ("honourable minister for health", ""),
        ("in 2022, the world health organization published new and updated recommendations for care", ""),
        ("see appendix f (section 6) for full evidence profiles and reviews", ""),
        ("table 1. outline of research gaps that arose during the guideline meeting", ""),
        (
            (
                "the federal ministry of health acknowledges all stakeholders who contributed selflessly to "
                "the development"
            ),
            "",
        ),
        (
            (
                "this guideline emerged from a series of evidence-based steps and provides evidence-informed "
                "recommendations"
            ),
            "",
        ),
        ("we sincerely appreciate the support of unicef and who nigeria for their valuable guidance", ""),
    ),
    "nJ5zyL": (
        ("use of the guideline by eaes members will be monitored through an online survey", ""),
        ("4 purpose, scope and target users", "6 recommendations"),
        (
            (
                "in an annual survey of the european association for endoscopic surgery (eaes) research "
                "committee/guidelines"
            ),
            "",
        ),
        ("the guideline was sponsored and funded by the european association for endoscopic surgery;", ""),
        (
            (
                "the guideline will be published in surgical endoscopy & other interventional techniques, "
                "official journal"
            ),
            "",
        ),
        (
            (
                "this guideline applies primarily to healthcare professionals, policy makers, patients, and "
                "other stakeholders"
            ),
            "",
        ),
        ("this is a patient version of the eaes rapid guideline: updated systematic review, network", "2 key points"),
        ("unanimous consensus was achieved in the first delphi round for all recommendations (responses", ""),
    ),
    "nJW8bE": (
        (
            (
                "cari guidelines previously published a clinical practice guideline on the pharmacological "
                "management of adpkd."
            ),
            "",
        ),
    ),
    "nJeNmL": (
        ("fig. 1.1. framework for reducing postpartum haemorrhage (pph) morbidity and mortality", ""),
        (
            (
                "see latex balloon–loaded nelson catheter intrauterine tamponade (air filled) plus stitch "
                "and standard care"
            ),
            "",
        ),
        ("see “external aortic compression for postpartum haemorrhage treatment” [evidence base table]", ""),
        ("see “uterine massage (after delivery of the placenta for 1-2 hours and empty the clots)", ""),
        ("the priority questions and outcomes that guided the evidence synthesis and decision-making for these", ""),
        ("these guidelines are intended to inform the development of relevant national and subnational health", ""),
        (
            (
                "these outcomes reflect the prioritised outcomes used in previous world health organization "
                "(who) guidelines on pph."
            ),
            "",
        ),
        ("these outcomes reflect the prioritised outcomes used in the development of the", ""),
        ("these outcomes reflect the prioritised outcomes used in the development of this recommendation", ""),
        ("these outcomes reflect the prioritized outcomes used in previous who guidelines on pph.", ""),
        ("these outcomes reflect the prioritized outcomes used in the development of the", ""),
        ("these outcomes reflect the prioritized outcomes used in the development of this recommendation", ""),
    ),
    "nV6X3n": (
        ("for further information on prescribing and fitting hearing aids. the ci task force reviewed the", ""),
        ("in 2020, a panel of 30 international specialists in the fields of otology, audiology", ""),
        (
            "in 2021 a working group from the anz hearing health collaborative adapted the global living guidelines",
            "",
        ),
        (
            (
                "the anz hearing health collaborative guidelines provide a structured, evidence-based "
                "framework to improve access"
            ),
            "",
        ),
        (
            "this area is well served by existing guidelines. the ci task force and anz hhc",
            "device programming and rehabilitation",
        ),
    ),
    "nV6zvn": (
        (
            "a consensus-based approach was adopted to move from evidence to recommendations, with informal",
            "how were values and preferences of patients incorporated?",
        ),
        (
            (
                "how this guideline was created: an international panel including four patients living with "
                "chronic back pain"
            ),
            "",
        ),
        ("how were people with lived experience involved?", ""),
        (
            "standards, methods, and processes for trustworthy guidance",
            "what research did the guideline panel request and review?",
        ),
    ),
    "nYYb4n": (
        ("author: dr lisa mackenzie", ""),
        ("author: professor ian olver", ""),
        ("author: professor jane phillips rn, phd", ""),
        ("author: professor liz ward", ""),
        ("author: professor sabe sabesan", ""),
        ("co-authors: a/prof eva segelov; cosa teleoncology guidelines working group", ""),
        ("co-authors: belinda morris; cosa teleoncology guidelines working group", ""),
        ("co-authors: clare burns; laurelie wall; cosa teleoncology guidelines working group", ""),
        ("co-authors: dr christopher steer; cosa teleoncology guidelines working group", ""),
        ("co-authors: dr david wyld; cosa teleoncology guidelines working group", ""),
        ("co-authors: dr rob zielinski; professor sabe sabesan; cosa teleoncology guidelines working group", ""),
        ("co-authors: fiona jonker; cosa teleoncology guidelines working group", ""),
        ("co-authors: leisa brown; cosa teleoncology guidelines working group", ""),
        (
            (
                "co-authors: professor ian olver; a/prof michael poulsen; dr sean brennan; cosa teleoncology "
                "guidelines working group"
            ),
            "",
        ),
        ("co-authors: professor jane phillips rn phd; cosa teleoncology guidelines working group", ""),
        ("co-authors: professor liz ward; professor sabe sabesan; cosa teleoncology guidelines working group", ""),
        ("co-authors: professor sabe sabesan; cosa teleoncology guidelines working group", ""),
        ("figure 1: australasian teletrial model", ""),
        ("figure one: queensland remote chemotherapy supervision model", ""),
        ("most of these aspects of telehealth have also been covered by articles published", ""),
        ("some of the content of this section is extracted from the tripartite national", ""),
    ),
    "nYvlZE": (
        ("a summary list of the recommendations is presented in the executive summary of this guideline", ""),
        ("all findings from the received doi statements were managed in accordance with the who doi", ""),
        ("following the final gdg meeting, an independent consultant and the responsible technical officer from", ""),
        ("if the participants were unable to reach a consensus, the disputed recommendation, or any other", ""),
        ("in accordance with the who handbook for guideline development, all gdg, twg and erg", ""),
        (
            "in summary, this scoping and consultation process led to the identification of priority questions",
            "2.10 quality assessment and grading of the evidence",
        ),
        ("studies identified for qualitative reviews were subjected to a simple quality appraisal system using", ""),
        ("the assessment of the quality of individual studies included in cochrane reviews follows a specific", ""),
        ("the corresponding grade tables for the recommendations are referred to in this section as", ""),
        ("the gdg meetings were guided by the following protocol: the meetings were designed to allow", ""),
        ("this annex refers only to implementation considerations for the new recommendations.", ""),
        (
            (
                "this document represents who’s normative support for using evidence-informed policies and "
                "practices in all countries."
            ),
            "based on these initial steps, the who steering group developed a framework for discussion",
        ),
    ),
    "noPKwE": (
        ("each ebr was assigned a grade by the expert working group, taking into account", ""),
        ("for details about this systematic review, please see the technical report", ""),
        ("no systematic reviews on this topic were undertaken in the development of this section.", ""),
        ("no systematic reviews were undertaken for this topic. practice points were based on selected", ""),
        (
            (
                "the information on non-aspirin chemopreventive candidate agents in this chapter is "
                "primarily summarised from"
            ),
            "",
        ),
        ("the lifestyle and dietary guidance in this chapter is primarily summarised from these reviews", ""),
        ("the search strategy, inclusion and exclusion criteria, and quality assessment are described in detail", ""),
        ("this chapter focuses on primary prevention, and summarises advances in the knowledge and application", ""),
        (
            (
                "this guideline includes evidence-based recommendations (ebr), consensus-based "
                "recommendations (cbr) and practice points (pp) as defined"
            ),
            "",
        ),
        ("this is a summary of the recommendations in these guidelines, numbered according to chapter", ""),
        (
            (
                "updated systematic reviews are currently in progress by world cancer research fund/american "
                "institute for"
            ),
            "",
        ),
        ("ⁱthese guidelines may be updated after 2017 as a result of updated guidance", ""),
    ),
    "noPQkE": (
        ("annan ra, aduku lne, agyapong naf. qualitative systematic review assessing the values and preferences", ""),
        ("huda t, hoque me, chowdhury mak, jahan nkj, aitken t, dibley mj. costs, cost-effectiveness", ""),
        ("papadopoulou e, lim yc, chin wy, dwan k, munabi-babigumira s, lewin s. lay health workers", ""),
        ("potani i, hanjahanja-phiri t, selemani a, mpinda i, mamani-mategula e, chibwana a et al.", ""),
        ("the definition of infants at risk of poor growth and development for the purpose", ""),
        ("the release of this new guideline is a milestone in the fight against wasting and", ""),
        ("this section of the guideline – new and updated recommendations and good practice statements", ""),
        (
            (
                "who applied rigorous and high-quality methods for evidence synthesis and guideline "
                "development, which have advanced"
            ),
            "",
        ),
        (
            (
                "who guideline on the dairy protein content in ready-to-use therapeutic foods for treatment "
                "of uncomplicated"
            ),
            "",
        ),
        (
            (
                "who guideline: assessing and managing children at primary health-care facilities to prevent "
                "overweight and obesity"
            ),
            "",
        ),
        ("who guideline: updates on the management of severe acute malnutrition in infants and children, 2013", ""),
    ),
    "noVdWL": (
        ("the guideline document was subsequently reviewed by all mwg members and modified until a consensus", ""),
        (
            (
                "this guideline document was commissioned by the european stroke organisation (eso). a "
                "multi-disciplinary module"
            ),
            "",
        ),
    ),
    "noaRMj": (
        ("credit: will stahl-timmins, bmj.", ""),
        ("figure 1: summary of baseline risks for key cardiovascular and kidney outcomes across risk strata.", ""),
        (
            (
                "guidance was drafted by the methods co-chair with direct input and contributions from "
                "clinical co-chairs,"
            ),
            "",
        ),
        ("how were patients involved?", ""),
        (
            ("panel meetings were subsequently facilitated by methods and clinical co-chairs, and were conducted on 5"),
            "",
        ),
        (
            "the focus of subsequent iterations of the living guideline will be guided by emerging",
            (
                "clinical decision-making has long centred around optimizing glycemic control and reductions "
                "in hba1c readings."
            ),
        ),
        ("the linked systematic review and network meta-analysis was updated as of july 2024 to include", ""),
        ("the panel included two patient partners with diabetes. despite intensive efforts to recruit more", ""),
        ("this is the first version of the living guideline. the guideline is part of the", ""),
        (
            (
                "this living bmj rapid recommendation was developed in accordance with standards for "
                "trustworthy guidance"
            ),
            "what research did the guideline panel request and review?",
        ),
        (
            "this living guideline complies with standards for trustworthy guidelines and commits to a living model",
            "",
        ),
        (
            (
                "to visualize the benefits and harms for the alternative therapeutics, we provide an "
                "interactive decision"
            ),
            "",
        ),
        (
            "what triggered this guideline and what is coming next?",
            "the focus of subsequent iterations of the living guideline will be guided by emerging",
        ),
        ("when will the guideline be updated?", "how were patients involved?"),
    ),
    "ny70vj": (
        ("1. contact information", ""),
        ("for any questions or additional information, please contact:", ""),
        ("practice guidelines on the surgical management of acute diverticulitis", ""),
        ("the guidelines subcommittee of the european association for endoscopic surgery", ""),
        ("this rapid guideline aims to provide recommendations on the surgical management", ""),
        ("the development of this document complied with the reporting checklist for public versions of", ""),
        ("a protocol was developed a priori by the steering group [guideline protocol. eaes rapid guideline:", ""),
        ("funding: this guideline was funded by the european association for endoscopic surgery & the european", ""),
        ("given that seven trials have been conducted in this field in the last few decades,", ""),
        (
            (
                "the guideline methodologist and trainee methodologists have developed a comprehensive "
                "literature search strategy with the"
            ),
            "individual patient data analysis",
        ),
        (
            "the steering group consisted of two general surgeons who either perform (saa) or have vast",
            "health question",
        ),
        ("this guideline is planned to be updated within 2031, unless substantial new evidence will be", ""),
        (
            (
                "this work is intended to assist gastrointestinal, endoscopic, and general surgeons, "
                "gastroenterologists, interventional radiologists, other"
            ),
            "",
        ),
        ("use of the guideline will be monitored through engagement with eaes members through an online", ""),
    ),
    "ny74yj": (
        ("high-quality health care is essential for the prevention of morbidity and mortality in pregnancy", ""),
        ("the world health organization (who) envisions a world where “every pregnant woman and newborn", ""),
        ("this section presents the two updated recommendations on the timing of induction of labour", ""),
        ("• apgar score less than 7 at 5 minutes • admission to a neonatal", ""),
        ("in 2019, the executive gsg for the who mph recommendations prioritized updating the existing", ""),
        (
            (
                "in the context of humanitarian emergencies, the adaptation of recommendations should "
                "consider integration"
            ),
            "",
        ),
        ("murano m, chou d, costa do nascimento ml, turner t. using the who-integrate evidence", ""),
        ("national and subnational subgroups may be established to adapt and implement these recommendations", ""),
        ("note: the etd table – which summarizes the balance between the desirable and undesirable", ""),
        (
            "the dissemination and implementation of these recommendations are to be considered by all stakeholders",
            "",
        ),
        (
            "the gdg acknowledges that there is planned or ongoing research relevant to some of",
            "annex 2. priority outcomes used in decision-making",
        ),
        ("the primary audience also includes managers of maternal and child health programmes, and relevant", ""),
        (
            (
                "the primary audience includes health professionals who are responsible for developing "
                "national and local"
            ),
            "",
        ),
        (
            (
                "the successful introduction of evidencebased policies (relating to the updated "
                "recommendations) depends on"
            ),
            "",
        ),
        (
            (
                "the successful introduction of these recommendations into national programmes and health "
                "services depends"
            ),
            "",
        ),
        ("these recommendations will also be of interest to pregnant women, as well as members", ""),
        ("these updated recommendations were developed in accordance with the standards and procedures in the", ""),
        ("who has established a new process for prioritizing and updating maternal and perinatal health", ""),
    ),
    "ny76yj": (
        (
            (
                "in the context of humanitarian emergencies, the adaptation of the current recommendation "
                "should consider"
            ),
            "",
        ),
        (
            ("national and subnational subgroups may be established to adapt and implement this recommendation based"),
            "",
        ),
        (
            (
                "the successful introduction of evidence-based policies (relating to updated "
                "recommendations) depends on well-planned and"
            ),
            "",
        ),
        ("1.2 rationale and objectives", "1.4 scope of the recommendation"),
        ("the dissemination and implementation of this recommendation are to be considered by all stakeholders", ""),
        (
            ("the following recommendation was adopted by the gdg. evidence on the effectiveness of this intervention"),
            "",
        ),
        ("the following section outlines the recommendation and the corresponding narrative summary of", ""),
        ("these outcomes reflect the prioritized outcomes used in the development of this recommendation, in", ""),
        (
            (
                "to ensure that the recommendation is correctly understood and appropriately implemented in "
                "practice, additional"
            ),
            "",
        ),
    ),
    "nyO1Yj": (
        ("abbreviation: ad, alzheimer's disease; als, amyotrophic lateral sclerosis; bbm, blood-based biomarker", ""),
        ("input from the association national early-stage advisory group (esag) made up of patients", ""),
        (
            "lastly, several studies have been published since our latest literature search update in november 2024",
            "",
        ),
        (
            (
                "this guideline has been informed by a corresponding systematic review of diagnostic test "
                "accuracy, published separately"
            ),
            "",
        ),
        (
            (
                "to address this gap, the alzheimer’s association has convened a panel of clinical and "
                "subject-matter experts"
            ),
            "",
        ),
        (
            (
                "to enhance the clinical relevance and applicability of the guideline, the panel was "
                "intentionally multidisciplinary"
            ),
            "",
        ),
        (
            "to meet the practical needs of clinical users implementing the recommendations found in this guideline",
            "",
        ),
    ),
    "nyONYj": (
        ("6th edition, january 2025", "malaria continues to be the leading cause of morbidity and mortality"),
        ("for detailed operational guidance, refer to the malaria case management", ""),
    ),
    "nyX5xL": (
        ("a systematic review was conducted to evaluate various outcomes for the development of cga guidelines", ""),
        ("below is a proposed approach for the setting of general and visceral surgery in the form", ""),
        ("the assessment of the 7 grade criteria, which were considered when formulating the statement, can", ""),
        ("the determination of the recommendation grade according to grade takes into account not only the", ""),
        ("the determination of the recommendation grade based on grade considers not only the reliability of", ""),
        ("the flowchart outlines the steps for conducting a comprehensive geriatric assessment (cga) based on", ""),
        ("the following is the process for the oncology setting as a screening cga-algorithm using a flowchart", ""),
        (
            (
                "the following process for the emergency department is presented as a screening "
                "cga-algorithm using a flowchart"
            ),
            "",
        ),
        (
            (
                "the following process for the orthogeriatric setting is presented as a "
                "screening-cga-algorithm using a flowchart"
            ),
            "",
        ),
        (
            "the grade criteria were considered in formulating this statement. the evaluation of these criteria can",
            "",
        ),
        ("the grade evidence profile assessment for this recommendation in the acute geriatrics setting can be", ""),
        (
            (
                "the guidelines for cga were developed using a systematic literature review and evaluating "
                "various outcomes"
            ),
            "",
        ),
        ("the proceeding for the five settings is shown below as a screening cga algorithm", ""),
        ("therapy toxicity is discussed in detail in the background text for recommendation 2", ""),
        ("when formulating the statement, the 7 criteria of grade were considered, which are", ""),
        ("when formulating this consensus-based recommendation, the 7 criteria of grade were also considered", ""),
        ("when formulating this statement, the 7 criteria of grade were also considered. the evaluation of", ""),
    ),
    "nyXKVL": (
        ("2.3 integration of recommendations from published who guidelines", "2.4 focus and approach"),
        ("2.5 evidence identification and retrieval", "2.6 quality assessment and grading of the evidence"),
        ("2.8 decision-making during the gdg meetings", "3. evidence and recommendations"),
        (
            (
                "during the formulation of recommendations, the gdg identified important research gaps. "
                "where the certainty"
            ),
            "",
        ),
        ("the corresponding grade tables for the recommendations are referred to in this section as", ""),
        (
            "this document was developed using the standard operating procedures described in the who handbook",
            "2.2 identifying priority questions and outcomes",
        ),
    ),
    "nyXP0L": (
        (
            (
                "(v) planning for the dissemination, implementation, impact evaluation and updating of the "
                "recommendations."
            ),
            "",
        ),
        ("for this recommendation update, trials were organized by drug class in order to assess the", ""),
        (
            (
                "the following section outlines the recommendations and the corresponding narrative summary "
                "of evidence for the"
            ),
            "",
        ),
        (
            (
                "the primary audience of these recommendations includes healthcare providers who are "
                "responsible for developing national"
            ),
            "",
        ),
        (
            (
                "the primary audience of these recommendations includes those who are responsible for "
                "developing national and"
            ),
            "scope of the recommendations",
        ),
    ),
    "nyXxZL": (
        ("a moncrieff g, finlayson k, cordey s, mccrimmon r, harris c, et al. first and", ""),
        (
            (
                "in march 2021, a who-convened guideline development group (gdg) re-evaluated evidence on "
                "imaging ultrasound before"
            ),
            "",
        ),
        (
            "this section provides the evidence summary and who recommendation. evidence on the effectiveness of an",
            "",
        ),
    ),
    "nyx0xL": (
        ("although cognitive issues have not featured as prominantly in stroke guidelines as may be expected", ""),
        ("in planning the work, we were keen that we represent all the clinical disciplines involved", ""),
        (
            "in this context the european stroke organisation (eso) commissioned a guideline, in agreement with the",
            "",
        ),
        ("the guideline followed best practice and adhered to the standard operating procedure of the eso", ""),
    ),
    "nyxpZL": (
        ("publication approval and public consultation", ""),
        ("the consortium seeks annual nhmrc approval of the living guideline under section 14a of the", ""),
    ),
    "ojmKvn": (
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        (
            (
                "there is an organisational indicator collected in the national stroke audit on whether "
                "patient selection"
            ),
            "",
        ),
    ),
    "pEQmQE": (
        ("potential biases in the review process in 2016, the nfog board established a guideline committee", ""),
        ("reference: the swedish medical birth register - a summary of content and quality.", ""),
        ("using the following search words: birth birthweight clinical controlled gestagen gestonorone", ""),
    ),
}
