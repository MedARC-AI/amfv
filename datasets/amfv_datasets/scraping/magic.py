"""Scrape MAGICapp guidelines into normalized markdown documents.

MAGICapp (https://app.magicapp.org) is a publishing platform for clinical
practice guidelines written with the GRADE methodology. Guideline-producing
organizations author on the platform and publish through it, so a single source
covers many institutions: the World Health Organization, Sundhedsstyrelsen
(Danish Health Authority), Nederlands Huisartsen Genootschap, the European
Stroke Organisation and others.

Unlike the other Meditron guideline sources, MAGICapp exposes a public,
unauthenticated JSON API, so no HTML listing has to be parsed to discover
documents:

* the catalogue at ``/api/v1/guidelines`` returns every published guideline
  with its metadata in one response, and
* each catalogue entry carries a ``jsonPath`` pointing at the full guideline as
  structured JSON, with nested sections and first-class recommendation objects.

Section bodies and recommendation text are HTML fragments, so they still go
through :func:`amfv_datasets.scraping.html.html_to_markdown`.

Attribution:
Meditron's guideline collection lists MAGIC as a source and ships a scraper for
it (epfLLM/meditron, gap-replay/guidelines/scrapers/scrapers.py, Apache License
2.0). That implementation drives the single-page application with Selenium and
is marked ``UNTESTED`` in the source; this module replaces the approach with
direct API access rather than porting it.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from lxml import html as lxml_html

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, html_to_markdown

BASE_URL = "https://app.magicapp.org"
API_BASE_URL = "https://api.magicapp.org"
MAGIC_DATASET_NAME = "magic-webscrape"
MAGIC_DATASET_DISPLAY_NAME = "MAGICapp Webscrape"
DOCUMENT_DELAY_SECONDS = 5.0

logger = logging.getLogger(__name__)

# The catalogue endpoint ignores `page`, `size` and `pageSize`; only `limit`
# narrows the response, and an oversized limit returns the full catalogue.
_CATALOGUE_LIMIT = 5000

# MAGICapp is multilingual: of ~460 published guidelines, 242 are English
# (en, en-gb, en-au, en-ca) and 218 are not — Dutch 100, Danish 84, French 13,
# German 8. No AMFV document states a language policy, and no other source on
# the Meditron list is multilingual, so this is a *default*, not a project rule.
# English is the default because the eval sets, the decomposer and the verifier
# are all English. Pass `languages=None` to collect the whole catalogue.
DEFAULT_LANGUAGES = ("en",)

# Front and back matter: how to read the guideline, who wrote it, how it was
# funded, what the abbreviations mean. It is not clinical content, and the same
# text recurring across many guidelines degrades retrieval.
#
# Chosen by frequency-analysing every section heading across 63 guidelines from 46
# publishing organisations, then keeping only headings whose subtree held no
# recommendation in any of them - about 8% of the readable text. `_is_skipped_section`
# re-checks that per section, so a publisher who files guidance under one of these
# headings keeps it. Headings deliberately absent because they do carry
# recommendations somewhere: Introduction, Background, Summary, Methodology.
#
# Container words are absent for a different reason. "Appendix", "annex" and
# "supplementary material" say where a section sits, not what it holds, so matching one
# drops a whole back-of-document container on no evidence about its contents.
# "appendix" was on this list until 2026-07-27 and cost real content: the American
# Dental Association guideline files a decision tree and a "Recommendation Implications"
# section under a section called "Appendix", and both were being deleted. What names
# methodology is the topic one level down, which is what `_SKIP_SECTION_TOPICS` matches.
_SKIP_SECTION_HEADINGS = frozenset(
    {
        "about this guideline",
        "applicability issues",
        "development of the guidelines",
        "glossary",
        "glossary and abbreviations",
        "how the guideline was made",
        "how this guideline was created",
        "how this guideline was made",
        "how to use these recommendations/understanding the recommendations",
        "methodology",
        "methods",
        "methods: how this guideline was created",
        "patient version",
        "research implication",
        "research implications",
        "what's new?",
        "abbreviations",
        "abbreviations and acronyms",
        "about the guidelines",
        # "abstract" is deliberately absent, for the same reason as "executive summary"
        # below. It reads like front matter and is not: a guideline's abstract states the
        # question, the population and what the evidence showed. Measured over all 26 in
        # the English catalogue, **only 3.3% of their sentences appear anywhere else in
        # their own rendered document**, and all 26 repeat less than half of themselves -
        # so the redundancy argument that put it here fails outright. What was being
        # deleted includes the European Stroke Organisation TIA guideline's definition of
        # high-risk TIA and its only statement that the recommendations apply to adults,
        # "Intracerebral hemorrhage accounted for 9% to 27% of all strokes worldwide", and
        # a lipid guideline's whole PICO question with its 70 mg/dl threshold.
        "acknowledgements",
        "acknowledgments",
        "acronyms and abbreviations",
        # The journal byline: "Guillaume Turc 1, Georgios Tsivgoulis 2,3, Heinrich J.
        # Audebert 4..." with affiliation numbers. 21 sections, 32,901 characters, no
        # recommendation among them. Exact names only - the substring "author" would take
        # "Authors' conclusions", which is not a byline at all: pEQmQE's says "Vaginal
        # progesterone appears to be beneficial in women with PPTB or short cervix for
        # preventing preterm birth before week 33-35". Naming each variant is the price of
        # not deleting that. 18 further byline sections were found under these four names
        # after "authors" alone was added, which had removed only about half the category.
        "author group",
        "authors",
        "authors and disclosures",
        "authorship",
        "authorship and contributions",
        "authorship, contributions and acknowledgments",
        "additional resources",
        "companion resources",
        "other resources",
        # "conclusion" is deliberately absent, for the same reason as "discussion". Read
        # end to end, 7 of the 19 sections it dropped carried a sentence a verdict could
        # rest on and the other 12 were sign-off prose, with nothing in the heading to
        # separate them: Lkk3pL's says active surveillance "will offer equivalent survival
        # rates to immediate CLND", jNxJmn's carries a weak recommendation for EVT without
        # alteplase, and jlAbxL's whole "Discuss, Recommend and Refer" instruction to
        # clinicians exists nowhere else in the corpus. The entry was only 7,800 characters
        # in total, so keeping the sign-off prose costs almost nothing.
        # The BMJ programme blurb under the one wording the `methods and processes` topic
        # cannot reach - "process" against the topic's "processes". Same text as the
        # entries that topic already drops: "About BMJ Rapid Recommendations. Translating
        # research to clinical practice is challenging", near-identical across the six
        # guidelines carrying it.
        "bmj rapid recommendations methods and process",
        "conflicts of interest",
        "contact information",
        "contact the guideline team",
        "copyright and disclaimer",
        # The plural the `declaration of interest` topic misses, which is the failure this
        # project keeps meeting: a topic word matches as a substring, and "declarations of
        # interest" does not contain "declaration of interest".
        "declarations of interest",
        # "discussion" was here and is deliberately gone. A Discussion section in a
        # guideline does what the word means in a journal article - it is where the panel
        # walks back through what the evidence did and did not show - so it is where the
        # trial names, thresholds and effect estimates live. Reading all 73 in the
        # catalogue found 30 of the 32 checked carry a statement a verdict could rest on:
        # "intensive blood pressure lowering to a systolic of 120 mmHg ... reduced
        # incidence of mild cognitive decline", "risk ratio 0.88 CI95 0.80-0.96", "we did
        # not identify any relevant RCTs for any of the questions".
        #
        # It was on the list because the audit that put it there asked whether any
        # matching section held a recommendation *object* - none of the 73 does, which is
        # true and useless, the same way it was for executive summaries. The word cannot
        # be narrowed either: n3QxOj has "Health system implications and discussion" full
        # of real content and "Discussions" full of boilerplate, in one document.
        # "evidence gaps" is deliberately absent. It names the one thing the heading
        # promises: what the evidence does NOT show. All three in the catalogue say so
        # outright - "There was a lack of available evidence on who should be referred
        # for a full audiological evaluation", "the paucity of literature in key areas",
        # "Additional evidence is required to inform appropriate HCC surveillance
        # recommendations for people with MAFLD". A finding of no evidence is a finding,
        # and it is the sentence a verdict rests on when a claim overstates the support.
        "ethical approval",
        "funding",
        # The same statement as a section rather than the bare word: "This project was
        # funded by the European Association for Endoscopic Surgery. The funding body had
        # no influence on the development of this guideline." 12 guidelines.
        "funding statement",
        "guatantor",
        "informed consent",
        # A journal submission field, 14 sections averaging 45 characters: "A specific
        # guarantor does not exist. The working group has jointly developed the manuscript."
        # Plural included: "a plural defeats a topic word" has bitten this project twice
        # before, and three sections here are headed "Guarantors".
        "guarantor",
        "guarantors",
        "guideline amendments",
        # Links to the same guideline in other languages, which the catalogue lists
        # separately and this scraper does not follow. An exact name and not a topic
        # word on purpose: "translation" also names real content, and the only other
        # section in the catalogue containing it is 19,291 characters of
        # "Knowledge translation for self-care interventions" (Lr21gL).
        "guideline translations",
        # Where the PDF lives and how to navigate the platform, never what the guideline
        # says. "how to use this guideline" was already here and stays: the sections that
        # survive it are the ones holding the recommendation-label key, rescued on their
        # content by `_defines_recommendation_strength`.
        "how to access and use the guideline",
        "how to access and use this guideline",
        "how to cite",
        "how to use these guidelines",
        "how to use this guideline",
        "ongoing feedback",
        # Which committee endorsed which funding criteria, and when. Two guidelines.
        "process report",
        # That a draft was circulated for comment, and to whom.
        "public consultation",
        # How the panel was assembled, how the reviews were commissioned, how topics were
        # prioritized, and when each step happened. 28 sections, 550,524 characters, and the
        # content guards fire on only two of them.
        #
        # The two parent headings - "Guideline development process" and "... and methods" -
        # are deliberately NOT here, though they are the largest part of the family at
        # 505,448 characters. A reader audit of all 59 of them found 20 carrying a checkable
        # clinical statement: disease burden, a risk factor, a threshold, a finding that no
        # studies were found. "Head and neck cancer is the fifth most common cancer
        # worldwide. Tobacco and alcohol account for up to 80% of all cases, and malnutrition
        # rates are reported between 30-50%" opens one of them, and no content guard detects
        # that shape. Only the children that are purely method are listed.
        "guideline development methods",
        "evidence retrieval, synthesis, and assessment",
        "evidence retrieval, synthesis and assessment",
        "formulating questions and selecting outcomes",
        "evidence for the guideline",
        "evidence for the guidelines",
        "timeline of guideline development activities",
        "declarations and management of interests",
        # The same paperwork under four more wordings the exact-name list did not carry.
        # noPQkE files a per-question table of who declared what as Annex 2, 8,059
        # characters of it. Four sections, 8,871 characters, no content guard on any.
        "declarations of interest for each guideline question",
        "managing declarations of interest",
        "declarations of interest register",
        "declaration of interest register",
        "interest-holders",
        "interest holders",
        # The platform explaining itself: "This guideline has been optimised for use in
        # MAGICapp. We therefore recommend using MAGICapp, where you can also generate a PDF."
        # Three sections, 2,692 characters. Edr04L's "Introduction (including guidance for use
        # of MAGICApp)" is deliberately absent - it is 9,070 characters of actual
        # introduction, opening with who the guidelines are designed to be used by, and the
        # platform note is appended to it.
        "magicapp",
        "magicapp tabs",
        "using magicapp",
        # The guideline's own project history and governance, filed as an appendix: how the
        # NHMRC commissioned it, who sat on which committee, and what each meeting decided.
        # One section, 43,900 characters, no content guard.
        # Two sections that were being removed by accident before headings were protected
        # from the removal passes: their heading text is itself a paperwork label, so the
        # rule that takes "Suggested citation" from a body was taking it from the heading
        # too. Named here so they are dropped on purpose instead.
        "suggested citation",
        "dissemination",
        "how the guidelines were made",
        "how the guidelines were developed",
        "how the guideline was developed",
        "methods: how this guideline was made",
        # The heading publishers put at the end over whatever is left: acknowledgements,
        # abbreviation lists, the panel roster, an address to write to. 5 sections, 13,040
        # characters. "Additional information" and "Further information" are the same
        # heading under different wording; the phrase also opens pointer sentences mid-body,
        # which `_POINTER_ONLY_RE` handles separately and this does not affect.
        "other information",
        "additional information",
        "further information",
        "administrative report",
        "working committee",
        "working committees",
        # What the panel thinks should be studied or written next. Two sections, 2,759
        # characters, neither carrying a recommendation, scope statement or finding.
        "future developments",
        "future development",
        "key areas for future development",
        # ERx1yL's directory of services for young people with cancer, written as numbered
        # headings with a description paragraph between every pair of bullets - the shape
        # `_drop_resource_directories` deliberately leaves alone, because removing a label
        # and one list there would strand the remaining entries. Taking the section by name
        # removes it whole. The near variants - additional, other, companion, supporting -
        # were already on this list before today.
        "resources",
        # How the guideline is kept up to date and what changed between versions. Two
        # sections, 14,943 characters, no content guard on either.
        "amendments to the guidelines",
        "amendments to the guideline",
        "define section headings and amendments to 2010 guidelines",
        # A table of external resources to consult, with the publisher's own note that they
        # "may be updated on a regular basis". Named exactly rather than by "summary" plus
        # "resources", because the only other heading carrying both words is j1QPrj's "Annex
        # 10: Summary of harms, benefits and resource requirements", and that opens with the
        # certainty legend - "High certainty (benefit), Moderate certainty (probable
        # benefit)" - which is the key a reader needs to interpret the rest.
        "summary table of relevant infection prevention and control resources",
        # A publishing programme explaining itself: "Translating research to clinical practice
        # is challenging... BMJ Rapid Recommendations aims to create trustworthy clinical
        # practice recommendations in record time." 8 sections, 149,400 characters.
        #
        # Keyed on the programme name, NOT on "background and methods", and the difference is
        # the whole point. Ten sections in the corpus carry that wording; the two that do not
        # name a programme are disease background and must be kept - 8nyb0E opens "Chronic
        # non-cancer pain comprises any painful condition that persists for three months or
        # longer... 15-19% of Canadian adults experience chronic non-cancer pain", which is a
        # definition with a threshold and a prevalence, and ERWQ1j's is the same shape for
        # COVID-19 and organ transplantation. No content guard detects either.
        "bmj rapid recommendations: background and methods",
        "background and methods: bmj rapid recommendations",
        "background and methods for bmj-rapidrecs",
        "primary care rapid recommendations: background and methods",
        "background and methods of the wikirecs project",
        # A download index, not an appendix of content: every entry is a PICO title over a
        # bullet reading "Baseline search (March 2021): Initial evidence synthesis" linking a
        # Google Drive PDF the scraper never fetched. Named exactly rather than by the word
        # "appendix", because Jn37kn files 60,826 characters of real guidance under "APPENDIX
        # 2: Supplementary information" - cleaning frequencies, standard precautions, and the
        # duration of precautions for specific infections.
        "figures, tables and supplementary information",
        # A link to the PDF export and the platform's warning about it - "any PDFs downloaded
        # from MAGICapp are auto-generated and have not been formatted or checked for
        # accuracy by WHO". One section, 254 characters, and the scraper never fetched the
        # PDF anyway.
        "pdf of the guideline",
        "pdf of the guidelines",
        # The journal's indexing terms, filed as a section of its own by one publisher.
        "keyword",
        "keywords",
        # A journal's structured front matter - "Systematic review", "Interpretation",
        # "Future directions" - describing what the association did and plans to do next.
        # One section, and no content guard fires on it.
        "research in context",
        "applicability issue",
        "dissemination and implementation of the guideline",
        "dissemination and implementation of the guidelines",
        # Who has a competing interest, and what the publisher will not be held liable for.
        # 16 sections across 16 guidelines and not one carries a recommendation, a scope
        # statement or a strength key. `declarations of interest` already covers the third
        # wording of the same thing.
        "disclosure",
        "disclosures",
        "disclosure of interest",
        "disclosure of interests",
        "disclaimer",
        "disclaimers",
        # The same search-method family as `search strategies`, written by publishers who
        # file it as a section of its own rather than a heading inside a body. Both are short
        # and describe only where the team looked: which clearinghouses were scanned, and
        # that a few articles could not be retrieved.
        "search for existing relevant guidelines and systematic reviews",
        "limitations of searches",
        # How the panel was assembled, how the evidence was searched and how the grades were
        # assigned - the same chapter `methods` and `methodology` already name, under a third
        # wording. Three sections, 19,554 characters, and no content guard fires on any of
        # them: no recommendation, no evidence table, no strength key, no scope statement.
        "guideline development methodology",
        # The same thing under the Australian Postnatal Care Guidelines' own wording. Named
        # separately because `_skip_lookup_key` strips "Appendix 2." and leaves the full
        # phrase, which is not an exact match for the entry above.
        "public consultation feedback",
        # "Evidence reports provide detailed information on the clinical questions, search
        # methods and evidence underpinning recommendations in these Guidelines", followed
        # by a list of the chapter names those reports cover. A contents page for documents
        # the scraper never fetched.
        "evidence reports",
        # A list of chapter titles the publisher has not written yet: "These guidelines are
        # regularly being updated and expanded. The following topics are currently under
        # development." Headings with no content behind them by definition.
        "topics under development",
        # "plain language summary" is deliberately absent, on the same measurement as
        # "abstract" and "executive summary": only 2.0% of the sentences in the 19 in this
        # catalogue appear anywhere else in their own document, and all 19 repeat less than
        # half of themselves. Simplified wording is not the same as duplicated content, and
        # these carry the guideline's own claims in its own voice - "about 2,800 Australians
        # are diagnosed with liver cancer, and around 2,400 die from liver cancer", "1 in
        # 200 users of blood thinners have a bleeding in the brain every year", "high
        # quality of evidence and strong recommendation for MT within 6 hours after stroke
        # symptom onset and a moderate QoE up to 24 h".
        "reading guide",
        # A bibliography under the one wording the exact name "references" cannot match.
        "reference list",
        "references",
        # The plural the `search strategy` topic misses. Raw query syntax: "CENTRAL
        # pre-dialy* or predialy*:ti,ab,kw in Clinical Trials".
        # The version banner and changelog at the top of a living guideline: "Version 10.1,
        # published 3 July 2026", which topics changed last month, and an invitation to
        # submit feedback. 3 sections, 15,653 characters.
        "what's new",
        "version history",
        # Scope statements, dropped 2026-08-06 on Evan's call - "everything above Postnatal
        # assessment and support should be removed". This reverses the 2026-07-27 decision
        # that took them OFF the list, and it is the one deletion in this list that the
        # North Star names as non-negotiable: a fact can be true for adults and false for
        # children, and this is the sentence that says which. jW0ZbL loses "The Guidelines
        # do not include: care for babies needing higher levels of care, such as preterm or
        # low birthweight babies." `_states_guideline_scope` still rescues a section that
        # states scope in its own body text, so what goes here is the dedicated front-matter
        # block, not every scope sentence in the corpus.
        "scope and audience",
        "scope and purpose",
        "scope and purpose of the guidelines",
        "objective of the guidelines",
        "search strategies",
        "supporting guideline reports",
        # When the guideline will next be looked at, and by whom. Every spelling observed
        # is listed, because these are exact names and one plural escaping is how the
        # categories above leaked in the first place. 44 sections across 13 guidelines.
        "update and further research",
        "updating of the guideline",
        "updating of the guidelines",
        "updating the guideline",
        "updating the guidelines",
        "updating the recommendation",
        "updating the recommendations",
        # "target audience" is deliberately absent. The name promises a list of who should
        # read the guideline, and two of its nine sections are exactly that - but the other
        # seven also state what the guideline covers or where it applies, which is the same
        # category that took `scope and audience` off this list. Lr21gL's is lost without
        # trace: WHO's statement that its self-care guidance "is relevant for all settings
        # and should, therefore, be considered as global guidance" survives nowhere else.
        # nyO1Yj is the case that decides it - the guideline whose recommendations carry
        # blood-biomarker sensitivity thresholds says here, and only here, that those tests
        # are not intended for primary care. A threshold and the boundary limiting it were
        # being deleted from the same document.
    }
)

# Organisations whose entire published output on MAGICapp is training material:
# tutorial copies of real guidelines, workshop exercises and sample documents.
# Verified as all-training across the catalogue rather than guessed from a name.
# Matching on the guideline title instead would be unsafe - 'test' and 'workshop'
# appear inside legitimate titles such as "Colorectal cancer screening with faecal
# immunochemical testing".
_TRAINING_INSTITUTIONS = frozenset(
    {
        "gela workshop malawi",
        "ges 2024 workshop guidelines",
        "magicapp tutorials",
        "magicapp workshops mapp",
    }
)

# Guidelines shorter than this are placeholders rather than guidance, e.g. one
# reading only "Evidence profiles for beta-blockers for hypertension, not yet
# publicly available" - 116 characters that would otherwise enter the corpus as a
# document about beta-blockers. Applied only to catalogue runs: scraping an
# explicit URL returns whatever that guideline has.
MIN_CONTENT_CHARS = 400

# A guideline whose own text says it is a demonstration rather than guidance. Keyed on
# the publisher's own words, the same way `GuidelineRef.is_archived` is - a document is
# dropped whole only on a fact a reader can go and check, never on our reading of its
# advice (see the recorded decision in the vault).
#
# The Norwegian Society on Thrombosis and Haemostasis publishes "Demo guideline with
# decision aid for aspirin in primary prevention" (DjxqOL), which opens: "This is a
# demonstration guideline NOT intended for the actual treatment of patients. It is a
# translation of the current Norwegian guideline ... but the translation is not
# authorized or proof-read and may contain errors." It then emits a WEAK recommendation
# to give "75 mg aspirin daily to persons with high cardiovascular risk" - a dose, in a
# document that disclaims clinical use and admits it may be mistranslated.
#
# Nothing here matches the title: "test" and "demo" appear inside real titles, and the
# catalogue's own `status` field cannot help either - it is NOTSET or absent on this
# document, and the guidelines marked DEV include published BMJ Rapid Recommendations.
# Checked over all 216 rendered English guidelines: both phrasings match this one
# document and nothing else.
_DEMONSTRATION_RE = re.compile(
    r"demonstration guideline|not intended for (?:the )?(?:actual )?treatment of patients",
    re.IGNORECASE,
)

# A guideline the publisher's own title marks as not yet final. Same bar as
# `_DEMONSTRATION_RE` and `GuidelineRef.is_archived`: a document is dropped whole only on
# a fact a reader can go and check. Monash publishes j97pAn as "DRAFT FOR PUBLIC
# CONSULTATION: Dementia Clinical Practice Guidelines and Principles of Care" - real
# guidance from a real panel that is still consulting its readers on it.
#
# Measured over the whole 460-entry catalogue: exactly one title matches. The word is
# boundary-matched because substrings are the trap here - "concept", a draft marker in
# other contexts, sits inside "Preconception" the same way "test" sits inside "faecal
# immunochemical testing". The catalogue `status` field is deliberately not consulted:
# the 8 guidelines marked DEV include finished, published BMJ Rapid Recommendations
# (jboXZL), so status describes the authoring workspace, not the content. English
# wording only - a publisher marking a draft in another language is not caught.
_DRAFT_TITLE_RE = re.compile(r"\bdrafts?\b", re.IGNORECASE)

# Decorations stripped from a heading before the skip-list lookup - see
# `_skip_lookup_key`. The label pattern covers "Appendix A.", "App E -", "Annex 6:"
# and bare section numbers such as "7.2"; it is anchored, so it can only ever remove
# a prefix.
_EMPHASIS_RE = re.compile(r"[*_]+")
_HEADING_LABEL_RE = re.compile(
    r"\A(?:(?:appendix|appendices|annex(?:es)?|app|attachment|supplement(?:ary)?)\b\.?\s*)?"
    r"(?:"
    r"[a-z]\s*[.\-–—:)]"  # a letter only when punctuation follows it: "A.", "E -"
    r"|\d+(?:\.\d+)*\s*[.\-–—:)]?"  # a number, where the punctuation is optional: "7.2", "6."
    r")?"
    r"\s*"
)

# Administrative and methodology topics, matched as substrings of the normalized
# heading. These name the sections that sit inside an appendix - the guideline's own
# development process, its working party, its conflict-of-interest register - which no
# claim can be verified against. Kept narrow on purpose: each was observed in the
# catalogue, and a substring that could plausibly appear in a clinical heading is left
# out. Both guards in `_is_skipped_section` still apply on top of this.
_SKIP_SECTION_TOPICS = frozenset(
    {
        # A stem rather than a word: publishers word these sections a dozen ways -
        # "Acknowledgement", "Publication and acknowledgments", "Authorship, contributions
        # and acknowledgements" - and exact names cannot keep up. All 109 headings containing
        # the stem across the English catalogue are administrative, and none holds a
        # recommendation (verified before adding).
        "acknowledg",
        # WHO's roster annex, which every one of its guidelines carries: "Annex 1. External
        # experts and WHO staff involved in the preparation of the recommendation" - name,
        # job title, department, city, thirty times over. 32 of these are in the English
        # catalogue holding 193,791 characters, none with a recommendation and not one
        # clinical sentence among them (four read end to end before adding this, the
        # largest and the smallest included). It is a naming near-miss rather than a new
        # category: the `contributor` topic already drops the one guideline that happens to
        # call its version "Annex 1. Guideline contributors: external experts and WHO
        # staff", and the sibling "Annex 3. Summary and management of declared interests"
        # is dropped too. Keyed on the phrase rather than "who staff" so it also reaches
        # the variant spelled "World Health Organization staff involved in...".
        "involved in the preparation",
        # "executive summary" is deliberately absent, and the reason is worth keeping.
        # On two guidelines it is a pure restatement of recommendations made in full
        # elsewhere, which argued for dropping it. Measured across all 62 English
        # guidelines that have one, that is the exception: 54 of 62 repeat less than half
        # of their summary anywhere else, and the Monash MDMA-assisted-psychotherapy
        # guideline (Ee438n) states all four of its recommendations and all its good
        # practice statements *only* there, as prose rather than recommendation objects -
        # so `_has_recommendation` cannot see them and the section looks droppable when
        # it is the guidance.
        # The *group*, not the process. Reading all 59 sections whose heading contained
        # the bare words "guideline development" found 20 of 58 hold something a verdict
        # could rest on, and the split is structural rather than textual: every roster,
        # declarations-of-interest annex, meeting log, timeline and voting grid was
        # clean, while the long narrative "process" write-ups leak more than half the
        # time, because they open with a background paragraph. Cancer Council's states
        # that country of birth correlates with hepatocellular carcinoma risk through
        # chronic hepatitis B; Eez3Kj's gives head and neck cancer malnutrition rates of
        # 30-50%; jlPRdj's carries a table of minimally important differences.
        #
        # The same heading text appears on both kinds - noPKwE's "Guideline development
        # process" is clean and jlAbxL's is not - so no wording can separate them, and
        # the narrow word is the only safe one. What that keeps is methodology prose,
        # which is job 3 and filterable downstream; what it stops keeping is guidance.
        "guideline development group",
        # Read before it was kept, 2026-08-05, because it was the only topic word in this
        # block carrying no justification of its own and so read as covered by the comment
        # above, which is about something else. All 3 sections are Cancer Council Australia
        # house format: an index of documents the scraper never fetches, listing each PICO
        # question's code and the file names of its attachments ("Evidence statement form
        # PPR1"). Not one answer or effect size among them, and the questions themselves
        # survive in fuller wording in the `clinical question list` sections, which are
        # kept. A question asserts nothing, so no verdict can rest on one.
        "technical report",
        # Who was on the panel and how it worked: membership lists, meeting attendance,
        # and the guideline's own development process. All observed; across the catalogue
        # none of the matching sections holds a recommendation.
        "methods and processes",
        "advisory panel",
        "technical team",
        "panel member",
        "working group",
        "steering committee",
        "meeting attendance",
        # Who wrote it. A stem, so it also covers "Contributorship" and "Guideline
        # contributors"; every matching heading across the English catalogue is an
        # authorship or membership list, none holds a recommendation, and the largest
        # is WHO's 117,757-character "Contributors and interests" chapter in LwRMXj.
        # Its subsections are headed "Recommendations for vector control",
        # "Recommendations for treatment" and so on - they are contributor lists
        # grouped by topic, not recommendations, so nothing keyed on the word
        # "recommendation" can be trusted inside this block; the parent heading is the
        # only thing that identifies it.
        "contributor",
        # The plural defeats "guideline development" above, because the substring match
        # needs "guideline development" and WHO writes "Guidelines Development Group".
        #
        # Narrowed to the roster, deliberately. The bare plural also matched Cancer
        # Council's "Guidelines development process" (E83abn), and reading that section
        # showed it is not only methodology: it states that country of birth correlates
        # with hepatocellular carcinoma risk through chronic hepatitis B, and that these
        # are "non-causal risk factors ... merely indicative of the likelihood of an
        # individual carrying a causal risk factor, such as a subtype of HBV with high
        # risk of HCC progression". A verifier can check a claim against that.
        "guidelines development group",
        "steering group",
        "review group",
        "working party",
        "project team",
        "terms of reference",
        "search strategy",
        # "clinical question list" is deliberately absent. Both in the catalogue hold the
        # guideline's PICO questions in full, with Population, Intervention, Control and
        # Outcomes spelled out - "For those who are of Aboriginal and/or Torres Strait
        # Islander descent what is the safety and effectiveness of screening using
        # strategies other than those recommended for the general population?". That is
        # the same PICO frame `_PICO_FRAME_HEADINGS` exists to rescue from under "Methods",
        # and it scopes every recommendation the guideline makes.
        # Back-of-document lists of links to evidence files we do not fetch, e.g.
        # "Update 4 (August 2025) - please click HERE". Deliberately narrow: the wider
        # words for this material do not qualify - "supplementary information" heads
        # 60,826 characters of real infection-control guidance in Jn37kn, and
        # "evidence table" heads 63,450 characters of qualitative research findings in
        # LAkxVE. Both stay.
        "living evidence update",
        "forest plot",
        "conflict of interest",
        "conflicts of interest",
        "conflicting interest",
        "competing interest",
        "declaration of interest",
        "declared interest",
    }
)

# Ceiling on what a heading alone may discard, measured as readable text rather than
# raw HTML. HTML length is a poor proxy for content: markup density varies by up to
# 628x across publishers (one search-strategy section is 1,673,817 characters of HTML
# holding 2,664 of text), so an HTML ceiling fires on markup, not on what a reader
# would lose. Across all 233 English guidelines the 916 sections the skip list governs
# max out at 77,448 characters of text, while the worst known near-miss - 75 prose
# recommendations filed under "Annex 6" - is 512,836, so this threshold clears every
# correct deletion observed and still catches that mistake more than 3x over.
MAX_SKIPPED_SECTION_CHARS = 150_000

# The two things a GRADE evidence-to-decision table always names: how sure the panel is, and
# which way the evidence points. Both are required, and more than once, so that a roster
# mentioning GRADE in passing is not mistaken for a table of judgements.
_GRADE_CERTAINTY_RE = re.compile(r"certainty of (?:the )?evidence", re.IGNORECASE)
_GRADE_EFFECT_RE = re.compile(r"favours (?:this|other) option|favours the (?:intervention|comparator)", re.IGNORECASE)

# A guideline explaining what its own recommendation labels mean. This is the interpretation
# contract for every recommendation in the document: without it, "the panel suggests" is just
# a verb, and a verdict on whether a recommendation is strong or conditional has nothing to
# rest on. It is the same sentence that took `methods` off the skip list, where the
# Alzheimer's guideline filed its key under "2 Methods" - but the key is not confined to one
# heading. It appears under five different skip-listed names, so a list edit cannot reach it
# and the body has to be asked instead.
_RECOMMENDATION_KEY_RE = re.compile(
    # Narrowed 2026-08-06. The wording that matters is a guideline mapping ITS OWN words or
    # symbols onto a strength - nyO1Yj's "'The panel recommends' indicates a strong
    # recommendation" - because without it the reader cannot tell what that guideline means.
    # The generic GRADE definition, "A strong recommendation is given when there is
    # high-certainty evidence...", is textbook material every guideline could carry, and it
    # was holding 14 front-matter sections in the corpus. The Australian pregnancy
    # guidelines make the point: their Reading guide defines "Recommended (Green)" and "Not
    # recommended (Red)", and those labels appear nowhere in the rendered corpus, which
    # prints the GRADE token instead - a legend for a scheme the reader never meets.
    r"indicates a (?:strong|conditional|weak) recommendation|"
    # The same contract drawn rather than written: one publisher keys its recommendations
    # by colour, and "The GREEN symbol denotes a non-GRADE-based strong recommendation"
    # is the only place j1WBYn says what its own symbols mean.
    r"symbol denotes an? [\w -]*recommendation|"
    # And the same contract drawn as a table. The CARI guidelines write their key as a grid -
    # a row headed Level 1 "We recommend", a row headed Level 2 "We suggest", and columns
    # spelling out what each means for patients, clinicians and policy. No sentence in it
    # says "indicates a strong recommendation", so the two patterns above miss it entirely,
    # and adding "guideline development methodology" to the skip list deleted the key from
    # three guidelines before this caught it. The quoted phrase beside the level is the
    # signal: it is the guideline naming its own wording, which is the whole point of a key.
    r"level\s*\d\s*[\"“'‘]?\s*we\s+(?:recommend|suggest)",
    re.IGNORECASE,
)
# A statement of who or what the guideline is for. The subject has to be the guideline or
# its recommendations, which is what separates it from prose that merely contains the
# words: a bibliography carries "aged 65" inside a paper title and a glossary defines
# terms with "applies to", and a looser pattern pulled 128,463 characters of References,
# Glossary and Abbreviations back into the corpus. Validated before shipping against seven
# sections that must match and seven that must not.
#
# This is the guard the North Star names as non-negotiable: "a fact can be true for adults
# but not pediatrics", and two of the sections it keeps exist only to say the guideline
# excludes children - jz7xeL's "These guidelines only refer to adults." and jxxdwj's "The
# guideline does not apply to cardiac surgery patients, patients outside the ICU or to
# children."
_GUIDELINE_SCOPE_RE = re.compile(
    r"(?:these|this|the)\s+(?:\w+\s+){0,3}(?:guidelines?|recommendations?|document)\s+"
    r"(?:only\s+)?(?:refers?\s+only\s+to|refers?\s+to|applies\s+to|apply\s+to|"
    r"does\s+not\s+(?:apply|cover|address)|do\s+not\s+(?:apply|cover|address)|covers?)"
    # "The scope of this guideline focuses on adolescents (aged 12-<17 years)" - the same
    # statement with "scope" as its subject. A coverage verb is required after it, and
    # "the" is excluded, because a committee roster recording that the group "agreed the
    # scope of the guideline" states no scope at all. Without both conditions three roster
    # sections came back into the corpus, one of them 9,295 characters.
    r"|scope\s+of\s+(?:this|these)\s+(?:\w+\s+){0,2}guidelines?\s+"
    r"(?:is|was|focuses|focused|covers?|includes?)"
    # The exclusion written without naming the guideline as its subject: "No guidance is
    # given on the use of antimalarial agents to prevent malaria in people travelling from
    # non-endemic settings." That is a scope statement in the sense that matters - it is what
    # stops a verifier confirming an out-of-scope claim against the document - and the two
    # patterns above both miss it, because its subject is the guidance rather than the
    # guideline. Found when "executive summary" went on the skip list and this sentence was
    # the only thing standing between WHO's malaria summary and deletion.
    r"|no\s+(?:guidance|recommendations?|advice)\s+(?:is|are)\s+(?:given|provided|made|included)\b",
    re.IGNORECASE,
)
# What a review concluded, as opposed to somebody's role in one. The distinction is the whole
# point: a declared-interest table says "was a DSMB member of the WOMAN trial" and mentions
# trials constantly without ever reporting a result, so any pattern matching "trial" or
# "cohort study" rescues disclosure tables by mistake - measured, 14 hits on one. These
# phrases only appear when a section is reporting what was found, including the finding that
# nothing was found, which the corpus keeps deliberately.
_EVIDENCE_FINDING_RE = re.compile(
    r"yielded no evidence|no evidence was (?:found|identified)|did not show|"
    r"no (?:studies|trials|RCTs) (?:were )?(?:found|identified)|showed no difference",
    re.IGNORECASE,
)

# Children that survive a skipped parent. A skip normally takes the whole subtree, which
# is right for what these headings usually hold - who sat on the panel, which databases
# were searched, when the group met. But publishers routinely file the guideline's PICO
# frame under "Methods", and it is the only place the corpus ever says who a
# recommendation applies to. The European Stroke Organisation's VTE guideline (Eea27E)
# is the clearest case: its "Population" reads "These recommendations refer to patients
# who have suffered an ischaemic stroke ... It specifically does not consider patients
# who have had intracerebral haemorrhage", and without it nothing in that document scopes
# its four recommendations. These are standard GRADE frame headings that recur across
# publishers, not one guideline's wording, which is what makes keeping them by name safe.
#
# Matched against `_skip_lookup_key`, so numbered variants like "3.2 Outcomes" are caught.
_PICO_FRAME_HEADINGS = frozenset(
    {
        "population",
        "populations",
        "intervention",
        "interventions",
        "comparator",
        "comparators",
        "outcome",
        "outcomes",
        "outcomes considered for guideline questions",
        "guideline scope",
        # The same "who does this apply to" statement under a different name. Malawi's
        # malaria guideline files it as "Scope and purpose of the guidelines" - "covers
        # uncomplicated malaria, severe malaria, malaria in pregnancy" - and two Australian
        # pregnancy guidelines as "Scope and audience". Their siblings under the same
        # parent are paperwork and stay dropped: NHMRC approval dates, "Download a list of
        # the recommendations", who developed the guideline, editorial language policy.
        "guideline recommendation questions",
        "recommendation questions",
        "clinical questions",
        # A named domain of GRADE's Evidence-to-Decision framework, which MAGICapp also
        # carries as a per-recommendation field and this scraper already renders as
        # "Patient values and preferences". Nine sections in the catalogue are headed
        # this way; the eight whose parent is not skipped all hold substantive prose,
        # several of them evidence-gap statements ("We did not provide the GDG with an
        # evidence-based description of patient experiences"). None is boilerplate.
        "values and preferences",
        "patient values and preferences",
        # The summary headings were rescued here between 2026-08-05 and 2026-08-06, so that a
        # container heading could not silently re-delete what had been restored one level
        # up. Removed again on Evan's call, reading jW0ZbL: with "About the Guidelines" now
        # dropping, he wants the summaries under it to go with it. A standalone "Abstract"
        # or "Executive summary" is unaffected - neither is on the skip list, so only the
        # copies nested inside a dropped parent are lost.
    }
)

# A section whose entire content is one sentence telling the reader to look somewhere
# else. Not a heading-name rule - it is keyed on the section holding nothing but the
# pointer, so it cannot reach a section that also carries guidance.
#
# 22 of these survive into the corpus, and not one could be the evidence a verdict rests
# on: "Please click here for this section", "Click here for PRISMA flow diagram", "Click
# here to see the declaration of interest register". The worst is a section headed
# "Recommendation" whose whole body is "See corresponding BMJ Rapid Recommendation:
# <url>" - a topically perfect match for a recommendation query that delivers none.
#
# Nothing is lost by dropping them. Where the pointer goes elsewhere in the same
# document, that other section is in the corpus with the real content; where it goes to
# another MAGICapp guideline, that guideline is in the corpus as its own document; and
# where it goes to a download, the scraper never fetched it anyway.
# "also" sits between the two halves often enough to matter - the sentence Evan found,
# "Please also refer to the topic Early Nutrition in Managing Complications", is the
# shape - and it changes nothing about what the sentence does.
_POINTER_ONLY_RE = re.compile(
    r"\A(?:please\s+)?(?:also\s+)?(?:click|see|refer\s+to|go\s+to)\b"
    # The same sentence with a different opening: "Further information on
    # contraindications and precautions is available in the Australian Medicines
    # Handbook." It directs and asserts nothing, exactly like the others.
    r"|\A(?:further|more|additional)\s+(?:information|detail|guidance|resources?)\b"
    # And the same again as an instruction: "To access the LEAPP technical report for
    # this recommendation, click here."
    r"|\A(?:to|for)\s+(?:access|view|download|read|obtain|find)\b",
    re.IGNORECASE,
)
# The longest genuine pointer in the corpus is 170 characters ("See also 8.2
# Investigation and management at colposcopy, for recommendations applying to specific
# combinations..."), so length alone cannot tell a pointer from prose that happens to
# open with "See". What can: a pointer only directs, it never asserts. A sentence
# carrying a measurement is making a claim, so it is prose whatever it opens with.
_MAX_POINTER_CHARS = 200
_MEASUREMENT_RE = re.compile(
    r"\d+\s*(?:%|mg|kg|ml|mL|g\b|mcg|µg|mmol|mmHg|IU\b|hours?|days?|weeks?|months?|years?)",
    re.IGNORECASE,
)

_MAX_HEADING_LEVEL = 6
_SHORT_CODE_RE = re.compile(r"^[A-Za-z0-9]{4,12}$")
_GUIDELINE_PATH_RE = re.compile(r"/guideline/(?P<short_code>[A-Za-z0-9]{4,12})")

# Strength values meaning "the panel assigned no GRADE strength".
_UNRATED_STRENGTHS = frozenset({"NOTSET", "NO_STRENGTH"})

# Strength value marking an editorial callout box rather than a recommendation.
_INFO_STRENGTH = "INFO"

# Strength values that cannot, on their own, make a section clinical content. Used
# only to decide whether a skip-listed heading is rescued - see `_has_recommendation`.
_NON_GUIDANCE_STRENGTHS = frozenset({_INFO_STRENGTH, "NO_STRENGTH"})

# `recommendation.status` review states meaning the publisher no longer stands behind
# the text as current guidance; these are dropped rather than emitted. UNDER_REVIEW
# and NEW_EVIDENCE still mark current guidance and are kept. Two recommendations
# across the whole catalogue carry POSSIBLY_OUTDATED.
_DROPPED_STATUSES = frozenset({"POSSIBLY_OUTDATED"})

# The same thing said in the text rather than in a field. Three publishers head a
# recommendation with a bolded marker - "DRAFT RECOMMENDATION - AUGUST 2024", "DRAFT
# UPDATE - February 2025", "DRAFT RECOMMENDATION FOR PUBLIC CONSULTATION - 27 MAY to 1
# JULY 2026" - while the `status` field says UPDATED or NEW. Advice the panel has not
# settled on is exactly what `_DROPPED_STATUSES` exists to keep out, so it goes the same
# way, the whole recommendation with it.
#
# Anchored at the start, which is what makes it safe: 13 recommendations open with the
# word and are dropped, while 4 that merely mention a draft in passing - "the Expert
# Advisory Committee will provide advice on draft recommendations" - are untouched.
_DRAFT_RECOMMENDATION_RE = re.compile(r"\A\s*draft\b", re.IGNORECASE)
# The same marker written into the remarks rather than the heading: "This is a draft
# recommendation that has not yet been approved by NHMRC." Requires both halves - the draft
# word and the not-yet-approved clause - so that a remark merely mentioning a draft document
# does not retire a recommendation the panel has signed off.
_UNAPPROVED_DRAFT_RE = re.compile(
    r"\bis\s+a\s+draft\s+recommendation\b[^.]*\bnot\s+yet\s+been\s+approved\b", re.IGNORECASE
)

# `keyInfo.evidenceStrength` is GRADE's certainty of evidence, but MAGICapp stores it
# with its own tokens, and one of them collides with a different GRADE concept.
#
# GRADE certainty has exactly four levels - High, Moderate, Low, Very low - and across
# 63 guidelines this field only ever takes NOTSET, WEAK, MODERATE, VERY_LOW and HIGH.
# `LOW` never appears: `WEAK` occupies that slot. Publishers confirm it in their own
# prose, e.g. a recommendation emitted here as WEAK is described in the same guideline's
# executive summary as "Conditional recommendation for, low certainty evidence".
#
# Emitting the raw token would put `WEAK` in the same label twice meaning two different
# things - once as the recommendation's strength (weak/conditional) and once as the
# certainty of its evidence (low) - which is unreadable and actively misleading.
_GRADE_CERTAINTY = {
    "HIGH": "High",
    "MODERATE": "Moderate",
    "WEAK": "Low",
    "VERY_LOW": "Very low",
}

# The same GRADE certainty scale, but the per-outcome field spells it differently and
# needs its own table. `keyInfo.evidenceStrength` above uses WEAK where GRADE says Low;
# `outcome.qualityOfEvidenceLevel` uses LOW directly and never emits WEAK. Across the
# corpus it takes exactly five values - VERY_LOW, LOW, MODERATE, HIGH and NOTSET -
# so sharing one table would silently drop every LOW rating on the floor.
_OUTCOME_CERTAINTY = {
    "HIGH": "high",
    "MODERATE": "moderate",
    "LOW": "low",
    "VERY_LOW": "very low",
}

# Effect measures worth naming. NOTSET and an absent type both mean the panel recorded
# a number without saying what kind, which is not something to present as an estimate.
_EFFECT_MEASURES = {"RR": "RR", "OR": "OR", "HR": "HR"}

# The three lists MAGICapp splits its outcomes across. All three carry the same
# identifying and certainty fields; only the effect fields differ in how often they
# are filled, so one renderer handles them.
_OUTCOME_LISTS = ("dichotomousOutcomes", "continuousOutcomes", "nonPoolableOutcomes")

# Publishers often open the recommendation body with their own label, and the label
# they choose carries meaning. The National Blood Authority states it outright: a
# recommendation is "based on evidence from the systematic review", a practice point
# is what the panel wrote "where the systematic review found insufficient high-quality
# data", and an expert opinion point covers questions "outside of the scope of the
# systematic review". Replacing those with a generic "Recommendation" heading would
# assert graded evidence the panel explicitly said it did not have.
#
# Measured over 63 guidelines: 440 bodies open with such a label - 211 bold
# "Recommendation N", 150 "Practice point", 37 "Expert Opinion Point N", 14
# "Consensus-based recommendation", 13 as a markdown heading, 9 "Good practice
# statement", the rest plain. Capitalisation varies by publisher, hence IGNORECASE.
# The label is whatever the body's first line says - bold is usual but not required -
# so long as it opens with one of these words, optionally behind a section number:
# labels like "2.1 Adapted evidence-based recommendation" (E83abn) and Phoenix
# Australia's "RESEARCH RECOMMENDATION" (41 bodies) otherwise get a generic
# "Recommendation" stacked on top - the research one then reading as clinical
# guidance. Enumerating full labels does not scale: the European Stroke Organisation
# writes "Expert opinion on mechanical thrombectomy in late time windows", which is
# descriptive rather than drawn from a fixed vocabulary.
_LABEL_OPENERS = re.compile(
    r"\A(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:"
    r"adapted\s+evidence[-\s]based\s+recommendation"
    r"|consensus[-\s]based\s+recommendation"
    r"|evidence[-\s]based\s+recommendation"
    r"|good[-\s]practice\s+statement"
    r"|expert\s+consensus\s+statement"
    r"|expert\s+opinion"
    r"|practice\s+point"
    r"|key\s+consideration"
    r"|key\s+statement"
    r"|research\s+recommendation"
    r"|statement"
    r"|recommendation"
    r")",
    re.IGNORECASE,
)

_LABEL_DECORATION_RE = re.compile(r"\A[#*\s]+|[*\s]+\Z")
# A first line that is one bold span end to end, with no "**" anywhere inside it.
_WHOLE_LINE_BOLD_RE = re.compile(r"\*\*((?:[^*]|\*(?!\*))+)\*\*")
_MAX_LABEL_CHARS = 120
_EMPTY_HEADING_RE = re.compile(r"^#{1,6}[ \t]*$\n?", re.MULTILINE)

# Wiki page furniture that Cancer Council Australia pasted into MAGICapp along with the
# text, styled grey to match a real MediaWiki edit link but inert - `[edit source]` rides
# inside table captions, and `Back to top` sits between sections as its own paragraph.
# Neither is the publisher's writing about medicine; both are their website's controls.
_BLANK_RUN_RE = re.compile(r"\n{3,}")
# "Back to top" appears four ways in the source - plain text, underlined text, a real
# link back to the page anchor, and any of those glued onto the end of a clinical
# paragraph ("...rarely survive 12 months. [Back to top](...)"). The first version
# matched only a bare line and left every other form in the corpus. The trailing form is
# only removed after sentence-ending punctuation, so prose that genuinely ends in those
# words is untouched.
_WIKI_CHROME_RE = re.compile(
    r"[ \t\xa0]*\\?\[edit source\\?\]"
    r"|^[ \t\xa0]*[*_]{0,2}\[?Back to top\]?(?:\([^)]*\))?[*_]{0,2}[ \t\xa0]*$\n?"
    r"|(?<=[.!?\)])[ \t\xa0]*\[?Back to top\]?(?:\([^)]*\))?[ \t\xa0]*$",
    re.MULTILINE,
)

# A line of nothing but dashes or equals signs, directly under a line of text, is a
# setext heading underline in markdown - so the paragraph above it stops being a
# paragraph and becomes a heading. Publishers write one as an ordinary visual
# separator: WHO ends a paragraph `...for use in all age groups.<br />--</p>`, and the
# whole paragraph about parenteral artesunate then renders as an `<h2>`. Escaping the
# first character leaves the characters the publisher wrote visible and stops them
# meaning anything. Only a run directly under text matches: a run under a blank line is
# an ordinary horizontal rule, and a table's `| --- |` separator starts with a pipe.
_ACCIDENTAL_SETEXT_RE = re.compile(r"^(?P<text>\S.*)\n(?P<rule>[-=]{2,})[ \t]*$", re.MULTILINE)

# CKEditor track-changes marker for text an editor has struck out.
_DELETION_CLASS = "ck-suggestion-marker-deletion"

# MAGICapp's own inline reference marker, e.g.
# `<cite class="magic-cite" data-ref-id="839066" data-label="">[3]</cite>`.
# The shared `html.py` already drops `[3]`-shaped markers, but it does so on the
# *markdown*, after conversion, and by then the element's boundaries are gone - so it
# takes the number and leaves everything the element was sitting next to. Removing the
# element here, before conversion, is what makes the surrounding text join up cleanly.
_CITATION_CLASS = "magic-cite"
# A reference the publisher wrote by hand as an italicized bracket rather than through
# the platform, `<i>[66]</i>`. Converted first, its number is then stripped downstream
# and the emphasis markers are left with nothing between them - a stray `**` in the
# middle of a sentence, which pairs with the next one and bolds the text between.
_MANUAL_CITATION_RE = re.compile(r"\A(?:\[\d+\])+\Z")
_CITATION_TAGS = frozenset({"i", "em", "b", "strong", "sup", "span"})
# Cancer Council Australia hangs the marker inside the link to the reference,
# `<a href="https://wiki.cancer.org.au/..."><sup>[1]</sup></a>`. Removing only the
# `<sup>` leaves an anchor with no text and a 300-character wiki URL, so the wrapper
# has to go too. Climbed only while the wrapper holds nothing but the marker.
_CITATION_WRAPPER_TAGS = _CITATION_TAGS | {"a"}
# Characters that end a word or a clause, after which a following word needs a space.
# Opening brackets are absent on purpose: nothing should be inserted after "(".
# What a publisher puts between two citation markers. The dashes matter as much as the
# comma: a cited range is written `[106] – [109]`, and keeping the dash once both markers
# go leaves it welded to the word before - "following the GRADE methodology– and the".
_CITATION_SEPARATORS = frozenset({",", ";", "-", "\u2013", "\u2014"})

# A whole anchor whose visible label is nothing but digits. Non-greedy attributes so
# it cannot run across two tags, and the label captured so the digits survive.
_NUMERIC_LINK_RE = re.compile(r"<a\b[^>]*>(\s*\d+\s*)</a>", re.IGNORECASE)
_CITATION_CLAUSE_ENDINGS = frozenset(".,;:)]}%\u2019\u201d")
_CITATION_EDGE_WHITESPACE = " \t\xa0"
# Punctuation that belongs to the sentence rather than to the citation, so the space
# that separated the citation from the previous word must not survive in front of it.
_CITATION_TRAILING_PUNCTUATION = ".,;:)]}!?"
# What is left when every item in a list was a reference marker: the separators, and
# nothing they separate. Requires at least one, so genuinely empty cells are untouched.
_ORPHANED_SEPARATORS_RE = re.compile(r"[\s\xa0]*[,;/&·•][\s\xa0,;/&·•]*")

# Paperwork the publisher wrote *inside* a section that also carries guidance, which the
# section skip list cannot reach - see `_drop_inline_paperwork`.
#
# This list is deliberately not the section skip list. Applied to lines inside a body,
# that list would also match "Conclusion" (18 in the catalogue), "Target audience" (78)
# and "Methods" (19), and a bold "Conclusion" in the middle of a clinical section is
# usually the clinical conclusion. Only labels that can be nothing but paperwork are
# here: every one was read in the catalogue before being added.
_INLINE_PAPERWORK_LABELS = frozenset(
    {
        "about these guidelines",
        "about this guideline",
        "about this living guideline",
        "conflict of interest register",
        "conflicts of interest",
        # The journal byline written into a clinical section's body, where the skip list
        # cannot reach it: rj1KVn opens "Corticosteroids for community-acquired pneumonia"
        # with "Contributors: Reed A.C. Siemieniuk, MD; Per O. Vandvik, MD, PhD; ...".
        # 5 guidelines. The plural only - "contributor" as a substring would take
        # "Contributors to the evidence base", which is not a byline.
        "contributors",
        # The byline under its other wording. EZ1w8n opens "Corticosteroids for sepsis" with
        # "Authors: Francois Lamontagne, *chair, critical care clinician*; Bram Rochwerg,
        # *critical care clinician*; ...", a name and an italic role repeated down the block.
        # 78 blocks across 12 guidelines. Exact labels, so "Authors' conclusions" - the
        # Cochrane heading - is untouched.
        "authors",
        "author",
        "guideline panel",
        "panel members",
        "panel member",
        "copyright",
        "declaration of interests",
        "declaration of interests by external contributors",
        "disclaimer",
        # How the guideline is distributed and where to send comments. Observed as
        # bold lines inside WHO's executive summary (LwRMXj), which opens with several
        # blocks of platform administration before any clinical content: which website
        # carries the PDF, which languages it was translated into, and the address to
        # e-mail feedback to.
        "dissemination",
        "feedback",
        # The same block, in WHO's own wording: how a recommended product reaches the
        # prequalification list, and the release history of the guideline itself.
        "link to who prequalification",
        "updating evidence-based guidance",
        # WHO's definition of its own vocabulary - what a guideline is, what a good
        # practice statement is, who the document is for. The same category as the
        # glossary and target-audience headings already on the section skip list.
        # Matched as a whole heading and never on the words inside it: 50 headings in
        # the catalogue read "Good practice statement 2", "Good practice statement 3"
        # and so on, and those are the recommendations themselves.
        "who guidelines, recommendations and good practice statements",
        "external review group",
        "funding",
        "guideline development group",
        "guideline development group (gdg)",
        "guideline development team",
        "guideline monitoring and auditing",
        "how to cite",
        "how to cite this guideline",
        # nyO1Yj's citation block is still not covered here, and now does not need to be:
        # reaching it through this list wanted three separate loosenings of a rule that
        # deletes text, so `_drop_publication_announcements` matches the shape of the pair
        # instead - the "published in ... :" line and the reference with a DOI under it.
        "observers",
        "public consultation and feedback",
        "publication approval",
        # A bibliography the publisher typed *inside* a section body rather than as a
        # section of its own. "references" is already an exact name on the section skip
        # list, but `_is_skipped_section` only ever looks at a section's own `heading`
        # field - a heading written into `text` is promoted straight to markdown with no
        # skip-list check, so it rides through. 19 such bibliographies across 4
        # guidelines, one carrying a live Lancet Oncology citation.
        "references",
        "updating",
        "who steering group",
    }
)

# Cheap pre-check: parsing every body would be wasteful when almost none carry these.
# A cheap pre-filter so the parse is skipped for fragments that cannot contain a label.
# It has to stay in step with `_INLINE_PAPERWORK_LABELS`: a label the hint cannot reach is
# dead, because this returns before the labels are ever consulted. Adding "contributors"
# without adding it here did exactly that, and the byline stayed in the corpus with no
# error anywhere. A test asserts every label is reachable, since nothing else would catch it.
_INLINE_PAPERWORK_HINT_RE = re.compile(
    r"authorship|participated in the development|about th[ie]s|copyright|how to cite|disclaimer"
    r"|funding|steering group|external review group|observers|publication approval"
    r"|public consultation|guideline development (group|team)|conflicts? of interest"
    r"|declaration of interests|monitoring and auditing|updating"
    r"|dissemination|feedback|prequalification|recommendations and good practice|references"
    r"|contributors|authors?|guideline panel|panel members?",
    re.IGNORECASE,
)
_AUTHORSHIP_MARKER_RE = re.compile(
    r"\A(authorship\b|the following[^.]{0,80}(participated|contributed|were involved))",
    re.IGNORECASE,
)
# Markdown has no superscript, so `markdownify` drops the tag and lets the characters run
# into the text beside them. That turns a platelet threshold of `20 x 10<sup>9</sup>/L`
# into "20 x 109/L" - a different number, and the load-bearing one in every recommendation
# of the McMaster ITP guideline. It also welds footnote markers onto the word before them:
# "Therapeutic Goods Administration (TGA)<sup>i</sup>" becomes "(TGA)i".
#
# Unicode has the superscript forms, so both are solved by keeping the character rather
# than the tag: `10⁹/L` reads correctly and `(TGA)ⁱ` is visibly a marker. Where a character
# has no superscript form the whole run falls back to a caret, `10^n`, which at least does
# not merge with its neighbour. 17,388 `<sup>` elements across the catalogue.
_SUPERSCRIPTS = str.maketrans(
    "0123456789+-=()abdeghijklmnoprstuvwxyz",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᵈᵉᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ",
)
_SUBSCRIPTS = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_SCRIPT_TAGS = {"sup": _SUPERSCRIPTS, "sub": _SUBSCRIPTS}
# Separators that may sit at normal size inside an otherwise convertible run, so a
# reference pair like <sub>2,3</sub> converts to "₂,₃" instead of falling back to a
# caret on the comma. Deliberately narrow: anything wider re-opens partial conversion.
# The whole tag to its closing ">": img is a void element, so nothing follows to
# close. DOTALL because publishers' alt texts carry real newlines.
_IMG_OPEN_RE = re.compile(r"<img", re.IGNORECASE)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)

_SCRIPT_PASSTHROUGH = frozenset({",", "."})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# A guard on a publisher typo: the widest real table in the catalogue is well inside
# this, and a stray `colspan="200"` would otherwise produce a 200-column row.
_MAX_TABLE_SPAN = 40
_EMPHASIS_TAGS = frozenset({"strong", "b", "u"})
# Emphasis markdown writes as paired markers, so it has to balance inside one line. `u` is
# absent: markdown has no underline, so it never emits a marker that could go unpaired.
_SPLITTABLE_EMPHASIS = frozenset({"strong", "b", "em", "i"})
# Bold-italic is two layers; three would be unusual and four has not been seen.
_MAX_EMPHASIS_SPLIT_PASSES = 4
_TABLE_TAGS = frozenset({"table", "figure"})
# A heading is a label, not a sentence; a bolded sentence is emphasis.
_MAX_INLINE_HEADING_CHARS = 80
# The authorship label runs to a full sentence ("The following Expert Advisory Panel
# members participated in the development of this recommendation:"), so it needs more
# room than a heading - but not unbounded, or a paragraph mentioning authorship matches.
_MAX_AUTHORSHIP_LABEL_CHARS = 160

# One unit of emptiness inside a tag: a whitespace character, or a non-breaking space
# written either as the entity or as the character itself.
#
# This MUST stay a character class and must never be rewritten as the alternation
# `(?:\s|&nbsp;|\xa0)`. Python's `\s` already matches `\xa0`, so in that form every
# non-breaking space matches two branches and a run of n of them has 2**n equally
# valid parses. That costs nothing while the overall match succeeds, but when the
# rest of the pattern fails the backtracking engine walks all of them. The German
# emergency-medicine guideline j7q2zn holds `<span style="color:#000000">` followed
# by 49 non-breaking spaces and no closing tag: 2**49 paths, measured at a clean
# doubling per added space, so the scraper never returned. A class matches each
# character exactly one way, leaving nothing to backtrack over.
_BLANK_UNIT = r"(?:[\s\xa0]|&nbsp;)"

# An inline element holding nothing but zero-width spaces, which Python counts as content:
# U+200B is a format character rather than whitespace, so `"​".strip()` returns it
# unchanged and every emptiness test above answers no. A dengue guideline brackets each of
# its citations with `<i>&#8203;</i>`, converting to a pair of markers with nothing between
# them - 30 stray asterisks in `n303gE` alone.
#
# Deleted rather than replaced by a space, unlike the blank tags above: the character is
# zero width, so the publisher wrote no gap and inserting one would put a space in front of
# their full stops. For the same reason this must NOT be folded into `_BLANK_UNIT`, which
# also drives the two rules that move edge characters out of an emphasis element. CommonMark
# counts U+200B as neither whitespace nor punctuation, so a marker parses happily beside one
# and moving it outside *breaks* the run - it took `LwRMXj` from 2 stray asterisks to 12.
# Requires at least one, and matches nothing else, so mixed content is left alone.
_ZERO_WIDTH_CHAR_RE = re.compile("[\u200b\u200c\u200d\ufeff\u2060]")
_ZERO_WIDTH_INLINE_TAG_RE = re.compile(
    r"<(strong|em|span|b|i|u|sup|sub)\b[^>]*>​++</\1>",
    re.IGNORECASE,
)

# An inline element whose only content is whitespace, e.g. "<strong>&nbsp;</strong>".
# Conversion discards the element and the space inside it, welding the words either
# side together: "treatment and<strong>&nbsp;</strong>with no contraindications" becomes
# "treatment andwith no contraindications". 2,466 of these across 44 of 63 sampled
# guidelines, so they are replaced by a plain space before conversion.
#
# The run is possessive: the closing tag it must be followed by starts with "<", which
# no `_BLANK_UNIT` can match, so giving characters back could never let the rest of the
# pattern succeed. Refusing to give them back makes a failed attempt one forward scan.
_BLANK_INLINE_TAG_RE = re.compile(
    rf"<(strong|em|span|b|i|u|sup|sub)\b[^>]*>{_BLANK_UNIT}*+</\1>",
    re.IGNORECASE,
)

# markdownify keeps whitespace found just inside an emphasis tag, and CommonMark
# forbids an emphasis delimiter beside a space, so "<i>low confidence)&nbsp;</i>"
# renders a stray literal asterisk. The space is moved outside the tag before
# conversion; clearing it took the catalogue's unbalanced-marker recommendations
# from 46 to 25. The run is matched in bounded chunks: an unbounded
# `+` made the close-tag pattern rescan to the end of every long whitespace run from
# every position inside it - quadratic, and two real guidelines hung the scraper.
# The substitution loop in `_markdown` moves any longer run out chunk by chunk.
_SPACE_AFTER_OPEN_RE = re.compile(rf"(<(?:strong|em|b|i|u)\b[^>]*>)({_BLANK_UNIT}{{1,16}})", re.IGNORECASE)
_SPACE_BEFORE_CLOSE_RE = re.compile(rf"({_BLANK_UNIT}{{1,16}})(</(?:strong|em|b|i|u)>)", re.IGNORECASE)

# The same fault as the two rules above, one step further out. CommonMark will not open
# an emphasis run whose first content character is punctuation when a word character sits
# against the marker, and will not close one whose last content character is punctuation
# or a space - so the markers survive into the text as literal asterisks. Publishers do
# this constantly: `<i>Judgement</i><strong>:</strong>` bolds the colon on its own, and
# one guideline uses `<strong><sup>______</sup></strong>` as a visual rule 84 times.
#
# Square brackets are deliberately NOT moved. `_NUMERIC_CITATION_RE` in the shared
# html.py matches `\[\d+\]`, so turning `*[299]*` into `[*299*]` puts the brackets either
# side of the emphasis and the citation number then survives into the visible text - 14
# documents, measured before this shipped.
_TRIMMABLE_EMPHASIS = frozenset({"strong", "em", "b", "i", "u"})
_EMPHASIS_EDGE = re.compile(r"[^\w\s\[\]]", re.UNICODE)

# The editor writes a colour span around every emphasis run, so the two runs in
# `<span><strong>T</strong></span><strong>ype 3</strong>` are cousins rather than
# siblings and no rule keyed on the neighbouring element could see them meet. These tags
# produce no markdown of their own, so reading through them changes what the emphasis
# rules decide without changing a character of what the document says. Whole-span
# unwrapping was tried first and is wrong: it also changes what every *later* pass sees,
# and `_drop_citations` then welds or splits words across the boundaries it removed.
_STYLING_WRAPPERS = frozenset({"span", "font"})
# Emphasis tags that mean the same thing when they meet: `<b>` and `<strong>` are both
# bold, `<i>` and `<em>` both italic. Runs merge only when their whole stack matches, so
# an underlined run never merges into a plain one, and the bold colon in
# `<i><strong>Immediate</strong></i><strong>:</strong>` never merges into the word before
# it - two different meanings, and joining them would misreport what the publisher wrote.
_EMPHASIS_SEMANTICS = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "u": "u"}
_MAX_EMPHASIS_MERGE_PASSES = 64

# Prose carried on the recommendation itself, beyond `text` and `remarks`.
_RECOMMENDATION_PROSE = (
    ("rational", "Rationale"),
    ("advice", "Practical advice"),
    ("implementation", "Implementation"),
    ("evaluation", "Evaluation"),
    ("research", "Research needed"),
)

# The Evidence-to-Decision worksheet on `keyInfo`: seven domains, each with the
# panel's judgment and the research behind it. Emitted under labelled headings so
# a downstream consumer can tell clinical evidence from economic or policy
# judgment, and drop what it does not want. See the AMFV vault note on keyInfo.
_KEY_INFO_DOMAINS = (
    ("evidence", "Certainty of the evidence"),
    ("benefits", "Benefits and harms"),
    ("preferences", "Patient values and preferences"),
    ("resources", "Resources and cost"),
    ("acceptability", "Acceptability"),
    ("feasibility", "Feasibility"),
    ("equity", "Health equity"),
)


class MagicFetchError(ScrapeError):
    """Raised when a MAGICapp catalogue or guideline document cannot be read."""


@dataclass(frozen=True)
class GuidelineRef:
    """A published MAGICapp guideline listed in the catalogue."""

    short_code: str
    guideline_id: int
    title: str
    json_path: str
    published_id: int = 0
    language: str = ""
    institution: str = ""
    publish_date: str = ""
    recommendation_count: int = 0
    disclaimer: str = ""
    status: str = ""
    description: str = ""
    last_search_date: str = ""

    @property
    def is_archived(self) -> bool:
        """Report whether the publisher has marked this guideline archived.

        The catalogue `description` is where they say so, in as many words: all
        five National Blood Authority Patient Blood Management modules carry
        "This guideline is now archived." and no other English guideline mentions
        archiving at all, so the phrase is precise rather than a heuristic.

        Neither field that sounds like it would carry this does. `status` is
        NOTSET on all five, and `isLatestPublished` is False on every one of the
        460 catalogue entries. What `publishDate` says is worse than useless
        here: it reads 2026-07-20 on modules whose last evidence search was 2013,
        because archiving is itself a publishing action.
        """
        return "archiv" in self.description.lower()

    @property
    def is_draft(self) -> bool:
        """Report whether the publisher's own title marks this guideline a draft.

        See `_DRAFT_TITLE_RE` for the measurement, the reason the match is
        word-bounded, and the reason the catalogue `status` field is not
        consulted.
        """
        return _DRAFT_TITLE_RE.search(self.title) is not None

    @property
    def page_url(self) -> str:
        """Return the human-readable MAGICapp URL for this guideline."""
        return f"{BASE_URL}/#/guideline/{self.short_code}"


@dataclass(frozen=True)
class GuidelineListingPage:
    """One page of catalogue results.

    MAGICapp returns the whole catalogue in a single response, so only page 1
    is ever populated; later pages are empty and stop the listing loop.
    """

    refs: list[GuidelineRef]
    total: int | None = None


def _https(url: str) -> str:
    """Upgrade a catalogue content URL to HTTPS.

    A handful of catalogue entries carry an `http://` jsonPath, and the file host
    answers plain HTTP with 403 - it is a protocol refusal, not a permission
    problem, so the same URL over HTTPS returns the document. Checking every
    English entry found 236 on HTTPS all reachable and 6 on HTTP all refused, and
    all 6 succeeded once upgraded. Left alone they would be silently skipped,
    losing real guidelines: 'VTE, Thrombophilia, Antithrombotic Therapy and
    Pregnancy' (39 recommendations) and 'Treatment of distal radius fractures in
    adults' (16) among them.

    Args:
        url: Content URL from a catalogue entry.
    """
    return f"https://{url[len('http://') :]}" if url.startswith("http://") else url


def _catalogue_url() -> str:
    return f"{API_BASE_URL}/api/v1/guidelines?limit={_CATALOGUE_LIMIT}"


def _guideline_json_url(short_code: str) -> str:
    return f"{BASE_URL}/#/guideline/{short_code}"


def magic_ref_from_url(client: httpx.Client, url: str) -> GuidelineRef:
    """Resolve a MAGICapp guideline URL to its catalogue entry.

    Args:
        client: HTTP client used to fetch the catalogue.
        url: MAGICapp guideline URL, for example
            https://app.magicapp.org/#/guideline/nyxpZL.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")
    if parsed.hostname not in {"app.magicapp.org", "magicapp.org"}:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")

    # MAGICapp uses hash routing, so the short code lives in the fragment.
    match = _GUIDELINE_PATH_RE.search(parsed.fragment or parsed.path)
    if match is None:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")

    short_code = match.group("short_code")
    # No language filter here: an explicit URL is an explicit request.
    for ref in list_published_guidelines(client, languages=None).refs:
        if ref.short_code == short_code:
            return ref
    raise MagicFetchError(f"No published MAGICapp guideline found for short code '{short_code}'")


def _catalogue_int(value: Any) -> int:
    """Coerce a numeric catalogue field, treating junk as 0 rather than aborting the run."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_catalogue(payload: Any, *, languages: tuple[str, ...] | None) -> GuidelineListingPage:
    """Build listing refs from a catalogue response.

    Args:
        payload: Decoded catalogue JSON.
        languages: Language prefixes to keep, e.g. `("en",)`. When None, every
            language is kept.
    """
    if not isinstance(payload, list):
        raise MagicFetchError("Could not parse MAGICapp catalogue: expected a JSON array")

    refs: list[GuidelineRef] = []
    skipped_languages: Counter[str] = Counter()
    skipped_training = 0
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        short_code = str(entry.get("shortCode") or "")
        json_path = _https(str(entry.get("jsonPath") or ""))
        language = str(entry.get("language") or "")
        institution = str(entry.get("institutionName") or "").strip()
        if not _SHORT_CODE_RE.match(short_code) or not json_path:
            logger.debug("Skipping catalogue entry without a short code or content path: %r", entry.get("name"))
            continue
        if institution.lower() in _TRAINING_INSTITUTIONS:
            skipped_training += 1
            continue
        if languages is not None and not language.lower().startswith(languages):
            skipped_languages[language or "unknown"] += 1
            continue
        refs.append(
            GuidelineRef(
                short_code=short_code,
                guideline_id=_catalogue_int(entry.get("guidelineId")),
                title=str(entry.get("name") or short_code).strip(),
                json_path=json_path,
                published_id=_catalogue_int(entry.get("publishedId")),
                language=language,
                institution=institution,
                publish_date=str(entry.get("publishDate") or ""),
                recommendation_count=_catalogue_int(entry.get("publishedRecommendationCount")),
                disclaimer=str(entry.get("disclaimer") or "").strip(),
                status=str(entry.get("status") or "").strip(),
                description=str(entry.get("description") or "").strip(),
                last_search_date=str(entry.get("publishLastSearchDate") or "")[:10],
            )
        )
    if skipped_training:
        logger.info("Skipped %d guideline(s) from tutorial and workshop organisations", skipped_training)
    if skipped_languages:
        logger.info(
            "Kept %d guideline(s); skipped %d in other languages (%s)",
            len(refs),
            sum(skipped_languages.values()),
            ", ".join(f"{lang}={count}" for lang, count in sorted(skipped_languages.items())),
        )
    return GuidelineListingPage(refs=refs, total=len(refs))


def list_published_guidelines(
    client: httpx.Client,
    page: int = 1,
    *,
    languages: tuple[str, ...] | None = DEFAULT_LANGUAGES,
) -> GuidelineListingPage:
    """Return published guideline refs from the MAGICapp catalogue.

    Args:
        client: HTTP client used to fetch the catalogue.
        page: Listing page number. MAGICapp returns the whole catalogue at once,
            so any page after the first is empty (default: 1).
        languages: Language prefixes to keep. Pass None to collect every
            language (default: DEFAULT_LANGUAGES).
    """
    if page > 1:
        return GuidelineListingPage(refs=[], total=None)
    response = client.get(_catalogue_url())
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise MagicFetchError("Could not parse MAGICapp catalogue: response was not JSON") from error
    return _parse_catalogue(payload, languages=languages)


def _drop_tracked_deletions(html_text: str) -> str:
    """Remove text an editor has marked for deletion.

    Guidelines edited with track changes on carry CKEditor suggestion markup, and
    the deleted wording sits in the published JSON right next to its replacement:

        (e.g. robotics) <ins>may</ins><del><ins>can</ins></del><del>may</del> be used

    Converting that without filtering yields "maycanmay". The damage lands on
    exactly the tokens a verifier reads - modal verbs, effect directions, author
    names and figures: "9<ins>6</ins><del>8</del>%" becomes "968%",
    "<ins>Minhas</ins><del>Sandercock</del>" becomes "MinhasSandercock", and one
    stroke guideline renders "there was a smallno difference in arm function".

    Deletions are dropped and insertions kept, because the insertion is the newer
    text - verified by insertions carrying the newer citations and figures.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if _DELETION_CLASS not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for element in root.find_class(_DELETION_CLASS):
        parent = element.getparent()
        if parent is None:
            continue
        # The deleted words go, but text following the span still belongs to the
        # sentence, so it is reattached before the element is removed.
        if element.tail:
            previous = element.getprevious()
            if previous is not None:
                previous.tail = (previous.tail or "") + element.tail
            else:
                parent.text = (parent.text or "") + element.tail
        parent.remove(element)
    # fragment_fromstring hangs text before the first tag on the created parent, so
    # serializing only the children would silently drop it.
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _split_emphasis_across_breaks(html_text: str) -> str:
    """Close and reopen emphasis at every line break it spans.

    Markdown emphasis must open and close inside one line. A publisher who bolds a run
    containing `<br>` produces `**` at the start of one line and its partner two lines
    later, so every renderer reads the pair as one bold run swallowing what lies between.
    Australia's alcohol guideline shows it plainly: `**To reduce the risk of injury and
    other harms to health, children and people under 18 years of age should not drink
    alcohol.` opens, a blank line follows, and `Why not drinking is important for young
    people**` closes.

    Giving each line its own pair of tags keeps what the publisher meant - all of it
    emphasised - and produces markdown that balances. 295 emphasis runs in the source
    cross a break this way, leaving 158 lines carrying an odd number of markers.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if "<br" not in html_text or not any(f"<{tag}" in html_text for tag in _SPLITTABLE_EMPHASIS):
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    # Repeated, because nesting is only reachable one layer at a time. A bold-italic run is
    # `<em><strong>one<br>two</strong></em>`, and `<em>` is visited first in document order
    # with no `<br>` of its own, so it is passed over; only once `<strong>` has been split
    # does `<em>` hold breaks directly and become splittable. Leaving it there produced
    # `***one**` - three openers against two closers, one stray asterisk on the line - on
    # 489 lines across the catalogue.
    for _pass in range(_MAX_EMPHASIS_SPLIT_PASSES):
        if not _split_emphasis_once(root):
            break
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _split_emphasis_once(root: Any) -> bool:
    """Split every emphasis element holding a direct line break. Reports whether any did.

    Args:
        root: Parsed fragment to rewrite in place.
    """
    split_any = False
    for element in list(root.iter()):
        tag = element.tag if isinstance(element.tag, str) else ""
        if tag not in _SPLITTABLE_EMPHASIS or element.find("br") is None:
            continue
        parent = element.getparent()
        if parent is None:
            continue
        # Rebuild the run as: emphasis, break, emphasis, break, ... so each line's share
        # carries its own tags. The breaks move out to sit between them.
        segments: list[list[Any]] = [[]]
        pending_text = element.text or ""
        for child in list(element):
            if isinstance(child.tag, str) and child.tag == "br":
                segments.append([])
                if child.tail:
                    segments[-1].append(child.tail)
                continue
            segments[-1].append(child)
        if pending_text:
            segments[0].insert(0, pending_text)
        if len(segments) < 2:
            continue
        index = parent.index(element)
        parent.remove(element)
        # Segments go in reversed, each insert pushing the previous ones right, so the
        # FIRST node inserted ends up LAST in document order. That node is what the
        # element's tail belongs to: the prose after `<strong>A<br>B</strong>and so on`
        # follows B, not A. Assigning to `parent[index]` instead put it after A, which
        # cut a paragraph in half and swapped the halves - 3 places in the corpus, one
        # of them the EAFT trial description in jbzG8j.
        tail_target = None
        for offset, segment in enumerate(reversed(segments)):
            if offset:
                separator = lxml_html.Element("br")
                parent.insert(index, separator)
                tail_target = tail_target if tail_target is not None else separator
            wrapper = lxml_html.Element(tag)
            for item in segment:
                if isinstance(item, str):
                    wrapper.text = (wrapper.text or "") + item
                else:
                    wrapper.append(item)
            if wrapper.text or len(wrapper):
                parent.insert(index, wrapper)
                tail_target = tail_target if tail_target is not None else wrapper
        if element.tail and tail_target is not None:
            tail_target.tail = element.tail
        split_any = True
    return split_any


def _drop_orphaned_captions(html_text: str) -> str:
    """Remove a caption whose only job was to name a picture that is not kept.

    Images are dropped entirely and deliberately, but the bolded line the publisher wrote
    above one survives — leaving a label with nothing under it. "PRISMA flow chart",
    "Risk of bias graph local recurrence at 2 years", "Blood Products and ITP Treatments
    Administered During Any Visit with Critical Bleeding (N=15)". Each names a figure and
    asserts nothing, so no verdict could ever rest on it.

    Only a caption sitting immediately above `<figure class="image">` goes. MAGICapp wraps
    **tables** in `<figure>` as well, and 227 captions across the catalogue introduce one
    of those — those are the label on a table that is kept, and removing them would strip
    real content of its heading. 94 caption-then-image pairs across 68 guidelines.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if "<figure" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    doomed = []
    for element in root.iter("p"):
        following = element.getnext()
        if following is None or following.tag != "figure":
            continue
        if "image" not in (following.get("class") or "").split():
            continue
        # The whole line has to be emphasised, the way a caption is - a paragraph of
        # prose that happens to precede a figure is not a caption.
        text = _label_text(element)
        marked = "".join(
            part.text_content() or ""
            for part in element.iter()
            if isinstance(part.tag, str) and part.tag in _EMPHASIS_TAGS
        )
        if text and len(" ".join(marked.split())) >= len(text) - 2:
            doomed.append(element)
    for element in doomed:
        element.getparent().remove(element)
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _render_scripts(html_text: str) -> str:
    """Keep superscripts and subscripts as characters, since markdown has no markup for them.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if "<sup" not in html_text and "<sub" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for element in list(root.iter()):
        table = _SCRIPT_TAGS.get(element.tag if isinstance(element.tag, str) else "")
        if table is None or len(element):
            continue
        text = element.text or ""
        if not text.strip():
            continue
        core = text.strip()
        # Publishers set trademark signs as superscript letters (Precivity<sup>TM</sup>);
        # capitals have no superscript characters, so the pair maps to the real sign.
        if element.tag == "sup" and core == "TM":
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            element.text = f"{leading}™{trailing}"
            element.tag = "span"
            continue
        converted = text.translate(table)
        # All or nothing. A partial conversion - "1**" becoming "¹**" - reads as a
        # superscript one followed by two literal asterisks, which is worse than not
        # converting at all. A run that cannot be fully converted keeps a caret instead,
        # so it still cannot merge into the word before it. The one exception is a
        # separator inside an otherwise convertible run - <sub>2,3</sub> reads fine as
        # "₂,₃" and fell back to "^2,3" on the comma alone.
        fully = any(character.translate(table) != character for character in core) and all(
            character.translate(table) != character or character in _SCRIPT_PASSTHROUGH for character in core
        )
        if fully:
            element.text = converted
        else:
            # The caret exists to stop a marker welding onto the word before it, and
            # inside a link it does the opposite: markdown reads `[^` as the start of a
            # footnote reference, so `<a><sup>WHO recommendations...</sup></a>` came out
            # as `[^WHO recommendations...](url)` - a broken link, and one case split a
            # URL down the middle as `https:/[^/www.cerqual.org/]`. Publishers set whole
            # footnote lines in superscript for styling, so a run that is a link's own
            # text is styling rather than notation and needs no marker.
            # Only when the superscript is the link's *whole* text. A marker sitting
            # inside longer link text is still notation and still needs its caret:
            # dropping it there welded "BRAF<sup>V600</sup>-mutant" into "BRAFV600-mutant".
            link = next((ancestor for ancestor in element.iterancestors() if ancestor.tag == "a"), None)
            inside_link = link is not None and link.text_content().strip() == text.strip()
            # Whitespace either side is preserved rather than stripped: the space in
            # "in the 2015 " lived inside the <sup>, and losing it welded "2015" onto the
            # first word of the link that followed.
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            core = text.strip()
            element.text = f"{leading}{core}{trailing}" if inside_link else f"{leading}^{core}{trailing}"
        element.tag = "span"
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _rejoin_across_citation(before: str, after: str, lookahead: str) -> str:
    """Rejoin the text either side of a removed citation marker.

    The publisher separates a word from its citation with a non-breaking space, so
    removing the marker alone leaves that space stranded in front of whatever followed
    it - almost always a full stop or a comma, giving "monitoring data, respectively ."
    Which side the space belongs to depends on what comes next, so it is decided here
    rather than by a fixed rule.

    Args:
        before: Text immediately preceding the citation.
        after: The citation's own tail, which is put back unchanged.
        lookahead: The first character a reader meets after the citation - the tail's
            own first character, or, when the tail is empty, the first character of the
            next element. A publisher may put the sentence's full stop in a sibling
            (`causes&nbsp;<cite/><span>.&nbsp;</span>The`), and reading only the tail
            leaves the separating space stranded in front of it.
    """
    if not lookahead:
        return before + after
    if lookahead in _CITATION_TRAILING_PUNCTUATION:
        # The punctuation ends the sentence the citation was hanging off, so the
        # separating space goes with the citation.
        return before.rstrip(_CITATION_EDGE_WHITESPACE) + after
    if before[-1:] in _CITATION_EDGE_WHITESPACE and lookahead in _CITATION_EDGE_WHITESPACE:
        # Spaces on both sides would otherwise become a double space.
        return before.rstrip(_CITATION_EDGE_WHITESPACE) + " " + after.lstrip(_CITATION_EDGE_WHITESPACE)
    if (before[-1:].isalnum() or before[-1:] in _CITATION_CLAUSE_ENDINGS) and lookahead.isalnum():
        # Nothing but the marker separated the two words, either because the space sat
        # *inside* it (`counselling<sup>[11] </sup>and`) or because the publisher relied
        # on the superscript to do the separating (`NICE<a><sup>[1]</sup></a>modelling`).
        # Removing it silently would weld them - the same damage `_BLANK_INLINE_TAG_RE`
        # exists to prevent - so the marker leaves a space behind, as a blank tag does.
        #
        # A clause ending counts as well as a letter, because the publisher relies on the
        # marker to separate a finished sentence from the next: `deficits<cite/>One third
        # of the acute PWI lesion`, `(CAST)<cite/>and the International Stroke Trial`. An
        # opening bracket deliberately does not, so `(<cite/>text` stays `(text`.
        return before + " " + after
    return before + after


def _leading_text(element: Any) -> str:
    """Return the first characters a reader would see from an element, or "".

    Args:
        element: The element following a citation marker, or None.
    """
    if element is None or not isinstance(element.tag, str):
        return ""
    return (element.text_content() or "")[:1]


def _is_manual_citation(element: Any) -> bool:
    """Report whether an element is a bracketed reference number the publisher formatted by hand."""
    return (
        isinstance(element.tag, str)
        and element.tag in _CITATION_TAGS
        and len(element) == 0
        and bool(_MANUAL_CITATION_RE.match((element.text or "").strip()))
    )


def _citation_root(element: Any) -> Any:
    """Return the outermost element that holds nothing but this citation marker.

    Args:
        element: The element carrying the reference number itself.
    """
    node = element
    while True:
        parent = node.getparent()
        if (
            parent is None
            or parent.tag not in _CITATION_WRAPPER_TAGS
            or len(parent) != 1
            or (parent.text or "").strip()
            or (node.tail or "").strip()
        ):
            return node
        node = parent


def _tag_name(element: Any) -> str:
    """The element's tag name, or "" for comments and processing instructions.

    Args:
        element: Parsed element to name.
    """
    return element.tag if isinstance(element.tag, str) else ""


def _is_styling_wrapper(element: Any) -> bool:
    """Report whether an element is a span or font that conversion emits nothing for.

    A citation carrier is never transparent. `_drop_citations` runs after the emphasis
    passes and keys on these elements, so they have to stay where the publisher put them.

    Args:
        element: Parsed element to test.
    """
    if _tag_name(element) not in _STYLING_WRAPPERS:
        return False
    if _CITATION_CLASS in (element.get("class") or ""):
        return False
    content = (element.text_content() or "").strip()
    return not (content and _MANUAL_CITATION_RE.fullmatch(content))


def _emphasis_at_end(element: Any) -> Any | None:
    """The emphasis element whose closing marker would land against whatever follows.

    Args:
        element: Element sitting immediately before the position in question.
    """
    if _tag_name(element) in _TRIMMABLE_EMPHASIS:
        return element
    if not _is_styling_wrapper(element) or len(element) == 0 or (element[-1].tail or "").strip():
        return None
    return _emphasis_at_end(element[-1])


def _emphasis_at_start(element: Any) -> Any | None:
    """The emphasis element whose opening marker would land against whatever precedes it.

    Args:
        element: Element sitting immediately after the position in question.
    """
    if _tag_name(element) in _TRIMMABLE_EMPHASIS:
        return element
    if not _is_styling_wrapper(element) or len(element) == 0 or (element.text or "").strip():
        return None
    return _emphasis_at_start(element[0])


def _neighbour_before(element: Any) -> tuple[str, Any | None]:
    """What sits against an emphasis element's opening marker, seen through wrappers.

    Reports the character immediately before it, or the emphasis element it touches with
    nothing at all in between. Only styling wrappers are transparent: a citation or a
    superscript standing between two runs genuinely keeps them apart, and whether it
    survives conversion is that pass's business rather than this one's.

    Args:
        element: Emphasis element to look back from.
    """
    node = element
    while node is not None:
        previous = node.getprevious()
        while previous is not None:
            tail = previous.tail or ""
            if tail:
                return tail[-1], None
            emphasis = _emphasis_at_end(previous)
            if emphasis is not None:
                return "", emphasis
            if not _is_styling_wrapper(previous):
                return "", None
            content = previous.text_content() or ""
            if content:
                return content[-1], None
            previous = previous.getprevious()
        parent = node.getparent()
        if parent is None:
            return "", None
        if parent.text:
            return parent.text[-1], None
        if not _is_styling_wrapper(parent):
            return "", None
        node = parent
    return "", None


def _neighbour_after(element: Any) -> tuple[str, Any | None]:
    """What sits against an emphasis element's closing marker. See `_neighbour_before`.

    Args:
        element: Emphasis element to look forward from.
    """
    node = element
    while node is not None:
        tail = node.tail or ""
        if tail:
            return tail[0], None
        following = node.getnext()
        if following is not None:
            emphasis = _emphasis_at_start(following)
            if emphasis is not None:
                return "", emphasis
            if not _is_styling_wrapper(following):
                return "", None
            content = following.text_content() or ""
            if content:
                return content[0], None
            node = following
            continue
        parent = node.getparent()
        if parent is None or not _is_styling_wrapper(parent):
            return "", None
        node = parent
    return "", None


def _emphasis_stack(element: Any) -> tuple[frozenset[str], Any] | None:
    """What a run of nested emphasis tags means, and the element holding its text.

    `<em><strong>x</strong></em>` is one run meaning bold-italic, described by the pair
    ({"em", "strong"}, the `<strong>`). Descending stops at the first element carrying
    text of its own, which is where merged content has to go.

    Args:
        element: Outermost element of the candidate run.
    """
    if _tag_name(element) not in _EMPHASIS_SEMANTICS:
        return None
    meanings = [_EMPHASIS_SEMANTICS[_tag_name(element)]]
    innermost = element
    while (
        len(innermost) == 1
        and not (innermost.text or "").strip()
        and not (innermost[0].tail or "").strip()
        and _tag_name(innermost[0]) in _EMPHASIS_SEMANTICS
    ):
        innermost = innermost[0]
        meanings.append(_EMPHASIS_SEMANTICS[_tag_name(innermost)])
    return frozenset(meanings), innermost


def _remove_and_prune(element: Any) -> None:
    """Remove an element, then any wrapper or emphasis ancestor it leaves empty.

    Bottom-up, stopping at the first ancestor still holding something. Climbing straight
    to the outermost emphasis ancestor instead detaches a `<strong>` holding *both* runs
    being merged, so the run being removed survives inside the detached subtree and the
    merge repeats on it forever - one fragment of `EPY83j` never finished converting.

    An emptied inline tag cannot simply be left behind either: `_BLANK_INLINE_TAG_RE`
    turns one into a space, which would split the very word the merge just repaired.

    Args:
        element: Element to remove.
    """
    while element is not None:
        parent = element.getparent()
        if parent is None:
            return
        tail = element.tail or ""
        if tail:
            previous = element.getprevious()
            if previous is not None:
                previous.tail = (previous.tail or "") + tail
            else:
                parent.text = (parent.text or "") + tail
        parent.remove(element)
        if not (_is_styling_wrapper(parent) or _tag_name(parent) in _EMPHASIS_SEMANTICS):
            return
        if len(parent) or (parent.text or ""):
            return
        element = parent


def _merge_adjacent_emphasis(html_text: str) -> str:
    """Join emphasis runs the editor split, so their markers stop colliding.

    A word split across two runs of the same meaning converts to markers pressed against
    each other: `<span><strong>T</strong></span><strong>ype 3 TZ colposcopy</strong>`
    becomes `**T****ype 3 TZ colposcopy**`, which no renderer reads as emphasis and which
    a reader sees as four asterisks inside a word. Publishers produce this constantly,
    because the editor opens a new element wherever a colour or a style changes mid-word,
    and the runs are almost never direct siblings - each sits in its own colour span.

    Only runs meaning exactly the same thing merge, and only where nothing at all lies
    between them. A bold colon following a bold-italic word is a different meaning and is
    left alone; `_trim_emphasis_edges` unwraps that one instead.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    lowered = html_text.lower()
    if not any(f"<{tag}" in lowered for tag in _TRIMMABLE_EMPHASIS):
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    merged_any = False
    for element in list(root.iter()):
        if element.getparent() is None:
            continue
        run = _emphasis_stack(element)
        if run is None:
            continue
        meanings, target = run
        # Bounded like the split pass above: every merge removes an element, so real
        # fragments stop well inside this and the cap is a hang guard only.
        for _pass in range(_MAX_EMPHASIS_MERGE_PASSES):
            character, neighbour = _neighbour_after(element)
            if character or neighbour is None:
                break
            following = _emphasis_stack(neighbour)
            if following is None or following[0] != meanings:
                break
            source = following[1]
            if source is target or source.getparent() is None:
                break
            # Never move a run into its own ancestor or descendant: the append would
            # reparent a subtree into itself.
            if target in source.iterancestors() or source in target.iterancestors():
                break
            if len(target):
                target[-1].tail = (target[-1].tail or "") + (source.text or "")
            else:
                target.text = (target.text or "") + (source.text or "")
            for moved in list(source):
                target.append(moved)
            # `_remove_and_prune` carries the removed run's trailing text to whatever now
            # precedes it, which is the run just merged into. Re-attaching that text as
            # well duplicated a clause of a WHO Ebola recommendation in `EZVOaE`.
            _remove_and_prune(source)
            merged_any = True
    if not merged_any:
        return html_text
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _trim_emphasis_edges(html_text: str) -> str:
    """Move punctuation out of an emphasis element's edge when it stops the run parsing.

    CommonMark decides whether `*` opens or closes from the characters on either side of
    it. A marker is only refused when punctuation sits **inside** the element against the
    marker and a word character sits **outside** it: `word<strong>:text</strong>` becomes
    `word**:text**`, where the opener is preceded by a letter and followed by punctuation,
    which is not a left-flanking run, so it never opens and both asterisks reach the
    reader. `<strong>haemorrhage?</strong>` at the end of a sentence is fine and is left
    alone - the closer is followed by whitespace, so it closes.

    That condition is the whole rule here. Checking the outside character rather than
    trimming every edge is what keeps this from rewriting markup that already works: a
    first version moved punctuation unconditionally and changed `**haemorrhage?**` into
    `**haemorrhage**?`, which reads identically and repaired nothing.

    Two shapes:

    * an element holding no letter or digit at all - a bolded colon, comma, full stop or
      a rule of underscores - is unwrapped, since emphasis around punctuation alone
      conveys nothing. 84 of these are one shape in `nJeNmL`,
      `<strong><sup>______</sup></strong>`, used as a visual separator.
    * an element whose first or last character is punctuation keeps the element, and that
      character moves outside it.

    Square brackets are never moved; see `_EMPHASIS_EDGE`.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    lowered = html_text.lower()
    if not any(f"<{tag}" in lowered for tag in _TRIMMABLE_EMPHASIS):
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")

    for element in list(root.iter()):
        if _tag_name(element) not in _TRIMMABLE_EMPHASIS:
            continue
        parent = element.getparent()
        if parent is None:
            continue
        content = element.text_content() or ""
        if not content.strip():
            continue
        # What makes the run fail to parse is a word character pressed against the
        # marker from outside - or another emphasis element with nothing between, whose
        # own marker lands against ours. `<i>Judgement</i><strong>:</strong>` converts
        # to `*Judgement***:**`: three markers in a row, none of which can pair off.
        # The neighbouring *tag* has to count, because by the time the markers exist
        # this pass has already run. Both sides are read through styling wrappers, since
        # the colour span the editor writes around each run is invisible to conversion
        # and has to be invisible here too.
        before, emphasis_before = _neighbour_before(element)
        after, emphasis_after = _neighbour_after(element)
        opens_badly = before.isalnum() or emphasis_before is not None
        closes_badly = after.isalnum() or emphasis_after is not None

        if not any(character.isalnum() for character in content):
            if not (opens_badly or closes_badly):
                continue
            # Unwrap by promoting the element's children, never by flattening it to
            # text: `<strong><sup>#</sup></strong>` holds a superscript that
            # `_render_scripts` has not seen yet, and replacing it with "#" silently
            # dropped the anti-weld caret in E83abn.
            index = parent.index(element)
            tail = element.tail or ""
            children = list(element)
            if children:
                for offset, child in enumerate(children):
                    parent.insert(index + offset, child)
                last = children[-1]
                last.tail = (last.tail or "") + tail
                if element.text:
                    previous = children[0].getprevious()
                    if previous is not None:
                        previous.tail = (previous.tail or "") + element.text
                    else:
                        parent.text = (parent.text or "") + element.text
            else:
                previous = element.getprevious()
                if previous is not None:
                    previous.tail = (previous.tail or "") + content + tail
                else:
                    parent.text = (parent.text or "") + content + tail
            parent.remove(element)
            continue

        while opens_badly and element.text and _EMPHASIS_EDGE.fullmatch(element.text[0]):
            moved, element.text = element.text[0], element.text[1:]
            previous = element.getprevious()
            if previous is not None:
                previous.tail = (previous.tail or "") + moved
            else:
                parent.text = (parent.text or "") + moved
        # Only when the element's last node is its own text, so a nested tag's trailing
        # character is left to its own turn through the loop.
        while closes_badly and len(element) == 0 and element.text and _EMPHASIS_EDGE.fullmatch(element.text[-1]):
            moved, element.text = element.text[-1], element.text[:-1]
            element.tail = moved + (element.tail or "")
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _unwrap_numeric_links(html_text: str) -> str:
    """Unwrap a link whose whole visible label is a number, keeping the number.

    `html.py` deletes bracketed citation markers with `_NUMERIC_CITATION_RE`, and it
    does so on the converted markdown - by which point markdownify has already turned
    `<a href="url">4</a>` into `[4](url)`. The regex eats the `[4]` and leaves a bare
    `(url)`, so a publisher's cross-reference to their own recommendations 3, 4 and 5
    reaches the corpus as `see Recommendations Nos. (https://…),(https://…)`. The
    number, which is the content, is gone, and what replaces it is not even a link.
    Removing the anchor here means there is no `[4](` left for that rule to find, and
    it still deletes real `[4]` citation markers, which is what it is for.

    The link's address is lost with the anchor, and that is the deliberate trade: in
    this corpus the number resolves to nothing anyway. 11 documents point at
    "Recommendation No. N" in prose, 16 number their recommendation headings, and the
    two sets do not overlap at all - the renderer labels recommendations by strength
    and certainty, never by number. So the address buys navigation nobody can follow,
    while the digit is what the publisher's sentence needs to read correctly.

    Only a label that is entirely digits is unwrapped: `4x` and ordinary link text are
    untouched, because `_NUMERIC_CITATION_RE` never matches those.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if "<a" not in html_text:
        return html_text
    return _NUMERIC_LINK_RE.sub(r"\1", html_text)


def _drop_citations(html_text: str) -> str:
    """Remove inline reference markers, taking the whitespace that was holding them.

    The shared `html.py` already deletes `[3]`-shaped markers, but it deletes them from
    the converted markdown, where the element that carried them no longer exists. Two
    things survive that the reader then sees:

    * the separator. `data, respectively&nbsp;<cite>[3]</cite>.` converts to
      `data, respectively [3].` and strips to `data, respectively .` - 293 stranded
      spaces before punctuation in the WHO malaria guideline alone.
    * the formatting. A reference the publisher typed by hand as `<i>[66]</i>` converts
      to `*[66]*` and strips to `**` - an unpaired bold marker sitting mid-sentence,
      which pairs with the next one and bolds everything between them.

    Removing the element here, before conversion, avoids both: the marker and its
    formatting go together, and `_rejoin_across_citation` decides what happens to the
    space. Confirmed against WHO's own published PDF of this guideline, where each of
    these positions carries a numbered citation.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if _CITATION_CLASS not in html_text and "[" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")

    doomed = [
        _citation_root(element)
        for element in root.iter()
        if isinstance(element.tag, str)
        and (_CITATION_CLASS in (element.get("class") or "").split() or _is_manual_citation(element))
    ]
    emptied: list[Any] = []
    doomed_set = {id(element) for element in doomed}
    for element in doomed:
        parent = element.getparent()
        if parent is None:
            continue
        # The separator between two adjacent markers goes with them. WHO writes
        # `annually&nbsp;<cite>[5]</cite>,<cite>[6]</cite>.`, and keeping that comma once
        # both markers are gone ends the sentence `annually,.` - 294 of these across 41
        # guidelines. It is punctuation belonging to the citation apparatus, not to the
        # prose, which is provable from its position: strictly between the two elements.
        # Only that position is touched, so a comma the publisher wrote is never at risk.
        separator = element.tail or ""
        following = element.getnext()
        if separator.strip() in _CITATION_SEPARATORS and following is not None and id(following) in doomed_set:
            element.tail = ""
        # What follows the marker is usually its tail, but a publisher may put the
        # sentence's full stop in a sibling element instead - WHO writes
        # `causes&nbsp;<cite/><cite/><span style="color:black">.&nbsp;</span>The`. Reading
        # only the tail leaves the separating space stranded in front of that full stop,
        # and reading the sibling wrongly would weld "Formulary" to a following
        # `<sup>12</sup>`, so what matters is the first character either way.
        tail = element.tail or ""
        lookahead = tail[:1] or _leading_text(element.getnext())
        previous = element.getprevious()
        rejoined = _rejoin_across_citation(
            (previous.tail if previous is not None else parent.text) or "", tail, lookahead
        )
        if previous is not None:
            previous.tail = rejoined
        else:
            parent.text = rejoined
        parent.remove(element)
        emptied.append(parent)

    # A cell holding nothing but a list of reference markers - which is what the
    # "References" column of an evidence-summary table is - keeps the commas that
    # separated them once the markers go, so it renders as ",,,,,,,,,". Cancer Council
    # Australia writes every such column as `<strong><sup>[3]</sup>, <sup>[26]</sup>,
    # ...</strong>`, and 228 cells across 6 guidelines came out that way. The commas
    # never said anything on their own, so a run of them is cleared.
    for parent in emptied:
        if _ORPHANED_SEPARATORS_RE.fullmatch(parent.text_content() or ""):
            for child in list(parent):
                parent.remove(child)
            parent.text = None

    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _drop_embedded_images(html_text: str) -> str:
    """Remove `<img>` tags before conversion, so no image markup reaches the corpus.

    Most figures are stored inline as base64 data (100,361 of 101,955 `<img>` tags
    across the catalogue; 1,587 embed by URL, 172 documents) - converted normally,
    each inline one becomes a markdown image whose "address" is the entire encoded
    blob, and the encoding was 53.9% of everything the scraper emitted. All are
    dropped alike, alt text included - a deliberate call; the alt texts are visual
    descriptions ("Green, gold and blue-coloured devices with loops"). Ordinary
    `<a>` links to image files are untouched: links, not embedded figures.

    Textual removal, not a parser pass: `<img>` is a void element (0 `</img>` in the
    catalogue, and every one of the 101,955 tags matches the pattern to its real
    end), and a parse-and-serialize round trip re-encodes unrelated attributes in
    the same fragment - percent-escaping publishers' non-ASCII anchor hrefs.

    Runs immediately before conversion, so every earlier pass sees the document
    exactly as the publisher wrote it - dropping images earlier would, for example,
    leave a wrapper tag newly empty for the blank-tag normalization to mangle.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if not _IMG_OPEN_RE.search(html_text):
        return html_text
    return _IMG_TAG_RE.sub("", html_text)


def _rebase_headings(html_text: str, base_level: int) -> str:
    """Move a publisher's own headings underneath the heading that contains them.

    MAGICapp gives a tree of sections carrying no heading levels at all, so a level has
    to be chosen for each - it comes from how deep the section sits. The *body* of a
    section then carries HTML the publisher wrote in a rich-text editor, with its own
    `<h4>` tags chosen with no knowledge of where the section ended up. Below depth
    three the two collide: ours are `#####` and `######` while theirs stay `####`, so a
    recommendation's own fields are tagged as structurally *above* the recommendation.
    1,594 recommendation headings across the corpus are immediately followed by a
    shallower one.

    Ben's NICE scraper has no such problem and never computes a level: NICE publishes
    well-formed nested chapters, so passing them through gives a clean hierarchy -
    `## Recommendations` / `### 1.1 Identifying risk` / `#### Full formal risk
    assessment` / `##### 1.1.7`, with 0 inversions in 181 headings. This makes MAGICapp
    output the same shape, so both sources read the same way in one corpus.

    The publisher's *relative* nesting is kept: the shallowest heading in the fragment
    becomes one level below `base_level` and the rest keep their offsets from it. So a
    body running h4 → h5 under a level-3 section becomes `####` → `#####`, and one
    running h2 → h3 in the same place becomes exactly the same - what the publisher
    chose as their starting number carries no meaning, only their structure does.

    Args:
        html_text: HTML fragment from the guideline JSON.
        base_level: Markdown level of the heading this fragment sits under.
    """
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    headings = [element for element in root.iter() if isinstance(element.tag, str) and element.tag in _HEADING_TAGS]
    if not headings:
        return html_text
    shallowest = min(int(element.tag[1]) for element in headings)
    for element in headings:
        offset = int(element.tag[1]) - shallowest
        # Markdown stops at six, so a deep section with deeply nested body headings
        # flattens rather than overflowing.
        element.tag = f"h{min(base_level + 1 + offset, _MAX_HEADING_LEVEL)}"
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _expand_table_spans(html_text: str) -> str:
    """Give every table row its full column count, by filling in spanned cells.

    Markdown has no way to express a cell that spans columns or rows, so a spanned cell
    converts to one ordinary cell and the row comes out short. Every cell after it then
    shifts left, and a header stops sitting over the numbers it labels.

    The damage is not that a number is wrong - it is that a correct number acquires the
    wrong label, which reads as true rather than as missing. In a GRADE
    diagnostic-accuracy table in the WHO malaria guideline the headers "Prevalence of
    5%", "of 10%" and "of 20%" span the last three of nine columns, over the
    true-positive and false-positive counts; flattened, they land under Threshold,
    Studies and Participants instead.

    A spanned cell becomes itself plus the blank cells it was standing in for, so the
    row regains its width and every later cell returns to its own column. 3,949 spanned
    cells across 90 of 228 English guidelines.

    The two span kinds have to be resolved together, walking the row column by column.
    Resolving them separately puts a row-span filler at the wrong place whenever an
    earlier cell in the same row also spans columns, because the filler's *column*
    is not its *position* in a row whose column spans have not been expanded yet. In
    the WHO antibiotic-prophylaxis dosing table (`Eg947L` Annex 4) a row opens with a
    cell spanning the three left-hand columns and a "VS" divider spans it from seven
    rows above: the filler landed at position 3, one place before the row's last cell
    instead of at column 4, and the row came out `|  |  |  | Mezlocillin | 2 g single
    dose |  | 32 |` - reading as though the comparator drug were "2 g single dose",
    given at no stated dose, to 32 women. 75 such cells across 8 guidelines.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if "span=" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for table in root.iter("table"):
        # Columns a cell from an earlier row still occupies: {column: rows remaining}.
        carried: dict[int, int] = {}
        for row in table.iter("tr"):
            cells = [cell for cell in row if isinstance(cell.tag, str) and cell.tag in {"td", "th"}]
            if not cells:
                continue
            # Each covered column is consumed by this row and, if the span reaches
            # further, kept for the next one.
            covered = carried
            carried = {column: remaining - 1 for column, remaining in covered.items() if remaining > 1}
            filler_tag = cells[0].tag

            rebuilt: list[Any] = []
            column = 0
            for cell in cells:
                # Step over the columns an earlier row's cell is still occupying, so
                # this cell lands in the first column actually free for it.
                while column in covered:
                    rebuilt.append(lxml_html.Element(filler_tag))
                    column += 1
                colspan = _span_value(cell, "colspan")
                rowspan = _span_value(cell, "rowspan")
                rebuilt.append(cell)
                rebuilt.extend(lxml_html.Element(cell.tag) for _ in range(colspan - 1))
                if rowspan > 1:
                    for offset in range(colspan):
                        carried[column + offset] = rowspan - 1
                column += colspan

            # Columns still covered past the row's last cell, and any gap before them.
            while covered and column <= max(covered):
                rebuilt.append(lxml_html.Element(filler_tag))
                column += 1

            for child in cells:
                row.remove(child)
            for index, cell in enumerate(rebuilt):
                row.insert(index, cell)
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _span_value(cell: Any, attribute: str) -> int:
    """Read a colspan or rowspan attribute, treating anything unusable as 1.

    Args:
        cell: A `td` or `th` element.
        attribute: Either "colspan" or "rowspan".
    """
    try:
        value = int(cell.get(attribute) or 1)
    except ValueError:
        return 1
    cell.attrib.pop(attribute, None)
    # A publisher typo of 200 would otherwise produce a 200-column row.
    return value if 1 <= value <= _MAX_TABLE_SPAN else 1


def _label_text(element: Any) -> str:
    """Return an element's text as one line, or "" for comments and processing nodes."""
    if not isinstance(element.tag, str):
        return ""
    return " ".join((element.text_content() or "").split())


def _paperwork_label(text: str) -> bool:
    """Report whether a line is one of the labels publishers put over their paperwork."""
    return _skip_lookup_key(text) in _INLINE_PAPERWORK_LABELS


def _opens_with_paperwork_label(element: Any) -> bool:
    """Report whether a paragraph is a paperwork label and its value on one line.

    The block rule above needs a label with a line to itself, because it removes the
    paragraphs that follow it. WHO does not always oblige: `<p><strong>Funding</strong>:
    WHO.</p>` puts the label and the answer in one paragraph with only the label bolded,
    so the whole-line-bold test fails and the block survives - four guidelines carried a
    visible "Funding: WHO." into the corpus under a label the list already knew.

    Matched only when the paragraph *begins* with the bolded label and a colon follows it
    immediately, so a sentence that merely mentions funding is untouched, and only that
    one paragraph is removed.

    Args:
        element: An element from a parsed section body.
    """
    if element.tag != "p" or len(element) == 0 or (element.text or "").strip():
        return False
    opener = element[0]
    if not isinstance(opener.tag, str) or opener.tag not in _EMPHASIS_TAGS:
        return False
    if not (opener.tail or "").lstrip().startswith(":"):
        return False
    label = " ".join((opener.text_content() or "").split()).rstrip(":")
    return bool(label) and _paperwork_label(label)


def _is_inline_heading(element: Any) -> bool:
    """Report whether an element is a line the publisher is using as a heading.

    Either a real heading tag, or a short paragraph whose whole text is bold or
    underlined. Length-capped because a bolded sentence is emphasis, not a heading.

    Args:
        element: An element from a parsed section body.
    """
    text = _label_text(element)
    if not text or len(text) > _MAX_INLINE_HEADING_CHARS:
        return False
    if element.tag in _HEADING_TAGS:
        return True
    if element.tag != "p":
        return False
    marked = " ".join(
        "".join(
            part.text_content() or ""
            for part in element.iter()
            if isinstance(part.tag, str) and part.tag in _EMPHASIS_TAGS
        ).split()
    )
    return bool(marked) and len(marked) >= len(text) - 2


def _identity(html_text: str) -> str:
    """Return the fragment untouched - the heading path's stand-in for a removal pass."""
    return html_text


def _drop_inline_paperwork(html_text: str) -> str:
    """Remove paperwork the publisher wrote inside a section it also uses for guidance.

    The [[Skip List]] can only drop a whole section, so paperwork written into the
    body of a clinical section is out of its reach. Three shapes are removed, all
    observed and all keyed on the publisher's own labels:

    * an "Authorship:" line followed by a table of panel members - the table is
      names, employers and specialties, and it sits directly inside recommendation
      sections such as "Initial DMARD therapy for people with rheumatoid arthritis",
      so dropping the section would take the recommendation with it;
    * a bold line naming paperwork ("Funding", "WHO Steering Group", "Disclaimer")
      followed by its paragraphs, up to the next such line;
    * the same, where the publisher separates the blocks with <br> inside a single
      paragraph rather than starting a new one - which is how ANZMUSC writes its
      whole introduction.

    Only labels in `_INLINE_PAPERWORK_LABELS` are matched, and removal always stops
    at the next label, so a mistake costs one block rather than the rest of the
    section.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if not _INLINE_PAPERWORK_HINT_RE.search(html_text):
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")

    doomed: list[Any] = []
    children = [child for child in root if isinstance(child.tag, str)]
    for index, child in enumerate(children):
        text = _label_text(child)
        if _AUTHORSHIP_MARKER_RE.match(text) and len(text) <= _MAX_AUTHORSHIP_LABEL_CHARS:
            following = next(
                (later for later in children[index + 1 :] if _label_text(later) or later.tag in _TABLE_TAGS),
                None,
            )
            if following is not None and following.tag in _TABLE_TAGS:
                doomed.extend((child, following))
            continue
        if _opens_with_paperwork_label(child):
            doomed.append(child)
            continue
        # A plain paragraph whose whole text is a paperwork label. `_is_inline_heading`
        # requires the line to be bold or underlined, and not every publisher marks it up.
        #
        # Only safe now that `_plain_heading` no longer runs the removal passes. Before that
        # it was tried twice and both times the corpus grew: this rule also matched the
        # HEADING "Authors", `_plain_heading` returned "", the heading stopped matching the
        # skip list, and the whole section came back - 73,006 characters in j9QY4j alone.
        if not _is_inline_heading(child):
            if not _paperwork_label(text):
                continue
            doomed.append(child)
            following = next((later for later in children[index + 1 :] if _label_text(later)), None)
            if following is not None and not _is_inline_heading(following):
                doomed.append(following)
            continue
        if not _paperwork_label(text):
            continue
        doomed.append(child)
        for later in children[index + 1 :]:
            if _is_inline_heading(later):
                break
            doomed.append(later)

    for element in doomed:
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)

    for paragraph in root.iter("p"):
        _drop_paperwork_runs(paragraph)

    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _drop_paperwork_runs(paragraph: Any) -> None:
    """Remove <br>-separated runs inside one paragraph that a paperwork label opens.

    Some publishers write a whole introduction as a single paragraph, with bold
    labels and <br> where another would start new paragraphs, so element-level
    removal cannot see the blocks. The paragraph is split on <br>, each run is
    judged by the label that opens it, and only doomed runs are removed - the rest
    of the paragraph, and any text before the first run, is left untouched.

    Args:
        paragraph: A <p> element from a parsed section body.
    """
    if not any(isinstance(child.tag, str) and child.tag == "br" for child in paragraph):
        return

    # A run is the text after one <br> plus the elements up to the next one. The prose
    # lives in the <br>'s tail rather than in any element, so a run has to be described
    # by where its text is stored, not by its elements alone.
    runs: list[dict[str, Any]] = [{"owner": paragraph, "attribute": "text", "nodes": []}]
    for child in paragraph:
        if isinstance(child.tag, str) and child.tag == "br":
            runs.append({"owner": child, "attribute": "tail", "nodes": [child]})
        else:
            runs[-1]["nodes"].append(child)

    dropping = False
    authorship_doomed = False
    doomed_runs: list[dict[str, Any]] = []
    trailing_text: list[str] = []
    for run in runs:
        own_text = getattr(run["owner"], run["attribute"]) or ""
        text = " ".join(
            (
                own_text + "".join(_label_text(node) + (node.tail or "") for node in run["nodes"] if node.tag != "br")
            ).split()
        )
        opener = next((node for node in run["nodes"] if isinstance(node.tag, str) and node.tag in _EMPHASIS_TAGS), None)
        label = _label_text(opener) if opener is not None else ""
        if _AUTHORSHIP_MARKER_RE.match(text) and len(text) <= _MAX_AUTHORSHIP_LABEL_CHARS:
            dropping = True
            authorship_doomed = True
        elif label and len(label) <= _MAX_INLINE_HEADING_CHARS:
            # Only a label decides; a run without one is body text belonging to
            # whichever label came before it, so it inherits that verdict.
            dropping = _paperwork_label(label)
        if dropping:
            doomed_runs.append(run)
            trailing_text.clear()
        elif text:
            trailing_text.append(text)

    if not doomed_runs:
        return

    # An authorship label introduces the table that follows the paragraph, so when
    # nothing but whitespace separates the label from the end of the paragraph, the
    # table goes with it.
    if authorship_doomed and not trailing_text:
        following = paragraph.getnext()
        if following is not None and isinstance(following.tag, str) and following.tag in _TABLE_TAGS:
            following.getparent().remove(following)

    for run in doomed_runs:
        setattr(run["owner"], run["attribute"], None)
        for node in run["nodes"]:
            node.tail = None
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)


def _label_table_recommendations(html_text: str) -> str:
    """Give a recommendation written as a table a heading made from its own label.

    Cancer Council Australia writes recommendations as tables - a label cell
    ("Evidence-based recommendation", "Practice point"), optionally a Grade column,
    then the statement in the rows below - with nothing in the data marking them as
    recommendations, so anything that finds recommendations by their headings walks
    past all of them. The label cell already speaks `_LABEL_OPENERS`; it becomes an
    <h4> before the table, and `_rebase_headings` nests it under the containing
    section like any publisher heading. The table itself is untouched. The glossary
    table that defines the labels opens "Type of recommendation", which does not
    match the vocabulary, so it self-excludes.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if "<table" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for table in list(root.iter("table")):
        rows = table.findall(".//tr")
        if not rows:
            continue
        cells = rows[0].xpath(".//td | .//th")
        if not cells:
            continue
        label = " ".join(cells[0].text_content().split())
        if not label or len(label) >= 60 or not _LABEL_OPENERS.match(label):
            continue
        # A [label, Grade] header row carries the grade in the next row's last cell.
        grade = ""
        if len(cells) >= 2 and " ".join(cells[1].text_content().split()).lower() == "grade" and len(rows) >= 2:
            tail_cells = rows[1].xpath(".//td | .//th")
            if tail_cells:
                value = " ".join(tail_cells[-1].text_content().split())
                if 0 < len(value) <= 8:
                    grade = value
        heading = table.makeelement("h4", {})
        heading.text = f"{label} (Grade {grade})" if grade else label
        table.addprevious(heading)
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _markdown(raw_html: Any, *, link_mode: LinkMode, base_level: int | None = None, as_heading: bool = False) -> str:
    """Convert a MAGICapp HTML fragment to markdown, dropping embedded images.

    MAGICapp inlines figures as base64 data URIs. Converted normally they become
    valid markdown images whose source is hundreds of thousands of characters of
    encoded PNG: measured over 63 guidelines they were 53.9% of everything this
    scraper emitted, and a single European Stroke Organisation guideline was 98.2%
    image data. Nothing downstream reads images, and 475 of 491 carried no alt
    text, so there is no caption to keep either.

    Args:
        raw_html: HTML fragment from the guideline JSON, or None.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
        base_level: Markdown level of the heading this fragment sits under. When set,
            the publisher's own headings are moved below it - see `_rebase_headings`.
            Left unset where a fragment has no place in the outline, such as the
            catalogue disclaimer or a heading being reduced to plain text
            (default: None).
        as_heading: Whether this fragment is a section heading rather than a body. A
            heading is being converted to plain text, not rendered, so every pass that
            removes non-clinical content is skipped for it.

            Without this, `_plain_heading` runs headings through the removal passes and
            any of them can erase one. A rule that took a plain paragraph reading
            "Authors" also took the heading "Authors" - and an emptied heading stops
            matching the skip list, so the whole section it named came back into the
            corpus, 73,006 characters of it in j9QY4j alone. (default: False)
    """
    source = _label_table_recommendations(
        _expand_table_spans(
            _drop_orphaned_captions(
                (_drop_inline_paperwork if not as_heading else _identity)(
                    _render_scripts(
                        _split_emphasis_across_breaks(
                            _drop_citations(
                                _unwrap_numeric_links(
                                    _trim_emphasis_edges(
                                        _merge_adjacent_emphasis(_drop_tracked_deletions(str(raw_html or "")))
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
    )
    # Blank tags nest ("<strong><em>&nbsp;</em></strong>") and edge whitespace moves
    # expose new blanks, so both substitutions run until the text stops changing. The
    # pass cap is a hang guard only; each pass strictly shrinks tags or moves
    # whitespace outward, so real fragments stabilize in a handful of passes.
    for _ in range(64):
        replaced = _ZERO_WIDTH_INLINE_TAG_RE.sub("", source)
        replaced = _BLANK_INLINE_TAG_RE.sub(" ", replaced)
        replaced = _SPACE_AFTER_OPEN_RE.sub(r"\2\1", replaced)
        replaced = _SPACE_BEFORE_CLOSE_RE.sub(r"\2\1", replaced)
        if replaced == source:
            break
        source = replaced
    else:
        logger.debug("Inline-tag normalization did not stabilize within 64 passes")
    if base_level is not None:
        source = _rebase_headings(source, base_level)
    markdown = html_to_markdown(_drop_embedded_images(source), link_mode=link_mode)
    # Publishers leave headings whose text is empty, which convert to a bare "##".
    markdown = _WIKI_CHROME_RE.sub("", _EMPTY_HEADING_RE.sub("", markdown))
    # Zero-width characters are invisible to a reader and break every rule that anchors at
    # the start of a sentence. Edr04L opens 505 of them across the corpus, and one at the
    # head of a block kept "The evidence analyses and risk of bias assessments for this
    # intervention can be found here" - a sentence the cross-reference rule matches on every
    # other count. 54 blocks start with one.
    #
    # Replaced with a space rather than deleted. Publishers use them as separators as well as
    # decoration, and deleting one glued "found" to its link - "can be found[here.]" - so the
    # visible text read "found here" with no gap and the wording stopped matching, which is
    # the same failure in a new place.
    markdown = _ZERO_WIDTH_CHAR_RE.sub(" ", markdown)
    markdown = _strip_question_numbers_from_headings(markdown)
    if not as_heading:
        markdown = _drop_pointer_blocks(markdown)
        # Before the directory rule, not after it. Both can claim the same list, and this one
        # takes the bold label above it as well - so if the other runs first the list disappears
        # and the label is left announcing content that is no longer there.
        markdown = _drop_resource_directories(markdown)
        markdown = _drop_link_directories(markdown)
        markdown = _drop_resource_lines(markdown)
        markdown = _drop_cross_reference_sentences(markdown)
        markdown = _drop_approval_stamps(markdown)
        markdown = _drop_source_credits(markdown)
        markdown = _drop_number_only_captions(markdown)
        markdown = _drop_search_method_blocks(markdown)
        markdown = _drop_review_citations(markdown)
        markdown = _drop_keyword_blocks(markdown)
        markdown = _drop_inline_appendices(markdown)
        markdown = _drop_citation_blocks(markdown)
        markdown = _drop_publication_announcements(markdown)
        markdown = _drop_bibliography_blocks(markdown)
        markdown = _drop_dateline_blocks(markdown)
        markdown = _drop_document_link_lists(markdown)
    markdown = _drop_grade_circles(markdown)
    # Removing a line leaves the blank line that was under it, so paragraphs drift apart.
    markdown = _BLANK_RUN_RE.sub("\\n\\n", markdown)
    return _ACCIDENTAL_SETEXT_RE.sub(r"\g<text>\n\\\g<rule>", markdown).strip()


_LINK_ONLY_ITEM_RE = re.compile(r"\A\s*[-*]\s+(?:<https?://\S+>|\[[^\]]*\]\(\S+\)|https?://\S+)\s*\Z")
_LIST_ITEM_RE = re.compile(r"\A\s*[-*]\s+")
# Two, because the atomic unit of these directories is one pair - a resource named on one
# line and its address indented beneath. jW0ZbL carries single pairs as well as long runs:
# "Lochia. A guide for lay people, including visual representations of each stage" with a
# Cleveland Clinic URL under it.
_MIN_LINK_DIRECTORY_ITEMS = 2
# What a directory entry never does is give advice. Guarding on the parent lines - the ones
# that label a link rather than being one - keeps the rule off a recommendation that happens
# to carry its source as a nested link. No block in the corpus trips this today; it is here
# so that lowering the minimum to two cannot quietly start deleting guidance later.
_DIRECTORY_LABEL_EXCLUSION_RE = re.compile(
    r"\b(?:should|recommend|we suggest|offer|must|advise|first-line)\b", re.IGNORECASE
)
# More than half the LEAF items. Measured on leaves rather than every line, a real
# directory sits well above this and a clinical list well below.
_MIN_LINK_DIRECTORY_SHARE = 0.5
# What is left of a reference entry once its addresses are taken out: a title, and nothing
# that could be read as advice. "Vaccination for Aboriginal and Torres Strait Islander
# people" is 58 characters and asserts nothing; "Pregnancy, Birth and Baby. Religious
# fasting - pregnancy and breastfeeding" is 73. Requiring the item to BE a link, as this rule
# first did, counted almost none of these - jW0ZbL's immunisation index has eleven entries
# and only two are bare addresses.
_MAX_REFERENCE_LABEL_CHARS = 140


def _is_reference_item(item: str) -> bool:
    """Report whether one list item is a citation: a title, an address, and nothing else.

    Args:
        item: One list item, including any continuation lines hanging off it.
    """
    if "http" not in item and "www." not in item:
        return False
    remainder = _LINK_TARGET_RE.sub(" ", item)
    remainder = " ".join(re.sub(r"\A\s*[-*]\s+", "", remainder).replace("[", " ").replace("]", " ").split())
    if len(remainder) > _MAX_REFERENCE_LABEL_CHARS:
        return False
    return not (_SENTENCE_CLAIM_RE.search(remainder) or _MEASUREMENT_RE.search(remainder))


_RESOURCE_LINE_RE = re.compile(
    r"\A\s*(?:[-*]\s+)?(?:websites?|web\s+address(?:es)?|online\s+resources?|useful\s+links?|"
    r"further\s+reading|resources?)\s*:",
    re.IGNORECASE,
)
_AVAILABLE_AT_RE = re.compile(r"\bavailable\s+(?:at|from|online\s+at)\b", re.IGNORECASE)


def _drop_resource_lines(markdown: str) -> str:
    """Remove a line that is a signpost to somewhere else on the web.

    Two shapes the directory rule cannot see, because neither is a nested pair. A line that
    opens with a resource label - "Website: cope.org.au", "Websites: <url> and <url>" - and a
    citation-style item that ends in an address: "Centre of Perinatal Excellence. National
    perinatal mental health guideline: available at <url>."

    Both name where something lives and assert nothing about a patient. A "Website:" line
    qualifies whether or not it carries a full address: publishers write bare domains
    ("Website: cope.org.au") and even bare names ("Website: ForWhen").

    A resource entry is not just its address line. jm83RE lists perinatal mental health
    services as a bolded name, a paragraph describing the service, then the address - so
    matching the address alone would leave the description stranded. From each address line
    this walks back over the entry and removes it whole, stopping at the bolded name that
    opens it. It aborts rather than guesses if it meets a heading first, and never crosses
    more than one entry's worth of lines, so the clinical prose above the block is safe.

    Only leaf lines go: an item with something nested beneath it is a heading for that
    content, and removing it would orphan what follows.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    lines = markdown.split("\n")

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    def visible_text(line: str) -> str:
        return " ".join(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line).split())

    doomed: set[int] = set()
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        following = next((lines[later] for later in range(number + 1, len(lines)) if lines[later].strip()), "")
        if _LIST_ITEM_RE.match(line) and _LIST_ITEM_RE.match(following) and indent(following) > indent(line):
            continue
        visible = visible_text(line)
        labelled = _RESOURCE_LINE_RE.match(visible)
        cited = _AVAILABLE_AT_RE.search(visible) and ("http" in line or "www." in line)
        if not labelled and not cited:
            continue
        if _MEASUREMENT_RE.search(visible) and not labelled:
            continue
        doomed.add(number)
        if not labelled:
            continue
        # Walk back over the entry this address belongs to, stopping at the bolded name
        # that opens it. A heading means we have left the block, so nothing is taken.
        span: list[int] = []
        for earlier in range(number - 1, max(-1, number - 9), -1):
            text = lines[earlier].strip()
            if not text:
                continue
            if text.startswith("#") or _LIST_ITEM_RE.match(lines[earlier]):
                span = []
                break
            span.append(earlier)
            if _WHOLE_LINE_BOLD_RE.fullmatch(text):
                break
        else:
            span = []
        doomed.update(span)
    if not doomed:
        return markdown
    return "\n".join(line for number, line in enumerate(lines) if number not in doomed)


def _drop_link_directories(markdown: str) -> str:
    """Remove a list that is a directory of other organisations' resources.

    Publishers append these to clinical sections: jW0ZbL ends its breastfeeding advice with
    a nested list of thirteen organisations and their web addresses - Pregnancy Birth and
    Baby, the Australian Breastfeeding Association, a helpline number, a WHO publication
    URL. Nothing in it could be the sentence a verdict rests on; it is a phone book. The
    skip list cannot reach them, because they sit inside a section carrying real guidance.

    Judged on the **leaf** items, not on the whole run. These lists nest - an organisation
    on one line, its address indented beneath, sometimes a phone number beside it - so
    counting every line dilutes the links below any useful threshold: the jW0ZbL directory
    is a third links by line and over half by leaf. A leaf is an item with nothing indented
    under it, which is where an address sits and where a clinical statement would sit too.

    A run needs at least four items and more than half its leaves to be nothing but a link.
    A recommendation carrying a citation never qualifies, because the whole leaf has to be
    the link and nothing else.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "http" not in markdown:
        return markdown
    lines = markdown.split("\n")
    doomed: set[int] = set()
    index = 0
    while index < len(lines):
        if not _LIST_ITEM_RE.match(lines[index]):
            index += 1
            continue
        end = index
        # An indented line that is not a bullet continues the item above it. jW0ZbL's
        # immunisation index puts every address on its own continuation line - "Vaccination
        # for women who are planning pregnancy, pregnant or breastfeeding" and then the URL
        # underneath - and treating that as the end of the run stopped the scan at the first
        # entry, so a 20-line index was never weighed at all.
        while end < len(lines) and (
            _LIST_ITEM_RE.match(lines[end])
            or not lines[end].strip()
            or (lines[end][:1].isspace() and lines[end].strip())
        ):
            end += 1
        items = [number for number in range(index, end) if _LIST_ITEM_RE.match(lines[number])]
        if not items:
            index = end if end > index else index + 1
            continue
        # Everything from an item up to the next one is that item: its own line plus any
        # continuation lines hanging off it.
        spans = {
            number: "\n".join(lines[number : (items[position + 1] if position + 1 < len(items) else end)])
            for position, number in enumerate(items)
        }
        # A leaf is an item nothing is nested under: the next item is no deeper than it.
        leaves = [
            number
            for position, number in enumerate(items)
            if position + 1 >= len(items)
            or len(lines[items[position + 1]]) - len(lines[items[position + 1]].lstrip())
            <= len(lines[number]) - len(lines[number].lstrip())
        ]
        if len(items) >= _MIN_LINK_DIRECTORY_ITEMS and leaves:
            links = sum(1 for number in leaves if _is_reference_item(spans[number]))
            # Every item, not only the parent labels. A run is removed whole, so a clinical
            # bullet sitting among reference entries goes with them however the ratio was
            # reached: three WHO recommendations in noPQkE - among them "Infant and young
            # child feeding counselling is recommended for mothers/caregivers of all infants
            # and young children" - are leaves in a run whose other leaves are source links.
            labels = [spans[number] for number in items]
            # Applies to every run, however long. It was once limited to short ones, because
            # a single label reading "should" had shielded a 31-entry directory in jW0ZbL.
            # That exception cannot stay now that a reference entry no longer has to BE a
            # link: widening it tipped noPQkE's WHO feeding chapter over the threshold, where
            # each recommendation - "Feeding assessments should include the following domains:
            # infant and mother/caregiver health status..." - is a parent bullet with the WHO
            # source documents listed under it. Fourteen recommendations went with the links.
            # The directory the exception was written for is now taken by
            # `_drop_resource_directories`, which reads the bold label above the list rather
            # than the bullets inside it.
            # Advice only, not measurements. A number no longer needs to shield a run,
            # because `_is_reference_item` already refuses to count an entry carrying one, so
            # a list of dosing links cannot reach the ratio in the first place. Testing for
            # one here did the opposite of its job: jW0ZbL indexes fifteen screening
            # instruments as name-plus-address pairs, and the single entry reading "PANDA:
            # Provides free perinatal mental health resources and a helpline, during
            # pregnancy and up to 12 months after birth" spared the whole index.
            advises = any(_DIRECTORY_LABEL_EXCLUSION_RE.search(label) for label in labels)
            if not advises and links / len(leaves) > _MIN_LINK_DIRECTORY_SHARE:
                doomed.update(range(index, end))
        index = end
    if not doomed:
        return markdown
    # No "would this empty the fragment" guard, unlike the pointer rule. These directories
    # are often a subsection's entire body, so refusing to empty one is refusing to remove
    # it at all - which is exactly what happened to jW0ZbL's breastfeeding directory. An
    # emptied body is handled a level up: a section left with no body and no children
    # emits nothing, so the heading goes with it.
    return "\n".join(line for number, line in enumerate(lines) if number not in doomed)


def _drop_pointer_blocks(markdown: str) -> str:
    """Remove a rendered paragraph whose whole content is one sentence pointing elsewhere.

    `_is_pointer_only` applies this test to a whole section. Publishers also write the same
    sentence beside real guidance, where that rule cannot reach it - QnoKGn ends a list of
    dysphagia practice points with "Please also refer to the topic Early Nutrition in
    Managing Complications", and the Stroke Foundation guidelines carry "Refer to the
    document on InformMe for information regarding...".

    Done on the rendered markdown rather than the HTML, because the two shapes publishers
    use are only the same thing after conversion: sometimes the sentence is a `<p>`, and
    sometimes - as in QnoKGn - it is loose text sitting after a `</ul>` with an `<a>` in the
    middle of it, which is not an element at all and cannot be matched as one.

    Three guards, the first two shared with the section rule. A length cap. A measurement
    test, because a sentence carrying a number is making a claim whatever it opens with -
    and only a real measurement counts, since "Section 5.2" is a section number and treating
    that as one would keep every pointer. And headings, list items, table rows and quotes
    are left alone: a bullet reading "see above" is part of the list's shape.

    If the pointers are the entire body, nothing is removed - that is `_is_pointer_only`'s
    case, and it drops the heading along with the sentence, where emptying the body here
    would leave a heading with nothing under it.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "\n\n" not in markdown and not _POINTER_ONLY_RE.match(markdown.strip()):
        return markdown
    blocks = markdown.split("\n\n")
    kept, dropped_any = [], False
    for block in blocks:
        stripped = block.strip()
        visible = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", stripped)
        visible = " ".join(visible.split())
        if (
            visible
            and not stripped.startswith(("#", "|", "-", "*", ">", "    "))
            and len(visible) <= _MAX_POINTER_CHARS
            and _POINTER_ONLY_RE.match(visible)
            and not _MEASUREMENT_RE.search(visible)
        ):
            dropped_any = True
            continue
        kept.append(block)
    if not dropped_any:
        return markdown
    remainder = "\n\n".join(kept)
    if not remainder.strip():
        return markdown
    return remainder


# Link targets are masked before sentences are split, because a URL is full of full stops
# and would otherwise be cut into pieces. Longest form first: an inline link's target, then
# an autolink, then a bare address.
_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)|<https?://[^>]*>|https?://\S+")
_MASK_RE = re.compile("\x00(\\d+)\x00")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(*‘“])")
# Only a whole, well-formed sentence is eligible to be dropped. A fragment left by a bad
# split - "e.g." and "et al." both end in a full stop - fails this and is kept.
#
# A sentence that ends the block may stop without punctuation, and publishers do that
# constantly when the last thing in it is a web address: "Details about the availability of
# medicines on the Pharmaceutical Benefits Scheme can be found at www.pbs.gov.au/" has no
# full stop, and neither does the AIHW report line in the same guideline. Requiring one kept
# both. Only the final sentence gets the exemption; a mid-block fragment still needs it.
_SENTENCE_SHAPE_RE = re.compile(r"\A[A-Z\[(*‘“].*[.!?]\Z", re.DOTALL)
_FINAL_SENTENCE_SHAPE_RE = re.compile(r"\A[A-Z\[(*‘“].*(?:[.!?]|\S)\Z", re.DOTALL)
# Wording that makes a sentence a cross-reference: it says the subject is handled in some
# other document. `_POINTER_ONLY_RE` cannot see these, because it anchors at the start of the
# text and these arrive mid-paragraph, after a clause that does assert something.
_CROSS_REFERENCE_RE = re.compile(
    r"\b(?:is|are|was|were)\s+(?:covered|addressed|described|discussed|dealt\s+with|based)\s+(?:by|in|on)\b"
    r"|\b(?:is|are)\s+contained\s+(?:within|in)\b"
    r"|\bcan\s+be\s+(?:found|accessed|downloaded|obtained|viewed)\b"
    r"|\bavailable\s+(?:here|at|from|online)\b"
    # The same sentence about a web page rather than a document: "The ANZ HHC's home page is
    # located HERE." One in the corpus, and it is the last thing standing between nV6X3n's
    # "Summary of the ANZ Living Guidelines" and being dropped as pointer-only.
    r"|\bis\s+located\s+(?:here|at)\b|\bcan\s+be\s+(?:reached|visited)\s+(?:here|at)\b"
    # "A summary of recommendations is available as supplemental content (Supplemental Table
    # I, <url>)." The document being pointed at is the journal's supplement, not this one.
    r"|\bis\s+available\s+as\s+(?:supplemental|supplementary|an?\s+appendix)\b"
    r"|\b(?:please\s+)?refer\s+to\b"
    r"|\bfor\s+(?:further|more|additional)\s+(?:information|details?|guidance)\b"
    r"|\bfurther\s+(?:information|details?)\b"
    # The named document as the subject rather than the object: "The AIHW Report, Screening
    # for Domestic Violence During Pregnancy, provides information on the different screening
    # tools used in different States and Territories: <url>". Five in the corpus, every one a
    # cross-reference; the claim guard is what keeps it off "ultrasound provides information
    # about fetal growth", which carries no address anyway.
    r"|\bprovides?\s+(?:further\s+|more\s+|detailed\s+)?information\s+(?:on|about)\b",
    re.IGNORECASE,
)
# "As primary postpartum haemorrhage often occurs during the third stage of labour, it is not
# within scope of the LEAPP Postnatal Care Guidelines." Dropped only when it sits beside a
# cross-reference that was itself dropped, which is the shape it always takes: the disclaimer
# and the address of the document that does cover the subject are written as a pair. A scope
# statement standing on its own says who the guideline applies to, and is kept - that is what
# `_states_guideline_scope` protects at section level.
_SCOPE_DISCLAIMER_RE = re.compile(r"\b(?:not\s+within|outside)\s+(?:the\s+)?scope\s+of\b", re.IGNORECASE)
# A sentence that instructs or quantifies is making a claim, whatever else it also does, so
# it is kept even when it carries an address. Same principle as `_MEASUREMENT_RE` guarding
# the pointer rule, widened to the verbs a recommendation is written with.
#
# Verb forms only, spelled out rather than closed with `\w*`. A trailing `\w*` matches the
# nominalization too, and the nouns are exactly what a cross-reference is made of: "These
# recommendations ... are based on the NICE 2021 Postnatal Care Guidelines" was kept because
# `recommend\w*` matched the word "recommendations" in it.
_SENTENCE_CLAIM_RE = re.compile(
    r"\b(?:should|must|shall|recommend(?:s|ed|ing)?|suggest(?:s|ed|ing)?|advise(?:s|d)?|advising|"
    r"offer(?:s|ed|ing)?|consider(?:s|ed|ing)?|prescrib(?:e|es|ed|ing)|administer(?:s|ed|ing)?|"
    # "screen" is deliberately absent. It is the one verb here that is far more often a noun
    # in this corpus - "screening tools", "the National Cervical Screening Program", "The
    # AIHW Report, Screening for Domestic Violence During Pregnancy" - and every sentence it
    # was protecting turned out to be a signpost: 14 of them, among them six copies of "For
    # more information and resources regarding screening for X, see the NCSP toolkit". A
    # sentence that really does instruct screening carries another verb with it ("Offer
    # screening for...", "women should be screened"), and those are still caught.
    r"monitor(?:s|ed|ing)?|assess(?:es|ed|ing)?|ensure(?:s|d)?|ensuring|"
    r"avoid(?:s|ed|ing)?|contraindicat\w*)\b"
    r"|\b(?:RR|OR|HR)\s*[=:]|95%\s*CI|\bp\s*[<=]\s*0",
    re.IGNORECASE,
)

# Two shapes that name a document without giving its address, so the web-address test above
# cannot see them. Both need every one of the three conditions below, because each phrase on
# its own appears in sentences that must be kept.
#
# "Further guidance on substance use, nutrition and physical activity is available in Section
# 6 of these guidelines." "Further discussion on bereavement care can be found in this
# section." "Further resources are available in the COPE Australian Clinical Practice
# Guideline."
_DOCUMENT_REFERENCE_PHRASE_RE = re.compile(
    r"\bfurther\s+(?:guidance|resources?|discussion|reading|detail)\b"
    # "Advice on follow-up and retesting those with positive results is available in the
    # Australian STI Management Guidelines for Use in Primary Care." Anchored, because the
    # same opening introduces real advice mid-sentence. Of the 14 in the corpus the locating
    # test separates them exactly: 8 name a document, and the 6 it keeps are guidance -
    # "Advice on diet and lifestyle is recommended to prevent and relieve heartburn".
    r"|\Aadvice\s+on\b|\A(?:further|more)\s+advice\b"
    # A flowchart is a document like any other, and pointing at one is the same act as
    # pointing at a report - the picture did not survive the scrape either way. Kj2WZL says
    # "See the flowchart for the literature search under reference." seven times and points
    # at its dystocia flowcharts four more. Anchored on "see", so a sentence that describes
    # what a flowchart shows is untouched; the locating test still has to agree.
    r"|\A(?:see|refer to)\s+the\s+(?:flow\s?chart|diagram|algorithm)\b",
    re.IGNORECASE,
)
# It has to say where the thing lives. Without this the phrase alone matches "there is
# further guidance on what to do", which locates nothing.
_LOCATES_A_DOCUMENT_RE = re.compile(
    r"\b(?:is|are)\s+(?:available|provided|detailed|described|given|contained|included|set\s+out)\b"
    r"|\bcan\s+be\s+found\b|\b(?:please\s+)?refer\s+to\b|\A(?:for|see)\b"
    r"|\bprovides?\s+(?:some\s+)?further\s+(?:guidance|resources?|discussion|reading|detail)\b",
    re.IGNORECASE,
)
# And it must not be about guidance that does not exist yet. Nine sentences in the corpus
# report a qualitative finding - "a number of providers, largely based in LMICs, felt they
# needed more information on the effectiveness of misoprostol (low confidence) and further
# guidance on successful implementation strategies" - which is CERQual evidence, not a
# signpost. Three more record what WHO plans to write next.
_FUTURE_WORK_RE = re.compile(
    r"\bneed(?:s|ed|ing)?\b|\baims?\s+to\s+develop\b|\bin\s+development\b|\bwill\s+provide\b"
    r"|\bwish(?:es|ed)?\s+for\b|\badvocate\s+for\b|\bto\s+justify\b",
    re.IGNORECASE,
)
# An instruction to the reader's mouse. Anchored at the start of the sentence, because jlAbxL
# glues one onto the end of a real finding with no separator - "the detection rate of liver
# metastatic disease is superior to CT **Click** **here** **to see Figure 2.**" - and an
# unanchored match would take the finding with it.
_CLICK_INSTRUCTION_RE = re.compile(
    r"\A\**\s*(?:please\s+)?click\b|\A(?:to|for)\b[^.]{0,70}\bclick\s+(?:here|on|to)\b",
    re.IGNORECASE,
)
# A sentence whose subject is a numbered figure or table and whose verb only says that the
# figure exists: "Fig. 2 summarizes the above categories and associated criteria as well as
# the potential for overlap." The figure is still in the document; the sentence adds nothing
# to it. 75 across 41 guidelines. Anchored at the sentence start, so the judgement written
# beside it - "The GDG acknowledged that the presence of multiple factors simultaneously
# could confer higher risk" - is a separate sentence and stays. The claim guard keeps the
# five that do assert something, among them "Table 3 outlines information that should be
# provided to patients and their carers".
_FIGURE_REFERENCE_RE = re.compile(
    r"\A(?:fig(?:ure)?\.?|table|box|chart|annex|appendix)\s*[A-Z]?\d+[a-z]?\.?\s+"
    r"(?:summari[sz]es|shows|illustrates|presents|depicts|displays|outlines|lists|describes|"
    r"provides|gives)\b",
    re.IGNORECASE,
)


def _mask_link_targets(text: str) -> tuple[str, list[str]]:
    """Replace every link target with a placeholder holding no sentence punctuation."""
    targets: list[str] = []

    def take(match: re.Match[str]) -> str:
        targets.append(match.group(0))
        return f"\x00{len(targets) - 1}\x00"

    return _LINK_TARGET_RE.sub(take, text), targets


def _unmask_link_targets(text: str, targets: list[str]) -> str:
    return _MASK_RE.sub(lambda match: targets[int(match.group(1))], text)


def _is_cross_reference_sentence(sentence: str, *, final: bool = False) -> bool:
    """Report whether one sentence only says that the subject lives in another document.

    Args:
        sentence: One sentence of a rendered prose block.
        final: Whether this is the last sentence in its block, which may end without
            punctuation. (default: False)
    """
    shape = _FINAL_SENTENCE_SHAPE_RE if final else _SENTENCE_SHAPE_RE
    if not shape.match(sentence.strip()):
        return False
    # Emphasis markers are stripped as well as link syntax, because a publisher can put them
    # anywhere and they break a wording test wherever they land. "**A Summary of Living
    # Recommendations (Guideline Version 5.0) is available** [**HERE**](url)" renders as
    # "available** **HERE", so "available here" never matched and the line survived in both
    # guidelines carrying it.
    visible = " ".join(re.sub(r"[*_]+", " ", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", sentence)).split())
    if _SENTENCE_CLAIM_RE.search(visible) or _MEASUREMENT_RE.search(visible):
        return False
    # An instruction to click needs no address to be useless: the link it refers to is the
    # address, and "Click to download the evidence report for infant feeding" carries nothing
    # else. 40 of these across 15 guidelines.
    if _CLICK_INSTRUCTION_RE.match(visible) or _FIGURE_REFERENCE_RE.match(visible):
        return True
    # Naming a document and saying where it sits is the same act as linking to it. Publishers
    # do it both ways, often in the same paragraph.
    if (
        _DOCUMENT_REFERENCE_PHRASE_RE.search(visible)
        and _LOCATES_A_DOCUMENT_RE.search(visible)
        and not _FUTURE_WORK_RE.search(visible)
    ):
        return True
    if "http" not in sentence and "www." not in sentence:
        return False
    return bool(_CROSS_REFERENCE_RE.search(visible))


def _drop_cross_reference_sentences(markdown: str) -> str:
    """Remove a sentence whose whole job is to name another document.

    `_drop_pointer_blocks` removes a paragraph that is nothing but a signpost. Publishers
    also write the signpost as the last sentence of a paragraph that starts with real
    content, and there the paragraph cannot be the unit: jW0ZbL's mpox chapter opens
    "Disseminated cryptococcosis skin lesions may resemble mpox under some circumstances"
    before adding where the cryptococcal guidance lives, and EZVOaE closes a paragraph of
    Ebola preparedness guidance with the address of two WHO toolkits. Dropping either
    paragraph would take the clinical sentence with it, so the sentence is the unit here.

    A sentence goes only when it carries a web address, says the subject is covered
    elsewhere, and asserts nothing itself - no instruction, no measurement, no estimate. A
    scope disclaimer next to one goes too, because the two are written as a pair.

    Blocks that are not prose are left alone, and a block is never emptied: if every
    sentence qualifies, the block is a pointer paragraph and `_drop_pointer_blocks` owns it.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    blocks, changed = markdown.split("\n\n"), False
    for index, block in enumerate(blocks):
        stripped = block.strip()
        # Bold is allowed through; list markers and italics are not. LqRV3n writes its whole
        # download index in bold - "**Click here to view and download the Companion Guide in
        # English.**" - so excluding every block that opens with an asterisk put nine of them
        # out of reach. A list item still belongs to `_drop_link_directories`, which weighs
        # the list as a whole, and a wholly italic block to `_drop_approval_stamps`.
        if (
            not stripped
            or stripped.startswith(("#", "|", ">", "    "))
            or _LIST_ITEM_RE.match(stripped)
            or (stripped.startswith("*") and not stripped.startswith("**"))
        ):
            continue
        masked, targets = _mask_link_targets(stripped)
        restored = [_unmask_link_targets(sentence, targets) for sentence in _SENTENCE_SPLIT_RE.split(masked)]
        keep = [
            not _is_cross_reference_sentence(sentence, final=position == len(restored) - 1)
            for position, sentence in enumerate(restored)
        ]
        if all(keep):
            continue
        keep = [
            kept and not (_SCOPE_DISCLAIMER_RE.search(sentence) and not _SENTENCE_CLAIM_RE.search(sentence))
            for kept, sentence in zip(keep, restored, strict=True)
        ]
        blocks[index] = " ".join(sentence for sentence, kept in zip(restored, keep, strict=True) if kept)
        changed = True
    if not changed:
        return markdown
    # A fragment may empty completely - jW0ZbL's "Secondary postpartum haemorrhage" opens
    # with two paragraphs that do nothing but name the NICE and RANZCOG documents covering
    # the subject. Its callers already handle that: a recommendation whose text renders away
    # is dropped, and a section keeps its heading for the recommendations underneath, or is
    # dropped entirely by `_is_pointer_only` when it has none.
    return "\n\n".join(block for block in blocks if block.strip())


# Anchored at the start of the sentence, and that is the whole safety argument. "Approved by
# NHMRC on 2 January 2025, expires 1 January 2030" is a stamp; "Oral semaglutide tablet is
# now approved by the United States Food and Drug Administration (FDA) and Health Canada,
# but not widely accessible in other countries" is a fact about a drug, and "The guideline
# was then reviewed and approved by the WHO Guideline Review Committee" is a sentence about
# the guideline's history. Only the first opens with the stamp wording, so only it matches.
_APPROVAL_STAMP_RE = re.compile(
    r"\A(?:update\s+)?approved\s+(?:by|in|on)\b"
    r"|\Aevidence\s+surveillance\s*:"
    r"|\Athis\s+(?:is\s+a\s+draft\s+recommendation|recommendation\s+is\s+currently\s+considered\s+stable)\b"
    r"|\A(?:date\s+of\s+(?:approval|publication|issue)|next\s+review|review\s+date)\b"
    r"|\Aexpires?\s",
    re.IGNORECASE,
)
# Publishers italicize a whole remarks block, so the stamp arrives wrapped in markers rather
# than as bare text. Matching has to see through them, and what survives has to be re-wrapped.
_WHOLE_BLOCK_ITALIC_RE = re.compile(r"\A\*(?!\*)(?P<body>.*?)\*\Z", re.DOTALL)


_BOLD_LABEL_LINE_RE = re.compile(r"\A\*\*(?!\*)(?P<label>[^*].*?)\*\*[:.]?\Z")
_DIRECTORY_HEADER_RE = re.compile(r"\b(?:resources?|providing|support|services?)\b", re.IGNORECASE)
# A directory header names a category and stops. These three exclusions are each paid for by
# something the measurement found the rule would otherwise have deleted.
#
# Length and the question mark: jO0lNL heads a hand-hygiene implementation checklist "4. How
# can you provide appropriate resources and a supportive institutional environment for good
# hand hygiene?", and jNxw7n uses a whole paragraph as a label.
_MAX_DIRECTORY_HEADER_CHARS = 80
# Advice: j98OoE writes two recommendations as labels - "We recommend that health services
# aim to develop professional support for First Nations Australians with chronic kidney
# disease" - with the bulleted detail of the recommendation underneath.
_DIRECTORY_HEADER_ADVISES_RE = re.compile(
    r"\b(?:we\s+recommend|we\s+suggest|should|must|shall|recommend(?:s|ed)?)\b", re.IGNORECASE
)
# And GRADE's own evidence-to-decision domains, which are the reason the header word cannot
# be trusted alone: across the corpus "resources" names cost and staffing analysis 300-odd
# times - "Certainty of evidence for required resources" in Ee4mAn opens a costing of
# paediatric intensive care - against a handful of real directories.
_GRADE_RESOURCE_DOMAIN_RE = re.compile(
    r"\bcertainty\s+of\s+(?:the\s+)?evidence\b"
    r"|\bresources?\s+(?:consideration|requirement|required|utili[sz]ation)"
    r"|\bmain\s+resource\b|\bevidence\s+on\s+resources\b"
    r"|\bimpact\s+on\s+the\s+organi[sz]ation\s+of\s+care\b",
    re.IGNORECASE,
)


# The entry under a directory label is not always a list. Sometimes it is one citation -
# "Couzos S & Murray R (eds) (2008) Aboriginal Primary Health Care. Melbourne: Oxford
# University Press." - or one service, as under "Family violence support services". The cap
# is what separates those from a topic section that happens to be labelled with the word
# "resources": EPY83j's "Human resource gaps in maternal and newborn health" runs to 1,002
# characters of prose about the global midwife shortage, where every real entry measured
# between 176 and 362.
_MAX_PROSE_DIRECTORY_ENTRY_CHARS = 400


def _is_prose_directory_entry(entry: str) -> bool:
    """Report whether a block under a directory label is one entry rather than a section.

    Args:
        entry: The rendered block sitting directly under the label.
    """
    if len(entry) > _MAX_PROSE_DIRECTORY_ENTRY_CHARS:
        return False
    if "http" not in entry and "www." not in entry:
        return False
    visible = " ".join(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", entry).split())
    return not (_SENTENCE_CLAIM_RE.search(visible) or _MEASUREMENT_RE.search(visible))


def _drop_resource_directories(markdown: str) -> str:
    """Remove a bold label announcing a list of services, together with that list.

    Evan's shape, and it is the conjunction that makes it safe: the header word on its own is
    unusable, because in GRADE guidelines "resources" usually names the cost-and-staffing
    domain rather than a directory. Requiring a bulleted list directly underneath separates
    them - the GRADE domains are followed by prose or a table.

    What goes: "Support for stillbirth and neonatal death", "Counselling and psychologist
    services", "Resources for Aboriginal and Torres Strait Islander people" and the entries
    beneath them - Red Nose, Bears of Hope, Pink Elephants, the Perinatal Loss Centre. Each
    entry names an organization and says what it offers the public. None of it is guidance.

    Only a bold label counts, not a markdown heading. ERx1yL writes the same directories under
    real headings, with a description paragraph between every pair of bullets, so removing the
    heading and one list would strand the rest of the entries under the heading above.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "**" not in markdown:
        return markdown
    blocks = markdown.split("\n\n")
    drop: set[int] = set()
    for index, block in enumerate(blocks):
        match = _BOLD_LABEL_LINE_RE.match(block.strip())
        if not match:
            continue
        label = match.group("label").strip().rstrip(":.").strip()
        if (
            not _DIRECTORY_HEADER_RE.search(label)
            or len(label) > _MAX_DIRECTORY_HEADER_CHARS
            or label.endswith("?")
            or _DIRECTORY_HEADER_ADVISES_RE.search(label)
            or _GRADE_RESOURCE_DOMAIN_RE.search(label)
        ):
            continue
        following = next((later for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        if following is None:
            continue
        entry = blocks[following].strip()
        if not _LIST_ITEM_RE.match(entry.splitlines()[0]) and not _is_prose_directory_entry(entry):
            continue
        drop.update({index, following})
    if not drop:
        return markdown
    remainder = "\n\n".join(block for index, block in enumerate(blocks) if index not in drop)
    return remainder if remainder.strip() else markdown


_FOOTNOTE_MARKER_RE = re.compile(r"\A(?:\\\*|\*(?!\*)|†|‡|§|¶|[¹²³⁴⁵⁶⁷⁸⁹])\s*")
# An author's affiliation, written as an empty link to a footnote anchor followed by the
# institution: "[](#_ftnref1) Department of Medicine, Universite de Sherbrooke, Sherbrooke,
# Canada". 34 across the corpus, all in BMJ Rapid Recommendation bylines.
#
# Keyed on the anchor, not on the institution words. "Department", "University" and above all
# "hospital" are clinical vocabulary here - a rule matching them takes "In-hospital or 30-day
# mortality: 1.08 (95% CI, 0.60-1.94)" and "hospital length of stay" with them.
_AFFILIATION_FOOTNOTE_RE = re.compile(r"\A\[\]\(#_ftnref\d+\)")


_CITATION_LABEL_RE = re.compile(
    r"\A(?:suggested|recommended|preferred)\s+citation\b|\A(?:how\s+to\s+)?cite\s+(?:this|as)\b",
    re.IGNORECASE,
)
_SOURCE_CREDIT_RE = re.compile(
    r"\A(?:adapted|reproduced|reprinted|modified|derived|taken)\s+(?:from|with|by)\b"
    r"|\A(?:source|credit|attribution)\s*:"
    r"|\Awith\s+permission\s+from\b"
    r"|\A(?:copyright|©)\b",
    re.IGNORECASE,
)
# The other kind of table footnote is the dangerous one: it defines a term, states a
# threshold, or records why the certainty was downgraded, and is often the only place that
# reasoning is written down. None of those may be mistaken for a credit line.
_FOOTNOTE_CONTENT_RE = re.compile(
    r"\b(?:should|must|shall|recommend(?:s|ed|ing)?|suggest(?:s|ed|ing)?|defined?\s+as|"
    r"downgrad\w*|upgrad\w*|imprecision|inconsistency|indirectness|risk\s+of\s+bias)\b"
    r"|\b(?:RR|OR|HR)\s*[=:]|95%\s*CI|\bp\s*[<=]\s*0"
    r"|\b\d+(?:\.\d+)?\s*(?:%|mg|mcg|µg|mmol|mL|IU|mmHg|hours?|days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)


# A caption that numbers a table and says nothing about it. Only the bare number qualifies:
# "**Table 1.** Scope of the guidelines" tells a reader what the table holds and stays, and
# so do the other 86 numbered captions in the corpus. Three are bare.
_NUMBER_ONLY_CAPTION_RE = re.compile(
    r"\A\*\*(?:table|figure|box|chart|appendix)\s*[A-Z]?\d+[a-z]?\.?\s*\*\*[:.]?\Z", re.IGNORECASE
)


_RENDERED_HEADING_RE = re.compile(r"\A(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*\Z")
# Headings that announce how the literature was looked for. Every one of these introduces
# database names, dates and query strings, never what the search returned.
_SEARCH_METHOD_HEADING_RE = re.compile(
    r"\A(?:search\s+strateg(?:y|ies)|search\s+methods?|literature\s+search(?:es)?|"
    r"databases?\s+searched|search\s+terms?|search\s+and\s+selection|"
    r"limitations?\s+of\s+searches?|three\s+month\s+bridging\s+search|"
    r"search\s+for\s+existing\s+relevant\s+guidelines?[\w \-]*)\Z",
    re.IGNORECASE,
)
# The section-level skip list has five content guards behind it. An in-body rule starts with
# none, so it carries its own: a block stating an effect, a dose, a recommendation or a
# finding is not merely a description of a search, whatever its heading says.
_SEARCH_BLOCK_CONTENT_RE = re.compile(
    r"\b(?:we\s+recommend|we\s+suggest|should|must|contraindicat\w*)\b"
    r"|\b(?:RR|OR|HR)\s*[=:]|95%\s*CI|\bp\s*[<=]\s*0"
    r"|\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|mmol|mL|IU|mmHg)\b"
    r"|\bcertainty\s+(?:of\s+the\s+evidence|was\s+(?:high|moderate|low))\b"
    r"|\bno\s+(?:studies|trials|evidence)\s+(?:were\s+)?(?:found|identified)"
    r"|\byielded\s+no\s+evidence|\bshowed\s+no\s+difference",
    re.IGNORECASE,
)
# And the clinical questions, which publishers file under the search heading that answered
# them. ERx1yL lists six under "Literature search" - "In patients presenting to primary care,
# what signs, symptoms and clinical features are predictive of cancer?" - and they are the
# questions the guideline exists to answer, not a description of how it looked for them.
_QUESTION_ITEM_RE = re.compile(r"\A\s*[-*]\s+.*\?\s*\Z")
_MIN_CLINICAL_QUESTIONS = 2


def _states_clinical_questions(body: str) -> bool:
    """Report whether a block carries a list of the guideline's own clinical questions."""
    return sum(1 for line in body.splitlines() if _QUESTION_ITEM_RE.match(line)) >= _MIN_CLINICAL_QUESTIONS


# GRADE's certainty circles. Every one of the 366 lines carrying them in the corpus also
# writes the certainty in words - "Quality of evidence: **Very low ⊕**" - so the symbol adds
# nothing a reader or a retriever can use, and a run of identical characters is noise in a
# text index. Checked before removing: no line carries a circle without the word beside it.
_GRADE_CIRCLE_RE = re.compile(r"[⊕⊝]+")
# A label announcing a block that is not there. The renderer writes these for the fields
# MAGICapp defines, and 280 of the 15,609 in the corpus stand over nothing, because the
# publisher left that field empty in a document where the others are filled.
_ORPHAN_LABEL_RE = re.compile(
    r"\A(?:\*{1,2})?\s*(?:certainty of the evidence|quality of evidence|summary of findings|"
    r"effect estimates?|benefits and harms|rationale|practical (?:advice|info)|"
    r"resources and cost|patient values and preferences|applies to)\s*:?\s*(?:\*{1,2})?\Z",
    re.IGNORECASE,
)


# A journal's indexing terms. Written as a bold label with the terms beside it, under it, or
# in the next block, depending on the publisher. 13 across 10 guidelines.
_KEYWORD_LABEL_RE = re.compile(
    r"\A(?:\*{1,2})?\s*(?:key\s*words?|mesh\s+terms?|index\s+terms?)\s*:?\s*(?:\*{1,2})?\s*:?\s*(?P<terms>.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
# A term list, not prose: separated, short, and asserting nothing. The longest in the corpus
# is 325 characters of bariatric surgery terms.
_MAX_KEYWORD_CHARS = 500


def _is_keyword_list(text: str) -> bool:
    """Report whether a run of text is a list of index terms rather than a sentence."""
    visible = " ".join(re.sub(r"[*_]+", "", text).split())
    if not visible or len(visible) > _MAX_KEYWORD_CHARS:
        return False
    if "," not in visible and ";" not in visible and " OR " not in visible:
        return False
    return not (_SENTENCE_CLAIM_RE.search(visible) or _MEASUREMENT_RE.search(visible))


_REVIEW_CITATION_LEAD_RE = re.compile(
    r"\A(?:this|the)\s+recommendation\s+(?:was|is)\s+informed\s+by\s+the\s+following\s+"
    r"(?:systematic\s+)?reviews?\b",
    re.IGNORECASE,
)
_REVIEW_CITATION_LABEL_RE = re.compile(r"\A\**\s*(?:systematic\s+reviews?|references?)\s*\**\s*:?\Z", re.IGNORECASE)
# What makes a block a citation rather than prose: a DOI, a review-registry identifier, an
# author list in "Surname AB," form, or the publisher's own note that the review is
# unpublished. All four appear in noPQkE's 19 citation runs.
_REVIEW_CITATION_RE = re.compile(
    r"\bdoi:\s*10\.|\bPROSPERO\s+\d{4}|\bCRD\d{6,}|\([Uu]npublished\)|\b[A-Z][a-z]+\s+[A-Z]{1,3},",
)


def _drop_review_citations(markdown: str) -> str:
    """Remove the list of systematic reviews a recommendation was built from.

    "This recommendation was informed by the following systematic reviews:" and the
    bibliography under it - author, title, journal, DOI or PROSPERO registration. It records
    where the evidence came from, not what the evidence showed, and the certainty ratings and
    effect estimates it produced are rendered with the recommendation itself.

    The run ends at the first block that is not a citation, so prose following the
    bibliography is untouched. A bare "Systematic reviews" label directly above the lead-in
    goes with it, since it would otherwise stand over nothing.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "informed by the following" not in markdown.lower():
        return markdown
    blocks = markdown.split("\n\n")
    doomed: set[int] = set()
    for index, block in enumerate(blocks):
        if not _REVIEW_CITATION_LEAD_RE.match(" ".join(block.split())):
            continue
        doomed.add(index)
        previous = next((earlier for earlier in range(index - 1, -1, -1) if blocks[earlier].strip()), None)
        if previous is not None and _REVIEW_CITATION_LABEL_RE.match(blocks[previous].strip()):
            doomed.add(previous)
        later = index + 1
        while later < len(blocks):
            flat = " ".join(blocks[later].split())
            if not flat:
                later += 1
                continue
            if flat.startswith("#") or not _REVIEW_CITATION_RE.search(flat):
                break
            doomed.add(later)
            later += 1
    if not doomed:
        return markdown
    remainder = "\n\n".join(block for index, block in enumerate(blocks) if index not in doomed)
    return remainder if remainder.strip() else markdown


def _drop_keyword_blocks(markdown: str) -> str:
    """Remove a keywords label and the index terms belonging to it.

    "KEYWORDS Alzheimer's disease, blood-based biomarkers, clinical practice guideline,
    diagnosis" - the journal's indexing terms, carrying no claim about anything. The terms
    sit beside the label, under it, or in the block after it, so both arrangements are
    handled; when the label stands alone the following block has to look like a term list
    before it is taken.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "eyword" not in markdown and "EYWORD" not in markdown and "esh term" not in markdown.lower():
        return markdown
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        match = _KEYWORD_LABEL_RE.match(block.strip())
        if not match:
            continue
        terms = match.group("terms").strip()
        if terms:
            if _is_keyword_list(terms):
                keep[index] = False
            continue
        following = next((later for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        if following is not None and _is_keyword_list(blocks[following]):
            keep[index] = keep[following] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# An appendix the publisher wrote as a bold line inside a section it also uses for
# guidance, instead of giving it a section of its own. jzb7Xj files five appendices under
# one "Appendices" heading: three are the guidance landscape, how the panel was picked and
# which organizations were written to, and two carry patients' values and the adverse-event
# evidence - "an increased risk of 0.8% for having persistent opioid use (low certainty)".
# Neither the section skip list nor any rule on the container can separate them, because at
# section level there is only the one container.
#
# Keyed on the appendix's own title. `_skip_lookup_key` already strips the "APPENDIX 2."
# label and the emphasis, so the title is what is left, and it is matched exactly.
# Deliberately not `_matches_skip_list`: that list carries topic substrings, and "methods"
# reaching inside an appendix would take the adverse-event appendix with it.
_PAPERWORK_APPENDIX_TITLES = frozenset(
    {
        # Which other US bodies have published opioid guidance and when - a survey of the
        # documents around this one. It names no drug, dose or effect: "In 2016, the
        # American Dental Association House of Delegates adopted a statement on the use of
        # opioids." Evan's call 2026-08-06.
        "description of existing guidance on opioids for acute dental pain",
        "additional description of the methods",
        "list of stakeholder organizations contacted and responses",
    }
)
_INLINE_APPENDIX_LABEL_RE = re.compile(r"\A(?:\\?[*_])+\s*(?:appendix|annex)\s+\d+\b.*(?:\\?[*_])+\s*\Z", re.IGNORECASE)


def _drop_inline_appendices(markdown: str) -> str:
    """Remove an appendix written as a bold line in a body, when it is paperwork.

    The label opens the appendix and the next one closes it, so a mistake costs one
    appendix rather than the rest of the section. A markdown heading also closes it: a
    heading is a section boundary the publisher did declare, and removal must not cross
    one.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "ppendix" not in markdown and "PPENDIX" not in markdown and "nnex" not in markdown.lower():
        return markdown
    lines = markdown.split("\n")
    keep = [True] * len(lines)
    dropping = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if _RENDERED_HEADING_LINE_RE.match(stripped):
            dropping = False
        elif _INLINE_APPENDIX_LABEL_RE.match(stripped):
            dropping = _skip_lookup_key(stripped.replace("\\", "")) in _PAPERWORK_APPENDIX_TITLES
        keep[index] = not dropping
    if all(keep):
        return markdown
    return "\n".join(line for line, kept in zip(lines, keep, strict=True) if kept)


# A bibliography with no label over it, which is the one shape the "References" label in
# `_INLINE_PAPERWORK_LABELS` cannot reach. jzb7Xj ends its appendices with four entries
# numbered e1 to e4 - the journal's online-only reference list - and nothing announces them.
#
# Recognized by the numbering AND the journal signature together, because the numbering
# alone is also how the same document writes its adverse-event evidence: "1. Harbaugh and
# colleagues determined the association between filling a prescription for opioids after a
# third-molar extraction ...". Those items carry no volume, issue and page range, so
# requiring one on every entry separates them.
_CITATION_ENTRY_RE = re.compile(r"\A\s*e?\d+\.\s+\S")
_JOURNAL_SIGNATURE_RE = re.compile(r"\b(?:19|20)\d{2};\s*\d+|\bdoi:\s*\S|\bPMID:|\bPMCID:", re.IGNORECASE)
_MIN_CITATION_BLOCK_ENTRIES = 2


def _drop_citation_blocks(markdown: str) -> str:
    """Remove a block that is nothing but numbered journal citations.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if not _JOURNAL_SIGNATURE_RE.search(markdown):
        return markdown
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        entries = [line for line in block.split("\n") if line.strip()]
        if len(entries) < _MIN_CITATION_BLOCK_ENTRIES:
            continue
        if not all(_CITATION_ENTRY_RE.match(entry) for entry in entries):
            continue
        if all(_JOURNAL_SIGNATURE_RE.search(entry) for entry in entries):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# A line saying where the guideline was published, and the citation it introduces.
#
# nyO1Yj opens its Methods with "**This guideline manuscript was published in** ***Alzheimer's
# and Dementia: The Journal of the Alzheimer's Association*** **on July 29th, 2025:**" and
# then the Palmqvist reference with its DOI. It is the "how to cite" block under wording no
# label list can match.
#
# This was recorded as unreachable once before and left alone: getting at it through
# `_INLINE_PAPERWORK_LABELS` needed three separate loosenings of a rule that deletes text -
# the label sits inside a colour span, it is a prefix of its line rather than the whole line,
# and the line is 131 characters against an 80-character cap. None of those are needed here,
# because this keys on the shape of the pair rather than on a label.
#
# The citation is what makes it safe. An announcement alone is just a sentence; an
# announcement whose next block carries a DOI, a PMID or a journal volume is a citation
# block, and a directive in the announcement disqualifies it. One pair in the corpus.
_PUBLICATION_ANNOUNCEMENT_RE = re.compile(r"\bpublish(?:ed|ation)\b", re.IGNORECASE)
_CITATION_SIGNATURE_RE = re.compile(r"\bdoi\.org/|\bdoi:\s*\S|\bPMID:|\bPMCID:|\b(?:19|20)\d{2};\s*\w", re.IGNORECASE)
_ANNOUNCEMENT_DIRECTIVE_RE = re.compile(
    r"\bwe (?:recommend|suggest)\b|\b(?:is|are) recommended\b|\bshould be\b", re.IGNORECASE
)
_MAX_ANNOUNCEMENT_CHARS = 250


def _drop_publication_announcements(markdown: str) -> str:
    """Remove a "this was published in ... :" line and the citation under it.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if not _PUBLICATION_ANNOUNCEMENT_RE.search(markdown):
        return markdown
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        text = _EMPHASIS_RE.sub("", " ".join(block.split())).strip()
        if not text.endswith(":") or len(text) > _MAX_ANNOUNCEMENT_CHARS:
            continue
        if not _PUBLICATION_ANNOUNCEMENT_RE.search(text) or _ANNOUNCEMENT_DIRECTIVE_RE.search(text):
            continue
        following = next((later for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        if following is None or not _CITATION_SIGNATURE_RE.search(blocks[following]):
            continue
        keep[index] = keep[following] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# A block that is nothing but citations of other organizations' publications.
#
# jm83RE appends one to almost every recommendation - "NICE (2015) *Diabetes in Pregnancy:
# management from preconception to the postnatal period (NG3)*. London: National Institute
# for Health and Care Excellence." - and they are not the evidence behind the recommendation,
# they are a further-reading list of other guidelines.
#
# Three conditions together, because any one alone is common in evidence narrative. Every
# line must open with an author or organization and a year in brackets; every line must close
# like a reference, with a city and publisher or a journal volume and page range; and no line
# may carry a reporting verb. That last one is what separates a bibliography from the
# sentences around it - "Fryer et al (2016) (14 trials, n=1863) suggests that self-management
# programs appear to have a small beneficial effect" opens and closes the same way and is a
# finding. 35 blocks in the corpus, 34 of them in jm83RE; all 35 were read.
_BIBLIOGRAPHY_OPENING_RE = re.compile(r"\A[A-Z][A-Za-z.,&'\- ]{0,70}\((?:19|20)\d{2}[a-z]?\)")
_BIBLIOGRAPHY_CLOSING_RE = re.compile(
    r"(?:[A-Z][\w\u2019'\- ]+:\s*[^.]{2,70}\.?|\b\d+\s*\([^)]{1,12}\)\s*:\s*\d+[-\u2013]?\d*\.?"
    r"|\bLondon\b|\bGeneva\b|\bCanberra\b)"
    # An editorial note the publisher appended to the reference - "[URL link non-functional]",
    # "Accessed: 7 August 2018" - sits after the publisher and must not hide the shape.
    r"(?:\s*\[[^\]]{0,60}\]|\s*Accessed:?[^.]{0,40}\.?)*\s*\Z"
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_REPORTING_VERB_RE = re.compile(
    r"\b(?:suggest|show|found|find|includ|report|investigat|conduct|randomis|randomiz|trial"
    r"|demonstrat|assess|compar|evaluat|examin|concluded|observ|analys)\w*\b",
    re.IGNORECASE,
)


def _drop_bibliography_blocks(markdown: str) -> str:
    """Remove a block whose every line is a citation of another publication.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        stripped = block.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        # Judged on the visible text: a citation is usually a link, and the address would
        # otherwise sit between the title and the publisher.
        # Emphasis is stripped too: publishers italicise the title and sometimes an
        # editorial note after the publisher - "*[URL link non-functional]*" - and the
        # markers would otherwise hide the shape of the line's ending.
        plain = _EMPHASIS_RE.sub("", _MARKDOWN_LINK_RE.sub(r"\1", stripped))
        lines = [line.strip() for line in plain.split("\n") if line.strip()]
        if not lines or _REPORTING_VERB_RE.search(plain):
            continue
        if all(_BIBLIOGRAPHY_OPENING_RE.match(line) and _BIBLIOGRAPHY_CLOSING_RE.search(line) for line in lines):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# A paragraph that is nothing but a date - the line a publisher signs a foreword off with.
# GELA closes the forewords of two guidelines with "13ᵗʰ June, 2025" and nothing else, left
# stranded between the foreword and the next heading.
#
# The whole block has to be the date, which is what makes this safe: a date inside a sentence,
# a table row, or a "last evidence search" line is untouched, because the rule never matches a
# prefix. Publishers write the ordinal in superscript, so those characters are folded down
# before the shape is tested.
_SUPERSCRIPT_LETTERS = str.maketrans("ᵗʰˢⁿᵈʳᵉ", "thsndre")
_MONTH_NAMES = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATELINE_RE = re.compile(
    rf"\A(?:\d{{1,2}}(?:st|nd|rd|th)?\s+)?{_MONTH_NAMES}\.?,?\s*(?:\d{{1,2}},?\s*)?(?:19|20)\d{{2}}\.?\Z",
    re.IGNORECASE,
)
_MAX_DATELINE_CHARS = 40


def _drop_dateline_blocks(markdown: str) -> str:
    """Remove a block whose whole text is a date.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        text = _EMPHASIS_RE.sub("", " ".join(block.split())).strip().translate(_SUPERSCRIPT_LETTERS)
        if text and len(text) <= _MAX_DATELINE_CHARS and _DATELINE_RE.match(text):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# A list of named links to documents that are not in the corpus, and the label over it.
#
# Eez2Kj closes 21 recommendations with "**SUPPORTING DOCUMENTS**" and then lines like
# "Systematic review report: [SR report-Post hysterectomy AIS.pdf](...)". The report is a PDF
# the scraper never fetched, so the line names a file the reader cannot reach and asserts
# nothing. 89 such blocks across nine guidelines; all were read, and the one flagged for
# content was a source credit, which is removed anyway.
#
# The whole block has to be links, which is what stops this reaching prose that merely
# contains one. Deliberately NOT done as an inline-heading rule keyed on the label: the block
# under "SUPPORTING DOCUMENTS" ends at the next BOLD line, and three of Eez2Kj's are followed
# by an italic "*Practical advice:*" and a paragraph about people who believe a hysterectomy
# has cured them - so the run would have carried on into the guidance.
# The trailing group allows the file extension publishers leave outside the link -
# "...(https://www.cancer.org.au/assets/pdf/sr-report-prevalent-vs-incident-hpv1618-peco-2).pdf".
_DOCUMENT_LINK_LINE_RE = re.compile(r"\A.{1,240}?:\s*\[[^\]]+\]\(https?://[^)]+\)[.\w]{0,8}\s*\Z")
_DOCUMENT_LIST_LABEL_RE = re.compile(r"\A(?:\\?[*_]){2}\s*[A-Z][A-Za-z ]{2,60}\s*(?:\\?[*_]){2}\Z")


def _drop_document_link_lists(markdown: str) -> str:
    """Remove a block of "name: link" lines, and the bold label above it.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "](http" not in markdown:
        return markdown
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        if not lines or not all(_DOCUMENT_LINK_LINE_RE.match(line) for line in lines):
            continue
        keep[index] = False
        previous = next((earlier for earlier in range(index - 1, -1, -1) if blocks[earlier].strip()), None)
        if previous is not None and _DOCUMENT_LIST_LABEL_RE.match(blocks[previous].strip()):
            keep[previous] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


def _drop_grade_circles(markdown: str) -> str:
    """Remove GRADE's certainty circles, leaving the certainty written in words.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "⊕" not in markdown and "⊝" not in markdown:
        return markdown
    # The circle usually trails a word with a space in front of it, so the space goes too.
    return re.sub(r"[  ]*[⊕⊝]+", "", markdown)


def _drop_orphan_labels(markdown: str) -> str:
    """Remove a field label that has nothing under it.

    "Certainty of the evidence:" with no certainty after it tells a reader only that the
    publisher left the field blank. 280 of these, against 15,329 labels that do introduce
    something and are left alone.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        if not _ORPHAN_LABEL_RE.match(block.strip()):
            continue
        following = next((blocks[later] for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        # A heading after the label does not make it an orphan. Publishers write their own
        # headings inside these fields, so "*Rationale:*" is regularly followed by "#####
        # Rationale and remarks" and then the rationale itself. Treating that as empty would
        # strip the label off content that is present.
        if following is None or _ORPHAN_LABEL_RE.match(following.strip()):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


def _drop_search_method_blocks(markdown: str) -> str:
    """Remove a heading describing a database search, and everything under it.

    The skip list matches section objects in the source JSON, so a chapter called "Search
    strategy" is already dropped. Publishers also write the same thing as a heading inside a
    section's body, where the list has never been able to reach it: nJW8bE puts "SEARCH
    STRATEGY" between "BACKGROUND" and the recommendation in every one of its sections, and
    "search strategies" has been on the skip list throughout without touching it.

    Scoped to this family on purpose. Applying the whole skip list to in-body headings would
    reach 522,369 characters across 48 guidelines, four fifths of it flagged - a much larger
    decision than this one, which is 8 blocks and 13,242 characters across 7 guidelines.

    The extent of a heading is everything up to the next heading at the same depth or
    shallower, so a search section with subsections goes whole and the chapter after it is
    untouched.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "#" not in markdown:
        return markdown
    lines = markdown.split("\n")
    doomed: set[int] = set()
    index = 0
    while index < len(lines):
        match = _RENDERED_HEADING_RE.match(lines[index])
        if not match:
            index += 1
            continue
        heading = _skip_lookup_key(_plain_heading(match.group("text"), link_mode=LinkMode.KEEP))
        if not _SEARCH_METHOD_HEADING_RE.match(heading):
            index += 1
            continue
        depth = len(match.group("hashes"))
        end = index + 1
        while end < len(lines):
            later = _RENDERED_HEADING_RE.match(lines[end])
            if later and len(later.group("hashes")) <= depth:
                break
            end += 1
        body = "\n".join(lines[index + 1 : end])
        visible = " ".join(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body).split())
        # A heading with nothing under it inside this fragment is not a block to remove: its
        # content is in another fragment, and taking the heading alone strands it. ERx1yL
        # ends a section with "Literature search" and opens the next with the six clinical
        # questions it introduces, so removing the bare heading left them under nothing.
        if visible and not _SEARCH_BLOCK_CONTENT_RE.search(visible) and not _states_clinical_questions(body):
            doomed.update(range(index, end))
        index = end
    if not doomed:
        return markdown
    return "\n".join(line for number, line in enumerate(lines) if number not in doomed)


def _drop_number_only_captions(markdown: str) -> str:
    """Remove a bold caption that is a table number with no title after it.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "**" not in markdown:
        return markdown
    blocks = markdown.split("\n\n")
    kept = [block for block in blocks if not _NUMBER_ONLY_CAPTION_RE.match(block.strip())]
    return "\n\n".join(kept) if len(kept) != len(blocks) else markdown


def _strip_question_numbers_from_headings(markdown: str) -> str:
    """Strip a question number and Executive Summary label from a heading inside a body.

    `_plain_heading` does the same for a section's own heading, but nyxpZL writes these as
    h3 tags inside the body of one section, where that function never sees them: "PICO 5
    **Executive Summary:** Subcutaneous methotrexate versus oral methotrexate for JIA". Nine
    headings, and the only part of each a reader or a retriever can use is the topic.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    if "PICO" not in markdown and "xecutive" not in markdown:
        return markdown
    lines = markdown.split("\n")
    for index, line in enumerate(lines):
        match = _RENDERED_HEADING_RE.match(line)
        if not match:
            continue
        remainder = _QUESTION_NUMBER_LABEL_RE.sub("", match.group("text")).strip(" *_:.\\").strip()
        if remainder and remainder != match.group("text"):
            lines[index] = f"{match.group('hashes')} {remainder}"
    return "\n".join(lines)


def _drop_source_credits(markdown: str) -> str:
    """Remove a table footnote whose whole content is where the table came from.

    nJW8bE closes its recommendation-strength legend with "* Adapted from GRADE working group
    (www.gradeworkinggroup.org)". The legend itself is exactly the content
    `_defines_recommendation_strength` exists to protect - it is the key saying what "Level 1"
    and "Level 2" mean - so the table stays and only the credit line goes. 26 of these across
    12 guidelines, among them five copies of an NHMRC levels-of-evidence citation.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    blocks = markdown.split("\n\n")
    keep = []
    dropped = False
    for block in blocks:
        stripped = block.strip()
        body = _FOOTNOTE_MARKER_RE.sub("", stripped, count=1).strip() if stripped else ""
        visible = " ".join(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body).split())
        # A citation instruction needs no footnote marker: "Suggested citation Living
        # Evidence for Diabetes Consortium. Australian Evidence-Based Clinical Guidelines for
        # Diabetes 2020." It tells a reader how to reference the document, not anything about
        # a patient. Three in the corpus.
        if _AFFILIATION_FOOTNOTE_RE.match(stripped):
            dropped = True
            continue
        plain = " ".join(re.sub(r"[*_]+", " ", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", stripped)).split())
        label = _CITATION_LABEL_RE.match(plain) if plain else None
        # The guard is applied to what follows the label, never to the label itself. Checking
        # the whole block kept every one of these: "Suggested" contains the recommendation
        # verb "suggest", so a rule meant to protect "we suggest X" was protecting the words
        # "Suggested citation".
        if label and not _FOOTNOTE_CONTENT_RE.search(plain[label.end() :]):
            dropped = True
            continue
        if (
            visible
            and _FOOTNOTE_MARKER_RE.match(stripped)
            and _SOURCE_CREDIT_RE.match(visible)
            and not _FOOTNOTE_CONTENT_RE.search(visible)
        ):
            dropped = True
            continue
        keep.append(block)
    if not dropped:
        return markdown
    return "\n\n".join(block for block in keep if block.strip())


def _drop_approval_stamps(markdown: str) -> str:
    """Remove a sentence recording who approved a recommendation and when.

    "Approved by NHMRC on 2 January 2025, expires 1 January 2030." "Approved by LEAPP
    Steering Committee 14 May 2026." "Evidence surveillance: inactive." These are
    provenance about the document, not about the patient - no verdict can rest on them, and
    they repeat under every recommendation in the guideline that carries them.

    Almost all of them sit in the italic Remarks block under a recommendation, so a whole
    block wrapped in emphasis markers is unwrapped, judged, and re-wrapped if anything
    survives. When nothing does, the block empties and the caller drops the "Remarks:" label
    with it, because it only writes the label when there is a body to put under it.

    Args:
        markdown: Rendered markdown for one fragment.
    """
    blocks, changed = markdown.split("\n\n"), False
    for index, block in enumerate(blocks):
        stripped = block.strip()
        if not stripped or stripped.startswith(("#", "|", ">", "    ", "**")):
            continue
        italic = _WHOLE_BLOCK_ITALIC_RE.match(stripped)
        inner = italic.group("body") if italic else stripped
        if inner.startswith(("-", "*")):
            continue
        masked, targets = _mask_link_targets(inner)
        restored = [_unmask_link_targets(sentence, targets) for sentence in _SENTENCE_SPLIT_RE.split(masked)]
        keep = [
            not (
                _APPROVAL_STAMP_RE.match(sentence.strip())
                and not _SENTENCE_CLAIM_RE.search(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", sentence))
            )
            for sentence in restored
        ]
        if all(keep):
            continue
        remainder = " ".join(sentence for sentence, kept in zip(restored, keep, strict=True) if kept).strip()
        blocks[index] = f"*{remainder}*" if remainder and italic else remainder
        changed = True
    if not changed:
        return markdown
    return "\n\n".join(block for block in blocks if block.strip())


def _has_recommendation(section: dict[str, Any]) -> bool:
    """Report whether a section or any descendant carries a real recommendation.

    INFO and NO_STRENGTH items do not count, so a section holding only those
    carries no clinical guidance. INFO marks an editorial callout box. NO_STRENGTH
    is the subtler one: it is a legitimate value on real recommendations - 113 of
    them across the ten largest English guidelines, e.g. Phoenix Australia's "For
    children and adolescents within the first three months after exposure to a
    potentially traumatic event..." - but none of those 113 sit under a
    skip-listed heading, whereas the WHO self-care guideline stores its "This is a
    living guideline from WHO..." navigation blurb as a NO_STRENGTH recommendation
    inside "How to use this guideline". Counting it rescued that front matter from
    the skip list and then stamped "Recommendation" on it.

    NOTSET must keep counting: the National Blood Authority files four real
    "Expert Opinion Point" recommendations under "Introduction" with that value,
    and they are the case this guard exists to protect.

    Only section skipping consults this. Rendering is unaffected, so NO_STRENGTH
    recommendations elsewhere still get their heading.

    Args:
        section: Section object from the guideline JSON.
    """
    for recommendation in section.get("recommendations") or []:
        if isinstance(recommendation, dict):
            strength = str(recommendation.get("strength") or "").strip().upper()
            if strength not in _NON_GUIDANCE_STRENGTHS:
                return True
    return any(isinstance(child, dict) and _has_recommendation(child) for child in section.get("subSections") or [])


def _fragment_text_chars(html_text: str) -> int:
    """Return the readable-text length of an HTML fragment.

    Args:
        html_text: HTML fragment from the guideline JSON.
    """
    if not html_text.strip():
        return 0
    return len(lxml_html.fragment_fromstring(html_text, create_parent="div").text_content())


def _subtree_text_chars(section: dict[str, Any]) -> int:
    """Return the readable-text size of a section's own body plus every descendant's.

    Args:
        section: Section object from the guideline JSON.
    """
    total = _fragment_text_chars(str(section.get("text") or ""))
    for child in section.get("subSections") or []:
        if isinstance(child, dict):
            total += _subtree_text_chars(child)
    return total


def _visible_subtree_text(section: dict[str, Any]) -> str:
    """Return a section's readable text plus every descendant's, as one string.

    Args:
        section: Section object from the guideline JSON.
    """
    parts = [str(section.get("text") or "")]
    for child in section.get("subSections") or []:
        if isinstance(child, dict):
            parts.append(_visible_subtree_text(child))
    joined = " ".join(part for part in parts if part.strip())
    if not joined.strip():
        return ""
    # Joined with spaces rather than `text_content()`, which concatenates text nodes with
    # nothing between them: across a table cell boundary that turns "Certainty of" and "the
    # evidence" into "Certainty ofthe evidence", and a phrase search then finds nothing.
    root = lxml_html.fragment_fromstring(joined, create_parent="div")
    return " ".join(" ".join(root.itertext()).split())


def _is_pointer_only(section: dict[str, Any], body: str) -> bool:
    """Report whether a section's whole content is one sentence pointing somewhere else.

    A body that rendered away to nothing counts too. That is what is left when every
    sentence the publisher wrote under the heading was a cross-reference, a resource address
    or a link directory - the heading names a subject the document does not actually cover,
    so it goes the same way as an explicit "click here". Only a section that had text to
    begin with qualifies: a heading that never carried a body is an outline level, and those
    are emitted as before.

    Args:
        section: Section object from the guideline JSON.
        body: The section's rendered body markdown.
    """
    if section.get("subSections") or section.get("picos") or (section.get("recommendations") or []):
        return False
    if not body:
        # Nothing under the heading at all: no body, no children, no recommendations, no
        # PICOs. It used to matter whether the section had started with text - a heading that
        # never carried one was treated as an outline level and kept - but an outline level
        # has children, and these have none. 173 dead headings across 52 guidelines, among
        # them jXXZNj's "Step-wise pain relief measures" and E5AbPE's "Guideline Status".
        return True
    if len(body) > _MAX_POINTER_CHARS:
        return False
    # A link's address is not part of the sentence a reader sees.
    visible = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body).strip()
    return bool(_POINTER_ONLY_RE.match(visible)) and not _MEASUREMENT_RE.search(visible)


def _is_draft_recommendation(recommendation: dict[str, Any]) -> bool:
    """Report whether the publisher marks this recommendation as a draft.

    Two places the marker appears. Most publishers put it at the head of the text. The
    Australian Postnatal Care Guidelines put it in the remarks instead - "Approved by LEAPP
    Steering Committee 14 May 2026. This is a draft recommendation that has not yet been
    approved by NHMRC." - where the text itself reads like any other recommendation. Both
    say the same thing: the panel has not signed this off, so it is not yet guidance.

    Args:
        recommendation: Recommendation object from the guideline JSON.
    """
    visible = _visible_subtree_text({"text": str(recommendation.get("text") or "")})
    if _DRAFT_RECOMMENDATION_RE.match(visible):
        return True
    remarks = _visible_subtree_text({"text": str(recommendation.get("remarks") or "")})
    return bool(_UNAPPROVED_DRAFT_RE.search(remarks))


def _holds_evidence_table(section: dict[str, Any]) -> bool:
    """Report whether a section carries a GRADE evidence-to-decision table.

    A second rescue beside `_has_recommendation`, and it exists because one heading can sit
    on opposite kinds of content. "Guideline Development Group" names a roster in 19 of the
    20 sections that carry it, and in the twentieth - WHO's antenatal nutrition guideline,
    "Annex 7: Guideline Development Group (GDG) judgement" - it names 27,750 characters of
    certainty ratings and effect directions, one row per recommendation. No wording in the
    heading separates those, so the body has to be asked.

    Both signals are required, and repeatedly, so that a roster mentioning GRADE once in
    passing is not rescued: across every section the skip list currently drops, this matches
    exactly one, the table above, and nothing else.

    Args:
        section: Section object from the guideline JSON.
    """
    text = _visible_subtree_text(section)
    return len(_GRADE_CERTAINTY_RE.findall(text)) >= 2 and len(_GRADE_EFFECT_RE.findall(text)) >= 2


def _defines_recommendation_strength(section: dict[str, Any]) -> bool:
    """Report whether a section explains what this guideline's recommendation labels mean.

    The third content rescue, and the one that reaches furthest. A guideline's key to its
    own labels - "A strong recommendation is given when there is high-certainty evidence
    ... this means that all, or nearly all, women will want the recommended intervention" -
    is what makes every recommendation in the document readable. Publishers file it under
    whatever they please: it was found under `about the guidelines`, `about this guideline`,
    `how to use this guideline` and `reading guide`, 11 sections across the corpus. No list
    edit can follow it around, which is why this asks the body.

    Args:
        section: Section object from the guideline JSON.
    """
    return bool(_RECOMMENDATION_KEY_RE.search(_visible_subtree_text(section)))


def _states_guideline_scope(section: dict[str, Any]) -> bool:
    """Report whether a section says who or what the guideline applies to.

    The fifth rescue, and the one protecting the fact the North Star treats as
    non-negotiable: a claim can be true for adults and false for children, so the sentence
    that says which one a guideline covers is what stops a paediatric claim being
    confirmed against an adult one. Publishers bury it wherever they like - most often in
    Methods, which is a heading this list drops wholesale.

    Only the section's **own** text is read, never its descendants, and that is the whole
    design. Keeping a section keeps everything under it, so a scope sentence buried in a
    child would drag the panel roster and the database list back with it. Where the
    sentence sits in a child, `_PICO_FRAME_HEADINGS` already lifts that child out and
    leaves the process prose behind, which is the better repair - so this defers to it.
    Measured over the Methods sections carrying a scope statement, 7 of 10 have it in
    their own text and the rest are the finer mechanism's business.

    Args:
        section: Section object from the guideline JSON.
    """
    own_text = str(section.get("text") or "")
    if not own_text.strip():
        return False
    return bool(_GUIDELINE_SCOPE_RE.search(_visible_subtree_text({"text": own_text})))


def _reports_evidence_findings(section: dict[str, Any]) -> bool:
    """Report whether a section states what a search or a study actually found.

    The fourth rescue, for the same reason as `_holds_evidence_table`: one heading, two
    kinds of content. Nine of the ten sections named for a working group are rosters; the
    tenth is WHO's mpox guideline, where "GDG topic-specific working groups" holds the
    review's own results - "comparative interventional trials, which yielded no evidence"
    and "Only one small cohort study ... did not show a difference in outcomes". A
    statement that nothing was found is exactly what the corpus is meant to keep, since a
    verifier that cannot see it has to guess instead.

    One match is enough, unlike the evidence table above, and the difference is deliberate.
    "Certainty of evidence" can appear in a roster by accident, so that guard needs
    corroboration; these phrases only appear when something is being reported. A statement
    that a search found nothing is the whole finding, so requiring a second one would
    discard it. The publisher's own structure forces the point: the mpox review splits its
    results across three sibling sections, each holding exactly one such sentence, so a
    threshold of two rescued none of them.

    Measured across every section the skip list drops corpus-wide, this reaches three, all
    in that one guideline.

    Args:
        section: Section object from the guideline JSON.
    """
    return bool(_EVIDENCE_FINDING_RE.search(_visible_subtree_text(section)))


# A section headed "Protocol" is two different things depending on the publisher, and the
# heading cannot tell them apart. EAES and its partner societies publish the pre-registered
# plan for the review under it - 11 sections, 239,800 characters, each opening like a journal
# paper with an abstract or an introduction. noPKwE uses the same heading for imaging
# protocols: "Post intravenous contrast enhanced CT chest abdomen and pelvis with oral
# contrast", "Coverage: L5/S1 to anal verge". Those are instructions for scanning a patient
# and must stay, so the opening decides rather than the name.
_PROTOCOL_HEADINGS = frozenset({"protocol", "protocols"})
_STUDY_PROTOCOL_OPENING_RE = re.compile(r"\A\s*(?:abstract|introduction|guideline protocol)\b", re.IGNORECASE)


def _is_study_protocol(section: dict[str, Any], heading: str) -> bool:
    """Report whether a Protocol section is the review's own plan rather than clinical steps.

    Args:
        section: Section object from the guideline JSON.
        heading: The section's heading, already reduced to plain text.
    """
    if _skip_lookup_key(heading) not in _PROTOCOL_HEADINGS:
        return False
    return bool(_STUDY_PROTOCOL_OPENING_RE.match(_visible_subtree_text(section).lstrip()))


# Headings that are paperwork for one publisher and content for everyone else. A publisher is
# internally consistent in a way the corpus as a whole is not: the Stroke Foundation opens all
# eight of its guidelines with an "Introduction" of about 12,100 characters, and every one of
# them begins "The Stroke Foundation is a national charity that partners with the community to
# prevent, treat and beat stroke". 96,945 characters of the same organizational blurb.
#
# Introduction is emphatically NOT on the general skip list and must not be: measured across
# the corpus, 86% of introductions carry something a verdict could rest on, including
# definitions carrying thresholds that appear nowhere else in their document. This table is
# how a publisher-specific exception is written without weakening that.
#
# Keyed on `institutionName` as the catalogue spells it, which is what `ref.institution`
# carries.
_INSTITUTION_SKIP_HEADINGS: dict[str, frozenset[str]] = {
    "Canadian Rheumatology Association": frozenset(
        {
            "methods",
        }
    ),
    # Built by listing every section that survives the general rules, keeping the ones whose
    # heading names paperwork, and reading each one. What is here is how the guideline was
    # made, who made it, who signed it off, how it will be spread and updated, and what its
    # words mean - never what it says about a patient.
    #
    # What was deliberately left out, having been read:
    #
    # - GELA's "Guideline development process and methods" (208,578 characters). It looks
    #   like every other methods section and is not: two of the three copies have a whole
    #   qualitative evidence synthesis written into the body, with GRADE-CERQual confidence
    #   ratings and a summary-of-qualitative-findings table on hand-hygiene compliance.
    # - Every "Methods" that reports what the search found rather than how it ran - WHO
    #   maternal, the European Association for Endoscopic Surgery, the European Stroke
    #   Organisation, Alzheimer's Association, National Pain Centre. They carry odds ratios,
    #   confidence intervals and outcome rankings.
    # - Plain language summaries. They read like front matter and are the recommendations
    #   restated for a patient: "Once blood pressure is lower than 185 mmHg systolic or 110
    #   mmHg diastolic, alteplase can be given safely."
    # - WHO's implementation chapters that carry recommendation objects, and the WHO oxygen
    #   roadmap (jO0qrL), which files its actual recommendations under "Governance",
    #   "Implementation plan" and "Assessment of budget requirements".
    # - WHO maternal's per-recommendation implementation annexes and the "introducing the
    #   model of care" chapters, which carry doses - misoprostol 400 or 600 mg, oxytocin
    #   10 IU/mL, iron and folic acid.
    # - Cancer Council's "Screening process", which is the National Cervical Screening
    #   Policy, and the Malawi "Implementation of PDMCC", which is a treatment schedule.
    # - The European Mosquito Control Association's "Summary of non-evidence publications",
    #   a narrative review of vector-control interventions.
    #
    # Keyed on `institutionName` as the catalogue spells it, which is what `ref.institution`
    # carries. Note the trailing space in the WHO entry - it is in the catalogue.
    "Stroke Foundation": frozenset({"introduction"}),
    # Both of this publisher's guidelines open with a "Summary" that summarizes nothing
    # clinical - it says who wrote the guideline, who funded it, when the last evidence
    # search ran and how to cite it. "Summary" is emphatically not general skip-list
    # material; across the catalogue it is usually the summary of recommendations.
    #
    # Conversation Aids and Chairside Guides are the patient-facing material, and both are
    # images: 11,339 characters of markup around one sentence saying what the images are,
    # and two subsections whose entire visible text is "A pdf version of this chairside
    # guide can be downloaded here". Nothing in either survives image removal.
    #
    # Interest-holders is a table of organization names against tick marks for which
    # recommendations each one commented on.
    "American Dental Association and the University of Pennsylvania School of Dental Medicine": frozenset(
        {
            "summary",
            "conversation aids",
            "chairside guides",
            "appendix 3. interest-holders",
        }
    ),
    "ANZ Hearing Health Collaborative": frozenset(
        {
            "overview of methodology",
            "patient hearing journey",
            "what are living guidelines?",
        }
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
        {
            "guideline panel composition",
            "guideline scope",
            "guideline update process and external review process",
        }
    ),
    "Australia & New Zealand Musculoskeletal Clinical Trials Network": frozenset(
        {
            "glossary, abbreviations and acronyms",
        }
    ),
    "Australian Living Evidence Collaboration": frozenset(
        {
            "methods for the 2020 australian clinical practice guidelines",
        }
    ),
    "BE-SAFE": frozenset(
        {
            "adaptation and implementation of the guideline",
            "guideline development process",
            "navigate the guideline and related content",
        }
    ),
    "CARI Guidelines: Caring for Australians and New Zealanders with Kidney impairment": frozenset(
        {
            "guideline development methodology",
            "guideline development methods",
            "implementation and audit",
        }
    ),
    "Cancer Council Australia": frozenset(
        {
            "applicability to the australian setting",
            "barriers to implementation",
            "citation",
            # Sections this publisher repeats near-identically across its own guidelines,
            # found by comparing bodies on shared 8-word runs: "the assigned lead authors
            # were asked to draft their guideline chapter using the following format", "the
            # guideline recommendations were approved by the Chief Executive Officer of the
            # NHMRC on 14 April 2023".
            "consideration of priority groups",
            "writing the content",
            "publication approval",
            "screening of literature results against pre-defined inclusion and exclusion criteria",
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
            "purpose",
            "purpose and scope",
            "purpose of this guidance",
            "remit (scope)",
            "review process",
            "scope",
            "scope of this guideline",
            "statement of intent/disclaimer",
        }
    ),
    "Deakin Lifespan Institute": frozenset(
        {
            "scope and equity considerations of the guidelines",
            "the purpose of the guidelines",
        }
    ),
    (
        "Deutsche Gesellschaft für Psychiatrie und Psychotherapie, Psychosomatik und Nervenheilkunde e. V. (DGPPN)"
    ): frozenset(
        {
            "preface",
            "scope and purpose",
        }
    ),
    "European Association for Endoscopic Surgery and other Interventional Techniques": frozenset(
        {
            "purpose, scope and target users",
        }
    ),
    "European Mosquito Control Association (EMCA)": frozenset(
        {
            "governance – stakeholders",
            "references to chapter 4",
        }
    ),
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
    "Malawi Ministry of Health": frozenset(
        {
            "acronyms & abbreviations",
        }
    ),
    "McMaster Centre for Transfusion": frozenset(
        {
            "scope and objectives of guidelines",
        }
    ),
    "Monash University, Faculty of Pharmacy and Pharmaceutical Sciences": frozenset(
        {
            "about this guideline",
            "governance and stakeholder involvement",
            "purpose of guideline",
        }
    ),
    "National Blood Authority": frozenset(
        {
            "governance",
            "governance and process",
        }
    ),
    "National Health and Medical Research Council (NHMRC)": frozenset(
        {
            "index",
            "scope",
        }
    ),
    "National Neonatology Forum of India": frozenset(
        {
            "literature search strategies",
            "scope of the guidelines",
        }
    ),
    "National Pain Centre": frozenset(
        {
            "scope",
            "scope of the guideline and how to use the guideline",
        }
    ),
    "Nordic Federation of Obstetrics and Gynecology": frozenset(
        {
            "contributions of authors",
        }
    ),
    "Norwegian Orthopaedic Association - The Norwegian Medical Association": frozenset(
        {
            "method and background",
        }
    ),
    "Sundhedsstyrelsen": frozenset(
        {
            "implementation",
        }
    ),
    "The CI Task Force": frozenset(
        {
            "overview of methodology",
            "what are living guidelines?",
        }
    ),
    "The George Institute for Global Health India": frozenset(
        {
            "endorsement of the guideline",
            "scope of guideline",
        }
    ),
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
    # No trailing space, though the catalogue has one: `institutionName` really is
    # "World Health Organization (WHO) " in the JSON and `_parse_catalogue` strips it, so a
    # key copied from the catalogue matches nothing. All 32 headings below were dead until
    # the rendered output was checked against the publisher totals and WHO was missing from
    # them entirely.
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


# Headings a general rule would drop, in the one guideline where it must not.
#
# The corpus-wide skip list is judged on the case each entry was added for and never on the
# other 211 guidelines it also reaches. An audit that read everything the list removes -
# 6,370,364 characters, one reader per entry - found 20 entries removing clinical content
# today. Two examples of what was going: WHO maternal's "Applicability issues" carries the
# misoprostol induction dose ("25 ug, 2-hourly") and the instruction to stock calcium
# gluconate against magnesium sulfate toxicity; the National Neonatology Forum's 1,480-
# character "Executive summary" IS the neonatal pain guideline, holding the whole
# recommendation table with its strengths and certainty ratings.
#
# This exempts those sections rather than deleting the entries, deliberately. "Executive
# summary" earns its place on the general list: re-measured over all 74, 65% of their
# content quotes the body, and dropping them is Evan's call of 2026-08-06 made on that
# measurement. Removing the entry would undo a measured decision to fix an unmeasured one.
# What the audit established is narrower and is exactly what is recorded here: the specific
# guidelines where the general rule is wrong.
#
# Every entry below was reported by a reader who quoted the passage being lost, and then
# verified by finding that quote in the section. 15 further reports were not confirmed and
# are absent.
_GUIDELINE_KEEP_HEADINGS: dict[str, frozenset[str]] = {
    "6nYJxE": frozenset(
        {
            # The timing vocabulary closing this guideline's Methodology - "Immediate:
            # without delay, or within minutes, not hours. Urgent: minutes to several
            # hours. Very early: within hours and up to 24 hours. Early: within 48 hours."
            # - which is the only place the guideline quantifies the timing words its own
            # recommendations use. The reader found it in all eight Stroke Foundation
            # guidelines; the four process blocks above it go by inline rule instead.
            "methodology",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A stroke is damage to brain cells from a sudden disruption
            # of blood supply - either arterial blockage or bleeding in the brain - producing
            # symptoms lasting more than 24 hours.
            "glossary and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Stroke survivors should be involved in decisions about their
            # care at all times, and where they lack or have limited capacity, family members
            # should be involved in the decision-making. Also: Stroke patients with aphasia or
            # cognitive disorders should be offered easy-English or aphasia-friendly information
            # sheets and consent forms, clearly explained to them and their families.
            "introduction",
        }
    ),
    "WE8wOn": frozenset(
        {
            # The timing vocabulary closing this guideline's Methodology - "Immediate:
            # without delay, or within minutes, not hours. Urgent: minutes to several
            # hours. Very early: within hours and up to 24 hours. Early: within 48 hours."
            # - which is the only place the guideline quantifies the timing words its own
            # recommendations use. The reader found it in all eight Stroke Foundation
            # guidelines; the four process blocks above it go by inline rule instead.
            "methodology",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Stroke is sudden damage to brain cells causing symptoms that
            # last more than 24 hours, arising either from blockage of an artery or from bleeding
            # within the brain.
            "glossary and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Stroke patients with aphasia or cognitive impairment should
            # be offered easy-English or aphasia-friendly information sheets and consent forms,
            # clearly explained to them and their families.
            "introduction",
        }
    ),
    "8L0RME": frozenset(
        {
            # The timing vocabulary closing this guideline's Methodology - "Immediate:
            # without delay, or within minutes, not hours. Urgent: minutes to several
            # hours. Very early: within hours and up to 24 hours. Early: within 48 hours."
            # - which is the only place the guideline quantifies the timing words its own
            # recommendations use. The reader found it in all eight Stroke Foundation
            # guidelines; the four process blocks above it go by inline rule instead.
            "methodology",
            # Stroke Foundation glossary, 15,174 chars. Mixed: roughly half is genuine
            # methodology paperwork (Cochrane, Covidence, GRADE, PICO, MAGICapp, NHMRC,
            # COI form, public consultation), but the other half is clinical definitions
            "glossary and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Patients with aphasia or cognitive impairment should be
            # given easy-English / aphasia-friendly information sheets and consent forms,
            # explained to them and their families, when formal consent is required. Also: A
            # stroke survivor who lacks decision-making capacity should have family members
            # involved in decisions about their care.
            "introduction",
        }
    ),
    "E80D0E": frozenset(
        {
            # Epidemiology and burden of disease with hard numbers: global maternal
            # deaths, their geographic concentration, the share caused by obstetric
            # haemorrhage, and the annual count of women experiencing postpartum
            "executive summary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Tranexamic acid should not be mixed with oxytocin before
            # administration, because some tranexamic acid products can interact with it. Also:
            # Women should be monitored regularly for blood loss and clinical signs of bleeding in
            # the first few hours after birth.
            "dissemination and implementation of the recommendations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Postpartum haemorrhage is blood loss of 500 ml or more, and
            # severe postpartum haemorrhage is blood loss of 1000 ml or more. Also: Randomized
            # controlled trials have compared methods of assessing postpartum blood loss for
            # detecting postpartum haemorrhage.
            "methods",
        }
    ),
    "E80ezE": frozenset(
        {
            # Names the specific drugs and drug classes required for imminent preterm
            # birth care - dexamethasone or betamethasone as the antenatal
            # corticosteroids, magnesium sulfate, and a macrolide or penicillin as the
            "applicability issues",
        }
    ),
    "EK0DDj": frozenset(
        {
            # The only section this entry removes, and it is not a changelog - it is the
            # living-guideline update narrative of the DGPPN S3 Schizophrenia guideline,
            # which restates revised recommendations in full and carries clinical
            "what's new?",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Care for people with schizophrenia should be
            # multi-professional and multimodal in every phase, delivered with a consistently
            # empathetic and appreciative therapeutic attitude.
            "preface",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - For people with schizophrenia, the choice between outpatient
            # and inpatient care is set by the stage of the disease and the severity and acuity of
            # symptoms, with acute episodes treated by acute inpatient psychiatric care followed
            # by rehabilitation and then outpatient treatment. Also: Schizophrenia is classified
            # under diagnosis code F20 in ICD-10.
            "scope and purpose",
        }
    ),
    "EKeJyL": frozenset(
        {
            # The GRADE outcome-prioritization step: a table of average importance scores (1-9)
            # given to candidate outcomes by stakeholders and external experts. No
            "prioritization of the outcomes",
            # The magnesium sulfate safety block for pre-eclampsia and eclampsia:
            # calcium gluconate must be stocked as the antidote for magnesium sulfate
            # toxicity, the infusion rate must be closely monitored, and the woman must
            "applicability issues",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Calcium supplementation for pregnant women at risk of
            # pre-eclampsia is given at a daily dose of 1.5–2.0 g. Also: For women with severe
            # pre-eclampsia at 34–36 weeks of gestation, interventionist management is established
            # as more effective than expectant management.
            "research implications",
        }
    ),
    "EZVOaE": frozenset(
        {
            # WHO Ebola/Marburg infection-prevention glossary, 8,140 chars - states what
            # cleaning does and does not achieve and defines the direct and indirect
            # routes of contact transmission for filoviruses.
            "glossary",
        }
    ),
    "EaG1dL": frozenset(
        {
            # Clinical definition of complete mesocolic excision (CME), plus a
            # survival-benefit recommendation for right-sided colon cancer and the
            # factors that drive the choice.
            "patient version",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Complete mesocolic excision involves central ligation of the
            # ileocolic artery and vein and a distal colon resection margin of at least 5 cm.
            "methods",
        }
    ),
    "Edr04L": frozenset(
        {
            # Three checkable clinical statements sit inside an otherwise methodological
            # section, and none of them survives anywhere else in the rendered guideline
            # (verified by grep against
            "development of the guidelines",
        }
    ),
    "Ee438n": frozenset(
        {
            # The complete numbered recommendation set for MDMA-assisted psychotherapy
            # in PTSD: dose, number of dosing sessions, minimum age, symptom-duration
            # threshold, a named severity-scale cutoff, and a strong recommendation
            "executive summary",
            # PTSD / MDMA-assisted therapy glossary, 10,096 chars - defines the
            # guideline's primary outcome instrument, its item count and its score
            # range, and the regulatory conditions under which MDMA may be prescribed.
            "glossary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Muscle tightness, jaw clenching, excessive sweating,
            # decreased appetite, insomnia and nausea are adverse events associated with
            # MDMA-assisted psychotherapy.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The efficacy of specific MDMA products for PTSD has been
            # established through the routine drug approval pathway in Australia.
            "purpose of guideline",
        }
    ),
    "Ee4Orn": frozenset(
        {
            # Platelet-count response definitions with numeric thresholds,
            # burden-of-disease claims about death and intracranial haemorrhage in
            # severe ITP, and the panel's patient values-and-preferences statement.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Intracranial hemorrhage complicated about 1.1% of adult and
            # 0.7% of pediatric ITP hospitalizations, with a mortality rate of about 27%. Also: A
            # critical bleed in immune thrombocytopenia is a bleed into a critical anatomical site
            # (intracranial, intraspinal, intraocular, retroperitoneal, pericardial, or
            # intramuscular with compartment syndrome) or an ongoing bleed causing hemodynamic
            # instability or respiratory compromise.
            "scope and objectives of guidelines",
        }
    ),
    "Eea27E": frozenset(
        {
            # Clinical claims about which deep vein thromboses matter and why VTE and
            # bleeding risks differ after stroke - checkable statements about risk, not
            # process.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A recent cerebral infarct increases the risk of intracranial
            # bleeding, and the risk of venous thromboembolism is higher after stroke than in many
            # other medical conditions.
            "search strategy",
        }
    ),
    "Eez3Kj": frozenset(
        {
            # An 18,660-character summary of an evidence-based nutrition guideline for
            # head and neck cancer. The visible opening is mostly scope, but it carries
            # a burden-of-disease claim and states the guideline is presented as
            "executive summary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Head and neck cancer patients smoking more than 20
            # cigarettes per day are more likely to require enteral tube feeding. Also: Intensive
            # dietary counselling, with oral nutritional supplements and/or tube feeding, is
            # recommended before, during and for up to 6 months after treatment.
            "introduction and guideline development",
        }
    ),
    "EezrQj": frozenset(
        {
            # Clinical epidemiology about how GFR decline should be read differently by
            # age, plus everything that makes the guideline's absolute numbers
            # interpretable. The section is where the KDIGO GFR-plus-albuminuria risk
            "methods: how this guideline was created",
        }
    ),
    "Eg9eVL": frozenset(
        {
            # Oxytocin safety instructions for labour augmentation: do not leave the
            # woman unattended, closely monitor the intravenous infusion rate (called
            # out as extremely crucial where gravity drips are used), observe the
            "applicability issues",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Oxytocin alone for augmentation of labour has clearly
            # established benefits.
            "research implications",
        }
    ),
    "EgXyej": frozenset(
        {
            # The elemental calcium tablet strength for a pre-pregnancy calcium
            # supplementation programme, tied to the WHO Model List of Essential
            # Medicines. A milligram dose of a supplement is exactly the kind of number
            "applicability issues",
        }
    ),
    "Evqmmn": frozenset(
        {
            # Australian blood supply administration: the National Blood Agreement, Lifeblood
            # collection contracts, CSL Behring manufacturing, Commonwealth/state fu
            "supply considerations",
            # A research agenda listing topics for future study, not guidance on managing a
            # patient.
            "evidence gaps and potential research priorities",
            # The National Blood Authority critical bleeding guideline defines its whole
            # target population inside Methodology, under Study selection criteria. It
            # is a clinical definition, which the audit rules count as a checkable claim
            "methodology",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Group O RhD positive red blood cells may be used in a major
            # haemorrhage protocol for adult males and for females over 50 years of age. Also:
            # About 6.5% of the Australian population has group O RhD negative blood.
            "challenges",
        }
    ),
    "Jn37kn": frozenset(
        {
            # A research agenda listing what further studies the guideline developers want done;
            # every bullet is a call for more research, not guidance.
            "research gaps",
            # NHMRC infection-control glossary, 37,740 chars - carries the particle-size
            # threshold that separates aerosols from droplets, which is what airborne
            # versus droplet precautions turn on, and the clinical definitions of the
            "glossary",
            # A "process report" keep lived here until 2026-08-11, for one sentence about
            # uniform and clothing requirements reducing cross-infection. Checked before
            # removing: the body states the same guidance eight times, including the
            # recommendation itself and its trial evidence, so the 23,535-character
            # appendix bought nothing the document does not already carry.
        }
    ),
    "Kj2R8j": frozenset(
        {
            # The timing vocabulary closing this guideline's Methodology - "Immediate:
            # without delay, or within minutes, not hours. Urgent: minutes to several
            # hours. Very early: within hours and up to 24 hours. Early: within 48 hours."
            # - which is the only place the guideline quantifies the timing words its own
            # recommendations use. The reader found it in all eight Stroke Foundation
            # guidelines; the four process blocks above it go by inline rule instead.
            "methodology",
            # Stroke Foundation glossary, 15,081 chars - a near-identical copy of the
            # 8L0RME glossary (word-level diff differs by about 8 tokens). Carries the
            # same 24-hour stroke/TIA thresholds and the same procedure definitions.
            "glossary and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Stroke survivors should be involved in decisions about their
            # own care at all times, and where they lack or have limited capacity, family members
            # should be involved instead. Also: Stroke patients with aphasia or cognitive
            # impairment should be given easy-English / aphasia-friendly information sheets and
            # consent forms, explained to them and their families.
            "introduction",
        }
    ),
    "L6zBvL": frozenset(
        {
            # A 'Definitions' subsection giving the anatomic distance thresholds that
            # define low- versus mid-rectal cancer, which is the population every
            # recommendation in the guideline applies to.
            "methods",
            # The inclusion-criteria table holds the only statement anywhere in this
            # document of what 'low-rectal' and 'mid-rectal' cancer mean in centimetres
            # from the anal verge, and which tumours are excluded as high-rectal. Both
            "protocol",
        }
    ),
    "LAR07n": frozenset(
        {
            # Deakin fathers' mental-health glossary, 3,430 chars - defines the symptom
            # clusters the guideline's outcomes are reported against, and the perinatal
            # time window.
            "glossary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The longitudinal evidence base identifying preconception
            # predictors of fathers' mental health in the early parenting years is well
            # established.
            "key areas for future development",
        }
    ),
    "LAkxVE": frozenset(
        {
            # A 'Related guidelines' subsection that reproduces NICE's clinical
            # recommendations for reducing caesareans - with gestational timings, a
            # partogram action-line interval, and an explicit contraindication list -
            "methods",
            # A statement of what related systematic reviews FOUND: four named
            # interventions are asserted, with citations, to reduce caesarean births or
            # increase spontaneous vaginal births. Two of the four - simulation-based
            "research implications",
        }
    ),
    "Lpv2kE": frozenset(
        {
            # The body-mass-index eligibility threshold and the age boundaries of the
            # planned subgroups. The surviving render recommends sleeve gastrectomy or
            # Roux-en-Y gastric bypass 'for the management of severe obesity' and
            "protocol",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Severe obesity is defined as a body mass index above 35
            # kg/m², or above 30 kg/m² when other conditions such as diabetes, high blood pressure
            # or sleep apnoea are present.
            "purpose, scope and target users",
        }
    ),
    "Lq0orj": frozenset(
        {
            # A "Key recommendations" block giving the WHO clinical recommendations and
            # good practice statements for filovirus disease — patient monitoring,
            # laboratory monitoring and downstream interventions.
            "executive summary",
            # A 'Patient values and preferences' subsection reporting that filovirus
            # survivors have frequent significant psychological distress, plus the
            # panel's adopted values statement that was then applied to every PICO in
            "methods",
        }
    ),
    "LqgJ3E": frozenset(
        {
            # Effect estimates and risk factors for COVID-19 in pregnancy, with numbers:
            # relative risk of stillbirth and preterm birth, a BMI threshold, a
            # maternal-age threshold, and vaccine-effectiveness percentages against
            "executive summary",
        }
    ),
    "LrRxrL": frozenset(
        {
            # WHO glossary, 6,666 chars - names exactly which drugs the GLP-1
            # recommendations cover, defines BMI as a formula, and sets the age cutoff
            # for 'adult'.
            "glossary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Obesity is defined as a body mass index of 30 kg/m2 or
            # higher. Also: Liraglutide is a GLP-1 receptor agonist given as a once-daily
            # subcutaneous injection; the same block also settles that semaglutide is a weekly
            # subcutaneous or daily oral GLP-1 receptor agonist and that tirzepatide is a weekly
            # subcutaneous GIP/GLP-1 dual agonist.
            "scope and target audience",
        }
    ),
    "LwRMXj": frozenset(
        {
            # WHO malaria terminology, 40,224 chars - operational case and surveillance
            # definitions used throughout the guideline, plus recommended age bands and
            # a serious-adverse-event definition.
            "glossary",
        }
    ),
    "LwvKej": frozenset(
        {
            # The bowel-preparation recommendation itself with timing, the site-specific
            # regimen (oral antibiotics alone vs MBP + oral antibiotics vs enema for
            # rectal resection), the 1-3 litre laxative volume, and the laxative adverse
            "patient version",
        }
    ),
    "LwvpGj": frozenset(
        {
            # The finding of the WHO scoping review of what women want from antenatal
            # care, with its certainty rating - the patient-values evidence the whole
            # ANC guideline is built around.
            "methods",
        }
    ),
    "NnV76E": frozenset(
        {
            # Stroke Foundation glossary, 14,894 chars - near-identical copy. Carries
            # the pulmonary embolism and deep vein thrombosis definitions plus the
            # atrial fibrillation definition.
            "glossary and abbreviations",
            # The Stroke Foundation parks its timing vocabulary at the end of its
            # Methodology section. These four lines are the only place the guideline
            # quantifies the words its own recommendations use - 'urgent brain CT',
            "methodology",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Stroke patients with aphasia or cognitive impairment should
            # be given easy-English / aphasia-friendly information sheets and consent forms,
            # explained to them and their families, when formal consent is required. Also: A
            # stroke survivor who lacks or has limited decision-making capacity should have family
            # members involved in decisions about their care.
            "introduction",
        }
    ),
    "QnoKGn": frozenset(
        {
            # The timing vocabulary closing this guideline's Methodology - "Immediate:
            # without delay, or within minutes, not hours. Urgent: minutes to several
            # hours. Very early: within hours and up to 24 hours. Early: within 48 hours."
            # - which is the only place the guideline quantifies the timing words its own
            # recommendations use. The reader found it in all eight Stroke Foundation
            # guidelines; the four process blocks above it go by inline rule instead.
            "methodology",
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere in the guideline.
            "cost effectiveness summaries",
            # Stroke Foundation glossary, 14,993 chars - near-identical copy. Includes
            # the 'drip and ship' thrombolysis pathway, which describes when
            # thrombolysis is started and where the patient is transferred.
            "glossary and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Stroke survivors should be involved in decisions about their
            # own care, and where the patient lacks capacity or has limited capacity, family
            # members should be involved in the decision-making. Also: Stroke patients with
            # aphasia or cognitive impairment should be offered easy-English / aphasia-friendly
            # information sheets and consent forms, explained to them and their families, when
            # formal consent is required.
            "introduction",
        }
    ),
    "VLpK8j": frozenset(
        {
            # The timing vocabulary closing this guideline's Methodology - "Immediate:
            # without delay, or within minutes, not hours. Urgent: minutes to several
            # hours. Very early: within hours and up to 24 hours. Early: within 48 hours."
            # - which is the only place the guideline quantifies the timing words its own
            # recommendations use. The reader found it in all eight Stroke Foundation
            # guidelines; the four process blocks above it go by inline rule instead.
            "methodology",
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere in the guideline.
            "cost effectiveness summaries",
            # Stroke Foundation glossary, 14,993 chars - near-identical copy. Carries
            # the enteral feeding route definition and the thrombolysis service-model
            # definition.
            "glossary and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Stroke survivors should be involved in decisions about their
            # own care, and when they lack or have limited capacity, family members should be
            # involved in the decision-making instead. Also: Stroke patients with aphasia or
            # cognitive impairment should be given easy-English / aphasia-friendly information
            # sheets and consent forms, explained to them and their families, when formal consent
            # is required.
            "introduction",
        }
    ),
    "j1QPrj": frozenset(
        {
            # A reversal of a prior recommendation, naming the drug now recommended for
            # acute and maintenance tocolysis. A verdict on whether nifedipine is
            # recommended for tocolysis rests exactly here.
            "executive summary",
            # A declarative footnote at the end of the section defines maintenance
            # tocolytic therapy by a timing threshold: tocolytic use continued after the
            # first 48 hours, with either the same drug or a different one. That is a
            "research implication",
        }
    ),
    "j1Wqrn": frozenset(
        {
            # The whole 6,426-character section is clinical content, not front or back
            # matter. It is the applicability-and-practical-issues narrative of a BMJ
            # Rapid Recommendation on SGLT-2 inhibitors and GLP-1 receptor agonists in
            "how to use these recommendations/understanding the recommendations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - SGLT2 inhibitors are taken as daily oral medications while
            # GLP-1 receptor agonists are given as weekly injections, and diabetic ketoacidosis
            # and genital infection are harms of SGLT2 inhibitors while severe gastrointestinal
            # events are a harm of GLP-1 receptor agonists.
            "methods to inform values and preferences discussions for recommendations",
        }
    ),
    "j1kmYn": frozenset(
        {
            # A gestational-age timing threshold - one ultrasound scan before 24 weeks
            # for accurate dating - stated as a requirement for the post-term pregnancy
            # recommendations. The identical sentence is removed again from [ny74yj] and
            "applicability issues",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Mechanical methods for induction of labour have been
            # evaluated in more than 100 randomized trials involving over 22 000 women.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - No single method of labour induction has been shown to be
            # superior to the other available methods.
            "research priorities",
        }
    ),
    "j20X4n": frozenset(
        {
            # A GRADE-certainty effect claim for partial posterior (Toupet)
            # fundoplication versus total posterior fundoplication, across dysphagia,
            # serious postoperative complications and reoperation.
            "patient version",
            # Prevalence of gastroesophageal reflux. The surviving Introduction keeps
            # the claim in words only - 'GERD affects a substantial proportion of the
            # general population' - and the figure appears nowhere else in the document.
            "protocol",
        }
    ),
    "j2QQ4E": frozenset(
        {
            # Infection-prevention clinical instructions for the maternal peripartum
            # infection guideline: surgical aseptic technique at caesarean section with
            # a stated effect (reducing postoperative complications including
            "applicability issues",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - There is good evidence that routine prophylactic antibiotics
            # prevent infectious morbidity in women with episiotomy or after normal low-risk
            # labour.
            "research implications",
        }
    ),
    "j2QZZE": frozenset(
        {
            # A clinical definition of early-onset group B streptococcal disease with
            # its site and time window, the annual burden, and the four named screening
            # strategies for deciding who gets intrapartum antibiotic prophylaxis, with
            "executive summary",
            # Restates an existing WHO clinical recommendation, with its population and
            # intervention, as context for the new question.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Trial evidence directly comparing universal GBS screening
            # (including rapid intrapartum testing) with risk-based screening was already
            # available at the time of this recommendation.
            "updating the recommendations",
        }
    ),
    "j7q7Gn": frozenset(
        {
            # Recommendation to operate on fit patients with paraesophageal hernia, a
            # mesh recurrence-reduction claim, and the mesh erosion / dysphagia adverse
            # event with its severity and frequency.
            "patient version",
            # The only symptom list for paraesophageal hernia in the document. The
            # surviving Introduction says symptoms 'can be respiratory and
            # cardiovascular' but names none of them; anemia, heartburn, dyspnea and
            "protocol",
        }
    ),
    "j98OoE": frozenset(
        {
            # A research agenda: how future studies should be conducted and which topics the
            # Working Group wants studied. No clinical guidance and no recommendation
            "suggestions for future research",
            # CARI chronic-kidney-disease glossary, 6,660 chars - makes a substantive
            # health-outcome claim about cultural connection and defines the
            # patient-values terms the guideline's recommendations are framed in.
            "glossary",
        }
    ),
    "jDReJn": frozenset(
        {
            # A stated adverse consequence of late cord clamping (neonatal jaundice)
            # with the instruction to screen and treat for it, plus the instruction not
            # to leave a woman alone in the first hours after delivery of the baby and
            "applicability issues",
            # The clinical definition of postpartum haemorrhage with its blood-loss
            # threshold and time window, plus a pointer that the 32 adopted
            # recommendations are set out in boxes inside this same section.
            "executive summary",
        }
    ),
    "jDRvgn": frozenset(
        {
            # The complete 2,140-character summary of a mosquito-control guideline:
            # vector epidemiology in Europe by species and territory, and the
            # recommendations themselves with the reason their strength is limited.
            "executive summary",
            # A "glossary" keep lived here until 2026-08-10 - its own note called it the
            # weakest of the fifteen. Evan's call: read in full, it is ~85% research
            # vocabulary, and it goes.
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Sustained success of mosquito vector control depends on the
            # participation and engagement of local communities. Also: Community participation via
            # social media can be used to track and forecast mosquito-borne disease outbreaks.
            "governance – stakeholders",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Mosquito vector control interventions used in Europe have
            # been shown in studies to reduce human mosquito-borne disease (epidemiological
            # outcomes).
            "methods",
        }
    ),
    "jDeeDL": frozenset(
        {
            # The new WHO recommendations on Ebola-specific therapeutics: named
            # monoclonal antibodies recommended, a neonatal age cutoff, and conditional
            # recommendations against two named drugs, plus the trial they rest on.
            "executive summary",
            # An ancestor of a section that carries The case fatality rate of Ebola virus disease
            # was about 39.5% in the 2013–2016 West Africa outbreak and about 66% in the 2018–2020
            # Democratic Republic of the Congo and Uganda outbreak.. Kept because a skipped
            # section is never descended into, so the keep on the section itself would never be
            # reached.
            "methods: how this guideline was created",
        }
    ),
    "jMMeqj": frozenset(
        {
            # A 10,653-char section from Monash University (psychotropic medications in
            # people living with dementia and in residential aged care) that the same
            # skip key matches. Most of it is GRADE and Evidence-to-Decision
            "about this guideline",
            # 35,194 characters of clinical instruction for dementia and changed
            # behaviours, laid out as numbered Good Practice Statements about assessment
            # of unmet needs, non-pharmacological strategies, documentation, and
            "executive summary",
            # Dementia guideline glossary, 20,375 chars - names the specific members of
            # a drug class in clinical use, and defines adverse drug events and advance
            # care planning.
            "glossary",
        }
    ),
    "jNxJmn": frozenset(
        {
            # A 'Values and preferences' subsection with the panel's outcome importance
            # ratings and the absolute risk differences it deemed important, and then
            # the recommendation itself stated inside Methods.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Tenecteplase is superior to alteplase as a thrombolytic
            # agent, on the evidence of small randomized trials.
            "scope",
        }
    ),
    "jO0lNL": frozenset(
        {
            # Country-level epidemiology with rates: neonatal and under-five mortality
            # per 1000 live births for Nigeria and sub-Saharan Africa, and the share of
            # under-five deaths occurring in the neonatal period.
            "executive summary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Hand hygiene training should reach non-clinical hospital
            # staff such as cleaners, not only clinical staff, and should be delivered regularly.
            "implementation considerations",
        }
    ),
    "jO3B7j": frozenset(
        {
            # The WHO population-ageing projection that frames this guideline's whole
            # burden-of-disease case. Absent from the survivor. (The other epidemiology
            # figure in this section, appendicitis risk of 5.2% between ages 65 and 75,
            "protocol",
        }
    ),
    "jOKYGj": frozenset(
        {
            # The guideline's definition of overdiagnosis, given twice in complementary
            # forms, plus the claim about how overdiagnosis scales with adenoma
            # detection rate. Overdiagnosis is one of the three burdens the entire
            "how the guideline was made",
        }
    ),
    "jWN6oE": frozenset(
        {
            # An exact drug dose, dosing interval and tablet-dilution method for oral
            # misoprostol used to induce labour, plus two facility-readiness
            # instructions (tocolytics must be available; the facility must be prepared
            "applicability issues",
            # An induction-of-labour rate figure, and the section then runs straight
            # into a "General principles related to the practice of induction of labour"
            # list whose first clinical instruction is visible before the excerpt cuts
            "executive summary",
        }
    ),
    "jXXZNj": frozenset(
        {
            # The entire neonatal pain-relief guideline. This section is 1,480
            # characters and holds the whole recommendation table: intervention,
            # suggested modality, strength of recommendation and certainty of evidence
            "executive summary",
        }
    ),
    "jlPRdj": frozenset(
        {
            # Scope of the WHO influenza recommendations including a post-exposure
            # timing window for antiviral prophylaxis, plus pooled baseline risk
            # estimates for hospitalisation and death and the definitions of high and
            "executive summary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Randomized controlled trials have evaluated corticosteroids
            # as a treatment for severe influenza.
            "guideline development process",
        }
    ),
    "n303gE": frozenset(
        {
            # A "Recommendations summary" heading followed by the actual WHO
            # recommendations for arboviral disease (dengue, chikungunya, Zika, yellow
            # fever), each with its named drug, its direction and its GRADE certainty
            "executive summary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Arbovirus epidemics are increasing in size and frequency.
            "guideline development and implementation",
            # An ancestor of a section that carries Randomized controlled trial evidence exists
            # for the treatment of Zika, chikungunya and yellow fever.. Kept because a skipped
            # section is never descended into, so the keep on the section itself would never be
            # reached.
            "guideline development process",
        }
    ),
    "n3QAOj": frozenset(
        {
            # Appendix listing the scope questions the guideline set out to answer, grouped by
            # role - every line is one of the question headings already used verbat
            "clinical question list",
            # Cancer Council chemotherapy glossary, 16,065 chars - defines the dosing
            # quantity used to calculate carboplatin dose, plus allogeneic/autologous
            # transplant and bioavailability.
            "glossary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Patients self-administering oral cancer therapy (oral
            # chemotherapy and oral targeted therapy) need education, written information and
            # follow-up to achieve correct dosing and adherence. Also: Dosing errors in cancer
            # chemotherapy harm patients: an overdose can be fatal, and an underdose can worsen
            # disease control and patient outcomes.
            "foreword",
        }
    ),
    "nBAZDL": frozenset(
        {
            # The outcome list inside the Methodology section of the National
            # Neonatology Forum of India hypoglycemia guideline carries a numeric
            # diagnostic threshold, plus a clinical definition of major neurological
            "methodology",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Prompt detection and treatment of hypoglycemia in at-risk
            # neonates can prevent immediate and long-term adverse outcomes.
            "scope of the guidelines",
        }
    ),
    "nBAezL": frozenset(
        {
            # The panel's minimally important benefit threshold, inside the 'Values and
            # preferences' passage. This is the numeric bar the panel used to decide
            # whether a well-informed patient would accept proactive therapeutic drug
            "how this guideline was created",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Randomised trials have evaluated proactive therapeutic drug
            # monitoring during induction with biologics other than intravenous infliximab (e.g.
            # adalimumab). Also: Neutralising anti-drug antibodies bind the active site of a
            # biologic drug, block its action and increase its clearance, reducing its effect.
            "methodology",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Validated tools are available in clinical use to
            # risk-stratify patients with inflammatory bowel disease, inflammatory arthritis or
            # psoriasis. Also: Randomised trial evidence exists comparing proactive therapeutic
            # drug monitoring of biologic drugs with no monitoring or reactive monitoring in
            # inflammatory bowel disease, inflammatory arthritis and psoriasis.
            "navigate the guideline and related content",
            # An ancestor of a section that carries Anti-drug antibody results can be interpreted
            # on their own, without reference to the patient's serum drug (TNF inhibitor)
            # concentration.. Kept because a skipped section is never descended into, so the keep
            # on the section itself would never be reached.
            "implementation",
            # An ancestor of a section that carries Anti-drug antibody results can be interpreted
            # on their own, without reference to the patient's serum drug (TNF inhibitor)
            # concentration.. Kept because a skipped section is never descended into, so the keep
            # on the section itself would never be reached.
            "implementation and adaptation of this guideline",
        }
    ),
    "nBRK8n": frozenset(
        {
            # A named external study with its participant count and what it found - not
            # how a search ran, but its result - plus the full patient
            # values-and-preferences narrative. The section reports a discrete choice
            "methods: how this guideline was created",
        }
    ),
    "nBkgRE": frozenset(
        {
            # A 'Diagnostic criteria' subsection giving the full radiological case
            # definition of extracranial and intracranial artery dissection, plus the
            # pathology distinguishing mycotic and blood blister-like aneurysms from
            "methods",
        }
    ),
    "nBpo1j": frozenset(
        {
            # Burden of disease plus the clinical definitions that scope the guideline —
            # the gestational-age cutoff for preterm and the weight range defining low
            # birth weight.
            "executive summary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - In preterm or low-birth-weight infants, early enteral
            # feeding means starting enteral feeds before 72 hours of age, and delayed enteral
            # feeding means starting at or after 72 hours.
            "scope",
        }
    ),
    "nJ5zyL": frozenset(
        {
            # Head-to-head comparative effectiveness ranking of common bile duct stone
            # clearance strategies (intraoperative ERCP vs preoperative ERCP vs common
            # bile duct exploration), plus the natural-history complications of a
            "patient version",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Common bile duct stones are diagnosed with MRCP, ERCP or
            # intraoperative cholangiography, and present acutely as acute cholecystitis,
            # cholangitis or biliary pancreatitis.
            "methods",
        }
    ),
    "nV6X3n": frozenset(
        {
            # The audit flagged "Glossary and abbreviations" here, but that heading is only a
            # wrapper: the 12,979 characters it was flagged for - AB word lists, speech
            # audiometry, what an audiologist does, audiometric thresholds - live in its
            # Glossary child, and the general list drops that child by its own name. Naming
            # the parent rescued an empty heading and none of the content. Both children are
            # named instead, which restores the definitions and leaves no bare heading.
            # The parent's own name is on the corpus-wide list, so exempting only the
            # children leaves the parent to take them with it; the wrapper and the Glossary
            # child are both named. The "abbreviations and acronyms" child left this set
            # 2026-08-10 with the glossary-family cut: a pure expansion table, dropped by
            # the per-guideline rule while the clinical glossary beside it stays.
            "glossary and abbreviations",
            "glossary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Adults with sensorineural or mixed hearing loss should be
            # offered referral for cochlear implant evaluation when the three-frequency (500,
            # 1000, 2000 Hz) unaided pure tone average is 60 dB HL or greater in any ear together
            # with an unaided AB-word phoneme score of 70% or less in any ear.
            "guideline amendments",
        }
    ),
    "nV6zvn": frozenset(
        {
            # The numeric decision thresholds the whole recommendation is built on. The
            # section states the anchor-based minimal important difference (MID) for
            # every key outcome, then sets the bar the panel actually applied: half the
            "methods: how this guideline was created",
        }
    ),
    "noPKwE": frozenset(
        {
            # Cancer Council Australia colorectal-cancer glossary, 13,453 chars. Almost
            # every entry is a checkable clinical claim, not paperwork: drug regimen
            # composition, what named operations remove, test mechanisms, a
            "glossary and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - For a patient presenting with symptoms suggestive of
            # colorectal cancer, the target maximum interval from first presentation to healthcare
            # until diagnosis is 120 days. Also: Routine mechanical bowel preparation before bowel
            # surgery remains an acceptable option for surgeons who prefer it.
            "barriers to implementation",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Patients with late-stage cancer often present with severe
            # symptoms and are diagnosed promptly, yet have worse outcomes than patients whose
            # diagnosis took longer.
            "methodological issues",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Clinicians caring for a person diagnosed with colorectal
            # cancer should take account of the specific needs of younger patients, Aboriginal and
            # Torres Strait Islander patients, and culturally and linguistically diverse patients.
            "target populations",
        }
    ),
    "noPQkE": frozenset(
        {
            # WHO child-wasting guideline, 11,193 chars - the numeric recovery criteria
            # for exiting nutritional treatment live in the glossary, with z-score
            # cutoff, MUAC in millimetres, age range and required number of visits. Also
            "glossary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Neonates less than 28 days old require different clinical
            # management approaches and protocols than older infants. Also: Mortality in children
            # with severe wasting and/or nutritional oedema remains high, particularly in
            # inpatient care and after exit from treatment.
            "scope",
            # An ancestor of a section that carries Direct comparative evidence exists testing one
            # set of inpatient admission criteria against another in children.. Kept because a
            # skipped section is never descended into, so the keep on the section itself would
            # never be reached.
            "evidence retrieval, synthesis, and assessment",
            # An ancestor of a section that carries Direct comparative evidence exists testing one
            # set of inpatient admission criteria against another in children.. Kept because a
            # skipped section is never descended into, so the keep on the section itself would
            # never be reached.
            "guideline development process and methods",
        }
    ),
    "noaRMj": frozenset(
        {
            # The entry's entire footprint is this one section, and the section is not
            # paperwork. It is the only place in the guideline that states (1) the
            # findings of the linked systematic review of patient values and preferences
            "how this guideline was made",
        }
    ),
    "ny70vj": frozenset(
        {
            # Clinical definition of complicated diverticulitis, its symptom set and
            # complications, and the three named surgical options with the factors
            # selecting between them.
            "patient version",
        }
    ),
    "nyXKVL": frozenset(
        {
            # The findings of the qualitative evidence synthesis on the postnatal
            # period, and the formal definition of a 'positive postnatal experience'
            # (Box 2.1) that is the overarching outcome for the whole guideline.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Randomized trials have compared bed-sharing with the mother
            # or caregiver against no bed-sharing for neonatal and infant outcomes in term
            # newborns. Also: There is specific evidence and a specific recommendation on sleep
            # time for the newborn period.
            "changes from the approved scope of this guideline",
        }
    ),
    "nyXP0L": frozenset(
        {
            # A clinical definition carrying the exact blood-pressure thresholds that
            # scope the whole guideline on antihypertensive drugs in pregnancy.
            "executive summary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - In pregnant women being treated for non-severe hypertension,
            # antenatal follow-up visits should be scheduled every two to four weeks when blood
            # pressure is well controlled and more frequently when it is not, and blood pressure
            # should not be lowered below the lower limits of normal. Also: Women with non-severe
            # hypertension in pregnancy should be counselled on the risks, benefits and treatment
            # options before treatment decisions are made.
            "dissemination and implementation of the recommendations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - There is direct evidence establishing the cost-effectiveness
            # of antihypertensive medication for hypertension in pregnant women.
            "methods",
        }
    ),
    "ojmKvn": frozenset(
        {
            # The timing vocabulary closing this guideline's Methodology - "Immediate:
            # without delay, or within minutes, not hours. Urgent: minutes to several
            # hours. Very early: within hours and up to 24 hours. Early: within 48 hours."
            # - which is the only place the guideline quantifies the timing words its own
            # recommendations use. The reader found it in all eight Stroke Foundation
            # guidelines; the four process blocks above it go by inline rule instead.
            "methodology",
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere in the guideline.
            "cost effectiveness summaries",
            # Stroke Foundation glossary, 14,894 chars - near-identical copy. Same
            # 24-hour TIA threshold and urgent-assessment instruction.
            "glossary and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Stroke survivors should be involved in decisions about their
            # own care, and where they lack or have limited decision-making capacity, family
            # members should be involved in the decision-making. Also: Patients with aphasia or
            # cognitive impairment who need to give formal consent should be offered easy-English
            # / aphasia-friendly information sheets and consent forms, explained to them and their
            # families.
            "introduction",
        }
    ),
    "pEQmQE": frozenset(
        {
            # The progesterone doses and routes studied, and the cervical-length,
            # gestational-age and obstetric-history thresholds that define who the
            # recommendation covers.
            "methods",
        }
    ),
    "nyXxZL": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A positive pregnancy experience, the outcome pregnant women
            # themselves want from antenatal care, consists of maintaining physical and
            # sociocultural normality, maintaining a healthy pregnancy and fetus, having an
            # effective transition to positive labour and birth, and achieving positive
            # motherhood.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Few studies exist on women's and health workers' views and
            # experiences of antenatal imaging ultrasound in South America, Central America and
            # Caribbean countries.
            "research implications",
        }
    ),
    "nyX5xL": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Performing a comprehensive geriatric assessment leads to
            # long-term cost savings in the healthcare system.
            "facilitating and hindering factors for the application of the guideline and quality indicators",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Adults aged 65 and older are hospitalised at roughly three
            # times the rate of adults under 65 (41,890 vs 14,553 full inpatient treatments per
            # 100,000 in Germany, 2021). Also: A comprehensive geriatric assessment is a
            # multidimensional, interdisciplinary diagnostic and therapeutic process spanning
            # medical, functional, mental, social and environmental domains that feeds into a
            # single overall treatment and care plan.
            "information about the guideline",
        }
    ),
    "nyO1Yj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Two blood tests that measure the same Alzheimer's analyte
            # (e.g., p-tau217) can have different diagnostic performance because they use
            # different assay technology or antibodies. Also: The same diagnostic test has
            # different negative and positive predictive values in populations with different
            # disease prevalence, such as AD pathology in primary versus specialty care.
            "guideline scope",
        }
    ),
    "ny76yj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A randomised trial of more than 3000 women has evaluated
            # routine antibiotic prophylaxis for operative vaginal birth.
            "methods",
        }
    ),
    "ny74yj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Randomised trial evidence comparing induction of labour at
            # or beyond term with expectant management comes from more than 30 trials and over 20
            # 000 women and babies.
            "methods",
        }
    ),
    "nYYb4n": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Cancer patients living outside metropolitan areas have worse
            # survival and disease-related outcomes than metropolitan patients.
            "foreword",
        }
    ),
    "nVY73L": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Large for gestational age is a birth weight above the 97th
            # percentile and small for gestational age is a birth weight below the 3rd centile.
            "scope of the guidelines",
        }
    ),
    "n3QxOj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Keratinocyte cancer (formerly non-melanoma skin cancer)
            # comprises basal cell carcinoma and cutaneous squamous cell carcinoma.
            "foreword",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Bowen's disease is cutaneous squamous cell carcinoma in
            # situ.
            "glossary of technical terms and abbreviations",
        }
    ),
    "n3QGej": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Non-invasive prenatal testing determines fetal Rh D status
            # from a sample of maternal blood.
            "methodology",
        }
    ),
    "mL6yYj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Severe aortic stenosis typically affects older and frail
            # people.
            "bmj rapid recommendations: background and methods",
        }
    ),
    "jzQAlE": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Clinicians managing severe pre-eclampsia should discuss the
            # risks, benefits and treatment options with the woman so that care is chosen by
            # informed, shared decision-making.
            "dissemination and implementation of the recommendations",
        }
    ),
    "jz7rXL": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Direct research evidence exists on patients' values and
            # preferences regarding lipid-lowering drugs. Also: Statins are the primary drug
            # treatment for reducing cardiovascular events, alongside lifestyle interventions.
            "how these recommendations were created",
        }
    ),
    "jz5DdE": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Screening by colonoscopy requires bowel preparation and
            # carries associated harms.
            "barriers to implementation",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A colonoscopy is performed by inserting a long flexible tube
            # with a camera through the rectum to inspect the colon and look for cancer.
            "glossary",
        }
    ),
    "jxxdwj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Randomised controlled trials have directly evaluated the
            # treatment of new-onset atrial fibrillation in critically ill adult ICU patients.
            # Also: Pooled meta-analytic effect estimates exist for the drug comparisons in
            # new-onset atrial fibrillation in critically ill adults.
            "methods",
        }
    ),
    "jlAbxL": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Everolimus, sunitinib and peptide receptor radionuclide
            # therapy have trial evidence of efficacy as systemic therapies for neuroendocrine
            # neoplasms.
            "guideline development process",
        }
    ),
    "jbXYZn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The upper vagina and cervix should be visually examined to
            # exclude trauma as the cause of bleeding before a uterine balloon tamponade is
            # placed.
            "dissemination, adaptation and implementation of the recommendation",
        }
    ),
    "jXXBBj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - HIF-PHIs are recommended for treating anaemia in children
            # (12 years or younger) with chronic kidney disease.
            "scope of guideline",
        }
    ),
    "jXXAdj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Pain in cancer survivors, and acute pain caused by cancer
            # treatment, is best managed by referral to a specialist pain medicine physician.
            "scope of this guideline",
        }
    ),
    "jW9Gpn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The earliest evidence that surgical decompression benefits
            # patients with space-occupying hemispheric infarction came only from adults aged 60
            # years or younger treated within 48 hours of stroke onset.
            "methods",
        }
    ),
    "jW0ZbL": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The Edinburgh Postnatal Depression Scale is validated for
            # screening depression during pregnancy, not only after birth. Also: Lochia is normal
            # postpartum vaginal discharge that changes over days to weeks from red or red-brown
            # to brownish or pink, then to whitish or yellow-white.
            "glossary",
        }
    ),
    "jOK05j": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A good clinical outcome after acute ischaemic stroke is
            # defined as a modified Rankin Scale score of 0-2 at 90 days; the same block also
            # fixes excellent outcome as mRS 0-1 and successful reperfusion as mTICI score >=2b.
            # Also: The average time from the start of intravenous thrombolysis infusion to
            # arterial puncture differs markedly between patients admitted directly to a
            # thrombectomy-capable centre and patients first admitted to a stroke unit without
            # thrombectomy facilities.
            "methods",
        }
    ),
    "jNW0VL": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The recommendations for screening and treating juvenile
            # idiopathic arthritis-associated uveitis rest on high-quality evidence from
            # randomised trials.
            "evidence summary",
        }
    ),
    "j7mQNn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Ergometrine/methylergometrine is contraindicated in women
            # with hypertensive disorders.
            "dissemination and implementation of the recommendations",
        }
    ),
    "j2QPrE": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Trial evidence shows that bladder catheterization for fewer
            # than 7 days after repair of an obstetric urinary fistula is as effective as 7 days.
            "research implications",
        }
    ),
    "j1k9Jn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Clinicians should discuss risks, benefits and treatment
            # options with women being managed for severe hypertension in pregnancy.
            "dissemination and implementation of the recommendations",
        }
    ),
    "j1WYVn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A good functional outcome after acute ischaemic stroke
            # corresponds to a modified Rankin Scale score of 0-2 at three months, and an
            # excellent outcome to a score of 0-1.
            "methods",
        }
    ),
    "j1Q1Xj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - As of 2023 Australia's national bowel cancer screening
            # program screened people aged 50 to 74 with a faecal immunochemical test every 2
            # years.
            "applicability to the australian setting",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Colonoscopy is an examination performed by inserting a long
            # flexible tube with a camera through the rectum to look for changes in the colon or
            # detect cancer.
            "glossary",
        }
    ),
    "j1O57n": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - In adults with a distal radial (wrist) fracture treated
            # surgically, osteosynthesis with a volar angular stable locking plate gives better
            # patient-reported outcomes (DASH and PRWE) than K-wire fixation or external fixation,
            # and is the preferred surgical method. Also: The published literature shows that
            # supervised rehabilitation is better than a single instruction session for
            # uncomplicated wrist-fracture cases.
            "monitoring",
        }
    ),
    "aEeKpL": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Severe aortic stenosis typically affects older and frail
            # people.
            "background and methods for bmj-rapidrecs",
        }
    ),
    "LwqZeE": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - An ultrasound scan before 24 weeks of gestation is needed
            # for accurate estimation of gestational age.
            "applicability issues",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Induction of labour should only be performed where
            # monitoring equipment and facilities for safe caesarean section are available. Also:
            # Women considering induction of labour should be counselled on the benefits and
            # side-effects of the different induction methods and engaged in shared
            # decision-making.
            "dissemination adaptation and implementation of the recommendation",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Randomized trial evidence comparing outpatient with
            # inpatient induction of labour covers more than 1500 women and comes only from high-
            # or upper-middle-income countries.
            "methods",
        }
    ),
    "LwqRXE": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Vitamin D supplementation is recommended as part of routine
            # antenatal care for pregnant women.
            "dissemination and implementation of recommendations",
        }
    ),
    "Lwq0oE": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Low-dose aspirin is beneficial for women at moderate or high
            # risk of pre-eclampsia. Also: Direct evidence establishes the ideal time to stop
            # aspirin treatment given to prevent pre-eclampsia.
            "research implications",
        }
    ),
    "LqGR0E": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - WHO recommends routine zinc supplementation for pregnant
            # women as part of antenatal care.
            "dissemination and implementation of the recommendation",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Pregnant women's own stated priorities for pregnancy care
            # include maintaining physical and sociocultural normality, a healthy pregnancy and
            # baby, an effective transition to positive labour and birth, and achieving positive
            # motherhood.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Elemental iron is given at 30 mg or 60 mg alongside folic
            # acid in routine antenatal care in settings that provide it.
            "research implications",
        }
    ),
    "LpvNkE": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - In a patient with acute intracerebral haemorrhage, oral
            # anticoagulant use can be assumed from a positive medical history without waiting for
            # laboratory confirmation of anticoagulant activity.
            "methods",
        }
    ),
    "Lpmozn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Randomised trial evidence comparing balanced electrolyte
            # solutions with normal saline in kidney transplantation exists, and amounts to ten
            # randomised controlled trials with 1,306 participants. Also: People with chronic
            # kidney disease have high rates of cardiovascular morbidity and mortality.
            "guideline development methods",
        }
    ),
    "Lkk3pL": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Melanoma is one of the most common cancers in Australia,
            # with more than 13,000 new diagnoses and more than 1,750 deaths each year.
            "foreword",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Targeted and systemic drug therapies improve life expectancy
            # in patients with melanoma.
            "guideline development process",
        }
    ),
    "LGm87E": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Transanal total mesorectal excision is a surgical technique
            # used to treat cancer of the rectum, the lowest part of the bowel.
            "purpose, scope and target users",
        }
    ),
    "LAag6L": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Maternal deaths occurred among pregnant patients in the
            # studies of appendicitis management that this guideline reviewed.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Appendicitis in pregnancy should usually be treated with
            # surgery, using keyhole (laparoscopic) surgery early in pregnancy and open surgery
            # later in pregnancy. Also: In pregnant patients with appendicitis, laparoscopic
            # (keyhole) appendectomy is the suggested surgical approach up to the 20th week of
            # gestation.
            "patient version",
        }
    ),
    "L4Q5An": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The ivermectin regimen trialled in adults with
            # mild-to-moderate COVID-19 was a single 200 microgram/kg dose on day 1 added to
            # standard care. Also: Ivermectin improves viral clearance at day 10 and shortens time
            # to clinical recovery in COVID-19.
            "methods and processes",
        }
    ),
    "EvqB0n": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A person with mpox remains infectious until all lesions have
            # crusted, the scabs have fallen off and a fresh layer of skin has formed underneath.
            # Also: A naturally ventilated room meeting airborne precaution criteria should have
            # an average ventilation rate of 160 litres per second per patient.
            "search strategy and terminology for reported routes of mpxv infection",
            # An ancestor of a section that carries Antiretroviral therapy should be started
            # within 7 days of HIV diagnosis in people who are ART-naive or have interrupted
            # treatment.. Kept because a skipped section is never descended into, so the keep on
            # the section itself would never be reached.
            "breastfeeding technical working group pre-gdg discussion",
            # An ancestor of a section that carries Antiretroviral therapy should be started
            # within 7 days of HIV diagnosis in people who are ART-naive or have interrupted
            # treatment.. Kept because a skipped section is never descended into, so the keep on
            # the section itself would never be reached.
            "gdg topic-specific working groups",
            # An ancestor of a section that carries Antiretroviral therapy should be started
            # within 7 days of HIV diagnosis in people who are ART-naive or have interrupted
            # treatment.. Kept because a skipped section is never descended into, so the keep on
            # the section itself would never be reached.
            "hiv antiretroviral technical working group pre-gdg discussion",
            # An ancestor of a section that carries Antiretroviral therapy should be started
            # within 7 days of HIV diagnosis in people who are ART-naive or have interrupted
            # treatment.. Kept because a skipped section is never descended into, so the keep on
            # the section itself would never be reached.
            "infection prevention and control technical working group",
            # An ancestor of a section that carries Antiretroviral therapy should be started
            # within 7 days of HIV diagnosis in people who are ART-naive or have interrupted
            # treatment.. Kept because a skipped section is never descended into, so the keep on
            # the section itself would never be reached.
            "methods: how this guideline was created",
        }
    ),
    "EgJmpn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - People with chronic kidney disease have high rates of
            # cardiovascular morbidity and mortality.
            "guideline development methodology",
        }
    ),
    "Eez2Kj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Liquid-based cytology cannot be performed on a
            # self-collected vaginal sample. Also: The cervical transformation zone is the region
            # where columnar epithelium is being replaced by metaplastic squamous epithelium.
            "glossary of terms and abbreviations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Randomised trial evidence directly compares different
            # management strategies for participants with oncogenic HPV (not 16/18) detected.
            # Also: Screening participants in whom HPV 16 or 18 is detected should be referred for
            # colposcopy.
            "guideline development process",
        }
    ),
    "EZvY8E": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Pregnant women's priorities for antenatal care extend beyond
            # a healthy baby to maintaining physical and sociocultural normality, an effective
            # transition to positive labour and birth, and achieving positive motherhood.
            "methods",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Routine antenatal iron and folic acid supplementation
            # provides either 30 mg or 60 mg of elemental iron.
            "research implications",
        }
    ),
    "EZYMwn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Functional independence after stroke is a modified Rankin
            # Scale score of 0-2.
            "methods",
        }
    ),
    "ERWdzj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - A P2/N95 respirator must remove 94-95% of small airborne
            # particles to be certified. Also: Personal protective equipment alone is sufficient
            # to prevent healthcare worker infection; it is the primary rather than the last line
            # of defence.
            "background to deliberations",
        }
    ),
    "ERWMXj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The definition and clinical importance of enlarged
            # perivascular spaces remain unclear and studies of them are scarce, whereas cerebral
            # microbleeds have been examined in large studies and recent reviews.
            "methods",
        }
    ),
    "EQ3m3L": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Intravenous oxytocin has been shown to be more
            # cost-effective than intramuscular oxytocin for preventing postpartum haemorrhage.
            "methods",
        }
    ),
    "EPY83j": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Community distribution of misoprostol to pregnant women for
            # self-administration after childbirth is an intervention of established clinical
            # effectiveness that WHO recommends.
            "methods",
        }
    ),
    "EKKOyE": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - In the WHO ACTION-I trial, preterm infants received
            # surfactant and mechanical ventilation as part of their respiratory support.
            "dissemination and implementation of the recommendations",
        }
    ),
    "EK0ldj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - For adjunctive tests used to detect oral potentially
            # malignant disorders or oral squamous cell carcinoma, the accepted performance limits
            # are a false-negative proportion of 5% or less and a false-positive proportion of 25%
            # or less.
            "methodology",
        }
    ),
    "E83abn": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - The Child-Pugh score is a 15-point scoring system used to
            # assess the prognosis of liver disease.
            "glossary",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Ethnocultural background is itself a risk factor for
            # hepatocellular carcinoma. Also: Country of birth is a causal risk factor for
            # hepatocellular carcinoma.
            "guidelines development process",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Six-monthly ultrasound surveillance in people with cirrhosis
            # reduces the lifetime chance of dying from hepatocellular carcinoma by about 14-15%.
            # Also: Validated risk prediction tools for identifying people at high risk of
            # hepatocellular carcinoma are clinically useful in people with chronic hepatitis B
            # infection.
            "key implementation considerations",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - HCC surveillance in people at high risk detects early-stage
            # tumours, increases receipt of curative treatment and improves overall survival.
            "purpose and scope",
        }
    ),
    "E52Obj": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Healthy adults should drink no more than 10 standard drinks
            # per week and no more than 4 standard drinks on any single day. Also: Drinking
            # alcohol increases the risk of many cancers, and the risk rises as consumption rises.
            "administrative report",
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - One Australian standard drink contains 10 g (12.5 ml) of
            # pure alcohol.
            "glossary",
        }
    ),
    "BjOM9n": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Volar locking plates had been shown superior to other
            # operative treatments for distal radius fracture before they came into widespread
            # use. Also: Comminution, older age and high-energy trauma increase the risk of losing
            # reduction after a distal radius fracture has been reduced.
            "method and background",
        }
    ),
    "8nyb0E": frozenset(
        {
            # Kept despite the skip rule for this heading, because the section carries something a
            # verdict could rest on - Chronic non-cancer pain is any painful condition that
            # persists for three months or longer and is not associated with a diagnosis of
            # cancer. Also: Acute and sub-acute pain is pain lasting less than three months.
            "scope of the guideline and how to use the guideline",
        }
    ),
    "nyONYj": frozenset(
        {
            # An ancestor of a section that carries Randomised controlled trials comparing
            # artemether-lumefantrine with dihydroartemisinin-piperaquine and with
            # artesunate-amodiaquine for uncomplicated P. falciparum malaria in sub-Saharan Africa
            # exist (17 and 36 trials respectively).. Kept because a skipped section is never
            # descended into, so the keep on the section itself would never be reached.
            "about the guidelines",
            # An ancestor of a section that carries Randomised controlled trials comparing
            # artemether-lumefantrine with dihydroartemisinin-piperaquine and with
            # artesunate-amodiaquine for uncomplicated P. falciparum malaria in sub-Saharan Africa
            # exist (17 and 36 trials respectively).. Kept because a skipped section is never
            # descended into, so the keep on the section itself would never be reached.
            "methods and processes",
        }
    ),
    "nBkO1E": frozenset(
        {
            # An ancestor of a section that carries Among patients with non-severe COVID-19, the
            # risk of hospitalization is about 6% in high-risk patients, 3% in moderate-risk
            # patients and 0.5% in low-risk patients (mortality about 0.6%, 0.3% and 0.05%).. Kept
            # because a skipped section is never descended into, so the keep on the section itself
            # would never be reached.
            "methods: how this guideline was created",
        }
    ),
    "jDePyL": frozenset(
        {
            # An ancestor of a section that carries Chronic insomnia disorder is defined by
            # difficulty with sleep initiation, duration, consolidation or quality occurring at
            # least three nights a week despite adequate opportunity for sleep, causing daytime
            # impairment and persisting at least one month.. Kept because a skipped section is
            # never descended into, so the keep on the section itself would never be reached.
            "glossary",
            # An ancestor of a section that carries Chronic insomnia disorder is defined by
            # difficulty with sleep initiation, duration, consolidation or quality occurring at
            # least three nights a week despite adequate opportunity for sleep, causing daytime
            # impairment and persisting at least one month.. Kept because a skipped section is
            # never descended into, so the keep on the section itself would never be reached.
            "other information",
        }
    ),
    "j2bBrj": frozenset(
        {
            # An ancestor of a section that carries The pure-tone average is the average of
            # hearing thresholds measured at 500, 1000 and 2000 Hz.. Kept because a skipped
            # section is never descended into, so the keep on the section itself would never be
            # reached.
            "glossary",
            # An ancestor of a section that carries The pure-tone average is the average of
            # hearing thresholds measured at 500, 1000 and 2000 Hz.. Kept because a skipped
            # section is never descended into, so the keep on the section itself would never be
            # reached.
            "glossary and abbreviations",
        }
    ),
    "L6RxYL": frozenset(
        {
            # An ancestor of a section that carries Anti-SARS-CoV-2 monoclonal antibodies act
            # primarily by neutralizing the virus, and evolving SARS-CoV-2 sublineages have
            # substantially reduced that neutralization.. Kept because a skipped section is never
            # descended into, so the keep on the section itself would never be reached.
            "methods: how this guideline was created",
        }
    ),
    "Ea7gOL": frozenset(
        {
            # An ancestor of a section that carries Evidence-based information describing
            # patients' experiences, values and preferences about diphtheria treatment decisions
            # is available.. Kept because a skipped section is never descended into, so the keep
            # on the section itself would never be reached.
            "methods: how this guideline was created",
        }
    ),
}


# Headings that are paperwork in ONE guideline, keyed on its `shortCode`.
#
# Built by reading every one of the 212 in-corpus guidelines end to end - one reader per
# document, each required to quote the section it proposed removing. The publisher table
# above cannot express these: the same heading is paperwork in one of a publisher's
# guidelines and content in another, and several publishers file their process notes under a
# heading no other publisher uses.
#
# Every entry below was checked, after it was proposed, against the whole subtree it would
# remove - not the heading, and not the paragraph the reader quoted. That check rejected 89
# proposals sitting above recommendation objects, 71 carrying an effect estimate, a dose or a
# directive, and 17 asking for a parent line that could not be taken without its children.
# Four more were rejected for naming a heading the guideline uses several times, where the
# reader had judged one instance: jlAD9L files 24 sections called "Body of evidence" and all
# but one are evidence-appraisal tables, and Lkk3pL's twenty "Introduction" sections are
# melanoma surveillance guidance.
#
# Everything here is document-scoped even where its reader offered it as a publisher rule.
# Every guideline was read by its own reader, so publisher scope would widen the blast radius
# without finding anything the sweep had missed.
_GUIDELINE_SKIP_HEADINGS: dict[str, frozenset[str]] = {
    "6nYJxE": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stroke-family shared expansion table.
            "abbreviations",
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere in the guideline.
            "cost effectiveness summaries",
            # A numbered list of the eight PICO-style questions this chapter sets out to answer
            # (8.1-8.8) - a pure scope statement of what the document covers, containing only
            # interrogatives and no assertion a verdict could rest on.
            "clinical questions",
        }
    ),
    "8L0RME": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stroke-family shared expansion table.
            "abbreviations",
            # A numbered table of contents of the twelve questions this chapter sets out to
            # answer - twelve interrogative sentences, no assertions, so no verdict could ever
            # rest on it.
            "clinical questions",
        }
    ),
    "BjOM9n": frozenset(
        {
            # A single sentence pointing to an external website for the public consultation
            # responses - guideline-development process plus navigation, nothing checkable.
            "hearings",
        }
    ),
    "E52Obj": frozenset(
        {
            # Purpose statement saying only what the document is for and which national strategy
            # it feeds.
            "aim",
            # Development process: the public call for evidence, its dates and inclusion
            # criteria, and pointers to Appendix 5 and the NHMRC website.
            "capturing new evidence",
            # GRADE / Evidence-to-Decision process description with no clinical content.
            "how the evidence was used",
            # Scope statement listing who the document is written for.
            "target audience",
            # Navigation: what each section contains, where the EtD framework, glossary and
            # acronym list live, and which tab MAGICapp shows.
            "the guideline format",
        }
    ),
    "E5mWbE": frozenset(
        {
            # A single sentence saying what the document is and does, with no clinical content
            # at all.
            "conclusion",
        }
    ),
    "E83abn": frozenset(
        {
            # Pure scope statement listing the service settings the document applies to.
            "health care settings in which the guidelines will be applied",
            # States who the document is written for and who it is not written for; no medical
            # content.
            "intended users",
        }
    ),
    "EK0DDj": frozenset(
        {
            # A pointer telling the reader that this is the abbreviated English version and that
            # background text and tables live in the full German guideline.
            "german version",
            # Front matter naming who made the guideline: the publishing societies, the 45
            # participating organisations, the DGPPN Steering Group, project management and
            # coordination staff, AWMF methodological support, the expert group roster, and the
            "publisher",
            # A GRADE method description explaining what strong, weak and open recommendations
            # mean, plus its child 'Note on evidence assessment' explaining the mixed GRADE /
            # SIGN-Oxford / expert-consensus grading used during the transition.
            "wording of recommendations",
        }
    ),
    "EKeJyL": frozenset(
        {
            # Reports the yield of the guideline-development process - scoping-survey
            # respondents, number of systematic reviews retrieved, number of GRADE tables
            # prepared, number of consultation participants - with no clinical finding attached
            "results",
        }
    ),
    "EPY83j": frozenset(
        {
            # A dead heading: the workbook content under it is already removed, so only the empty
            # Annex 8 label survives; named so the heading goes too.
            "contextualizing the guidelines – workbook",
            # An 8-step policy-making framework for adopting this guidance document into
            # national policy; nothing clinical.
            "contextualizing guidance",
            # Guideline-development scoping record: what the December 2010 scoping meeting
            # agreed and a question-by-cadre matrix whose colour coding is lost in the scrape.
            "the scoping questions",
        }
    ),
    "ERWdzj": frozenset(
        {
            # Describes who sat on the Infection Prevention and Control Panel, how consensus was
            # reached, and how much weight a consensus recommendation carries.
            "background to deliberations",
        }
    ),
    "ERx1yL": frozenset(
        {
            # The landing section's own text: who produced and funded the guidance, who it is
            # written for, and a bullet list of what the document sets out to do.
            "early detection of cancer in ayas",
        }
    ),
    "EZVOaE": frozenset(
        {
            # Two sentences stating what the document is for and listing which job roles should
            # read it; no clinical content.
            "purpose and target audience",
        }
    ),
    "EZVlYE": frozenset(
        {
            # A front-matter abbreviation table of organisation names and methodology framework
            # acronyms; it contains no sentences at all, and PPH is spelled out in
            "acronyms and abrebiations",
        }
    ),
    "EZYMwn": frozenset(
        {
            # A closing section about the guideline itself - the GRADE development process, how
            # many of its own recommendations rest on low certainty, the expert-poll voting,
            # which PICO questions need future trials, and advocacy about implementation gaps in
            "discussion",
        }
    ),
    "EaG1dL": frozenset(
        {
            # A single closing sentence about the guideline itself - that it was produced to
            # high methodological standards by an interdisciplinary panel - with no clinical
            # statement, number, or definition in it.
            "conclusion",
        }
    ),
    "EaKvXL": frozenset(
        {
            # A description of the GRADE / evidence-to-decision method and a pointer to which
            # evidence-to-decision tables exist, not a clinical claim.
            "grade assessment and evidence-to-decision tables",
        }
    ),
    "Ee438n": frozenset(
        {
            # A wrapper whose only two children describe a companion document and list other
            # guidelines and regulatory web pages to read alongside this one - pointers, not
            # medicine.
            "supporting materials to implement the guideline",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "Ee4Orn": frozenset(
        {
            # A numbered list of the 14 PICO questions the document sets out to answer - every
            # sentence is an interrogative that asserts nothing, and each question's population,
            # intervention and comparator are restated in the recommendation statement it
            "guideline recommendation questions",
        }
    ),
    "Ee4mAn": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Expansion table; the Definitions sibling stays.
            "abbreviations",
            # Back matter: where to download the PDF, licence and citation terms, the
            # consortium's partner institutions, named coordinators, advisory group and staff
            # rosters, and the funding statement.
            "global evidence, local adaptation (gela) project information",
            # An implementation-monitoring plan: indicators to track, routing of data into the
            # national health information system, and reporting cadence - no clinical
            # instruction.
            "monitoring and evaluation",
            # Who oversaw the process (oversight team, steering group, GDG, secretariat,
            # methodologists) plus a table of capacity-strengthening courses and workshops.
            "organisation, planning, budget and training",
            # A statement of the GELA guideline-development method's aims and the checklists it
            # was built on.
            "overview",
            # A scope statement naming who the guideline is for, followed by a bulleted list of
            # end-user categories (policymakers, managers, health workers, NGOs, parents).
            "target audience",
        }
    ),
    "Eez2Kj": frozenset(
        {
            # A scope-and-purpose statement that only says what the document covers.
            "context",
            # How the guideline was made (consultation with professional bodies and consumers)
            # plus its version history and updating plans.
            "development of these guidelines",
            # Governance history of the committees that oversee the screening program (SMC,
            # QSMC, Clinical Advisory Group) and the review status of their Quality Framework.
            "safety monitoring",
            # Says who the document is written for, adds a clinician-discretion disclaimer, and
            # points at the framework used to monitor compliance with it.
            "target readership",
        }
    ),
    "EezrQj": frozenset(
        {
            # A caption for an infographic the scraper dropped, plus a pointer telling the
            # reader where other content lives; it states no medical fact of its own.
            "visual summary",
        }
    ),
    "EgJmpn": frozenset(
        {
            # Front matter about the document itself - version number, date written, last-search
            # date, a link to the superseded guideline, the full work group member list, and 21
            # university/hospital affiliations, with no clinical statement anywhere in it.
            "guideline information",
        }
    ),
    "EgXyej": frozenset(
        {
            # The guideline-development outcome-prioritisation table - the PICO key questions
            # plus the list of outcomes the panel chose to judge the evidence on - carrying no
            # effect estimate, dose, threshold or epidemiological figure, and byte-identical to
            "priority outcomes for decision-making",
        }
    ),
    "EvqB0n": frozenset(
        {
            # Roster of the people and bodies who produced the guideline - WHO technical team,
            # Steering Committee, systematic review teams, technical working groups, 2022 GDG
            # members and external reviewers - plus a thank-you for IT support.
            "a cknowledgements",
            # A solicitation for Member States to submit data to the WHO Global Clinical
            # Platform, listing the platform's objectives and linking to its statistical
            # analysis plan - no clinical claim.
            "collection of standardized data and the who clinical platform",
        }
    ),
    "Evqmmn": frozenset(
        {
            # Orientation text announcing what the chapter below it will cover; its children are
            # judged separately (Donors and supply issues is kept).
            "challenges",
            # Dissemination (website, MAGICapp, journal summary, e-learning modules) and the
            # plan for updating the guideline.
            "implementing, evaluation and maintaining the guideline",
            # A pointer to a downloadable MHP template file that is not in the render; it states
            # no clinical content itself.
            "major haemorrhage protocol (mhp)",
        }
    ),
    "Jn37kn": frozenset(
        {
            # The NHMRC process report appendix, 23,535 characters of prioritisation
            # criteria and appraisal logistics; read before dropping, 2026-08-11.
            "process report",
        }
    ),
    "Kj2R8j": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stroke-family shared expansion table.
            "abbreviations",
            # A numbered table of contents for the chapter - 32 clinical questions (5.1 to 5.32)
            # stated as questions, with no answers and no assertions.
            "clinical questions",
        }
    ),
    "Kj2WZL": frozenset(
        {
            # AGREE II quality appraisal of six candidate source guidelines with include/exclude
            # conclusions - guideline-development method, not medicine.
            "assessment of evidence",
            # A single sentence pointing to the publisher's separate methods handbook.
            "description of the method used",
            # Boilerplate GRADE method description of what each recommendation type means and
            # when the Danish Health Authority issues it; no clinical content.
            "description of the strength and implications of recommendations",
            # Plan for auditing whether the guideline is being followed, plus the registries to
            # use for it.
            "monitoring",
            # One caption sentence pointing at tables that are not in the render - nothing
            # follows it before the next heading.
            "tables concerning focused question 1-3",
        }
    ),
    "L6RxYL": frozenset(
        {
            # Panel membership, chairs, selection criteria, conflict-of-interest statement and
            # MAGIC's methodological role - entirely committee paperwork.
            "who made this guideline?",
        }
    ),
    "L6zBvL": frozenset(
        {
            # A single sentence naming the societies whose panel wrote the guideline and
            # restating its scope; it states no clinical finding, direction, or recommendation.
            "conclusion",
        }
    ),
    "LAR07n": frozenset(
        {
            # A table of contents describing the document's own parts: Principles of Care, the
            # five recommendation themes, the two recommendation categories, Key Areas for
            # Future Development, Glossary.
            "what the guidelines include",
            # A target-audience list naming who should read the document; no medical content.
            "who the guidelines are for",
        }
    ),
    "LAag6L": frozenset(
        {
            # A single sentence about the document itself — it says a guideline was produced by
            # a panel using an evidence-to-decision framework, and names no intervention,
            # population, direction or number.
            "conclusion",
        }
    ),
    "LG45vn": frozenset(
        {
            # Pure front matter: proposed revision date, lead editor, lead author, co-author
            # list, conflict-of-interest declarations, funding source, data-sharing statement,
            # keyword block, and a list of what changed since the 2015 version - no clinical
            "introduction",
        }
    ),
    "LGm87E": frozenset(
        {
            # A one-sentence statement about what the document is and does, with no clinical
            # content.
            "conclusion",
            # An empty container left after the figures were stripped - 27 horizontal rules and
            # no text at all.
            "figures",
        }
    ),
    "Lkk3pL": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Label legend, ruled out 2026-08-09.
            "table 4. nhmrc approved recommendation types and definitions",
            # A bare bullet list of the subsection titles that follow - a table of contents, no
            # clinical content.
            "drug therapies in patients with advanced melanoma",
            # An evidence-to-decision note explaining why the panel issued no recommendation
            # here.
            "note on recommendations based on this evidence",
        }
    ),
    "Lpv2kE": frozenset(
        {
            # A single sentence about how the document was produced and by whom - no clinical
            # claim, no number, no procedure named.
            "conclusion",
        }
    ),
    "LqGR0E": frozenset(
        {
            # Publication and distribution logistics (online download, print runs, distribution
            # lists, UN-language translations) plus version history and monitoring plans, with
            # an explicit statement that there are no implementation considerations, so nothing
            "dissemination and implementation of the recommendation",
        }
    ),
    "LqgJ3E": frozenset(
        {
            # Version history table (versions 16.0 and 16.1 with dates and a list of editorial
            # changes made to the document).
            "coronavirus (covid-19) infection in pregnancy – summary of updates",
            # The guideline's methods and authorship section: databases searched, a library
            # contact address, who wrote and reviewed it, and a funding statement.
            "identification and assessment of evidence",
            # Four subsections that are all paperwork: a pointer to Tables 1 and 2 that did not
            # survive scraping, a caption for a missing Table 2, a description of how the RCOG
            # meta-analysis was run with no results attached, and a 66-item numbered
            "ii: summary of key studies and meta-analysis on maternal and pregnancy outcomes",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "Lr21gL": frozenset(
        {
            # Describes the GDG's evidence-to-decision process and how future research questions
            # were formulated from it.
            "centring human rights and equity in self-care interventions",
            # A signed endorsement letter from the WHO Director-General about the guideline
            # itself, not about clinical practice.
            "foreward",
            # Discusses the evidence ecosystem and the dissemination of evidence into policy,
            # then carries Tables 5.1-5.3, which are lists of unanswered research questions
            # rather than findings.
            "knowledge translation for self-care interventions",
            # Describes the publication and updating model for the document and points to
            # another section.
            "living guideline approach",
            # A scope-and-purpose statement that only says what kinds of content the document
            # contains.
            "objectives",
            # Sets out WHO's organizational research agenda against its triple-billion targets;
            # contains no clinical or epidemiological claim.
            (
                "research on self-care and self-care interventions contributing to world health "
                "organization's triple-billion goals"
            ),
            # Names who the guideline is written for (policy-makers, programme managers, product
            # developers) and that countries may adapt it locally; no clinical co
            "target audience",
            # Guidance on how future research should be designed and conducted, framed entirely
            # as open questions.
            "towards an appropriate approach to research on self-care interventions",
        }
    ),
    "Lr2a8L": frozenset(
        {
            # A one-sentence pointer to a different WHO document plus its publication date; it
            # states nothing about exposure risk itself.
            "risk assessment and management of exposure",
            # The review's protocol and search machinery - PICO question tables, database search
            # strings with hit counts, how findings were to be stratified, an emptied PRISMA
            # flowchart, and lists of included papers by author/year/aim - with no effect
            "systematic review for prevention, identification, management of covid-19 in health and care workers",
        }
    ),
    "LrRxrL": frozenset(
        {
            # Six sentences that each begin "This guideline will..." - rights, equity and
            # public-health-approach principles about the document itself, with no checkable
            # medical claim.
            "guiding principles",
            # A scope-and-purpose statement that only says what the guideline covers, who it is
            # for and what it aims to do - no clinical content.
            "purpose",
        }
    ),
    "LwRK5j": frozenset(
        {
            # An empty authoring placeholder heading at the very end of the document, with no
            # text and no children.
            "write section name here",
        }
    ),
    "LwRMXj": frozenset(
        {
            # The parent text is entirely about WHO's evidence requirements and study-conduct
            # expectations for investigators, not about malaria; the per-intervention
            # research-gap lists nested under it are left in place.
            "research needs",
        }
    ),
    "Lwq0oE": frozenset(
        {
            # A bare list of the outcomes the panel prioritized when deciding, with no effect
            # estimates or thresholds; the identical list is reproduced inside both
            # recommendation objects.
            "priority outcomes used in decision-making",
        }
    ),
    "LwqZeE": frozenset(
        {
            # A bare list of the outcomes the panel rated critical or important for GRADE
            # decision-making, asserting nothing itself, and reproduced word for word inside the
            # recommendation object's rationale (render lines 334-372).
            "priority outcomes used in decision-making",
        }
    ),
    "LwvKej": frozenset(
        {
            # A single sentence naming the three issuing societies and stating that the document
            # contains recommendations; it carries no clinical claim, no intervention and no
            # number.
            "conclusion",
        }
    ),
    "LwvpGj": frozenset(
        {
            # A bibliographic table of 21 other WHO publications with year, responsible WHO
            # department, and how each was handled (referenced / cross-checked / superseded) -
            # document provenance, no clinical content.
            "other who guidelines with recommendations relevant to routine anc",
        }
    ),
    "NnV76E": frozenset(
        {
            # A bare framing question stating what the evidence review covers - an interrogative
            # with no assertion, so no verdict could ever rest on it.
            "clinical question",
        }
    ),
    "QnoKGn": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stroke-family shared expansion table.
            "abbreviations",
            # A numbered list of the 18 PICO/clinical questions this chapter sets out to answer
            # - a scope statement about the document, containing no assertions a verdict could
            # rest on.
            "clinical questions",
        }
    ),
    "VLpK8j": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stroke-family shared expansion table.
            "abbreviations",
            # A numbered list of the five clinical questions this chapter sets out to answer - a
            # scope statement of what the document covers, containing no assertions a verdict
            # could rest on.
            "clinical questions",
        }
    ),
    "WE8wOn": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stroke-family shared expansion table.
            "abbreviations",
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere in the guideline.
            "cost effectiveness summaries",
            # A bare numbered list of the 19 PICO questions the chapter sets out to answer - it
            # states what the document covers and asserts nothing checkable.
            "clinical questions",
        }
    ),
    "bEvGJj": frozenset(
        {
            # A single closing paragraph about the guideline itself - that it was developed
            # under GRADE and trustworthy-guideline principles, a pointer to a companion
            # guideline on mechanical ventilation, and an exhortation to readers on how to apply
            "conclusion",
        }
    ),
    "j1O57n": frozenset(
        {
            # Guideline-development methodology: working group, PICO framing, AGREE II / AMSTAR
            # / Risk of Bias / QUADAS II appraisal tools, public consultation, and pointers to
            # appendices.
            "description of the method used",
            # Generic GRADE legend explaining what strong/weak/good-practice recommendation
            # labels mean and what they imply for clinicians - no clinical content about distal
            # radial fracture.
            "description of the strength and implications of the recommendations",
            # Implementation-monitoring chapter: expected effects of the guideline on practice
            # variation and how to track uptake through national registries and audits.
            "monitoring",
            # Caption-only stub for a figure the scraper dropped - the entire body is the
            # appendix title repeated.
            "radiological measuring of the radial - angle and length",
            # Literature-search strategy: databases, who ran the searches, date ranges, search
            # terms in four languages, inclusion criteria - reports how the search ran, never
            # what it found.
            "search description",
            # Caption-only stub for a figure the scraper dropped - the entire body is the
            # appendix title repeated.
            "treatment algorithm for distal radial fracture with dorsal angulation",
        }
    ),
    "j1Q1Xj": frozenset(
        {
            # The PICO questions that drove the update, plus who formulated them; it asks, it
            # never answers.
            "clinical questions",
            # Scope statement listing the settings the chapter applies to.
            "health care settings in which the guidelines will be applied",
            # States who the guideline chapter is written for, nothing about medicine.
            "intended users",
            # Dissemination and adoption considerations for stakeholders - awareness campaigns,
            # professional education, access - with no clinical instruction or model of care.
            "resourcing",
        }
    ),
    "j1QPrj": frozenset(
        {
            # A legend listing the permitted GRADE evidence-to-decision judgement wordings for
            # each domain (desirable effects, certainty, equity, acceptability), containing no
            # drug, outcome, population or number.
            "reference table for summary of findings",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "j1WBYn": frozenset(
        {
            # The rationale and administrative process by which the steering committee decided
            # which old recommendations to retire; ends with a pointer to a summary
            "guideline pruning",
            # A one-line caption for a table that is not in the scrape, pointing at the record
            # of which recommendations were dropped during guideline restructuring.
            "guideline pruning (restructuring)",
            # A pointer stub containing nothing but two links to separate WHO infection
            # prevention and control guidelines - no recommendation, no clinical content, and no
            # child sections.
            "immediate implementation of appropriate infection prevention and control measures",
            # A directory of other WHO publications - each entry is a title, a URL and a
            # description of what that document covers and who it is for, with no clinical claim
            # of its own.
            "resources for supporting clinical management of covid-19",
        }
    ),
    "j1WYVn": frozenset(
        {
            # The entire section is Supplemental Table 1, a per-author table of intellectual and
            # financial disclosures for the nine module working group members (affiliations,
            # trial roles, honoraria, grants), with no clinical content and nothing a verdict
            "supplemental material",
        }
    ),
    "j1k9Jn": frozenset(
        {
            # A bare table of the outcome labels the guideline panel prioritised for
            # decision-making, with no effect estimates, ratings, numbers or clinical statements
            # - and the same outcome list is already reproduced inside each recommendation's
            "priority outcomes for decision-making",
        }
    ),
    "j1kmYn": frozenset(
        {
            # The GRADE outcome-prioritisation list used during guideline development - a bare
            # roster of critical vs important outcomes with no assertion attached, every item of
            # which reappears in section 3 with actual effect estimates.
            "priority outcomes used in decision-making",
            # A future-research agenda: the GDG's list of evidence gaps plus two suggested PICO
            # questions for studies not yet done, with no clinical finding of its own.
            "research priorities",
        }
    ),
    "j20X4n": frozenset(
        {
            # The ranking table itself is gone; all that survives is an interpretive footnote
            # about how to read the missing table's labels, which states no clinical fact.
            "ranking per outcome table",
        }
    ),
    "j7m4dn": frozenset(
        {
            # A guideline-development artifact: the PICO key question restated plus the bare
            # list of outcome names the panel prioritized for decision-making, with no effect
            # data, doses, definitions or medical assertions.
            "priority outcomes for decision-making",
        }
    ),
    "j7q7Gn": frozenset(
        {
            # A single sentence about the guideline itself - that it exists, who wrote it, and
            # that its method was trustworthy - with no clinical claim in it.
            "conclusion",
        }
    ),
    "j98OoE": frozenset(
        {
            # An account of how the guideline was developed: who was consulted, the three
            # engagement strategies, the COVID-19 interruption, and the expert peer-review
            # round.
            "community consultation",
            # A description of the cover artwork's symbolism, ending in a reproduction-rights
            # notice with CARI's postal address and email.
            "flow & thrive",
            # A land acknowledgement plus a note on which other documents and taskforces existed
            # while this guideline was being written.
            "historical context for these guidelines",
            # An empty parent whose two children ('Scope of the guidelines', 'Aim') only state
            # what the document covers and why it was written.
            "objective",
            # A statement of how this document lines up with other CARI documents and what CARI
            # plans to publish next, with no clinical content of its own.
            "what do the other guidelines say?",
        }
    ),
    "jDRvgn": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # ~85% research vocabulary, Evan's call 2026-08-10.
            "glossary",
            # A pure scope-and-purpose statement: which workshop the document came out of, how
            # it differs from earlier publications, and what its authors hope it achieves.
            "objectives of the document and expected outcomes",
            # A plain bibliography for chapter 6 - fourteen citation entries with journal names,
            # DOIs and page numbers, no medical content.
            "references to chapter 6",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jDePyL": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Expansion table.
            "abbreviations used",
            # Version history plus plans for future editions of the guideline.
            "what is new in this version and what is coming next?",
        }
    ),
    "jDeeDL": frozenset(
        {
            # The standard WHO front-matter block: title, ISBNs, Creative Commons licence terms,
            # sales/rights/licensing addresses, third-party-material and general disclaimers,
            # and the layout credit, with no medical content.
            "copyright",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jMMGZj": frozenset(
        {
            # A scope-and-purpose statement whose first paragraph repeats the Abstract's opening
            # two sentences verbatim, with the rest saying only that this is an expedited
            # recommendation issued ahead of the full ESO guidelines and that the two drug
            "introduction",
        }
    ),
    "jMMeqj": frozenset(
        {
            # Dissemination package: descriptions of the Companion Guide, factsheets and
            # education sessions, plus seven tables that are a directory of external Australian
            # organisations with hyperlinks and 'click here to download' Google Drive links.
            "supporting materials to implement the guideline",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jNW0VL": frozenset(
        {
            # A literature-search and screening description - citation counts, reviewer
            # initials, a pointer to the dropped PRISMA figure, and which source tables were
            # rebuilt - with no pooled results, effect estimates or clinical findings.
            "evidence summary",
        }
    ),
    "jO0lNL": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Expansion table; the Definitions sibling stays.
            "abbreviations",
            # Back matter: edition and publication date, MAGICapp download link, Creative
            # Commons licence and citation instructions, project partner institutions and staff
            # lists, and the EDCTP funding grant.
            "global evidence, local adaptation (gela) project information",
            # A research agenda produced by the panel meeting and mapped onto
            # evidence-to-decision criteria; it states what should be studied next, not what is
            # known.
            "research gaps",
            # A scope statement listing who the document is written for.
            "target audience",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jO0qrL": frozenset(
        {
            # A reference list of the national oxygen plans cited, with publisher, date and URL.
            "bibliography",
            # A four-column table of the people who attended the framework meeting - title,
            # first name, last name, affiliation - and nothing else.
            "who national oxygen scale-up framework meeting in-person attendees",
        }
    ),
    "jO3B7j": frozenset(
        {
            # A single sentence stating what the document covers and who it is for, containing
            # no clinical assertion.
            "conclusion",
            # Nothing but bold captions for PRISMA flow charts and risk-of-bias graphs whose
            # images the scraper stripped, separated by horizontal rules.
            "figures",
        }
    ),
    "jOKYGj": frozenset(
        {
            # An empty placeholder announcing that a future recommendation will be written here;
            # no recommendation object and no medical content.
            "cadx recommendation",
            # Version history and update plans for the document itself, plus pointers to
            # numbered recommendations elsewhere; contains no clinical statement.
            "timeline and key changes",
        }
    ),
    "jW9PJn": frozenset(
        {
            # Pure guideline-development process: GRADE workshops for panel members, topic
            # selection, outcome-importance voting, PICO phrasing, drafting assignments, and the
            # consensus teleconference with conflict-of-interest voting rules.
            "method",
            # A scope-and-purpose statement about the document: which earlier guidelines
            # existed, that they used an older grading matrix, and that this one will use GRADE
            # instead.
            "objectives",
        }
    ),
    "jWN6oE": frozenset(
        {
            # Guideline-development process only: how the scoping questions were scored by
            # external experts, which topics were included or excluded, and how many Cochrane
            # reviews were selected - no clinical finding, effect estimate or definition.
            "results",
            # Two tables of 1-9 importance scores that consulted experts gave to candidate
            # scoping questions and candidate outcomes during topic prioritization - a record of
            # the panel's priority-setting exercise, containing no medical assertion.
            "scoping and prioritization of the topics covered in the guidelines",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jXXAdj": frozenset(
        {
            # Health-policy rationale for why this document was commissioned, plus the working
            # group's decision to adapt overseas guidelines - no clinical claim in it.
            "the need for an australian guideline",
            # Audience/scope statement plus the standard 'not a substitute for clinical
            # judgment' disclaimer.
            "who this guideline is intended for",
        }
    ),
    "jbXYZn": frozenset(
        {
            # Chapter preamble saying which stakeholders should disseminate and implement the
            # recommendation; it carries no clinical content of its own, and its child 4.3 does,
            # so the subtree must stay.
            "dissemination, adaptation and implementation of the recommendation",
        }
    ),
    "jlAbxL": frozenset(
        {
            # A five-item list of external PDFs, web links and a position-statement citation,
            # with no clinical statement of its own.
            "resources for patients and health professionals",
        }
    ),
    "jlPRdj": frozenset(
        {
            # A one-sentence funding declaration naming the sponsor of guideline development,
            # sitting under the parent heading "How was this guideline created?".
            "financial support",
            # An empty annex heading whose four subsections (Complete flowchart; Treatment for
            # seasonal influenza; Prophylaxis for influenza; Zoonotic influenza associated with
            # high mortality in humans) each contain nothing but a pointer to a PDF/PowerPoint
            "tools associated with the guideline",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jm83RE": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stats vocabulary subsection; Health terms sibling stays.
            "methodological terms",
            # Links to downloadable summary sheets, and nothing else.
            "health professional summary sheets",
            # Appendix 3, which is a list of attachments - administrative report, search
            # strategies, PRISMA diagram - and the committee membership under it.
            "methods for the 2020 australian clinical practice guidelines: pregnancy care",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jxBJyn": frozenset(
        {
            # A MAGICapp platform artifact at the very end of the document: a caption block for
            # print/web barcode images the scraper already dropped, leaving only the two labels.
            "barcodes",
        }
    ),
    "jxxdwj": frozenset(
        {
            # Four bold caption lines that are all that remains of dropped supplement
            # attachments; no medical statement of any kind.
            "supplemental material",
        }
    ),
    "jz7rXL": frozenset(
        {
            # Guideline-development narrative: panel composition and conflicts of interest, what
            # reviews the panel commissioned, how outcomes were selected, the panel survey
            # process, GRADE/MAGICapp appraisal, videoconferences, preliminary votes and
            "how these recommendations were created",
        }
    ),
    "n3QGej": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Label legend, ruled out 2026-08-09.
            "definition of the strength of recommendations",
            # A one-sentence pointer to two care-pathway flow charts that are off-site images,
            # plus their two link lines - no clinical content survives.
            "care pathway",
            # Empty parent whose only two children are dissemination (where the guideline will
            # be posted) and the five-year update plan.
            "implementing, evaluating and maintaining the guideline",
        }
    ),
    "n3QxOj": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Expansion table; the clinical glossary sibling stays.
            "abbreviations",
            # Methodology table defining NHMRC grades A-D.
            "evidence based recommendation grades",
            # How the guideline was scoped: Management Committee review of the 2008 questions,
            # selection criteria, date range of the literature, and the table of which questions
            # got a systematic review.
            "guideline scope",
            # Scope statement listing the care settings the document covers.
            "healthcare settings in which the guidelines will be applied",
            # Statement of who the document is for; no clinical content at all.
            "intended users",
            # Legend defining the guideline's recommendation categories (methodology), appearing
            # twice - once as a cross-reference sentence and once as the definiti
            "nhmrc approved recommendation types and definitions",
        }
    ),
    "nBAezL": frozenset(
        {
            # Correspondence block: two author names and their email addresses.
            "get in touch",
        }
    ),
    "nBkO1E": frozenset(
        {
            # One citation of the re-analysed prospective meta-analysis plus five bolded
            # captions for forest-plot figures that the scraper has already dropped, so nothing
            # numeric or clinical survives here.
            "a",
            # A description of the guideline's own revision process - who decides which drugs
            # get covered and which new studies prompted this version - with no clinical claim,
            # dose, threshold or effect estimate in it.
            "what triggered this update",
        }
    ),
    "nBpo1j": frozenset(
        {
            # Edition and publication details plus the MAGICapp download link, and its two
            # children - Project details (Creative Commons licence, citation and translation
            # disclaimer, partner institutions, coordinator contact, management team, advisory
            "global evidence, local adaptation (gela) project information",
            # A health-system monitoring plan - which administrative levels to monitor, what to
            # measure, how often to audit - with no clinical instruction.
            "monitoring and evaluation",
            # A statement of who the document is written for - policymakers, programme managers,
            # trainers - with no clinical content.
            "target audience",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "nJ5zyL": frozenset(
        {
            # Two sentences that only say what the document covers and how it was produced, with
            # no clinical claim in them.
            "conclusion",
        }
    ),
    "nV6X3n": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Expansion table; the cochlear-implant glossary with its dB HL thresholds stays.
            "abbreviations and acronyms",
        }
    ),
    "nYvlZE": frozenset(
        {
            # A two-column table of the PICO questions the panel chose to ask and the outcome
            # names it prioritised - scoping output from the development process, with no
            # findings, effect estimates or clinical instructions.
            "priority guideline questions and outcomes",
        }
    ),
    "noPKwE": frozenset(
        {
            # A barriers-to-implementation section, and this single instance says only that
            # there are none.
            "barriers to implentation",
            # Scope statement naming the care settings the document applies to.
            "healthcare settings in which the guideline will be applied",
            # Audience statement saying who the guideline is written for.
            "intended users",
            # Scope statement listing which Australian populations the guideline covers, closing
            # with a note about what the search strategies included.
            "target populations",
        }
    ),
    "noPQkE": frozenset(
        {
            # The full list of PICO-style questions the guideline set out to answer -
            # interrogative sentences only, with no answers, effect estimates or assertions
            # anywhere in the section.
            "guideline questions",
            # A pure statement of what the guideline is for and what it is meant to inform; no
            # clinical content.
            "purpose",
            # Lists who the document is written for and mentions companion operational tools;
            # entirely about the document, not about medicine.
            "target audience",
        }
    ),
    "noaRMj": frozenset(
        {
            # Version history of the document plus its plan for future updates - nothing
            # clinical.
            "timeline and key changes",
        }
    ),
    "ny70vj": frozenset(
        {
            # A single sentence about the document itself - who produced it and that it will
            # guide decisions - containing no clinical claim, no effect estimate and no
            # recommendation content.
            "conclusion",
        }
    ),
    "nyO1Yj": frozenset(
        {
            # Audience-and-scope front matter saying who the document is written for and what it
            # is intended to inform; its one substantive item, the definition of a dementia
            # specialist, is repeated verbatim in the Methods section.
            "target audience",
        }
    ),
    "nyONYj": frozenset(
        {
            # A programme-monitoring and guideline-updating section: audit indicators for the
            # Ministry of Health plus MAGICapp version control and revision plans, with no
            # clinical claim in it.
            "monitoring and auditing criteria",
            # Provenance narrative around a committee roster listing each member's name,
            # gender, district and role - personal data the corpus must not ship, Evan's
            # call of 2026-08-10. Nothing in the section's 6,141 characters asserts anything
            # a medical claim could rest on; the systematic-review attribution goes with it.
            "who developed the guideline",
        }
    ),
    "nyX5xL": frozenset(
        {
            # Contains no prose at all - five hyperlinks to appendix PDFs that are not in the
            # corpus.
            "cost-benefit analysis",
            # Purely about distributing the guideline - materials produced, who wrote the
            # patient short version, conferences, website listings, regional adaptations,
            # planned publications - with no clinical instruction or model of care.
            "dissemination and implementation",
            # Barriers to adoption, dissemination channels, billing codes, and quality
            # indicators for auditing the guideline itself - monitoring of the document, not
            # medicine.
            "facilitating and hindering factors for the application of the guideline and quality indicators",
            # Validity window and the schedule/process for revising the guideline - a standard
            # AWMF administrative section.
            "validity and update procedure",
        }
    ),
    "nyXKVL": frozenset(
        {
            # The plan for monitoring adoption of the guideline across Member States and the
            # coverage indicators proposed for that monitoring framework.
            "monitoring and evaluating the impact of the guideline",
            # A table of other WHO publications with their publication years, responsible WHO
            # departments, and how each was handled in producing this document - a list of
            # documents and organisations, not medicine.
            "other who guidelines with recommendations relevant to routine postnatal care",
        }
    ),
    "ojmKvn": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stroke-family shared expansion table.
            "abbreviations",
            # A bare numbered list of the five PICO-style questions this chapter set out to
            # answer - it states the chapter's scope and asserts nothing clinical.
            "clinical questions",
        }
    ),
    "pEQmQE": frozenset(
        {
            # Cochrane-style study-selection and appraisal bookkeeping: the search yield, lists
            # of included and excluded studies with exclusion reasons, and per-study
            # risk-of-bias judgements (allocation, blinding, attrition, selective reporting,
            "description of studies",
            # A pure scope-and-purpose statement saying only what the review set out to cover
            # and for whom, containing no clinical claim.
            "objectives",
        }
    ),
    "nyXxZL": frozenset({}),
    "nyXP0L": frozenset(
        {
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "ny74yj": frozenset({}),
    "n303gE": frozenset(
        {
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jXXZNj": frozenset(
        {
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jW0ZbL": frozenset(
        {
            # Glossary-family cut of 2026-08-10: every glossary/abbreviation section in
            # the corpus was read and verdicted (92 sections); pure expansion tables and
            # label legends go, clinical and case definitions stay.
            # Stats vocabulary subsection; Health terms sibling stays.
            "methodological terms",
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "jDReJn": frozenset(
        {
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "j2QZZE": frozenset(
        {
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "j2QPrE": frozenset({}),
    "LwqRXE": frozenset({}),
    "Lq0orj": frozenset(
        {
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "Eez3Kj": frozenset(
        {
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
    "EZvY8E": frozenset({}),
    "E80D0E": frozenset(
        {
            # Front matter in this guideline, and named here rather than corpus-wide because the
            # heading is a container, not a kind of content: 36 of the 61 guidelines that have an
            # Executive summary put their whole recommendation set in it. A reader read this one
            # and found nothing a verdict could rest on.
            "executive summary",
        }
    ),
}


def _is_skipped_section(section: dict[str, Any], heading: str, institution: str = "", short_code: str = "") -> bool:
    """Report whether a section is front/back matter that can be dropped.

    Skipping a section drops its whole subtree, so the heading alone is not
    enough to decide. Publishers do file clinical guidance under generic
    headings - the National Blood Authority puts four recommendations under
    'Introduction', beginning "Management of pregnancies at risk of fetal anaemia
    ... should be undertaken in facilities with appropriate expertise in
    ultrasound imaging". Anything holding a recommendation is therefore kept
    whatever it is called, which is what makes the heading list safe to extend.

    Args:
        section: Section object from the guideline JSON.
        heading: The section's heading, already reduced to plain text.
        institution: The publisher, as `institutionName` in the catalogue. Consulted only
            for `_INSTITUTION_SKIP_HEADINGS`, so a heading that is paperwork for one
            publisher and content for the rest can be dropped without touching the general
            list. (default: "")
        short_code: The guideline's own code. Consulted only for
            `_GUIDELINE_SKIP_HEADINGS`, for a heading that is paperwork in this one
            guideline and content elsewhere in the same publisher's output. (default: "")
    """
    # A publisher rule takes precedence over the content guards, deliberately. The guards
    # exist because a heading alone cannot tell paperwork from content across 44 publishers;
    # inside one publisher's output it can, because they are internally consistent. The
    # Stroke Foundation's Introduction is the case that forced the decision: it is 12,071
    # characters of charity blurb repeated in all eight guidelines, and the scope guard
    # rescues every copy on one clause buried in it - "The Clinical Guidelines cover the
    # whole continuum of stroke care, across 8 chapters". Evan's call 2026-08-06.
    key = _skip_lookup_key(heading)
    # First, and it wins over everything: a section a reader found clinical content in,
    # which a corpus-wide rule would otherwise drop. This is the most specific evidence
    # there is - someone read that section, in that guideline, and quoted what would go.
    if key in _GUIDELINE_KEEP_HEADINGS.get(short_code, frozenset()):
        return False
    if key in _INSTITUTION_SKIP_HEADINGS.get(institution, frozenset()):
        return True
    # The narrowest rule, and like the publisher rule it takes precedence over the guards.
    if key in _GUIDELINE_SKIP_HEADINGS.get(short_code, frozenset()):
        return True
    if not _matches_skip_list(heading) and not _is_study_protocol(section, heading):
        return False
    if _has_recommendation(section):
        return False
    if _holds_evidence_table(section):
        return False
    if _defines_recommendation_strength(section):
        return False
    if _states_guideline_scope(section):
        return False
    if _reports_evidence_findings(section):
        return False
    # Front matter is small. A skip-listed heading carrying a large body is not front
    # matter, and the recommendation check above cannot see recommendations written as
    # prose: one WHO guideline has 75 of them inside a section called "Annex 6", none
    # of which are recommendation objects. Rename that "Appendix 6" and a heading-driven
    # skip would silently delete over 500,000 characters of readable clinical guidance.
    return _subtree_text_chars(section) <= MAX_SKIPPED_SECTION_CHARS


def _skip_lookup_key(heading: str) -> str:
    """Reduce a heading to the form the skip list is written in.

    Publishers decorate headings in ways that do not change what the heading
    means, and the lookup is an exact string match, so every decoration defeats
    it. Three are common enough to matter, all observed:

    - emphasis, `**How to use these guidelines**` (Cancer Council Australia bolds
      every heading, which bypassed the whole skip list for that publisher)
    - leading section numbers, `7.2 Conflicts of interest`
    - appendix and annex labels, `Appendix A. Guideline development process`,
      `App E - Working Party members and project team contributions`

    Only the lookup uses this. `_plain_heading` still produces what is rendered,
    so output is unchanged for every heading that does not become skippable.

    Args:
        heading: The section's heading, already reduced to plain text.
    """
    key = _EMPHASIS_RE.sub("", heading).strip().lower()
    key = _HEADING_LABEL_RE.sub("", key)
    return " ".join(key.strip(" .-–—:").split())


def _matches_skip_list(heading: str) -> bool:
    """Report whether a heading names front or back matter.

    Two ways to match, both against the normalized key. Exact names come from
    `_SKIP_SECTION_HEADINGS`. `_SKIP_SECTION_TOPICS` holds substrings instead,
    because the administrative sections inside an appendix are named by their
    topic and the publisher's exact wording varies too much to enumerate -
    "Working Party members and project team contributions" is one guideline's
    phrasing of a section every guideline has.

    Container words - appendix, annex, supplementary material - are deliberately
    absent from both. They say where a section sits, not what it holds, so
    matching one drops a whole back-of-document container on no evidence about
    its contents. Matching the topic one level down drops the same material in
    units small enough that a mistake is cheap, and it is why the WHO "Annex 6"
    scenario behind MAX_SKIPPED_SECTION_CHARS cannot arise from this list.

    Args:
        heading: The section's heading, already reduced to plain text.
    """
    key = _skip_lookup_key(heading)
    if not key:
        return False
    return key in _SKIP_SECTION_HEADINGS or any(topic in key for topic in _SKIP_SECTION_TOPICS)


# The guideline's internal question number and the label in front of the topic: "PICO 5
# **Executive Summary:** Subcutaneous methotrexate versus oral methotrexate for JIA". Neither
# half means anything outside the document that wrote it, and both push the clinical topic -
# which is the only part a reader or a retriever can use - to the end of the heading. 9
# headings, all in nyxpZL.
#
# The markers arrive escaped. `_plain_heading` renders the heading through `_markdown`
# first, so a bold label in the source reaches this as "\*\*Executive Summary:\*\*" - a
# pattern written for bare asterisks matches none of them.
_EMPHASIS_MARKER = r"(?:\\?[*_])*"
_QUESTION_NUMBER_LABEL_RE = re.compile(
    rf"\A{_EMPHASIS_MARKER}\s*PICO\s*\d+[a-z]?{_EMPHASIS_MARKER}\s*[:.]?\s*"
    rf"(?:{_EMPHASIS_MARKER}\s*executive\s+summary\s*{_EMPHASIS_MARKER}\s*[:.]?)?\s*"
    rf"|\A{_EMPHASIS_MARKER}\s*executive\s+summary\s*{_EMPHASIS_MARKER}\s*[:.]\s*",
    re.IGNORECASE,
)


def _plain_heading(raw_heading: str, *, link_mode: LinkMode) -> str:
    """Reduce a section heading to a single line of text.

    A leading question number and "Executive Summary" label are stripped, leaving the
    clinical topic. Only when something is left: nyxpZL heads one section "PICO 4 Executive
    Summary:" with no topic after it, and emptying that heading would take the label off the
    two recommendations underneath.
    """
    heading = _markdown(raw_heading, link_mode=link_mode, as_heading=True)
    heading = " ".join(heading.replace("#", " ").split())
    remainder = _QUESTION_NUMBER_LABEL_RE.sub("", heading).strip(" *_:.\\").strip()
    return remainder or heading


def _intervention_arms(key_info: dict[str, Any]) -> str:
    """Summarize what a recommendation compared, as "intervention versus comparator".

    `keyInfo.interventions` names the arms in plain words - "Placebo", "Usual care",
    "Ezetimibe", "Lay health workers" - on 27% of recommendations across 37
    publishing organisations. It is the readable half of the PICO question, carried
    on the recommendation itself.

    Args:
        key_info: The recommendation's `keyInfo` object.
    """
    # Grouped by the PICO each arm belongs to. A recommendation answering four questions
    # carries all eight arms in one flat list, and merging them reads as a single combined
    # comparison that no trial performed: WHO's caesarean-section prophylaxis guideline
    # came out as "1st/2nd and 3rd generation cephalosporins versus broad-spectrum
    # penicillins and non-antistaphylococcal penicillins", where the source holds four
    # separate two-arm comparisons that `picoId` pairs up exactly.
    groups: dict[Any, tuple[list[str], list[str]]] = {}
    for arm in key_info.get("interventions") or []:
        if not isinstance(arm, dict):
            continue
        name = str(arm.get("intervention") or "").strip()
        if not name:
            continue
        interventions, comparators = groups.setdefault(arm.get("picoId"), ([], []))
        element = str(arm.get("picoElement") or "").strip().upper()
        if element == "C" or arm.get("isComparator"):
            comparators.append(name)
        elif element == "I":
            interventions.append(name)
    pairs: list[str] = []
    for interventions, comparators in groups.values():
        if not interventions:
            continue
        arms = ", ".join(dict.fromkeys(interventions))
        if comparators:
            arms += f" versus {', '.join(dict.fromkeys(comparators))}"
        if arms not in pairs:
            pairs.append(arms)
    if not pairs:
        return ""
    if len(pairs) == 1:
        return pairs[0]
    return "\n".join(f"- {pair}" for pair in pairs)


def _pico_arms(recommendation: dict[str, Any], *, link_mode: LinkMode) -> str:
    """Recover what a recommendation compared from its PICOs when `keyInfo` is silent.

    Publishers do not always fill in `keyInfo.interventions`, and when they leave it
    empty the comparison can still be sitting on the recommendation's own PICO objects
    as `intervention` and `comparator`. `_pico_markdown` would normally surface those,
    but it suppresses a PICO carrying no `summary` - a question naming an intervention
    without reporting anything about it. That is the right call for the evidence block
    and the wrong one for the arms: what a recommendation was weighed against is the
    substance of it, summary or no summary.

    128 recommendations in 11 guidelines fall in that gap, among them every pediatric
    recommendation in the ASH thrombocytopenia guideline, whose adult half names its
    arms and whose pediatric half did not - "Corticosteroids versus No corticosteroids"
    is in the source and reached the corpus nowhere.

    Args:
        recommendation: Recommendation object from a section's `recommendations` array.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
    """
    interventions: list[str] = []
    comparators: list[str] = []
    for pico in recommendation.get("picos") or []:
        if not isinstance(pico, dict):
            continue
        # Only the PICOs `_pico_markdown` suppresses. Where it does render, it already
        # prints the same Intervention and Comparator a few lines below, and repeating
        # them here would put the same words in the document twice.
        if _markdown(pico.get("summary"), link_mode=link_mode).strip():
            continue
        for field, names in (("intervention", interventions), ("comparator", comparators)):
            value = " ".join(_markdown(pico.get(field), link_mode=link_mode).replace("#", " ").split())
            if value:
                names.append(value)
    if not interventions:
        return ""
    if not comparators:
        return ", ".join(dict.fromkeys(interventions))
    return f"{', '.join(dict.fromkeys(interventions))} versus {', '.join(dict.fromkeys(comparators))}"


def _split_leading_label(text: str) -> tuple[str, str]:
    """Split a publisher's own opening label off the recommendation body.

    Returns `(label, remaining_text)`, falling back to ("Recommendation", text)
    when the body does not open with one.

    Deliberately conservative. Some publishers bold the entire recommendation -
    "**RECOMMENDATION 3: A companion of choice is recommended for all women
    throughout labour and childbirth.**" - so a first line that is one whole bold
    span is unwrapped before the cut (cutting inside the span would leave a dangling
    "**" in the body), and anything that would consume the whole body, or that runs
    long, is left alone. Emitting a duplicated label is untidy; dropping the
    recommendation is data loss.

    Args:
        text: Converted markdown for the recommendation body.
    """
    original = text
    first_line, newline, rest = text.partition("\n")
    whole_bold = _WHOLE_LINE_BOLD_RE.fullmatch(first_line.strip())
    if whole_bold:
        first_line = whole_bold.group(1).strip()
        text = first_line + newline + rest
    candidate = " ".join(_LABEL_DECORATION_RE.sub("", first_line).split())
    if not _LABEL_OPENERS.match(candidate):
        return "Recommendation", original

    # Cut by position rather than by searching for the label text: publishers put
    # non-breaking spaces inside labels ("Recommendation\xa011"), and the candidate
    # above has had its whitespace normalized, so it would not be found verbatim.
    # A colon, where present, separates the label from the statement itself, so it
    # is split off before the length check - the line as a whole is often long
    # ("**Recommendation 1:** The guideline panel suggests against ...") while the
    # label part is short.
    colon = first_line.find(":")
    if 0 <= colon < len(first_line) - 1:
        cut, label = colon + 1, candidate.split(":", 1)[0].strip()
    else:
        cut, label = len(first_line), candidate.rstrip(":")
    if len(label) > _MAX_LABEL_CHARS:
        return "Recommendation", original

    consumed = text[:cut]
    remainder = text[cut:].lstrip(": \t\xa0").lstrip()
    # The cut can consume the opening half of a bold pair whose closer is still in
    # the body: publishers bold "LABEL: statement" as one span, as several adjacent
    # spans, or across a line break. Emphasis pairs do not interleave, so when the
    # consumed prefix holds an unpaired "**" its closer is the body's first "**" -
    # remove exactly that one, wherever it sits, and balanced emphasis that belongs
    # to the body ("**Do X** carefully") keeps its markers.
    if consumed.count("**") % 2 and remainder.count("**") % 2:
        first_marker = remainder.find("**")
        remainder = (remainder[:first_marker] + remainder[first_marker + 2 :]).lstrip()
    elif not remainder.startswith("**") and remainder.startswith("*") and consumed.count("*") % 2:
        remainder = remainder[1:].lstrip()
    if not remainder.strip():
        return "Recommendation", original
    label = " ".join(label.replace("**", " ").split())
    return label.strip("*").strip(":.-–— *\xa0"), remainder


def _recommendation_markdown(
    recommendation: dict[str, Any], *, level: int, link_mode: LinkMode, moved_picos: frozenset[int] = frozenset()
) -> str:
    """Render one recommendation as markdown, with its GRADE strength if set.

    Args:
        recommendation: Recommendation object from the guideline JSON.
        level: Markdown heading level for the recommendation's label, one deeper
            than the section holding it.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
        moved_picos: `picoId`s whose evidence renders here, under the owning
            recommendation, instead of at the section listing them
            (default: frozenset()).
    """
    review_status = str(recommendation.get("status") or "").strip().upper()
    if review_status in _DROPPED_STATUSES:
        logger.debug("Dropping recommendation with review status %s", review_status)
        return ""
    if _is_draft_recommendation(recommendation):
        logger.debug("Dropping recommendation the publisher marks a draft")
        return ""
    text = _markdown(recommendation.get("text"), link_mode=link_mode, base_level=level)
    if not text:
        return ""
    strength = str(recommendation.get("strength") or "").strip().upper()

    # MAGICapp reuses the recommendation structure for editorial callout boxes and
    # marks them with strength INFO; the catalogue's publishedRecommendationCount
    # excludes them. Labelling them as recommendations would present boxes such as
    # "Box 1.1 Relevant Sustainable Development Goals" as clinical advice, so they
    # are emitted as ordinary content instead.
    if strength == _INFO_STRENGTH:
        return text

    # Take the publisher's own opening label when there is one, rather than stripping
    # it and writing "Recommendation" over the top. It is both the more accurate word
    # and often the only place the record says the item is ungraded: of the items
    # labelled practice or expert opinion points in the body, most carry
    # strength NOTSET, so nothing else in the object distinguishes them.
    base_label, text = _split_leading_label(text)
    if not text:
        return ""

    # Publishers populate `strength` unevenly; NOTSET and NO_STRENGTH mean the
    # panel did not assign one, so they are omitted rather than reported.
    label = base_label if not strength or strength in _UNRATED_STRENGTHS else f"{base_label} ({strength})"
    key_info = recommendation.get("keyInfo")
    key_info = key_info if isinstance(key_info, dict) else {}
    certainty = _GRADE_CERTAINTY.get(str(key_info.get("evidenceStrength") or "").strip().upper())
    if certainty:
        label = f"{label} — certainty of evidence: {certainty}"

    # A heading rather than bold text, so each recommendation is navigable in an
    # outline and gives a heading-aware chunker somewhere to cut. As bold text it
    # was invisible to both: one guideline rendered 101 headings and 279 bold
    # label lines, so the recommendations - the part a verifier most needs - were
    # the least addressable thing in the document. It also stays on its own line,
    # so markup at the start of the body still renders.
    parts = [f"{'#' * min(level, _MAX_HEADING_LEVEL)} {label}"]

    # Some publishers put the clinical situation the recommendation applies to in a
    # separate heading, e.g. "Recommendation 1: When considering therapy for patients
    # with chronic non-cancer pain". That scope is not always restated in the body.
    scope = _markdown(recommendation.get("heading"), link_mode=link_mode)
    scope = " ".join(scope.replace("#", " ").split())
    if scope and scope not in text:
        parts.append(f"*Applies to:*\n\n{scope}")

    parts.append(text)

    arms = _intervention_arms(key_info) or _pico_arms(recommendation, link_mode=link_mode)
    if arms:
        parts.append(f"*Compared:*\n\n{arms}")

    # The label sits on its own line, like every other labelled block here: `remarks`
    # is publisher-authored HTML, and a markdown table must begin at the start of a
    # line, so a value glued to the label would stop a leading table from rendering.
    remarks = _markdown(recommendation.get("remarks"), link_mode=link_mode, base_level=level)
    if remarks:
        parts.append(f"*Remarks:*\n\n{remarks}")

    for field, heading in _RECOMMENDATION_PROSE:
        body = _markdown(recommendation.get(field), link_mode=link_mode, base_level=level)
        if body:
            parts.append(f"*{heading}:*\n\n{body}")

    for field, heading in _KEY_INFO_DOMAINS:
        body = _markdown(key_info.get(field), link_mode=link_mode, base_level=level)
        research = _markdown(key_info.get(f"{field}ResearchEvidence"), link_mode=link_mode, base_level=level)
        # Not a duplicate of the domain judgment: it carries local applicability the
        # panel added by hand, e.g. "LU Code for celecoxib in Ontario covers only RA
        # and OA" or why measuring vitamin D in every newborn is impractical.
        extra = _markdown(key_info.get(f"{field}AdditionalConsideration"), link_mode=link_mode, base_level=level)
        if body:
            parts.append(f"*{heading}:*\n\n{body}")
        if research:
            parts.append(f"*{heading} — research evidence:*\n\n{research}")
        if extra:
            parts.append(f"*{heading} — additional considerations:*\n\n{extra}")

    rendered_picos: set[int] = set()
    for pico in recommendation.get("picos") or []:
        if not isinstance(pico, dict):
            continue
        pico_id = pico.get("picoId")
        if pico_id not in moved_picos or pico_id in rendered_picos:
            continue
        rendered = _pico_markdown(pico, link_mode=link_mode, base_level=min(level, _MAX_HEADING_LEVEL))
        if rendered:
            rendered_picos.add(pico_id)
            parts.append(rendered)

    return _drop_orphan_labels("\n\n".join(parts))


def _number(value: Any) -> str:
    """Render a stored number the way a person would write it: 75.0 as 75, 2.37 as 2.37."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _outcome_estimates(pico: dict[str, Any]) -> str:
    """One line per outcome: the numbers the panel weighed, which the prose often omits.

    MAGICapp stores a results table behind every evidence question - each outcome with
    its effect, confidence interval, how many studies and participants it rests on, and
    GRADE certainty - and publishes it on the guideline's own page. The scraper dropped
    all of it, keeping only the panel's written summary. That is usually fine and
    occasionally serious: WHO's caesarean-prophylaxis guideline says "it is unclear
    whether anti-staphylococcal cephalosporins reduce maternal sepsis" eleven times,
    while the table it dropped records RR 2.37 (95% CI 0.1 to 56.41) from 75 participants
    in 1 study, rated very low certainty. The prose is right - the interval spans no
    effect - but a verifier asked whether those antibiotics prevent sepsis could only
    ever quote the word "unclear", never how thin the evidence behind it was.

    One line each, in MAGICapp's own display order, is a few hundred characters against
    the ~506 million characters of raw payload this leaves behind: 22,187 outcomes across
    the English corpus, about 6% growth on the rendered text.

    Skipped: outcomes the publisher hid, outcomes with no name, and outcomes carrying
    nothing but a name - a bare label asserts nothing and is noise in a retrieved passage.

    Args:
        pico: PICO object from a section's or recommendation's `picos` array.
    """
    outcomes = pico.get("outcomes")
    if not isinstance(outcomes, dict):
        return ""
    lines: list[str] = []
    for name in _OUTCOME_LISTS:
        for row in outcomes.get(name) or []:
            if not isinstance(row, dict) or row.get("isHidden"):
                continue
            title = " ".join(str(row.get("outcome") or "").split())
            if not title:
                continue
            # `outcome` is a plain-text field spliced straight into a markdown bullet,
            # and publishers use a bare asterisk in it as a footnote marker - "Preterm
            # birth*", "Hearing impairment/deafness (in adulthood)*". Unescaped, those
            # open an emphasis run that never closes: 10 lines across 4 guidelines.
            title = title.replace("*", "\\*")
            parts: list[str] = []
            measure = _EFFECT_MEASURES.get(str(row.get("relativeEffectType") or "").strip().upper())
            effect = _number(row.get("relativeEffect"))
            if measure and effect:
                low, high = (
                    _number(row.get("relativeEffectConfidenceLow")),
                    _number(row.get("relativeEffectConfidenceHigh")),
                )
                # The confidence type is spelled CI95 in the data and 95% CI in prose.
                interval = f" (95% CI {low} to {high})" if low and high else ""
                parts.append(f"{measure} {effect}{interval}")
            participants = _number(row.get("interventionTotalParticipants"))
            if participants and participants != "0":
                parts.append(f"{participants} participants")
            studies = " ".join(str(row.get("interventionStudies") or "").split())
            if studies and studies != "0":
                parts.append(f"{studies} study" if studies == "1" else f"{studies} studies")
            certainty = _OUTCOME_CERTAINTY.get(str(row.get("qualityOfEvidenceLevel") or "").strip().upper())
            if certainty:
                parts.append(f"certainty {certainty}")
            if not parts:
                continue
            lines.append(f"- {title}: {', '.join(parts)}")
    return "\n".join(lines)


def _pico_markdown(pico: dict[str, Any], *, link_mode: LinkMode, base_level: int) -> str:
    """Render the readable head of a PICO: its question and its findings.

    A PICO states a clinical question precisely enough to answer with numbers -
    which Population, which Intervention, which Comparator - and `summary` reports
    what the evidence showed, in prose with clinical specificity: "Minimally
    important difference for pain on a 10-cm visual analogue scale (VAS) is a
    reduction of 1 cm."

    Everything else is dropped. `outcomes` holds the effect estimates and per-study
    arrays, and it is essentially all of the payload: across 1,798 PICOs the four
    fields kept here are 0.04% of 462 million characters. Those numbers are not
    text a verifier can match a claim against, and 85% of the summaries kept here
    appear nowhere else in their document (1,612 of 1,890, whole English catalogue).

    Args:
        pico: PICO object from a section's `picos` array.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
        base_level: Markdown level of the section heading this PICO sits under.
    """
    question = [
        (label, _markdown(pico.get(field), link_mode=link_mode, base_level=base_level))
        for field, label in (
            ("population", "Population"),
            ("intervention", "Intervention"),
            ("comparator", "Comparator"),
        )
    ]
    question = [(label, value) for label, value in question if value]
    summary = _markdown(pico.get("summary"), link_mode=link_mode, base_level=base_level)
    estimates = _outcome_estimates(pico)
    # A question needs an answer of some kind. Prose is one; the outcome table is the
    # other, and often the better one - it is what the prose is describing. What stays
    # out is a question with neither: 172 of these, all three parts filled in and
    # nothing reported, e.g. "Population: Patients with carotid stenosis / Intervention:
    # Trans-carotid artery revascularisation / Comparator: stenting or endarterectomy"
    # and no finding at all. That is a perfect topical match delivering nothing, the
    # same trap as a pointer-only section. A panel's own "no studies were identified"
    # sentence is kept, because a statement of absence is a finding; an empty field is
    # not one, and a reader cannot tell an unanswered question from an unfilled form.
    # A summary alone still goes, having lost the population it applies to.
    if not question or not (summary or estimates):
        return ""
    # The summary goes on its own line below the label: the American Dental
    # Association writes its summaries as tables, and a markdown table must begin
    # at the start of a line, so gluing it to the label left it rendering as a
    # paragraph of literal pipes.
    lines = "\n".join(f"- {label}: {value}" for label, value in question)
    block = f"**Evidence question**\n\n{lines}"
    if summary:
        block = f"{block}\n\n*Summary of findings:*\n\n{summary}"
    return f"{block}\n\n*Effect estimates:*\n\n{estimates}" if estimates else block


def _is_labelled_recommendation(block: str) -> bool:
    """Report whether a rendered block was emitted as a recommendation.

    `_recommendation_markdown` returns a heading for a recommendation, the bare
    text for an INFO callout box, and "" for one it drops - so the leading "#"
    is what separates the three.

    Args:
        block: A block returned by `_recommendation_markdown`.
    """
    return block.startswith("#")


def _moved_pico_ids(payload: dict[str, Any], *, link_mode: LinkMode) -> frozenset[int]:
    """PICOs that render under their owning recommendation instead of their section.

    A PICO appears twice in the source: on the section that introduces the question
    and on the recommendation that answers it, matched by `picoId` with identical
    text (all 3,974 recommendation-level summaries in the catalogue match their
    section twin exactly, measured 2026-08-04). Rendering only the section-level
    list files evidence wherever the listing happens to sit: Ea7gOL lists all five
    of its PICOs on "5. Recommendation for antibiotics treatment" while two belong
    to antitoxin recommendations two sections later, so the sensitivity-testing
    evidence reads as the basis of the antibiotics advice.

    A PICO moves only when the move cannot change what the document says, just
    where it says it: exactly one recommendation owns it, that recommendation
    renders, both copies of the PICO render, and the owner sits outside the subtree
    of the section listing it (inside that subtree, the section-level position
    already reads correctly). PICOs shared by several recommendations stay at their
    section: every extra print of a shared PICO is a duplicate, and duplicating
    them is the measured 1.16-million-character mistake that got the first attempt
    at this reverted.

    Args:
        payload: Guideline document JSON.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
    """
    listed: dict[int, tuple[str, dict[str, Any]]] = {}
    owners: dict[int, list[tuple[str, dict[str, Any], dict[str, Any]]]] = {}

    def visit(sections: Any, path: str) -> None:
        for index, section in enumerate(sections or []):
            if not isinstance(section, dict):
                continue
            here = f"{path}.{index}"
            for pico in section.get("picos") or []:
                if isinstance(pico, dict) and pico.get("picoId") is not None:
                    listed.setdefault(pico["picoId"], (here, pico))
            for recommendation in section.get("recommendations") or []:
                if not isinstance(recommendation, dict):
                    continue
                for pico in recommendation.get("picos") or []:
                    if isinstance(pico, dict) and pico.get("picoId") is not None:
                        owners.setdefault(pico["picoId"], []).append((here, recommendation, pico))
            visit(section.get("subSections"), here)

    visit(payload.get("sections"), "s")
    for recommendation in payload.get("recommendations") or []:
        if not isinstance(recommendation, dict):
            continue
        for pico in recommendation.get("picos") or []:
            if isinstance(pico, dict) and pico.get("picoId") is not None:
                owners.setdefault(pico["picoId"], []).append(("root", recommendation, pico))

    moved: set[int] = set()
    for pico_id, (where, section_copy) in listed.items():
        owning = owners.get(pico_id) or []
        if len(owning) != 1:
            continue
        owner_path, recommendation, recommendation_copy = owning[0]
        if owner_path == where or owner_path.startswith(f"{where}."):
            continue
        # Both copies must render: an unrenderable section copy means the move would
        # add content the document never printed, an unrenderable recommendation
        # copy means it would lose some.
        if not _pico_markdown(section_copy, link_mode=link_mode, base_level=2):
            continue
        if not _pico_markdown(recommendation_copy, link_mode=link_mode, base_level=2):
            continue
        # Mirrors _recommendation_markdown's early exits: a recommendation that will
        # not print a labelled block cannot receive the PICO.
        if str(recommendation.get("status") or "").strip().upper() in _DROPPED_STATUSES:
            continue
        if _is_draft_recommendation(recommendation):
            continue
        if str(recommendation.get("strength") or "").strip().upper() == _INFO_STRENGTH:
            continue
        text = _markdown(recommendation.get("text"), link_mode=link_mode, base_level=2)
        if not text or not _split_leading_label(text)[1]:
            continue
        moved.add(pico_id)
    return frozenset(moved)


# Headings a publisher wrote INSIDE a section body, keyed on the guideline's `shortCode`.
#
# The section tables above cannot reach these. A publisher who files "Target audience" or
# "Evidence synthesis" as an <h3> in the middle of a section it also uses for guidance has
# put that paperwork somewhere no rule about sections can see, and 175 of the 191 headings
# the readers named turned out to be exactly that.
#
# Guideline-scoped for the same reason as `_GUIDELINE_SKIP_HEADINGS`, only more so: the words
# involved - "Recommendations", "Aim", "Objectives", "Resources", "Methods", "Evidence base" -
# are paperwork in one guideline and content in the next. An inline "Recommendations" in
# L4Q5An points at where the recommendations live; in most guidelines a heading of that name
# is the recommendations.
_GUIDELINE_INLINE_SKIP_HEADINGS: dict[str, frozenset[str]] = {
    "Edr04L": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
        }
    ),
    "Lkk3pL": frozenset(
        {
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            # Label legend rendered as an inline table caption, ruled out 2026-08-09.
            "table 4. nhmrc approved recommendation types and definitions",
        }
    ),
    "VLpK8j": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The living-update machinery, the other half of this publisher's Methodology:
            # how the literature is monitored each month, who reviews what turns up, and how
            # an evidence summary and GRADE profile get redrafted and signed off. In all
            # eight of its guidelines, alongside the four blocks already named.
            "literature identification",
            "clinical expert review",
            ("data extraction, updating evidence summary and grade profile"),
            # The four process blocks inside Methodology, leaving the fifth. That section
            # is on the keep list because of what closes it - "Immediate: without delay,
            # or within minutes, not hours. Urgent: minutes to several hours. Very early:
            # within hours and up to 24 hours. Early: within 48 hours." That is the only
            # place these guidelines quantify the timing words their own recommendations
            # use. Removing the section takes it; removing these four does not.
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere.
            "cost effectiveness summaries",
        }
    ),
    "Kj2R8j": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The living-update machinery, the other half of this publisher's Methodology:
            # how the literature is monitored each month, who reviews what turns up, and how
            # an evidence summary and GRADE profile get redrafted and signed off. In all
            # eight of its guidelines, alongside the four blocks already named.
            "literature identification",
            "clinical expert review",
            ("data extraction, updating evidence summary and grade profile"),
            # The four process blocks inside Methodology, leaving the fifth. That section
            # is on the keep list because of what closes it - "Immediate: without delay,
            # or within minutes, not hours. Urgent: minutes to several hours. Very early:
            # within hours and up to 24 hours. Early: within 48 hours." That is the only
            # place these guidelines quantify the timing words their own recommendations
            # use. Removing the section takes it; removing these four does not.
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere.
            "cost effectiveness summaries",
        }
    ),
    "6nYJxE": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The living-update machinery, the other half of this publisher's Methodology:
            # how the literature is monitored each month, who reviews what turns up, and how
            # an evidence summary and GRADE profile get redrafted and signed off. In all
            # eight of its guidelines, alongside the four blocks already named.
            "literature identification",
            "clinical expert review",
            ("data extraction, updating evidence summary and grade profile"),
            # The four process blocks inside Methodology, leaving the fifth. That section
            # is on the keep list because of what closes it - "Immediate: without delay,
            # or within minutes, not hours. Urgent: minutes to several hours. Very early:
            # within hours and up to 24 hours. Early: within 48 hours." That is the only
            # place these guidelines quantify the timing words their own recommendations
            # use. Removing the section takes it; removing these four does not.
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere.
            "cost effectiveness summaries",
        }
    ),
    "WE8wOn": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The living-update machinery, the other half of this publisher's Methodology:
            # how the literature is monitored each month, who reviews what turns up, and how
            # an evidence summary and GRADE profile get redrafted and signed off. In all
            # eight of its guidelines, alongside the four blocks already named.
            "literature identification",
            "clinical expert review",
            ("data extraction, updating evidence summary and grade profile"),
            # The four process blocks inside Methodology, leaving the fifth. That section
            # is on the keep list because of what closes it - "Immediate: without delay,
            # or within minutes, not hours. Urgent: minutes to several hours. Very early:
            # within hours and up to 24 hours. Early: within 48 hours." That is the only
            # place these guidelines quantify the timing words their own recommendations
            # use. Removing the section takes it; removing these four does not.
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere.
            "cost effectiveness summaries",
        }
    ),
    "QnoKGn": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The living-update machinery, the other half of this publisher's Methodology:
            # how the literature is monitored each month, who reviews what turns up, and how
            # an evidence summary and GRADE profile get redrafted and signed off. In all
            # eight of its guidelines, alongside the four blocks already named.
            "literature identification",
            "clinical expert review",
            ("data extraction, updating evidence summary and grade profile"),
            # The four process blocks inside Methodology, leaving the fifth. That section
            # is on the keep list because of what closes it - "Immediate: without delay,
            # or within minutes, not hours. Urgent: minutes to several hours. Very early:
            # within hours and up to 24 hours. Early: within 48 hours." That is the only
            # place these guidelines quantify the timing words their own recommendations
            # use. Removing the section takes it; removing these four does not.
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere.
            "cost effectiveness summaries",
        }
    ),
    "8L0RME": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The living-update machinery, the other half of this publisher's Methodology:
            # how the literature is monitored each month, who reviews what turns up, and how
            # an evidence summary and GRADE profile get redrafted and signed off. In all
            # eight of its guidelines, alongside the four blocks already named.
            "literature identification",
            "clinical expert review",
            ("data extraction, updating evidence summary and grade profile"),
            # The four process blocks inside Methodology, leaving the fifth. That section
            # is on the keep list because of what closes it - "Immediate: without delay,
            # or within minutes, not hours. Urgent: minutes to several hours. Very early:
            # within hours and up to 24 hours. Early: within 48 hours." That is the only
            # place these guidelines quantify the timing words their own recommendations
            # use. Removing the section takes it; removing these four does not.
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere.
            "cost effectiveness summaries",
        }
    ),
    "jDRvgn": frozenset(
        {
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            # ~85% research vocabulary; Evan's call 2026-08-10.
            "glossary",
        }
    ),
    "ojmKvn": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The living-update machinery, the other half of this publisher's Methodology:
            # how the literature is monitored each month, who reviews what turns up, and how
            # an evidence summary and GRADE profile get redrafted and signed off. In all
            # eight of its guidelines, alongside the four blocks already named.
            "literature identification",
            "clinical expert review",
            ("data extraction, updating evidence summary and grade profile"),
            # The four process blocks inside Methodology, leaving the fifth. That section
            # is on the keep list because of what closes it - "Immediate: without delay,
            # or within minutes, not hours. Urgent: minutes to several hours. Very early:
            # within hours and up to 24 hours. Early: within 48 hours." That is the only
            # place these guidelines quantify the timing words their own recommendations
            # use. Removing the section takes it; removing these four does not.
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere.
            "cost effectiveness summaries",
        }
    ),
    "NnV76E": frozenset(
        {
            # The citation label and its copyright line - "(c) No part of this publication
            # can be reproduced... Stroke Foundation" - removed 2026-08-11.
            "citation",
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The living-update machinery, the other half of this publisher's Methodology:
            # how the literature is monitored each month, who reviews what turns up, and how
            # an evidence summary and GRADE profile get redrafted and signed off. In all
            # eight of its guidelines, alongside the four blocks already named.
            "literature identification",
            "clinical expert review",
            ("data extraction, updating evidence summary and grade profile"),
            # The four process blocks inside Methodology, leaving the fifth. That section
            # is on the keep list because of what closes it - "Immediate: without delay,
            # or within minutes, not hours. Urgent: minutes to several hours. Very early:
            # within hours and up to 24 hours. Early: within 48 hours." That is the only
            # place these guidelines quantify the timing words their own recommendations
            # use. Removing the section takes it; removing these four does not.
            # What GRADE is and what its evidence-to-decision framework weighs.
            "brief summary of grade",
            # The GRADE strength legend - what strong and conditional mean.
            "strength of recommendations",
            # How to read the 'per 1000 people' column in the evidence profile tables.
            "explanation of absolute effect estimates used",
            # How to interpret the cost-effectiveness notes elsewhere.
            "cost effectiveness summaries",
        }
    ),
    "jm83RE": frozenset(
        {
            # 13 blocks, all of them a further-reading list of other organizations'
            # publications and websites. Read in full: not one carries a claim.
            "resources",
        }
    ),
    "8nyb0E": frozenset(  # 9,357 characters
        {
            # The closing three blocks are external review for standards adherence, the
            # MAGICapp format and dissemination story, and the publication date plus the
            "external review",
            # The closing three blocks are external review for standards adherence, the
            # MAGICapp format and dissemination story, and the publication date plus the
            "guideline format",
            # Four consecutive blocks of pure guideline-development paperwork: the
            # methods preamble, panel composition and conflict-of-interest management,
            "methodology",
            # Four consecutive blocks of pure guideline-development paperwork: the
            # methods preamble, panel composition and conflict-of-interest management,
            "panel composition and conflict of interest management",
            # Four consecutive blocks of pure guideline-development paperwork: the
            # methods preamble, panel composition and conflict-of-interest management,
            "selection and prioritization of questions and outcomes",
            # The closing three blocks are external review for standards adherence, the
            # MAGICapp format and dissemination story, and the publication date plus the
            "update of the guideline",
        }
    ),
    "E52Obj": frozenset(  # 5,262 characters
        {
            # 3 blocks. Pregnancy section's public-call-for-evidence and
            # GRADE-eligibility paragraphs (render line 916).
            "additional evidence",
            # Describes the public call for submissions and why that material could not
            # be GRADE-rated; reports no result.
            "additional scientific evidence",
            # Provenance blurb in the plain English summary: who NHMRC is and that an
            # expert committee guided the review.
            "how the guidelines were developed",
        }
    ),
    "EK0ldj": frozenset(  # 936 characters
        {
            # A one-sentence statement of what the guideline is trying to achieve.
            "purpose",
            # The guideline's scope statement: which patients and which cancers the
            # document covers and excludes, and the two questions it was written to
            "scope",
        }
    ),
    "ERWdzj": frozenset(  # 14,793 characters
        {
            # Invitation to send feedback plus a contact email address.
            "public consultation",
            # A statement of what the guideline is for and what it deliberately does not
            # cover, ending in a pointer to the Taskforce's other guidelines.
            "purpose",
            # A single bibliographic citation of the Taskforce's own methods paper.
            "scientific publications",
            # Lists which populations and Australian care settings the document covers -
            # document coverage only, no clinical content.
            "scope",
            # Update cadence, NHMRC approval process and how to send feedback - entirely
            # about the document, not about medicine.
            "updating and public consultation",
        }
    ),
    "EZVlYE": frozenset(  # 6,416 characters
        {
            # Describes the process of adopting the document into national and
            # subnational guidelines and protocols and adapting it for humanitarian
            "adaptation",
            # A single sentence saying which population the document covers.
            "persons affected by the recommendation",
            # Describes how the guideline was produced and prioritised for updating -
            # the Executive Guideline Steering Group prioritisation process, the WHO
            "rationale and objectives",
            # Purely how the document will be distributed - WHO regional offices, the
            # WHO website and Reproductive Health Library, conferences, and translation
            "recommendation dissemination",
            # A scope statement consisting of the PICO question alone - one
            # interrogative sentence with no assertion, and the same question is
            "scope of the recommendation",
            # States only who the document is written for - guideline developers,
            # midwives, obstetricians, programme managers, ministries, professional
            "target audience",
        }
    ),
    "EZvY8E": frozenset(  # 2,513 characters
        {
            # Pure process description - the WHO handbook procedure, GRADE and
            # GRADE-CERQual, and the DECIDE evidence-to-decision framework; it reports
            "guideline development methods",
            # 2 blocks. A single sentence listing who is meant to read the guideline -
            # policy-makers, programme managers, professional societies, health
            "target audience",
        }
    ),
    "Ea7gOL": frozenset(  # 1,270 characters
        {
            # A single sentence saying how the guideline aligns with a WHO programme
            # goal - institutional positioning of the document, with no clinical
            "broader context",
            # Three bullets stating what the guideline aims to do (give recommendations,
            # support Member State adaptation, inform the research agenda) plus a 'Who
            "what are the guideline's objectives?",
        }
    ),
    "EaKvXL": frozenset(  # 660 characters
        {
            # The bare list of comparisons the rapid review was designed to look for,
            # with no result or claim attached to any of them.
            "intervention and comparator",
            # The inclusion criteria of the rapid review (which studies were eligible),
            # a review-design statement with no medical claim in it.
            "population",
        }
    ),
    "Ee438n": frozenset(  # 1,792 characters
        {
            # Says who the document is written for and notes that a dissemination plan
            # exists.
            "target audience",
            # Plans for future updating - review interval, who reconvenes, and the
            # authoring platform.
            "updating the guideline",
            # A pure scope statement listing topics the document excludes; every
            # sentence is about the document's coverage, not about MDMA or PTSD.
            "what the guideline does not address",
        }
    ),
    "Ee4mAn": frozenset(  # 4,411 characters
        {
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # The GRADE evidence-to-decision walkthrough, and Tables 3 and 4 under it -
            # the legend for the four certainty levels and the criteria that map to
            # recommendation strength. Written as a numbered subheading here, a bold line
            # in jO0lNL and a fifth-level heading in Ee4mAn; all three reduce to the same
            # key. The section around it stays: it holds the qualitative evidence
            # syntheses this publisher writes into its methods chapter.
            "decision-making process to reach a recommendation",
            # Thanks to named individuals, steering group, GDG members, methodologists
            # and partner organisations, with a pointer to the declarations-of-interest
            "acknowledgements",
            # Screening, extraction, thematic analysis and GRADE-CERQual confidence
            # assessment for the qualitative synthesis; contains no finding.
            "data collection and analysis",
            # Search sources and dates, language limits, inclusion criteria and analysis
            # approach for the qualitative synthesis; the findings live under **Main
            "qualitative evidence",
            # Study-inclusion rules for the qualitative evidence synthesis - which study
            # designs and populations were eligible.
            "selection criteria",
        }
    ),
    "Eg9eVL": frozenset(  # 839 characters
        {
            # A pure audience-and-scope statement saying who the guideline is for and
            # what it does and does not cover, with no clinical content.
            "target audience",
        }
    ),
    "EvqB0n": frozenset(  # 59 characters
        {
            # A single bullet calling for guideline-development value-and-preference
            # surveys - a statement about how future guidelines should be produced, not
            "methods questions",
        }
    ),
    "Evqmmn": frozenset(  # 7,321 characters
        {
            # Who developed the guideline and who funded it.
            "acknowledgements and endorsements",
            # Explains why the National Blood Authority decided to update the 2011
            # module - the document's own rationale, with no clinical claim.
            "clinical need for this guideline",
            # A three-bullet pointer saying what the three companion technical volumes
            # contain.
            "related material",
        }
    ),
    "Jn37kn": frozenset(  # 884 characters
        {
            # Four bullets that are each only the title of a study someone should run,
            # asserting no finding, number, dose or recommendation.
            "chlorhexidine resistance",
        }
    ),
    "L4Q5An": frozenset(  # 982 characters
        {
            # A table assigning each respiratory-support topic to the panel that wrote
            # it, plus the review-and-approval chain; no clinical content and no
            "recommendations",
        }
    ),
    "Lq0orj": frozenset(  # 1,887 characters
        {
            # A scope disclaimer plus the lead-in to a list of other WHO guidance (the
            # list itself did not survive scraping).
            "other who documents",
        }
    ),
    "LwRMXj": frozenset(  # 2,313 characters
        {
            # A single lead-in sentence naming the panel that wrote the four core
            # principles; the four principles themselves are sibling headings and are
            "core principles",
            # A purpose statement listing the aims of the document itself, including
            # informing the research agenda for future updates.
            "objectives",
            # Names who the document is written for; contains no statement about
            # malaria.
            "target audience",
        }
    ),
    "j2bBrj": frozenset(  # 3,261 characters
        {
            # Copyright and reproduction-permission notice with the publication date.
            "citation",
            # A purpose statement about the document and a disclaimer that it is not an
            # inflexible recipe.
            "purpose",
            # A statement of what the document covers and what it excludes, with
            # cross-links to other sections.
            "scope",
            # Names who the document is written for; contains no medical assertion.
            "target audience",
            # Explains what the guideline is for and how guidelines differ from care
            # pathways, then gives generic advice to find local implementation barriers
            "use",
        }
    ),
    "j7mQNn": frozenset(  # 1,179 characters
        {
            # A two-sentence purpose statement about what the document is meant to
            # achieve; no dose, effect, threshold or definition.
            "aim",
            # Names who the document is written for - health professionals, programme
            # managers, ministries, professional societies - and makes no clinical
            "target audience",
        }
    ),
    "jDeeDL": frozenset(  # 2,065 characters
        {
            # A single sentence about how future Ebola virus disease research should be
            # designed and reported - core outcome sets, standardized case report forms,
            "areas of future research",
            # Four bullets of unanswered questions about supportive care bundles,
            # implementation, renal replacement and co-infection - open questions only,
            "optimized supportive care",
            # Two unanswered questions about whether rapid diagnostic tests could
            # complement existing testing and shorten time to treatment - no test
            "rapid diagnostic tests",
            # Seven bullets that are all unanswered research questions about monoclonal
            # antibody therapy - every one is phrased as a question and none states a
            "therapeutics",
        }
    ),
    "jMMeqj": frozenset(  # 1,889 characters
        {
            # Administrative complaints process for a regulator, ending in a pointer to
            # resources that the scraper did not capture.
            "lodging complaints about medication management",
            # States why the document exists, who funded it, and the hybrid adopt/adapt
            # method used to build the recommendations.
            "purpose of guideline",
            # Says who the document is written for and that a companion guide and
            # dissemination plan exist.
            "target audience",
            # Plans for updating the document and the platform it is hosted on.
            "updating the guideline",
        }
    ),
    "jO0lNL": frozenset(  # 10,405 characters
        {
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # Tables 3 and 4 again. This guideline writes 2.4 as a bold line rather than a
            # heading, so the block above ends at the next bold line and stops short of
            # them; here they are named directly. Both are legends - the four GRADE
            # certainty levels, and the criteria that map to recommendation strength.
            "table 3: description of the interpretation of the grade four levels of certainty of evidence",
            "table 4: grade etd criteria and considerations that link to the strength of recommendations",
            # The GRADE evidence-to-decision walkthrough, and Tables 3 and 4 under it -
            # the legend for the four certainty levels and the criteria that map to
            # recommendation strength. Written as a numbered subheading here, a bold line
            # in jO0lNL and a fifth-level heading in Ee4mAn; all three reduce to the same
            # key. The section around it stays: it holds the qualitative evidence
            # syntheses this publisher writes into its methods chapter.
            "decision-making process to reach a recommendation",
            # Who made the guideline: steering group, GDG and secretariat membership
            # tables, methodologists, evidence-synthesis teams, external reviewers and a
            "contributors to the guideline development process",
            # A single bullet about measuring how the national guideline document and
            # its policy were disseminated - dissemination of the guideline itself, with
            "health system",
        }
    ),
    "jOKYGj": frozenset(  # 2,731 characters
        {
            # Everything from this divider to the end of the section is document process
            # - the scope of this first iteration, which outcomes the panel chose to
            "what is coming next?",
        }
    ),
    "n303gE": frozenset(  # 2,304 characters
        {
            # Survey mechanics for the panel - how options are presented, the contact
            # email, the question count.
            "how does this survey work?",
            # Describes how the evidence for baseline rates was looked for; says nothing
            # about what was found.
            "one more small problem!",
            # States the purpose of the guideline document itself; no clinical content.
            "the aim",
            # Methodological rationale for the survey - why trial populations differ
            # from real ones; no estimate, dose or recommendation.
            "the issues",
            # Generic explanation of how baseline absolute risk is chosen and applied
            # across the guideline - the process, not any finding.
            "the solution",
            # A bare framing heading with no body text under it; it runs straight into
            # the next divider.
            "this survey asks you to make a decision about what baseline rates we should use",
        }
    ),
    "n3QGej": frozenset(  # 4,243 characters
        {
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "definition of the strength of recommendations",
            # Who made it: the Expert Reference Group, the list of member colleges and
            # organisations, and the funding statement.
            "acknowledgements and endorsements",
            # The origin story of the document: a 2016 scoping exercise, inconsistency
            # of prior guidance, the NBA/RANZCOG agreement to collaborate, and formation
            "clinical need for this guideline",
            # The final block is nothing but the bolded caption line for Figure 8.2,
            # whose figure is absent from the scraped text, so it carries no number, no
            "figure 8.2 international units (ius) of rh d immunoglobulin issued since 2003–04",
            # A pure scope-and-purpose statement saying what the document covers and who
            # it is aimed at.
            "intent of the guideline",
            # Three bullets describing what Volumes 1-3 of the technical report contain
            # - a pointer to other files.
            "related material",
        }
    ),
    "n3QxOj": frozenset(  # 2,423 characters
        {
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations",
            # Says how this chapter was produced and who led it.
            "common concerns raised by patients (section lead: helena rosengren)",
            # Says how this chapter was produced and who led it, nothing about the
            # disease.
            "early detection (section lead: david whiteman)",
            # Review question MS1 plus its PICO table; no results.
            "metastatic disease and systematic therapies (section lead: alex guminski)[",
            # Review question RT1 plus its PICO table; no results.
            "radiotherapy (section lead: gerald fogarty)",
            # The review question SX1 and its PICO table (population, risk factors,
            # outcomes searched for) - the shape of the search, not what it found; the
            "surgical treatment (section lead: peter callan)",
        }
    ),
    "nBpo1j": frozenset(  # 24,188 characters
        {
            # The GRADE evidence-to-decision walkthrough, and Tables 3 and 4 under it -
            # the legend for the four certainty levels and the criteria that map to
            # recommendation strength. Written as a numbered subheading here, a bold line
            # in jO0lNL and a fifth-level heading in Ee4mAn; all three reduce to the same
            # key. The section around it stays: it holds the qualitative evidence
            # syntheses this publisher writes into its methods chapter.
            "decision-making process to reach a recommendation",
            # A two-column decoder table of acronyms, almost all of them
            # guideline-methodology terms (AGREE II, AMSTAR II, CASP, CERQual, EtD, GDG,
            "abbreviations",
            # Who made the guideline: steering group, guideline development group,
            # secretariat, methodologists, evidence-synthesis teams, external reviewers,
            "contributors to the guideline development process",
            # How conflicts of interest were declared, assessed and managed, and the
            # four-level classification used for them.
            "declarations and management of interests",
            # The signature block - the name and job title of the official who signed
            # the document off.
            "dr. binyerem c. ukaire, fwacs",
            # A three-sentence signpost saying that a priority-setting process and a
            # steering-group consultation are described in the two subsections below.
            "formulating questions and selecting outcomes",
            # Project governance and a capacity-strengthening table of courses and
            # workshops offered to guideline group members (an 8-week online short
            "organization, budget, planning and training",
        }
    ),
    "nV6X3n": frozenset(  # 3,967 characters
        {
            # Glossary-family cut of 2026-08-10 (see the section table's group note).
            "abbreviations and acronyms",
            # Citation string, copyright notice and publication date.
            "citation",
            # A statement of what the guideline is for and that it is continuously
            # updated - no medical content.
            "purpose",
            # A scope statement listing which topics the document covers and which it
            # excludes, with a cross-reference link.
            "scope",
            # Explains how to use the document and how to handle local implementation
            # barriers; gives no clinical instruction.
            "use",
        }
    ),
    "noPKwE": frozenset(  # 2,435 characters
        {
            # 2 blocks. Both occurrences (Preparation for surgery, and Follow-up after
            # curative resection) are a bare bulleted table of contents for the chapter
            "chapter subsections",
            # Caption for an image the scraper dropped; the surviving text is only the
            # rate units and a data source line.
            (
                "figure 1.10 trends in number of new cases and age-standardised incidence rates(a) for "
                "colorectal cancer in australian females, 1982 to 2007, projected to 2020"
            ),
            # Caption for an image the scraper dropped; the surviving text is only a
            # data-source and standardisation note.
            "figure 1.4 age-standardised mortality rates for colorectal cancer, australia, 1968–2014",
            # Caption for an image the scraper dropped; the surviving text is only a
            # data-source line.
            (
                "figure 1.7 crude participation in the national bowel cancer screening program, by "
                "remoteness area, 2013–2014"
            ),
            # Grading-method boilerplate: a table defining evidence-based recommendation
            # / consensus-based recommendation / practice point, and a child table
            "nhmrc approved recommendation types and definitions",
        }
    ),
    "noVdWL": frozenset(  # 1,192 characters
        {
            # How the guideline's PICO questions were drafted, approved by the ESO
            # Guidelines Board and Executive Committee, and how many of them there are
            "formation of pico questions",
        }
    ),
    "ny70vj": frozenset(  # 1,371 characters
        {
            # 3 blocks. Everything after the Introduction is future-tense protocol
            # paperwork with no finding, effect estimate, dose or recommendation in it:
            "conclusion",
            # Everything after the Introduction is future-tense protocol paperwork with
            # no finding, effect estimate, dose or recommendation in it: the guideline's
            "objectives",
        }
    ),
    "nyONYj": frozenset(  # 512 characters
        {
            # A heading with no body that only describes how the recommendations were
            # made (GRADE certainty assessment and evidence-to-decision considerations)
            (
                "as detailed in the methods (section 2.4), these updated recommendations are based on "
                "the best available evidence and a grade assessment of the certainty of evidence, with "
                "explicit consideration of benefits and harms, values and preferences, and system-level "
                "implementation considerations"
            ),
            # A navigation pointer with no body, telling the reader which numbered
            # sections of the document hold the dosing tables.
            (
                "note: detailed information on dosing, treatment duration, and formulations by age and "
                "weight is provided in the relevant sections (5.3.1, 5.4.1) of the guideline and should "
                "be consulted when prescribing"
            ),
            # The same navigation pointer with no body, this time pointing at section
            # 6.2 for severe-malaria dosing.
            (
                "note: detailed information on dosing, treatment duration, and formulations by age and "
                "weight is provided in the relevant sections (6.2) of the guideline and should be "
                "consulted when prescribing"
            ),
        }
    ),
    "nyXxZL": frozenset(  # 3,338 characters
        {
            # Pure process description - WHO handbook procedures, GRADE and
            # GRADE-CERQual appraisal, the DECIDE evidence-to-decision framework, and
            "guideline development methods",
            # Two sentences: which WHO departments undertook the update, and a statement
            # of what the document does not cover (Doppler ultrasound for a
            "rationale and objectives",
            # 2 blocks. A single paragraph naming the intended readers of the document
            # (policy-makers, programme managers, professional societies, health
            "target audience",
        }
    ),
    "nyxpZL": frozenset(  # 1,734 characters
        {
            # A statement of what the document sets out to do, with no clinical content.
            "purpose",
            # Says which topics the guideline covers and how its questions were
            # prioritised, not what to do clinically.
            "scope",
            # Names who the document is written for and which settings it applies to,
            # plus the professional group that will be consulted.
            "target population and audience",
        }
    ),
}

# A bolded line the publisher is using as a heading has no level, so it is treated as deeper
# than any real one: its block ends at the next bold line or at the first `#` heading.
_INLINE_BOLD_HEADING_LEVEL = 7
# Anchored, unlike `_WHOLE_LINE_BOLD_RE`, which finds bold anywhere in a line: a line that
# is bold from end to end is being used as a heading, one that merely contains bold is not.
_INLINE_BOLD_LINE_RE = re.compile(r"\A(?:\\?\*){2}\s*(.+?)\s*(?:\\?\*){2}\Z")


def _drop_inline_sections(markdown: str, short_code: str) -> str:
    """Remove a heading written inside a section body, and the block it opens.

    The block runs to the next heading of the same or a shallower level, which is the
    boundary the publisher itself declared, so a mistake costs one block rather than the
    rest of the section. A deeper heading is inside the block and goes with it.

    Args:
        markdown: Rendered markdown for one section body.
        short_code: The guideline's own code.
    """
    headings = _GUIDELINE_INLINE_SKIP_HEADINGS.get(short_code, frozenset())
    if not headings:
        return markdown
    lines = markdown.split("\n")
    keep = [True] * len(lines)
    dropping = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        match = _RENDERED_HEADING_RE.match(stripped)
        if match:
            level, text = len(match.group("hashes")), match.group("text")
        else:
            bold = _INLINE_BOLD_LINE_RE.match(stripped)
            level, text = (_INLINE_BOLD_HEADING_LEVEL, bold.group(1)) if bold else (0, "")
        if level:
            if dropping and level <= dropping:
                dropping = 0
            if not dropping and _skip_lookup_key(text) in headings:
                dropping = level
        keep[index] = not dropping
    if all(keep):
        return markdown
    return "\n".join(line for line, kept in zip(lines, keep, strict=True) if kept)


def _section_markdown(
    section: dict[str, Any],
    *,
    depth: int,
    link_mode: LinkMode,
    moved_picos: frozenset[int] = frozenset(),
    institution: str = "",
    short_code: str = "",
) -> tuple[list[str], int]:
    """Render one section and its descendants into markdown blocks.

    Returns the blocks and the number of recommendations emitted with a label,
    which `build_magic_guideline_text` checks against the publisher's own count.

    Args:
        section: Section object from the guideline JSON.
        depth: Nesting depth, which sets the markdown heading level.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
        moved_picos: `picoId`s that render under their owning recommendation, so
            the section-level copy is skipped here (default: frozenset()).

        institution: The publisher, passed down so `_is_skipped_section` can apply a
            publisher-specific heading rule. (default: "")
        short_code: The guideline's own code, passed down the same way so
            `_is_skipped_section` can apply a rule written for this one guideline.
            (default: "")
    """
    blocks: list[str] = []
    emitted = 0
    heading = _plain_heading(str(section.get("heading") or ""), link_mode=link_mode)
    if heading and _is_skipped_section(section, heading, institution, short_code):
        logger.debug("Skipping boilerplate section %r", heading)
        for child in section.get("subSections") or []:
            if not isinstance(child, dict):
                continue
            child_heading = _plain_heading(str(child.get("heading") or ""), link_mode=link_mode)
            if _skip_lookup_key(child_heading) not in _PICO_FRAME_HEADINGS:
                continue
            # Promoted to the skipped parent's own level, not one below it, so removing
            # the parent does not leave a gap in the heading levels.
            child_blocks, child_emitted = _section_markdown(
                child,
                depth=depth,
                link_mode=link_mode,
                moved_picos=moved_picos,
                institution=institution,
                short_code=short_code,
            )
            blocks.extend(child_blocks)
            emitted += child_emitted
        return blocks, emitted

    body = _markdown(section.get("text"), link_mode=link_mode, base_level=min(depth + 1, _MAX_HEADING_LEVEL))
    # A section that is nothing but "click here for this section" goes with its heading:
    # the heading alone is a topical match that delivers nothing.
    #
    # Judged BEFORE the inline rules run, and it has to be. A section dropped here loses its
    # recommendations too, so emptying its body first turns a rule that removes a
    # further-reading list into one that removes the guidance beside it: taking jm83RE's
    # "Resources" blocks this way cost six recommendations, among them "Recommend commencing
    # pelvic floor muscle strength training from 20 weeks gestation."
    if _is_pointer_only(section, body):
        logger.debug("Skipping pointer-only section %r", heading)
        return [], emitted
    body = _drop_inline_sections(body, short_code)
    # Publishers sometimes wrap a chapter in an outline level of the same name: a section
    # with no text of its own whose only child repeats its heading exactly. Emitting both
    # puts a heading in the document that names something with nothing under it, and
    # gives a heading-aware reader a boundary where there is no content change. 13 of
    # these across 6 guidelines; one is a dead end with no body and no children at all.
    children = [child for child in (section.get("subSections") or []) if isinstance(child, dict)]
    duplicates_child = (
        not body
        and len(children) == 1
        and _skip_lookup_key(heading)
        and _skip_lookup_key(_plain_heading(str(children[0].get("heading") or ""), link_mode=link_mode))
        == _skip_lookup_key(heading)
    )
    if heading and not duplicates_child:
        blocks.append(f"{'#' * min(depth + 1, _MAX_HEADING_LEVEL)} {heading}")
    if body:
        blocks.append(body)

    for pico in section.get("picos") or []:
        if not isinstance(pico, dict) or pico.get("picoId") in moved_picos:
            continue
        rendered = _pico_markdown(pico, link_mode=link_mode, base_level=min(depth + 1, _MAX_HEADING_LEVEL))
        if rendered:
            blocks.append(rendered)

    for recommendation in section.get("recommendations") or []:
        if not isinstance(recommendation, dict):
            continue
        rendered = _recommendation_markdown(
            recommendation, level=depth + 2, link_mode=link_mode, moved_picos=moved_picos
        )
        if rendered:
            blocks.append(rendered)
            emitted += _is_labelled_recommendation(rendered)

    for child in section.get("subSections") or []:
        if isinstance(child, dict):
            child_blocks, child_emitted = _section_markdown(
                child,
                depth=depth + 1,
                link_mode=link_mode,
                moved_picos=moved_picos,
                institution=institution,
                short_code=short_code,
            )
            blocks.extend(child_blocks)
            emitted += child_emitted
    return blocks, emitted


_SHINGLE_WORDS = 8
_WORD_RE = re.compile(r"[a-z0-9]+")
_RENDERED_HEADING_LINE_RE = re.compile(r"\A#{1,6}\s")
_RECOMMENDATION_HEADING_RE = re.compile(r"\A#{1,6}\s+.*\brecommendation\b", re.IGNORECASE)
# What makes a repeated block worth keeping in both places. A guideline states the same
# effect estimate under every recommendation that rests on it, and the second copy is not
# redundant - it is that recommendation's evidence. Delete it and a reader who finds the
# recommendation finds no numbers under it. 1,100 of the 5,948 repeated blocks carry one of
# these, 651,211 characters, and they stay.
_EVIDENCE_IN_BLOCK_RE = re.compile(
    r"\b(?:RR|OR|HR|aOR|aRR|MD|SMD|NNT|NNH)\s*[=:]?\s*\d|95%\s*(?:CI|confidence)"
    r"|\bp\s*[<=>]\s*0?\.\d|\bcertainty\s+(?:high|moderate|low|very\s+low)"
    r"|\b\d+\s+participants?\b|\b\d+\s+(?:studies|trials|RCTs)\b"
    r"|\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|μg|mmol|mL|IU|mmHg)\b"
    r"|\b(?:hazard|odds|risk)\s+ratio|\brelative\s+risk\b|\bmean\s+difference\b",
    re.IGNORECASE,
)


def _block_shingles(block: str) -> set[str]:
    """Return every run of eight consecutive words in a block."""
    words = _WORD_RE.findall(block.lower())
    return {" ".join(words[index : index + _SHINGLE_WORDS]) for index in range(len(words) - _SHINGLE_WORDS + 1)}


# Blocks a named guideline writes into its body that no rule about headings can reach,
# keyed on `shortCode`. Each entry is the opening of the block to remove and, where the run
# is longer than one block, the opening of the first block that must be KEPT.
#
# The tables above all key on a heading. These are prose: a pointer to a technical report
# sitting under a recommendation, a funding sentence closing a background section. Nothing
# labels them, and matching their wording anywhere in the corpus would be a rule about a
# sentence rather than about a document, which is how a general rule starts damaging
# guidelines nobody looked at.
#
# Matched against the block reduced to lowercase with its emphasis stripped, and removal
# fails closed: if the stop phrase is not found after the opening one, only the opening
# block goes. Applied to the assembled document, so it reaches text rendered from a
# recommendation as well as from a section body.
_GUIDELINE_DROP_BLOCKS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "nV6X3n": (
        # A pointer to the Technical Report, under four separate recommendations.
        ("for more detailed information on the development of this recommendation", ""),
    ),
    "jm83RE": (
        # A download pointer under most section headings.
        ("download health professional summary sheet", ""),
        # The opening of "Topics under development", and only that. The section itself is
        # NOT on the skip list, deliberately: it reads like a placeholder and is not - Pelvic
        # floor health and Vaccines under it carry ten recommendations between them,
        # including "Recommend commencing pelvic floor muscle strength training from 20 weeks
        # gestation". Dropping the section cost all ten.
        ("these guidelines are regularly being updated and expanded", ""),
    ),
    "nyX5xL": (
        # The Appendix is the last thing in the document and is 535 characters of links to
        # files - a PRISMA chart, the Delphi voting results, the evidence tables - none of
        # which survives as text. Its heading reduces to nothing, so no heading rule can
        # name it.
        ("appendix", None),
    ),
    "nyxpZL": (
        # Who produces the guideline and who funds it, closing the Background section.
        (
            "an australian living guideline for the management of juvenile idiopathic arthritis is being produced by",
            "",
        ),
    ),
}

# Paperwork blocks to remove, named one guideline at a time.
#
# Folded into magic.py from its own module on 2026-08-11, so the scraper ships as one file;
# it is a list rather than logic, and none of it is code a reader needs to follow.
#
# Each key is a guideline's `shortCode`. Each entry is the opening of a block to remove and,
# where a run of blocks goes together, the opening of the first block that must be KEPT. An
# empty second element removes that block alone; `None` runs to the end of the document and is
# only ever written out deliberately.
#
# Built by reading all 212 in-corpus guidelines end to end, one reader each, hunting the
# paperwork left behind after whole sections of it had already been removed - further-reading
# lists, author bylines and affiliations, search strings, pointers to reports and files that did
# not survive scraping, funding and version notes. 2,174 proposals came back; 595 were refused,
# including every one whose text carried a recommendation, an effect estimate, a dose or a
# directive, and every proposed section with a recommendation object anywhere beneath it.
#
# Matched against the block reduced to lowercase with emphasis and heading marks stripped, and
# tolerant of a leading run of footnote markers or table wreckage.
_GUIDELINE_PAPERWORK_DROP_BLOCKS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "6nY0Lg": (
        # 2 blocks, 490 characters. authors, panels and affiliations (1); references and further
        # reading
        # (1).
        ("for more information, we refer to the specific recommendations for each type of surgery.", ""),
    ),
    "6nYJxE": (
        # The disclaimer closing Methodology, hanging off the timing block with no divider
        # of its own so no heading rule reaches it.
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        # 6 blocks, 1,484 characters. other paperwork (4); funding, publication and version
        # notes (1);
        # pointers to a report, file or figure (1).
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
        # The disclaimer closing Methodology, hanging off the timing block with no divider
        # of its own so no heading rule reaches it.
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        # 7 blocks, 1,866 characters. other paperwork (6); funding, publication and version
        # notes (1).
        ("antiplatelet use along with af is collected as part of the national stroke audit.", ""),
        ("data are collected against a clinical indicator on early antiplatelet therapy and long term", ""),
        ("there is a clinical indicator collected on anticoagulation therapy in the national stroke audit", ""),
        ("there is a clinical indicator collected on cholesterol-lowering therapy in the national stroke audit", ""),
        ("there is a clinical indicator collected on early antiplatelet therapy and long term (secondary)", ""),
        ("there is a clinical indicator collected on provision of education regarding risk factor modification", ""),
        ("this recommendation will be superseded by the update above once it is approved.", ""),
    ),
    "8nyb0E": (
        # 11 blocks, 8,387 characters. other paperwork (5); funding, publication and version
        # notes (2);
        # references and further reading (1); search strategies and screening (1); authors,
        # panels and
        # affiliations (1); pointers to a report, file or figure (1).
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
        # 2 blocks, 757 characters. authors, panels and affiliations (1); pointers to a report,
        # file or
        # figure (1).
        ("alle hearing responses are published on www.håndleddsbrudd.no.", ""),
        ("we thank norwegian orthopaedic association for the mandate and trust we were given", ""),
    ),
    "E52Obj": (
        # 6 blocks, 3,561 characters. other paperwork (3); pointers to a report, file or figure
        # (3).
        ("for more advice on alcohol and pregnancy, please visit the alcohol and drug foundation", ""),
        ("source: australian institute of health and welfare, 2017.", ""),
        ("source: healthlink bc, 2019 and international alliance for responsible drinking, 2019.", ""),
        ("where can i get help? more information about alcohol and young people can be found", ""),
        ("where can i get help? seek professional advice if you have questions about this information.", ""),
        ("where can i get help? the australian government department of health recommends the services", ""),
    ),
    "E5AbPE": (
        # 12 blocks, 5,855 characters. pointers to a report, file or figure (5); other paperwork
        # (2);
        # funding, publication and version notes (2); references and further reading (1);
        # authors, panels
        # and affiliations (1); search strategies and screening (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
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
        # 10 blocks, 4,059 characters. other paperwork (6); dissemination and updating (2);
        # funding,
        # publication and version notes (1); pointers to a report, file or figure (1).
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
        # 25 blocks, 12,890 characters. other paperwork (8); authors, panels and affiliations
        # (7); search
        # strategies and screening (4); funding, publication and version notes (3);
        # dissemination and
        # updating (2); pointers to a report, file or figure (1).
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
        # 9 blocks, 5,477 characters. other paperwork (6); references and further reading (1);
        # pointers to
        # a report, file or figure (1); dissemination and updating (1).
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
        # 5 blocks, 4,637 characters. other paperwork (3); dissemination and updating (1);
        # pointers to a
        # report, file or figure (1).
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
        # 4 blocks, 763 characters. search strategies and screening (1); authors, panels and
        # affiliations
        # (1); other paperwork (1); funding, publication and version notes (1).
        (
            ("for each recommendation, the corresponding chapter in the guidelines provides more detailed information"),
            "",
        ),
        ("in each instance, the guideline development working group was able to reach a decision about", ""),
        ("in these guidelines, implications of the recommendations for clinical practice and the health system", ""),
        ("where to find information about liver cancer and liver cancer treatment", ""),
    ),
    "EK0DDj": (
        # 28 blocks, 12,023 characters. other paperwork (15); funding, publication and version
        # notes (5);
        # dissemination and updating (4); pointers to a report, file or figure (3); references
        # and further
        # reading (1).
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
        # 2 blocks, 397 characters. other paperwork (2).
        ("community dental health coordinators and policy makers also can use the recommendation statements", ""),
        ("the target user for this guideline includes the following: general and specialty dentists", ""),
    ),
    "EKKOyE": (
        # 18 blocks, 4,778 characters. pointers to a report, file or figure (12); references and
        # further
        # reading (2); other paperwork (2); dissemination and updating (1); funding, publication
        # and
        # version notes (1).
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
        # 6 blocks, 2,542 characters. other paperwork (6).
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
        # 1 blocks, 225 characters. pointers to a report, file or figure (1).
        (
            ("evidence profiles for oral anticoagulants versus antiplatelet therapy for preventing stroke in patients"),
            "",
        ),
    ),
    "EPY83j": (
        # 17 blocks, 8,186 characters. authors, panels and affiliations (8); references and
        # further
        # reading (6); search strategies and screening (1); other paperwork (1); pointers to a
        # report,
        # file or figure (1).
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
        # 5 blocks, 9,191 characters. search strategies and screening (3); funding, publication
        # and
        # version notes (2).
        ("this is a high priority recommendation and we do not expect to update", ""),
        ("this is a high priority recommendation and will be updated as soon", ""),
        ("this is a low priority recommendation and we do not expect", ""),
        ("this is a moderate priority recommendation and we do not expect to update", ""),
        ("this is a moderate priority recommendation and will be updated when new evidence", ""),
    ),
    "EQ3m3L": (
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
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
        # 5 blocks, 4,106 characters. other paperwork (3); funding, publication and version
        # notes (1);
        # dissemination and updating (1).
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
        # 2 blocks, 732 characters. other paperwork (1); pointers to a report, file or figure (1).
        ("find evidence summaries, decision aids and practical issues in user-friendly formats here:", ""),
        ("practical issues see the full evidence summaries and practical issues relevant to", ""),
    ),
    "ERWMXj": (
        # 2 blocks, 1,018 characters. other paperwork (1); references and further reading (1).
        ("future guidelines should assess interventions for the common clinical presentations of svd including", ""),
        ("stroke presentations of svd such as ‘lacunar’ stroke or ich, are included within current regional", ""),
    ),
    "ERWQ1j": (
        # 6 blocks, 4,853 characters. other paperwork (2); references and further reading (2);
        # authors,
        # panels and affiliations (1); search strategies and screening (1).
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
        # 3 blocks, 1,576 characters. funding, publication and version notes (1); authors,
        # panels and
        # affiliations (1); pointers to a report, file or figure (1).
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
        # 6 blocks, 1,288 characters. other paperwork (2); funding, publication and version
        # notes (1);
        # authors, panels and affiliations (1); pointers to a report, file or figure (1);
        # references and
        # further reading (1).
        ("algorithms for the assessment of individual cancers are detailed in the", ""),
        ("cosa convened a working group of multidisciplinary health professionals", ""),
        ("if you are an aya cancer patient, survivor or family member seeking advice", ""),
        ("the information in this section draws significantly from the excellent resource", ""),
        ("this guidance has been produced by the clinical oncological society of australia", ""),
        ("this information should be read in conjunction with recommendations and text under", ""),
    ),
    "EZ1w8n": (
        # 1 blocks, 1,098 characters. authors, panels and affiliations (1).
        ("authors: francois lamontagne, chair, critical care clinician ; bram rochwerg, critical care clinician", ""),
    ),
    "EZVOaE": (
        # 9 blocks, 2,720 characters. pointers to a report, file or figure (4); other paperwork
        # (4);
        # funding, publication and version notes (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        (
            (
                "international human rights law includes fundamental commitments of states to enable women "
                "and adolescent"
            ),
            "",
        ),
        # 5 blocks, 2,134 characters. other paperwork (2); references and further reading (1);
        # dissemination and updating (1); pointers to a report, file or figure (1).
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
        # 3 blocks, 1,311 characters. authors, panels and affiliations (1); dissemination and
        # updating
        # (1); other paperwork (1).
        ("as part of the who’s normative work on supporting evidence-informed policies and practices", ""),
        ("evidence on these interventions was evaluated by a guideline development group (gdg) composed", ""),
        ("this section provides the who recommendation adopted by the gdg on antenatal mms,", ""),
    ),
    "Ea7gOL": (
        # 5 blocks, 1,271 characters. pointers to a report, file or figure (3); funding,
        # publication and
        # version notes (2).
        ("a planned update is already ongoing to address clinical questions related to the prevention", ""),
        ("blue boxes (right side of diagram) represent the probability tree where dat is given", ""),
        ("full summary of the evidence synthesis is available here.", ""),
        ("red boxes (left side of diagram) represent the probability tree where allergy testing", ""),
        ("update and access: the living guideline is written, disseminated, and updated on an online platform", ""),
    ),
    "EaG1dL": (
        # 9 blocks, 3,259 characters. funding, publication and version notes (3); pointers to a
        # report,
        # file or figure (3); authors, panels and affiliations (2); other paperwork (1).
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
        # 4 blocks, 5,872 characters. pointers to a report, file or figure (2); authors, panels
        # and
        # affiliations (1); other paperwork (1).
        ("table of evidence provides details regarding the safety and efficacy of continuing", ""),
        ("the evidence table provides details regarding the safety and efficacy of continuing versus", ""),
        ("this guideline document was developed using the grade methodology and aims to assist", ""),
        (
            "this guideline was initiated by the eso and prepared according to eso standard",
            "the mwg undertook the following steps:",
        ),
    ),
    "EaKvXL": (
        # 5 blocks, 2,341 characters. references and further reading (2); other paperwork (1);
        # pointers to
        # a report, file or figure (1); funding, publication and version notes (1).
        ("details of standard precautions and best practices for prevention and control of filovirus", ""),
        ("extracted from the rapid advice guideline: personal protective equipment for use in a filovirus", ""),
        ("pubmed, google and google scholar were searched for the key words (compliance, attitudes, beliefs,", ""),
        ("this topic is addressed in more detail elsewhere", ""),
        ("who should develop specifications and training materials on use of ppe and disposal protocols.", ""),
    ),
    "Edr04L": (
        # 15 blocks, 25,907 characters. other paperwork (9); references and further reading (2);
        # funding,
        # publication and version notes (2); search strategies and screening (2).
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
        # 4 blocks, 1,299 characters. other paperwork (2); pointers to a report, file or figure
        # (1);
        # funding, publication and version notes (1).
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
        # 11 blocks, 6,495 characters. other paperwork (4); authors, panels and affiliations
        # (3); funding,
        # publication and version notes (2); search strategies and screening (2).
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
    "EeGaAL": (
        # 1 blocks, 238 characters. pointers to a report, file or figure (1).
        ("please access the full guideline and associated visual summary of the recommendations", ""),
    ),
    "Eea27E": (
        # 2 blocks, 1,237 characters. funding, publication and version notes (1); authors,
        # panels and
        # affiliations (1).
        ("recently, the european stroke organisation (eso) updated its policy on preparation and publication", ""),
        ("the eso guidelines committee invited the lead author (md) to form and chair", ""),
    ),
    "Eea3zE": (
        # 3 blocks, 1,707 characters. authors, panels and affiliations (2); other paperwork (1).
        ("the european stroke initiative (eusi) last published recommendations on management of ich in 2006.", ""),
        ("the working group formulated 20 pico questions, each one examining two outcomes", ""),
    ),
    "Eez2Kj": (
        # The label over the supporting-document links, which are removed by shape but leave
        # it behind - the links render in a different fragment, so the label is not beside
        # them when that pass runs. 21 copies.
        ("supporting documents", ""),
        # 17 blocks, 6,885 characters. other paperwork (12); pointers to a report, file or
        # figure (4);
        # authors, panels and affiliations (1).
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
        # 5 blocks, 2,237 characters. other paperwork (3); funding, publication and version
        # notes (2).
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
        # The process half of "Methods: how this guideline was created", 5,094 characters,
        # from "Who was involved?" to the paragraph before the prognosis discussion: panel
        # recruitment, conflicts, what reviews were commissioned, the outcome-prioritisation
        # survey, the GRADE framework and how minimal important differences were agreed.
        # The section itself is on the keep list and must stay: what follows the cut is the
        # KDIGO risk stratification, the epidemiology that a small GFR decline means
        # something different at different ages, the five-year time frame the absolute
        # effects are estimated over, and the values-and-preferences statements. The
        # surviving risk-stratified sections cannot be read without them.
        (
            "who was involved?",
            "what is the approach to prognosis and risk prediction for cardiovascular and kidney outcomes?",
        ),
        # 3 blocks, 1,369 characters. other paperwork (2); authors, panels and affiliations (1).
        ("guidance was drafted by the methods chair with input from the clinical chair", ""),
        ("panel meetings were facilitated by methods and clinical co-chairs, and were conducted in july 2023", ""),
        ("this bmj rapid recommendation was developed in accordance with standards for trustworthy guidance", ""),
    ),
    "Eg947L": (
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
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
        # 8 blocks, 5,027 characters. pointers to a report, file or figure (3); other paperwork
        # (2);
        # dissemination and updating (2); funding, publication and version notes (1).
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
        # 1 blocks, 499 characters. other paperwork (1).
        (
            (
                "stakeholders from all who regions participated in the preliminary online survey. feedback "
                "from the survey"
            ),
            "",
        ),
    ),
    "EgXyej": (
        # 7 blocks, 4,025 characters. other paperwork (5); dissemination and updating (2).
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
        # 15 blocks, 4,200 characters. other paperwork (5); dissemination and updating (3);
        # pointers to a
        # report, file or figure (3); references and further reading (2); authors, panels and
        # affiliations
        # (1); funding, publication and version notes (1).
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
        # 5 blocks, 1,509 characters. funding, publication and version notes (2); pointers to a
        # report,
        # file or figure (1); authors, panels and affiliations (1); dissemination and updating
        # (1).
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
        # 5 blocks, 3,428 characters. authors, panels and affiliations (4); references and
        # further reading
        # (1).
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
        # 13 blocks, 5,033 characters. pointers to a report, file or figure (9); other paperwork
        # (3);
        # search strategies and screening (1).
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
        # The disclaimer closing Methodology, hanging off the timing block with no divider
        # of its own so no heading rule reaches it.
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        # 17 blocks, 4,830 characters. other paperwork (12); references and further reading (2);
        # funding,
        # publication and version notes (1); dissemination and updating (1); pointers to a
        # report, file or
        # figure (1).
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
        # 7 blocks, 2,060 characters. other paperwork (3); funding, publication and version
        # notes (1);
        # authors, panels and affiliations (1); references and further reading (1); pointers to
        # a report,
        # file or figure (1).
        ("for a more detailed literature review, please see appendix 1d under references.", ""),
        ("pregnant/parturient women and others interested in information on dystocia are also welcome to read", ""),
        ("rationale not to update in 2017 based on feedback from professional companies,", ""),
        ("the primary target group for the national clinical guideline are healthcare professionals", ""),
        ("the purpose of the national clinical guideline is to ensure the use of evidencebased procedures", ""),
        ("the relevant patient organisations were represented in the established reference group and had", ""),
        ("updating the recommendation is not considered necessary in 2017", ""),
    ),
    "L4Q5An": (
        # 29 blocks, 27,086 characters. authors, panels and affiliations (10); other paperwork
        # (8);
        # funding, publication and version notes (5); dissemination and updating (4); search
        # strategies
        # and screening (1); pointers to a report, file or figure (1).
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
        # 4 blocks, 1,603 characters. other paperwork (2); funding, publication and version
        # notes (1);
        # authors, panels and affiliations (1).
        ("each dot represents a week of time. in deciding which drugs to cover, the who", ""),
        ("how this guideline was created: this living guideline is from the world health organization (who)", ""),
        ("this guideline is related to two other who living guidelines for covid-19:", ""),
        ("updates and access: this is a living guideline; therefore, recommendations may be updated", ""),
    ),
    "L6zBvL": (
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        (
            (
                "we will develop this clinical practice guideline according to robust methodological "
                "standards outlined by"
            ),
            "",
        ),
        # 14 blocks, 19,765 characters. authors, panels and affiliations (5); search strategies
        # and
        # screening (4); pointers to a report, file or figure (2); other paperwork (2); funding,
        # publication and version notes (1).
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
        # 7 blocks, 6,371 characters. authors, panels and affiliations (4); other paperwork (2);
        # dissemination and updating (1).
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
        # 2 blocks, 670 characters. other paperwork (1); references and further reading (1).
        ("if you work with boys or men in health or education settings, these guidelines", ""),
        ("note: effect sizes reported are unadjusted for consistency. for effects adjusted for potential", ""),
    ),
    "LAag6L": (
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("use of the guideline by eaes members will be monitored through an online survey", ""),
        # 4 blocks, 1,077 characters. authors, panels and affiliations (2); other paperwork (1);
        # pointers
        # to a report, file or figure (1).
        ("an update of this rapid guideline is planned to take place in 2028", ""),
        ("the guideline was sponsored and funded by the european association for endoscopic surgery", ""),
        ("the guideline will be published in surgical endoscopy & other interventional techniques", ""),
        ("there was unanimous consensus on the direction, the strength, and the wording of the recommendations", ""),
    ),
    "LAkxVE": (
        # 5 blocks, 8,151 characters. authors, panels and affiliations (3); other paperwork (2).
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
        # 3 blocks, 348 characters. pointers to a report, file or figure (2); references and
        # further
        # reading (1).
        ("figure 1: partner types for clinical practice(estcourt cs et al 2022)", ""),
        ("sample letter to be sent to contacts. for documents required by law in your country", ""),
        ("sample letter to be sent to family doctor. for documents required by law in your", ""),
    ),
    "LGm87E": (
        # 10 blocks, 2,471 characters. funding, publication and version notes (4); authors,
        # panels and
        # affiliations (2); search strategies and screening (2); pointers to a report, file or
        # figure (1);
        # other paperwork (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("| | | | --- | --- | | type of recommendation | definition", ""),
        # 19 blocks, 4,020 characters. other paperwork (12); search strategies and screening
        # (3); authors,
        # panels and affiliations (2); pointers to a report, file or figure (2).
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
        # 3 blocks, 1,270 characters. pointers to a report, file or figure (2); search
        # strategies and
        # screening (1).
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
        # 20 blocks, 8,077 characters. other paperwork (9); funding, publication and version
        # notes (6);
        # authors, panels and affiliations (3); search strategies and screening (1);
        # dissemination and
        # updating (1).
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
    "LpvNkE": (
        # 1 blocks, 214 characters. pointers to a report, file or figure (1).
        ("a pragmatic flow chart (figure 1), guiding on how to treat according to the principles", ""),
    ),
    "Lq0orj": (
        # 5 blocks, 1,135 characters. authors, panels and affiliations (2); other paperwork (1);
        # funding,
        # publication and version notes (1); search strategies and screening (1).
        ("a more detailed algorithm is in development.", ""),
        ("a priori voting rules were in place if the gdg failed to reach consensus", ""),
        ("as described in the methods section, priority questions were identified to define the guideline scope", ""),
        ("draft recommendations underwent internal and external peer review and final approval by the who", ""),
        ("methods these guidelines were developed in accordance with the who handbook for guideline development", ""),
    ),
    "LqGR0E": (
        # 8 blocks, 4,324 characters. other paperwork (5); dissemination and updating (2);
        # authors, panels
        # and affiliations (1).
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
    "LqR80n": (
        # 1 blocks, 411 characters. pointers to a report, file or figure (1).
        ("the objective of this guideline is to review the updated evidence of urate-lowering therapy", ""),
    ),
    "LqRV3n": (
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        (
            ("the recommendations in this living guideline apply to all healthcare settings in australia including"),
            "",
        ),
        # 15 blocks, 5,288 characters. pointers to a report, file or figure (10); dissemination
        # and
        # updating (3); funding, publication and version notes (1); other paperwork (1).
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
        # 9 blocks, 2,514 characters. other paperwork (4); pointers to a report, file or figure
        # (3);
        # references and further reading (2).
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
        # 23 blocks, 4,930 characters. pointers to a report, file or figure (19); other
        # paperwork (2);
        # references and further reading (1); search strategies and screening (1).
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
        # 9 blocks, 2,483 characters. pointers to a report, file or figure (3); other paperwork
        # (2);
        # dissemination and updating (1); funding, publication and version notes (1); references
        # and
        # further reading (1); search strategies and screening (1).
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
        # 10 blocks, 5,510 characters. references and further reading (5); other paperwork (3);
        # authors,
        # panels and affiliations (2).
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
        # 66 blocks, 31,983 characters. references and further reading (61); other paperwork
        # (3); authors,
        # panels and affiliations (1); pointers to a report, file or figure (1).
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
        # 12 blocks, 7,281 characters. pointers to a report, file or figure (4); authors, panels
        # and
        # affiliations (4); references and further reading (2); other paperwork (2).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        (
            (
                "national and subnational subgroups may be established to adapt and implement these "
                "recommendations based"
            ),
            "",
        ),
        # 11 blocks, 9,403 characters. other paperwork (6); authors, panels and affiliations
        # (2); pointers
        # to a report, file or figure (1); funding, publication and version notes (1);
        # dissemination and
        # updating (1).
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
        # 7 blocks, 5,674 characters. other paperwork (2); references and further reading (2);
        # dissemination and updating (1); pointers to a report, file or figure (1); search
        # strategies and
        # screening (1).
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
        # 9 blocks, 3,509 characters. other paperwork (2); authors, panels and affiliations (2);
        # pointers
        # to a report, file or figure (2); funding, publication and version notes (2); search
        # strategies
        # and screening (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
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
        # 8 blocks, 8,674 characters. other paperwork (4); pointers to a report, file or figure
        # (2);
        # dissemination and updating (1); authors, panels and affiliations (1).
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
        # 7 blocks, 13,706 characters. other paperwork (3); references and further reading (2);
        # dissemination and updating (1); authors, panels and affiliations (1).
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
        # The disclaimer closing Methodology, hanging off the timing block with no divider
        # of its own so no heading rule reaches it.
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        # 1 blocks, 2,840 characters. search strategies and screening (1).
        (
            (
                "development of questions questions have been extensively developed and reviewed over the "
                "four iterations"
            ),
            "brief summary of grade the guidelines were developed following the grade methodology",
        ),
    ),
    "QnoKGn": (
        # The disclaimer closing Methodology, hanging off the timing block with no divider
        # of its own so no heading rule reaches it.
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        # 8 blocks, 671 characters. funding, publication and version notes (3); dissemination
        # and updating
        # (3); authors, panels and affiliations (2).
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
        # The disclaimer closing Methodology, hanging off the timing block with no divider
        # of its own so no heading rule reaches it.
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        # 5 blocks, 2,046 characters. other paperwork (4); funding, publication and version
        # notes (1).
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
        # The disclaimer closing Methodology, hanging off the timing block with no divider
        # of its own so no heading rule reaches it.
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        # 8 blocks, 1,679 characters. other paperwork (7); pointers to a report, file or figure
        # (1).
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
    "ZjbDgn": (
        # 1 blocks, 165 characters. funding, publication and version notes (1).
        ("due to copyright issues the rationale for this recommendation can not be published", ""),
    ),
    "anBg0E": (
        # 2 blocks, 2,504 characters. authors, panels and affiliations (1); pointers to a
        # report, file or
        # figure (1).
        ("due to copyright issues the rationale for this recommendation can not be published in magicapp.", ""),
        ("| member | role/representing | professional specialty | conflict of interest ", "|"),
    ),
    "anBmDL": (
        # 6 blocks, 2,414 characters. authors, panels and affiliations (4); references and
        # further reading
        # (2).
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
        # 6 blocks, 2,633 characters. other paperwork (3); pointers to a report, file or figure
        # (1);
        # authors, panels and affiliations (1); search strategies and screening (1).
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
        # 6 blocks, 980 characters. authors, panels and affiliations (4); other paperwork (2).
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
        # 8 blocks, 2,779 characters. other paperwork (4); authors, panels and affiliations (2);
        # funding,
        # publication and version notes (1); references and further reading (1).
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
        # 12 blocks, 2,516 characters. pointers to a report, file or figure (7); other paperwork
        # (2);
        # funding, publication and version notes (1); references and further reading (1);
        # authors, panels
        # and affiliations (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
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
        # 6 blocks, 4,576 characters. dissemination and updating (2); funding, publication and
        # version
        # notes (1); pointers to a report, file or figure (1); authors, panels and affiliations
        # (1);
        # references and further reading (1).
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
        # 16 blocks, 16,775 characters. other paperwork (7); pointers to a report, file or
        # figure (4);
        # dissemination and updating (4); references and further reading (1).
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
        # 28 blocks, 7,980 characters. other paperwork (10); authors, panels and affiliations (5);
        # pointers to a report, file or figure (4); funding, publication and version notes (3);
        # references
        # and further reading (3); dissemination and updating (2); search strategies and
        # screening (1).
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
    "j1WRkn": (
        # 2 blocks, 1,402 characters. dissemination and updating (1); other paperwork (1).
        ("the aim of this guideline document is to assist physicians treating patients with ischaemic", ""),
    ),
    "j1WYVn": (
        # 3 blocks, 873 characters. pointers to a report, file or figure (1); authors, panels and
        # affiliations (1); other paperwork (1).
        ("in this document, we outline the current state of the evidence on the effect of ivt", ""),
        ("one group member (ww) did not vote or comment on this chapter because he", ""),
        ("this guideline document was developed following the grade methodology and aims to assist physicians", ""),
    ),
    "j1Wqrn": (
        # 4 blocks, 1,102 characters. other paperwork (2); pointers to a report, file or figure
        # (2).
        ("reproduced with permission from bmj, designed by will stahl-timmins", ""),
        ("the match-it decision support tool allows you to interact with the evidence and your patients", ""),
        ("we encourage professional societies and other actors in the evidence ecosystem to re-use", ""),
        ("what is my patient’s risk?", ""),
    ),
    "j1k9Jn": (
        # 2 blocks, 1,014 characters. other paperwork (2).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
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
        # 7 blocks, 6,315 characters. other paperwork (4); references and further reading (2);
        # dissemination and updating (1).
        ("1.2 rationale and objectives", "1.4 scope of the recommendations"),
        ("murano m, chou d, costa do nascimento ml, turner t. using the who-integrate evidence to", ""),
        ("the evidence is summarized in grade tables:", ""),
        ("the implementation and impact of these recommendations will be monitored at the health service,", ""),
        ("the updated recommendations on induction of labour at term or beyond, and outpatient settings for", ""),
        ("the world health organization (who) envisions a world where", ""),
        ("this section presents the two updated recommendations on the timing of induction of labour that", ""),
    ),
    "j20X4n": (
        # 23 blocks, 6,667 characters. other paperwork (6); funding, publication and version
        # notes (6);
        # authors, panels and affiliations (5); dissemination and updating (3); pointers to a
        # report, file
        # or figure (2); references and further reading (1).
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
        # 7 blocks, 3,272 characters. other paperwork (6); funding, publication and version
        # notes (1).
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
        # 2 blocks, 2,616 characters. other paperwork (2).
        (
            "monitoring and evaluating the guideline implementation",
            "annex 2. critical and important outcomes for decision-making",
        ),
        ("the quality of the supporting evidence rated as", ""),
    ),
    "j2QZZE": (
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
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
        # 12 blocks, 12,096 characters. other paperwork (7); pointers to a report, file or
        # figure (2);
        # authors, panels and affiliations (1); search strategies and screening (1); funding,
        # publication
        # and version notes (1).
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
        # 10 blocks, 392 characters. search strategies and screening (10).
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
        # 16 blocks, 4,196 characters. pointers to a report, file or figure (12); funding,
        # publication and
        # version notes (1); other paperwork (1); search strategies and screening (1);
        # dissemination and
        # updating (1).
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
        # 13 blocks, 4,819 characters. other paperwork (7); pointers to a report, file or figure
        # (3);
        # dissemination and updating (2); funding, publication and version notes (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("what is the effect of oxytocin for pph prevention on the priority outcomes?", ""),
        # 6 blocks, 2,638 characters. other paperwork (2); references and further reading (2);
        # dissemination and updating (1); pointers to a report, file or figure (1).
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
        # 26 blocks, 5,770 characters. authors, panels and affiliations (8); other paperwork
        # (7); funding,
        # publication and version notes (5); references and further reading (2); search
        # strategies and
        # screening (2); pointers to a report, file or figure (1); dissemination and updating (1).
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
        # 4 blocks, 325 characters. pointers to a report, file or figure (4).
        ("a summary infographic on the guideline recommendations and ungraded statements is provided", ""),
        ("figure 1. cultural safety for healthcare among first nations australians", ""),
        ("figure 5. self-management of chronic kidney disease among first nations australians", ""),
        ("figure 6. components of models of care for pre-dialysis", ""),
    ),
    "j9QY4j": (
        # 7 blocks, 1,566 characters. other paperwork (7).
        ("the wg formulated three pico questions. because these questions are closely intertwined", ""),
        ("the wg has formulated two pico questions. because these questions are closely related, an", ""),
        ("the working group formulated 6 pico questions. because these questions are closely related,", ""),
        ("the working group formulated one pico question.", ""),
        ("the working-group formulated one pico question.", ""),
        ("the working-group formulated one pico-question.", ""),
        ("the working-group formulated two pico-questions.", ""),
    ),
    "jDReJn": (
        # 8 blocks, 2,986 characters. other paperwork (4); references and further reading (4).
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
        # 19 blocks, 4,011 characters. references and further reading (17); other paperwork (2).
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
        # 11 blocks, 4,588 characters. other paperwork (4); dissemination and updating (3); search
        # strategies and screening (1); pointers to a report, file or figure (1); funding,
        # publication and
        # version notes (1); authors, panels and affiliations (1).
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
        # 3 blocks, 1,311 characters. funding, publication and version notes (2); pointers to a
        # report,
        # file or figure (1).
        ("part of these questions will be further addressed in next stages of be-safe project", ""),
        ("this guideline is developed as a part of the be-safe project, funded by the", ""),
        ("two pairs of authors extracted epoc factors data in duplicate. the factors reported per arm", ""),
        # The "Get in touch" contact blocks, removed 2026-08-10 on Evan's call that the corpus
        # must not ship personal contact details: the visible text shows institutional
        # addresses but the mailto targets underneath are the co-chairs' personal Gmail
        # accounts. The heading above them empties and goes with them.
        ("for any queries related to the clinical aspects of the guidelines, please contact", ""),
        ("for methodological queries, please contact the guideline methods chair", ""),
        ("for any other queries or comments, please contact", ""),
    ),
    "jDeeDL": (
        # 12 blocks, 1,447 characters. pointers to a report, file or figure (11); other
        # paperwork (1).
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
    "jMKQ9n": (
        # 1 blocks, 350 characters. authors, panels and affiliations (1).
        ("may want to fill this part out", ""),
    ),
    "jMMYPj": (
        # 1 blocks, 1,131 characters. authors, panels and affiliations (1).
        (
            (
                "our international panel included general practitioners, internists, paediatricians, "
                "pharmacists, physicians specialising in pain management"
            ),
            "",
        ),
    ),
    "jMMeqj": (
        # 7 blocks, 842 characters. references and further reading (2); pointers to a report,
        # file or
        # figure (2); other paperwork (2); search strategies and screening (1).
        ("australia government - guidance and resources for provider to support the aged care quality standards", ""),
        ("federal legislation relating to quality of care principles", ""),
        ("for further information about antidepressant continuation, please refer the to benefits and harms", ""),
        ("for more information about consent, please refer to the consent section.", ""),
        ("for more information about the evidence review, please refer to the technical report.", ""),
        ("for more information about the evidence update, please refer to the technical report.", ""),
        ("the evidence update for this guideline used the", ""),
    ),
    "jNW0VL": (
        # 10 blocks, 5,181 characters. other paperwork (7); references and further reading (3).
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
        # 9 blocks, 3,515 characters. authors, panels and affiliations (6); other paperwork (2);
        # references and further reading (1).
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
        # 6 blocks, 1,472 characters. search strategies and screening (4); references and
        # further reading
        # (1); other paperwork (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("an online survey was then implemented, where diverse national stakeholders were asked to vote", ""),
        ("priority topics on poverty-related diseases in the field of newborn and child health were", ""),
        ("the gela project focuses on newborn and child health. to identify priority topics within", ""),
        # 12 blocks, 8,238 characters. other paperwork (6); pointers to a report, file or figure
        # (4);
        # authors, panels and affiliations (1); references and further reading (1).
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
        # 16 blocks, 5,734 characters. other paperwork (6); funding, publication and version
        # notes (4);
        # authors, panels and affiliations (3); dissemination and updating (2); pointers to a
        # report, file
        # or figure (1).
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
        # 6 blocks, 4,905 characters. authors, panels and affiliations (4); funding, publication
        # and
        # version notes (2).
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
        # 14 blocks, 1,460 characters. search strategies and screening (8); pointers to a
        # report, file or
        # figure (6).
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
        # 3 blocks, 369 characters. funding, publication and version notes (2); search
        # strategies and
        # screening (1).
        ("(note that these are some examples of commercial services, but the list is not exhaustive", ""),
        ("additional screening tools used in australian states and territories", ""),
        ("these guidelines are regularly being updated and expanded. the following topics are currently", ""),
    ),
    "jW9PJn": (
        # 2 blocks, 1,775 characters. search strategies and screening (1); other paperwork (1).
        ("study eligibility the titles and abstracts of the identified citations were reviewed for relevance", ""),
        ("study identification we systematically searched medline (accessed via pubmed) and the cochrane", ""),
    ),
    "jWN6oE": (
        # 3 blocks, 1,658 characters. other paperwork (3).
        ("ideally, implementation of the recommendations should be monitored at the health-service level.", ""),
        ("monitoring and evaluating the guideline implementation", ""),
        ("the first indicator provides an overall assessment of the use of induction of labour", ""),
    ),
    "jXX6xj": (
        # 30 blocks, 8,016 characters. other paperwork (28); authors, panels and affiliations (1);
        # pointers to a report, file or figure (1).
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
        # 4 blocks, 329 characters. other paperwork (4).
        ("includes information, resources (e.g. publications and helplines), a pain measurement scale", ""),
        ("safe storage and disposal of pain medication", ""),
        ("source: abernethy et al for the australia-modified karnofsky performance status (akps) scale", ""),
        ("source: eastern cooperative oncology group 1982", ""),
    ),
    "jXXBBj": (
        # 3 blocks, 716 characters. other paperwork (3).
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
        # 10 blocks, 836 characters. pointers to a report, file or figure (8); search strategies
        # and
        # screening (2).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        (
            (
                "international human rights law includes fundamental commitments of states to enable women "
                "and adolescent"
            ),
            "",
        ),
        # 7 blocks, 3,462 characters. other paperwork (3); dissemination and updating (1);
        # pointers to a
        # report, file or figure (1); references and further reading (1); funding, publication
        # and version
        # notes (1).
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
        # 19 blocks, 8,106 characters. authors, panels and affiliations (8); references and
        # further
        # reading (7); other paperwork (2); pointers to a report, file or figure (1); funding,
        # publication
        # and version notes (1).
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
        # 3 blocks, 1,843 characters. search strategies and screening (1); pointers to a report,
        # file or
        # figure (1); other paperwork (1).
        ("our guideline also has limitations. first, the grade approach only allows for the strength", ""),
        ("the strengths of this guideline include its systematic approach to searching the literature", ""),
        ("the working group identified five areas for which pico questions were formulated", ""),
    ),
    "jlAbxL": (
        # 20 blocks, 1,275 characters. authors, panels and affiliations (13); other paperwork (2);
        # pointers to a report, file or figure (2); references and further reading (2); funding,
        # publication and version notes (1).
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
        # 6 blocks, 2,003 characters. pointers to a report, file or figure (2); other paperwork
        # (2);
        # references and further reading (2).
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
        # 28 blocks, 4,656 characters. other paperwork (15); references and further reading (6);
        # pointers
        # to a report, file or figure (4); funding, publication and version notes (2);
        # dissemination and
        # updating (1).
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
        # 9 blocks, 5,502 characters. other paperwork (4); authors, panels and affiliations (3);
        # references and further reading (2).
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
        # 3 blocks, 544 characters. funding, publication and version notes (1); authors, panels
        # and
        # affiliations (1); pointers to a report, file or figure (1).
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
        # 11 blocks, 3,095 characters. pointers to a report, file or figure (6); other paperwork
        # (2);
        # authors, panels and affiliations (2); search strategies and screening (1).
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
        # 13 blocks, 2,591 characters. references and further reading (5); pointers to a report,
        # file or
        # figure (4); other paperwork (4).
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
        # 10 blocks, 4,077 characters. other paperwork (6); authors, panels and affiliations
        # (2); pointers
        # to a report, file or figure (1); search strategies and screening (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("| | | --- | | priority outcomes | | maternal outcomes - pre-eclampsia", ""),
        # 7 blocks, 3,050 characters. other paperwork (4); authors, panels and affiliations (2);
        # references and further reading (1).
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
        # 6 blocks, 2,135 characters. other paperwork (3); funding, publication and version
        # notes (2);
        # pointers to a report, file or figure (1).
        ("abbreviation key cpg: clinical practice guidelines. fda: us food and drug administration.", ""),
        ("add a summary of evidence here and link ref", ""),
        ("external review and updating process once the panel produced the first draft", ""),
        ("in developing this guideline, the american dental association science and research institute,", ""),
        ("see the practical info tab for recommendations footnotes", ""),
        ("target audience these recommendations are intended primarily for general dentists.", ""),
    ),
    "mL6yYj": (
        # 1 blocks, 142 characters. pointers to a report, file or figure (1).
        ("for more detailed information see practical issues below the evidence profile in magicapp", ""),
    ),
    "n303gE": (
        # 8 blocks, 1,029 characters. other paperwork (5); pointers to a report, file or figure
        # (2);
        # funding, publication and version notes (1).
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
        # 21 blocks, 2,477 characters. authors, panels and affiliations (15); references and
        # further
        # reading (6).
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
        # 4 blocks, 423 characters. pointers to a report, file or figure (3); other paperwork (1).
        ("figure 8.1 vials of rh d immunoglobulin issued since 2003–04", ""),
        ("note: issues of rhophylac are too small to appear on the graph.", ""),
        ("printable guideline summary for health professionals", ""),
        ("the demand for products over recent years does not correlate with the change", ""),
    ),
    "n3QxOj": (
        # 15 blocks, 2,736 characters. other paperwork (7); pointers to a report, file or figure
        # (4);
        # search strategies and screening (3); authors, panels and affiliations (1).
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
        # 8 blocks, 3,563 characters. search strategies and screening (4); pointers to a report,
        # file or
        # figure (2); other paperwork (2).
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
        # 5 blocks, 975 characters. pointers to a report, file or figure (2); other paperwork (2);
        # authors, panels and affiliations (1).
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
        # 3 blocks, 1,302 characters. funding, publication and version notes (1); other
        # paperwork (1);
        # pointers to a report, file or figure (1).
        ("similarly, estimates on minimally important differences (mids) for pain, function and quality of life", ""),
        ("the evidence summary displayed as a grade summary of findings table represents the primary comparison", ""),
        ("this is a bmj rapid recommendation produced by magic", ""),
    ),
    "nBRK8n": (
        # 7 blocks, 3,456 characters. authors, panels and affiliations (4); other paperwork (3).
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
        # 5 blocks, 1,418 characters. other paperwork (2); pointers to a report, file or figure
        # (1);
        # funding, publication and version notes (1); authors, panels and affiliations (1).
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
        # 4 blocks, 2,514 characters. authors, panels and affiliations (2); other paperwork (1);
        # search
        # strategies and screening (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("justifications and remarks are developed by the gdg with support from the methodologists. these", ""),
        ("research gaps were identified during the guideline meeting when the evidence based on the", ""),
        ("table 2. outline of research gaps that arose during the guideline meeting", ""),
        # 11 blocks, 3,317 characters. other paperwork (8); pointers to a report, file or figure
        # (2);
        # funding, publication and version notes (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("use of the guideline by eaes members will be monitored through an online survey", ""),
        # 7 blocks, 2,169 characters. authors, panels and affiliations (3); funding, publication
        # and
        # version notes (2); other paperwork (1); pointers to a report, file or figure (1).
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
        # 1 blocks, 530 characters. pointers to a report, file or figure (1).
        (
            (
                "cari guidelines previously published a clinical practice guideline on the pharmacological "
                "management of adpkd."
            ),
            "",
        ),
    ),
    "nJeNmL": (
        # 12 blocks, 16,326 characters. other paperwork (4); pointers to a report, file or
        # figure (4);
        # dissemination and updating (2); funding, publication and version notes (1); references
        # and
        # further reading (1).
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
        # 5 blocks, 3,443 characters. other paperwork (4); authors, panels and affiliations (1).
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
        # 4 blocks, 2,989 characters. authors, panels and affiliations (4).
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
        # 21 blocks, 1,714 characters. authors, panels and affiliations (17); pointers to a
        # report, file
        # or figure (3); other paperwork (1).
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
        # 12 blocks, 23,531 characters. other paperwork (6); references and further reading (2);
        # pointers
        # to a report, file or figure (2); authors, panels and affiliations (1); search
        # strategies and
        # screening (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("each ebr was assigned a grade by the expert working group, taking into account", ""),
        # 11 blocks, 3,658 characters. other paperwork (5); pointers to a report, file or figure
        # (2);
        # authors, panels and affiliations (1); dissemination and updating (1); funding,
        # publication and
        # version notes (1); search strategies and screening (1).
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
        # 11 blocks, 13,901 characters. references and further reading (8); other paperwork (2);
        # dissemination and updating (1).
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
        # 2 blocks, 762 characters. authors, panels and affiliations (1); other paperwork (1).
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
        # 14 blocks, 7,001 characters. other paperwork (4); pointers to a report, file or figure
        # (2);
        # funding, publication and version notes (2); authors, panels and affiliations (2);
        # dissemination
        # and updating (2); references and further reading (2).
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
        # The patient version's contact block and its numbered label, removed 2026-08-10 on
        # Evan's call that the corpus must not ship personal contact details: it prints the
        # subcommittee chair's personal Hotmail address. The label is a bold line rather than
        # a heading, so the empty-heading pass cannot take it and it is named here.
        ("1. contact information", ""),
        ("for any questions or additional information, please contact:", ""),
        # Three of the Introduction's four blocks, removed 2026-08-10 on Evan's call: a
        # citation of the prior US practice guideline, the subcommittee's decision to
        # address the gap, and the aims-and-audience paragraph - provenance and scope
        # metadata, nothing a claim could rest on. The fourth block stays deliberately:
        # it carries the section's one set of checkable numbers, "an estimated 23%
        # prevalence of colonic diverticula and a mortality of 3% in patients admitted".
        ("practice guidelines on the surgical management of acute diverticulitis", ""),
        ("the guidelines subcommittee of the european association for endoscopic surgery", ""),
        ("this rapid guideline aims to provide recommendations on the surgical management", ""),
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("the development of this document complied with the reporting checklist for public versions of", ""),
        # 8 blocks, 8,434 characters. funding, publication and version notes (3); other
        # paperwork (3);
        # authors, panels and affiliations (1); search strategies and screening (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        ("high-quality health care is essential for the prevention of morbidity and mortality in pregnancy", ""),
        ("the world health organization (who) envisions a world where “every pregnant woman and newborn", ""),
        ("this section presents the two updated recommendations on the timing of induction of labour", ""),
        ("• apgar score less than 7 at 5 minutes • admission to a neonatal", ""),
        # 14 blocks, 6,632 characters. other paperwork (8); dissemination and updating (2);
        # funding,
        # publication and version notes (2); references and further reading (1); search
        # strategies and
        # screening (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
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
        # 6 blocks, 5,645 characters. other paperwork (3); funding, publication and version
        # notes (2);
        # pointers to a report, file or figure (1).
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
        # 7 blocks, 2,937 characters. other paperwork (4); authors, panels and affiliations (2);
        # pointers
        # to a report, file or figure (1).
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
        # 2 blocks, 304 characters. other paperwork (1); pointers to a report, file or figure (1).
        ("6th edition, january 2025", "malaria continues to be the leading cause of morbidity and mortality"),
        ("for detailed operational guidance, refer to the malaria case management", ""),
    ),
    "nyX5xL": (
        # 17 blocks, 3,567 characters. pointers to a report, file or figure (8); references and
        # further
        # reading (6); other paperwork (3).
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
        # 6 blocks, 17,821 characters. search strategies and screening (2); other paperwork (2);
        # references and further reading (1); funding, publication and version notes (1).
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
        # Boilerplate a rule already removes from another guideline by the same publisher,
        # found by comparing what each rule takes against what its twins still keep.
        (
            (
                "(v) planning for the dissemination, implementation, impact evaluation and updating of the "
                "recommendations."
            ),
            "",
        ),
        # 4 blocks, 2,156 characters. other paperwork (3); dissemination and updating (1).
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
        # 3 blocks, 1,407 characters. other paperwork (1); references and further reading (1);
        # dissemination and updating (1).
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
        # 4 blocks, 2,465 characters. pointers to a report, file or figure (2); authors, panels
        # and
        # affiliations (1); other paperwork (1).
        ("although cognitive issues have not featured as prominantly in stroke guidelines as may be expected", ""),
        ("in planning the work, we were keen that we represent all the clinical disciplines involved", ""),
        (
            "in this context the european stroke organisation (eso) commissioned a guideline, in agreement with the",
            "",
        ),
        ("the guideline followed best practice and adhered to the standard operating procedure of the eso", ""),
    ),
    "nyxpZL": (
        # 2 blocks, 636 characters. other paperwork (2).
        ("publication approval and public consultation", ""),
        ("the consortium seeks annual nhmrc approval of the living guideline under section 14a of the", ""),
    ),
    "ojmKvn": (
        # The disclaimer closing Methodology, hanging off the timing block with no divider
        # of its own so no heading rule reaches it.
        (
            "for all clinical guideline recommendations we make the assumption that "
            "healthcare professionals will be appropriately qualified",
            "",
        ),
        # 1 blocks, 185 characters. other paperwork (1).
        (
            (
                "there is an organisational indicator collected in the national stroke audit on whether "
                "patient selection"
            ),
            "",
        ),
    ),
    "pEQmQE": (
        # 3 blocks, 1,436 characters. authors, panels and affiliations (1); references and
        # further reading
        # (1); search strategies and screening (1).
        ("potential biases in the review process in 2016, the nfog board established a guideline committee", ""),
        ("reference: the swedish medical birth register - a summary of content and quality.", ""),
        ("using the following search words: birth birthweight clinical controlled gestagen gestonorone", ""),
    ),
}

# Merged in rather than replacing: the handful above were written by hand against a
# specific report and carry reasoning the generated list does not.
for _code, _rules in _GUIDELINE_PAPERWORK_DROP_BLOCKS.items():
    _GUIDELINE_DROP_BLOCKS[_code] = _GUIDELINE_DROP_BLOCKS.get(_code, ()) + _rules


def _block_opening(block: str) -> str:
    """Reduce a block to the form `_GUIDELINE_DROP_BLOCKS` is written in."""
    return _EMPHASIS_RE.sub("", " ".join(block.split())).strip().lstrip("#").strip().lower()


def _drop_guideline_blocks(markdown: str, short_code: str) -> str:
    """Remove blocks named for one guideline, from the assembled document.

    Args:
        markdown: The whole rendered guideline.
        short_code: The guideline's own code.
    """
    rules = _GUIDELINE_DROP_BLOCKS.get(short_code, ())
    if not rules:
        return markdown
    blocks = markdown.split("\n\n")
    openings = [_block_opening(block) for block in blocks]
    keep = [True] * len(blocks)
    for opening, stop in rules:
        for index, text in enumerate(openings):
            if not text.startswith(opening):
                continue
            if stop is None:
                # Explicitly to the end of the document. Only ever written out in the table,
                # never reached as a fallback - see the comment on the loop below.
                for position in range(index, len(blocks)):
                    keep[position] = False
                continue
            if not stop:
                keep[index] = False
                continue
            end = next((later for later in range(index + 1, len(blocks)) if openings[later].startswith(stop)), None)
            # Fail closed. A run that cannot find its end takes one block, not the rest of
            # the document.
            for position in range(index, end if end is not None else index + 1):
                keep[position] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# A heading left naming content that another rule has already taken. For a retriever this is
# worse than noise: the heading is a topical match that delivers nothing. 802 of them across
# 37 guidelines, 335 being a bare "### Practice point" in Cancer Council melanoma guidelines,
# where the practice points themselves are gone and only the labels remain.
#
# Structural, so it cannot cost content: a heading is removed only when there is nothing at all
# between it and the next heading of the same or a shallower level. Run to a fixpoint, because
# emptying the last child of a section empties the section.
#
# Markdown headings only, deliberately. Extending it to wholly bold lines reaches another 605
# dead labels and 17,600 characters, and it cannot tell them from a real subheading that happens
# to be the last thing in its section - it took out WHO's bolded PICO questions and an alcohol
# guideline's "Why not drinking is important for young people". A `#` heading with nothing under
# it is unambiguous; a bold line is a guess. The bold labels worth removing are named per
# guideline instead.
_MAX_EMPTY_HEADING_PASSES = 8


def _drop_empty_headings(markdown: str) -> str:
    """Remove a heading with no text and no subheading under it.

    Args:
        markdown: The whole rendered guideline.
    """
    for _ in range(_MAX_EMPTY_HEADING_PASSES):
        lines = markdown.split("\n")
        marks = []
        for position, line in enumerate(lines):
            match = _RENDERED_HEADING_RE.match(line.strip())
            if match:
                marks.append((position, len(match.group("hashes"))))
        doomed = set()
        for index, (line_no, level) in enumerate(marks):
            end = marks[index + 1][0] if index + 1 < len(marks) else len(lines)
            if "".join(lines[line_no + 1 : end]).strip():
                continue
            if index + 1 < len(marks) and marks[index + 1][1] > level:
                continue
            doomed.add(line_no)
        if not doomed:
            return markdown
        markdown = "\n".join(line for position, line in enumerate(lines) if position not in doomed)
    logger.debug("Empty-heading removal did not stabilize")
    return markdown


def _drop_repeated_blocks(markdown: str) -> str:
    """Remove a block whose every phrase already appears elsewhere in the same document.

    Deduplication rather than judgement. A block goes only when all of its eight-word runs
    are found in a block that is kept, so nothing can be lost: the words survive in the copy
    that stays. 5,948 blocks in the corpus repeat this way, 2,056,590 characters.

    Walks backwards, keeping the last copy. Forwards keeps the first, and the difference
    matters where it is wrong rather than in the total - a guideline that states a
    recommendation in a summary near the front and again in full under its own heading would
    lose the full statement and keep the summary.

    Three things are never removed: a heading, a block sitting under a Recommendation
    heading, and a block carrying an effect estimate, certainty rating, participant count or
    dose. The last is the exception Evan asked for and the reason the safe figure is
    1,405,337 characters rather than the full 2,056,590.

    Args:
        markdown: The whole assembled document, not one fragment - a repeat is only
            detectable against everything else in the same guideline.
    """
    blocks = markdown.split("\n\n")
    under_recommendation, flagged = False, []
    for block in blocks:
        stripped = block.strip()
        if _RENDERED_HEADING_LINE_RE.match(stripped):
            under_recommendation = bool(_RECOMMENDATION_HEADING_RE.match(stripped))
        flagged.append(under_recommendation)

    seen: set[str] = set()
    keep = [True] * len(blocks)
    for index in range(len(blocks) - 1, -1, -1):
        stripped = blocks[index].strip()
        if not stripped or _RENDERED_HEADING_LINE_RE.match(stripped) or flagged[index]:
            continue
        if _EVIDENCE_IN_BLOCK_RE.search(stripped):
            seen |= _block_shingles(stripped)
            continue
        shingles = _block_shingles(stripped)
        if not shingles:
            continue
        if shingles <= seen:
            keep[index] = False
        else:
            seen |= shingles
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


def build_magic_guideline_text(
    client: httpx.Client,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> tuple[str, int, str, int]:
    """Fetch a guideline's structured JSON and render it as markdown.

    Returns the markdown, the number of top-level sections that produced content,
    the title, and the number of recommendations that reached the markdown. The
    last of those is one more value than `nice.py`'s equivalent returns; it exists
    so `metadata.recommendations_in_content` can be set - see that key for why it
    is worth carrying.

    Args:
        client: HTTP client used to fetch the guideline document.
        ref: Catalogue entry identifying the guideline to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    response = client.get(ref.json_path)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise MagicFetchError(f"Could not parse guideline JSON for '{ref.short_code}'") from error
    if not isinstance(payload, dict):
        raise MagicFetchError(f"Could not parse guideline JSON for '{ref.short_code}': expected an object")

    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise MagicFetchError(f"No sections found for guideline '{ref.short_code}'")

    moved_picos = _moved_pico_ids(payload, link_mode=link_mode)
    blocks: list[str] = []
    section_count = 0
    emitted = 0
    # A top-level section whose heading is just the guideline's own title is a wrapper the
    # publisher uses to hold the version banner - "Version 10.1, published 3 July 2026" -
    # and it prints the title twice with nothing new under it. 21 across the corpus.
    document_title = _skip_lookup_key(_plain_heading(str(payload.get("name") or ""), link_mode=link_mode))
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading_key = _skip_lookup_key(_plain_heading(str(section.get("heading") or ""), link_mode=link_mode))
        # Never when it is the only one: EKevdL's whole document is a single section named
        # after the guideline, and skipping it left nothing to emit at all.
        if document_title and len(sections) > 1 and heading_key == document_title and not _has_recommendation(section):
            logger.debug("Skipping a section that repeats the guideline title")
            continue
        rendered, section_emitted = _section_markdown(
            section,
            depth=1,
            link_mode=link_mode,
            moved_picos=moved_picos,
            institution=ref.institution,
            short_code=ref.short_code,
        )
        emitted += section_emitted
        if rendered:
            blocks.extend(rendered)
            section_count += 1

    # Recommendations usually hang off a section, but not always: the Scandinavian
    # Society of Anaesthesiology publishes a guideline whose nine recommendations sit
    # on the document root with none in any section, and the catalogue's count of 9
    # confirms they are the real content. Walking only sections drops them silently.
    for recommendation in payload.get("recommendations") or []:
        if not isinstance(recommendation, dict):
            continue
        # Level 2, the same as a top-level section heading: these sit beside the
        # sections rather than inside one.
        rendered_recommendation = _recommendation_markdown(
            recommendation, level=2, link_mode=link_mode, moved_picos=moved_picos
        )
        if rendered_recommendation:
            blocks.append(rendered_recommendation)
            emitted += _is_labelled_recommendation(rendered_recommendation)

    # Empty headings go BEFORE deduplication, not after. A heading whose body was removed as a
    # duplicate still marks where that topic sat, and the words survive in the copy that was
    # kept; a heading that was already empty marks nothing.
    content = _drop_repeated_blocks(
        _drop_empty_headings(_drop_guideline_blocks("\n\n".join(blocks), ref.short_code))
    ).strip()
    if not content:
        raise MagicFetchError(f"No readable content for guideline '{ref.short_code}'")
    title = str(payload.get("name") or ref.title).strip() or ref.short_code
    return content, section_count, title, emitted


def scrape_magic_guideline(
    client: httpx.Client,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape one MAGICapp guideline into a normalized document.

    Args:
        client: HTTP client used to fetch the guideline document.
        ref: Catalogue entry identifying the guideline to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    content, section_count, title, recommendations_in_content = build_magic_guideline_text(
        client, ref, link_mode=link_mode
    )
    return ScrapedDocument(
        source="magic",
        external_id=f"magic-{ref.short_code}",
        title=title,
        url=ref.page_url,
        content=content,
        section_count=section_count,
        metadata={
            "short_code": ref.short_code,
            "guideline_id": ref.guideline_id,
            # The catalogue's guidelineId and publishedId differ on every one of its
            # 461 entries, and the document JSON's own guidelineId equals the
            # catalogue's publishedId. The version-pinned viewer URL
            # (/#/guideline/<publishedId>/section/<id>) needs this value;
            # `guideline_id` cannot build it.
            "published_id": ref.published_id,
            "institution": ref.institution,
            "language": ref.language,
            "publish_date": ref.publish_date,
            "recommendation_count": ref.recommendation_count,
            # How many recommendations reached `content`. Deliberately named for its
            # scope rather than as a total, because it counts something different
            # from `recommendation_count` above and the two are *not* expected to be
            # equal: that one is the publisher's tally of what the guideline holds,
            # this one is what survived rendering into the markdown.
            #
            # They differ for ordinary reasons. This scraper drops INFO callout boxes,
            # recommendations the publisher marked POSSIBLY_OUTDATED, and the odd
            # editorial item filed under a heading that gets skipped; publishers in
            # turn count recommendation records whose text is empty, and their tally
            # can lag their own document. Measured over all 228 English guidelines on
            # 2026-07-28: 187 equal, 15 where this number is larger, 19 with no
            # publisher count to compare, and 7 where it is smaller - every one of
            # those 7 checked by hand and none a defect.
            #
            # The value is in watching it *change*. Every field here is read with
            # `.get`, so if MAGICapp renames or moves one, the scraper emits a shorter
            # document and raises nothing. Carrying both numbers per document means
            # that silent loss is visible to anyone who looks, long after this run -
            # which is how `recommendations` living on the document root was found in
            # the first place, by noticing a catalogue count of 9 against a walk that
            # returned 0.
            "recommendations_in_content": recommendations_in_content,
            # The publisher's own statement of how the guideline may be used, present
            # on 165 of 461 catalogue entries but only 59 of the 242 English ones, as
            # an HTML fragment. Kept out of
            # `content` so it never reads as clinical text, but carried so the terms
            # travel with the document.
            "disclaimer": _markdown(ref.disclaimer, link_mode=link_mode),
            # Not every published guideline is final or current: the catalogue carries
            # DEV, INTERNAL_DRAFT, PUBLIC_REVIEW and EXTERNAL_REVIEW alongside settled
            # guidance, so the value is recorded. It is a weak signal - 389 of 461
            # entries are NOTSET or blank, including five archived National Blood
            # Authority modules whose own text warns it "may not reflect the currently
            # available evidence" - so the absence of a status proves nothing.
            "status": ref.status,
            # The date the panel last searched for evidence, and the only field in
            # the catalogue that actually tracks currency: populated on 241 of 242
            # English entries, against `status` which is NOTSET or absent on 87% of
            # them and `isLatestPublished` which is False on all 460. `publish_date`
            # is not a substitute - on the archived National Blood Authority modules
            # it reads 2026-07-20 while this field reads 2013-06-01, because
            # archiving is itself a publishing action. Recorded rather than filtered
            # on, so a later step can set its own age policy.
            "last_search_date": ref.last_search_date,
        },
    )


def _scrape_guideline_or_empty(
    client: httpx.Client,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode,
    include_drafts: bool = False,
) -> ScrapedDocument:
    """Scrape one catalogue guideline, returning empty content instead of raising.

    The catalogue lists guidelines whose bodies are placeholders, so rendering them
    produces nothing usable: 2 of 63 sampled guidelines render to zero characters,
    and others to a single line such as "Evidence profiles for beta-blockers for
    hypertension, not yet publicly available". `scrape_listing_documents` yields
    without catching, so letting that raise aborts the whole catalogue run rather
    than losing one document.

    Returns a document with empty content for anything unreadable or shorter than
    MIN_CONTENT_CHARS; callers drop those.

    Args:
        client: HTTP client used to fetch the guideline document.
        ref: Catalogue entry identifying the guideline to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
        include_drafts: Keep guidelines whose own title marks them drafts rather
            than dropping them (default: False).
    """

    def empty(reason: str) -> ScrapedDocument:
        logger.warning("Skipping MAGICapp guideline '%s' (%s): %s", ref.short_code, ref.title, reason)
        return ScrapedDocument(
            source="magic",
            external_id=f"magic-{ref.short_code}",
            title=ref.title,
            url=ref.page_url,
            content="",
            section_count=0,
        )

    if ref.is_archived:
        return empty("the publisher has archived this guideline")
    if ref.is_draft and not include_drafts:
        return empty("the publisher's own title marks it a draft, not final guidance")

    try:
        document = scrape_magic_guideline(client, ref, link_mode=link_mode)
    except (MagicFetchError, httpx.HTTPError) as error:
        return empty(str(error))
    if len(document.content) < MIN_CONTENT_CHARS:
        return empty(f"{len(document.content)} characters, below the {MIN_CONTENT_CHARS} minimum")
    if _DEMONSTRATION_RE.search(document.content):
        return empty("the guideline's own text says it is a demonstration, not guidance")
    return document


def scrape_magic(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
    languages: tuple[str, ...] | None = DEFAULT_LANGUAGES,
    include_drafts: bool = False,
) -> ScrapeRun:
    """Scrape MAGICapp documents from a URL or the published catalogue.

    Guidelines that cannot be read are logged and skipped, so `documents` is an
    upper bound on a catalogue run rather than an exact count.

    Args:
        documents: Number of documents to scrape. Ignored when `url` is set.
            When unset, the whole English catalogue is scraped (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: MAGICapp guideline URL to scrape as a single document (default: None).
        languages: Language prefixes to collect. MAGICapp is multilingual and no
            AMFV document states a language policy, so English is a default
            rather than a rule; pass None for the whole catalogue
            (default: DEFAULT_LANGUAGES).
        include_drafts: Keep guidelines whose own title marks them drafts, such
            as "DRAFT FOR PUBLIC CONSULTATION". Ignored when `url` is set - an
            explicit URL is an explicit request (default: False).
    """
    if url is not None:
        # An explicit URL is an explicit request, so an unreadable guideline is an
        # error to report rather than a document to skip silently.
        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_magic_guideline(client, magic_ref_from_url(client, url), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    with default_client() as client:
        first_page = list_published_guidelines(client, page=1, languages=languages)
    total = first_page.total if documents is None or first_page.total is None else min(documents, first_page.total)
    listed = scrape_listing_documents(
        documents=documents,
        client_factory=default_client,
        first_page_items=first_page.refs,
        list_page=lambda client, page: list_published_guidelines(client, page, languages=languages).refs,
        scrape_item=lambda client, ref: _scrape_guideline_or_empty(
            client, ref, link_mode=link_mode, include_drafts=include_drafts
        ),
        document_delay_seconds=DOCUMENT_DELAY_SECONDS,
    )
    return ScrapeRun(
        total=total,
        documents=(document for document in listed if document.content),
    )


__all__ = [
    "API_BASE_URL",
    "BASE_URL",
    "DEFAULT_LANGUAGES",
    "DOCUMENT_DELAY_SECONDS",
    "MAGIC_DATASET_DISPLAY_NAME",
    "MAGIC_DATASET_NAME",
    "GuidelineListingPage",
    "GuidelineRef",
    "MagicFetchError",
    "build_magic_guideline_text",
    "list_published_guidelines",
    "magic_ref_from_url",
    "scrape_magic",
    "scrape_magic_guideline",
]
