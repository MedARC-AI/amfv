"""VeriScore baseline: sliding-window claim extraction with verifiability filter.

Replicates the original prompting mode ("VeriScoreQA" in the MedScore paper,
which used this released prompting method for its VeriScore baseline): system
message and few-shot extraction templates are verbatim from
github.com/Yixiao-Song/VeriScore (claim_extractor.py and
prompt/extraction_qa_template.txt / extraction_non_qa_template.txt).
The original drives GPT-4 with these prompts; we serve them to the model
under evaluation instead.
"""

from __future__ import annotations

from ..base import BaseDecomposer, parse_claims

# Verbatim from VeriScore claim_extractor.py (ClaimExtractor.system_message),
# including the missing final period.
_SYSTEM = (
    "You are a helpful assistant who can extract verifiable atomic claims from "
    "a piece of text. Each atomic fact should be verifiable against reliable "
    "external world knowledge (e.g., via Wikipedia)"
)

# Verbatim from prompt/extraction_qa_template.txt.
_QA_TEMPLATE = r"""You are trying to verify how factual a response to a question or request is. To do so, you need to break down a sentence and extract as many fine-grained facts mentioned in the response. Each of these fine-grained facts should be verifiable against reliable external world knowledge (e.g., via Wikipedia). Any story, personal experiences, hypotheticals (e.g., "would be" or subjunctive), subjective statements (e.g., opinions), suggestions, advice, instructions, and other such content should not be included in the list. Biographical, historical, scientific, and other such texts are not personal experiences or  stories. You should extract verifiable facts from them. Each fact should also be describing either one single event (e.g., "Nvidia is founded in 1993 in Sunnyvale, California, U.S.") or single state (e.g., "UMass Amherst has existed for 161 years.") with necessary time and location information. Quotations should be extracted verbatim with the source when available. Listed references should be ignored.

Extract fine-grained facts from the sentence between <SOS> and <EOS>. You should focus on the named entities and numbers in the sentence and extract relevant information from the sentence. Do not extract claims from the question. The question and other sentences are only context for you to recover pronouns, definite phrases (e.g., "the victims" or "the pope"), and so on. Each fact should be understandable on its own and require no additional context. This means that you need to always related the extracted claims to the question. This also means that all entities must be referred to by name but not pronoun. Use the name of entities rather than definite noun phrases (e.g., 'the teacher') whenever possible. If a definite noun phrase is used, be sure to add modifiers (e.g., a embedded clause, a prepositional phrase, etc.). Each fact must be situated within relevant temporal and location whenever needed. Keep each fact to one sentence with zero or at most one embedded clause. You do not need to justify what you extract.

If there is no verifiable fact in the sentence, please write "No verifiable claim."

Here are some examples:

Question: What NASA programs would support our college in starting a robotics program?
Response: NASA has several programs that can support colleges in starting a robotics program. Here are a few:
<SOS>1. NASA Robotics Alliance Project (RAP): This program provides educational resources and support for robotics teams, including college-level teams, that are participating in NASA robotics competitions.<EOS>
2. NASA Minority University Research and Education Project (MUREP): This program provides funding and resources for colleges and universities with a significant minority student population to develop research and education programs in STEM fields, including robotics.
3. NASA's Robotics Education Project: This project provides robotics education materials and resources for educators, including college-level educators, to use in their classrooms.
4. NASA's Space Technology Mission Directorate (STMD): This directorate funds research and development in advanced technologies, including robotics, that can support NASA's mission to explore space.
Sentence to be focused on: 1. NASA Robotics Alliance Project (RAP): This program provides educational resources and support for robotics teams, including college-level teams, that are participating in NASA robotics competitions.
Facts:
- NASA has a program called NASA Robotics Alliance Project (RAP).
- NASA Robotics Alliance Project provides educational resources for robotics teams.
- NASA Robotics Alliance Project provides supports for robotics teams.
- NASA Robotics Alliance Project provides supports for college-level teams that are participating in NASA robotics competitions.

Question: What NASA programs would support our college in starting a robotics program?
Response: 2. NASA Minority University Research and Education Project (MUREP): This program provides funding and resources for colleges and universities with a significant minority student population to develop research and education programs in STEM fields, including robotics.
3. NASA's Robotics Education Project: This project provides robotics education materials and resources for educators, including college-level educators, to use in their classrooms.
4. NASA's Space Technology Mission Directorate (STMD): This directorate funds research and development in advanced technologies, including robotics, that can support NASA's mission to explore space.
<SOS>5. NASA's Human Exploration and Operations Mission Directorate (HEOMD): This directorate funds research and development in technologies that can support human space exploration, including robotics.<EOS>
You can visit NASA's website to learn more about each of these programs and how to apply for funding and support.
Sentence to be focused on: 5. NASA's Human Exploration and Operations Mission Directorate (HEOMD): This directorate funds research and development in technologies that can support human space exploration, including robotics.
Facts:
- NASA has a directorate called NASA's Human Exploration and Operations Mission Directorate (HEOMD).
- NASA's Human Exploration and Operations Mission Directorate funds research in technologies that can support human space exploration.
- NASA's Human Exploration and Operations Mission Directorate funds development in technologies that can support robotics.
- Robotics is a technology that can support human space exploration.

Question: How can I find a law firm that specializing in copyright related matters?
Response: There are a few ways to find a law firm that specializes in copyright-related matters:
1. Ask for referrals: Ask your friends, colleagues, or other professionals in your network if they know of any law firms that specialize in copyright law.
<SOS>2. Online search: You can search online using keywords like \"copyright law firm\" or \"copyright lawyer\" along with your location. This should bring up a list of law firms in your area that specialize in copyright law.<EOS>
3. Bar association referral service: Many bar associations have a referral service that can connect you with a lawyer or law firm that specializes in copyright law.
Sentence to be focused on: 2. Online search: You can search online using keywords like "copyright law firm" or "copyright lawyer" along with your location. This should bring up a list of law firms in your area that specialize in copyright law.
Facts:
No verifiable claim.

Question: How do trees know when to stop growing?
Thanks everyone i learned a lot more about trees.(:
Response: <SOS>Ah yes, tomatoes, this is a big problem with tomato plants.<EOS>
Sentence to be focused on: Ah yes, tomatoes, this is a big problem with tomato plants.
Facts:
No verifiable claim.

Question: with billions of stars emitting photons why is the night sky not bright?
Response: Exactly. The usual example is an emergency vehicle with its siren on. <SOS>As it approaches you, the pitch is higher, as it passes you and recedes the pitch drops - the sound is compressed on the approach and stretched as it recedes.<EOS>
Sentence to be focused on: As it approaches you, the pitch is higher, as it passes you and recedes the pitch drops - the sound is compressed on the approach and stretched as it recedes.
Facts:
No verifiable claim.

Question: I want Ubutu but i have a question first I'm not new to Ubuntu my friends use it i never had a chance to use it on my own PC i'm running on a window 8 and it has no info like product keys and such it was on the box and this is a hand me down PC my question is do i need those for the installation if so how do i retrieve this info
Response: <SOS>You do not need that information to boot or install ubuntu.<EOS> You might need to make a windows recovery disk.
Sentence to be focused on: You do not need that information to boot or install ubuntu.
Facts:
- One does not need product keys to install Ubuntu.

Question: I want Ubutu but i have a question first I'm not new to Ubuntu my friends use it i never had a chance to use it on my own PC i'm running on a window 8 and it has no info like product keys and such it was on the box and this is a hand me down PC my question is do i need those for the installation if so how do i retrieve this info
Response: You do not need that information to boot or install ubuntu. You might need to make a windows recovery disk. I would make one if you do not have one.
<SOS>You need a windows recovery disk in the event you have a problem with windows.<EOS>
Sentence to be focused on: You need a windows recovery disk in the event you have a problem with windows.
Facts:
- One needs a windows recovery disk if one has a problem with windows when installing Ubuntu.

Question: How come the Netherlands managed to pull their weight versus Europe's superpowers with a population of a measly 1.5 million?
The trading empire of the Dutch Republic would be impressive alone for it's achievements in trade and warfare, but it's astounding when you realize that by 1600, the Netherlands had 1.5m for population! Compare that to 18m of France, or 9m approx of Spain, or 5.6m by England.
I get that they were very sophisticated and had advanced commerce, production and politics. But still... 1.5 million? How is that enough manpower to arm enough soldiers on land to prevent invasions, and sailors to operate a huge fleet? And given the immense wealth and manpower of Spain and Portugal compared to that of the Dutch - what prevented them from just dropping in on Amsterdam and burn it so to keep them out of their business? Or maybe drop in on Calais and march on land if they didn't have enough naval power.
Response: I'm writing a paper on the Dutch army around that time at this very moment.
I'll try to answer a couple of your questions. <SOS>One thing to know is that the military strength of the Netherlands varied greatly in short periods of time.<EOS> I'll mostly be focussing on the period around 1600, because that's the year you mentioned in your question.
First thing to know is that the 'manpower' of the Netherlands itself doesn't really translate well into actual figures for the Dutch army.
Sentence to be focused on: One thing to know is that the military strength of the Netherlands varied greatly in short periods of time.
Facts:
- The military strength of the Netherlands varied greatly in short periods of time around 1600.

Question: How does a phone vibrate?
Response: Place magnet 1 on top and magnet 2 underneath the cardboard so they can influence each other magnetically. Now grab the bottom magnet and rotate and or move it randomly. You will notice the cardboard vibrate. <SOS>Inside a cell phone one of the magnets is attached to the phone body and the other is an energized coil, similar to a common speaker.<EOS>
NOTE: speakers vibrate so fast you can hear them, so a speaker is just a fancy vibrator
Sentence to be focused on: Inside a cell phone one of the magnets is attached to the phone body and the other is an energized coil, similar to a common speaker.
Facts:
- For a phone to vibrate, there is one magnet in a cell phone that is attached to the phone body.
- For a phone to vibrate, there is one magnet in a cell phone that is an energized coil.

Question: How did diarrhea kill so many people before and now it’s not that big of a deal?
Title really, is it just that our diet is better and decent food is much more abundant or is it just general hygiene?
Response: A lot of illnesses with high mortality rates that diarrhea is a symptom of are better treated today and are less common.
For example, cholera outbreaks occurred frequently in 19th century Europe due to poor sanitation standards. This disease caused severe diarrhea and can have a mortality rate of up to 50-60% if left untreated. Today, cholera is very rare since the public water supply is much more cleaner and less prone to contamination by disease. If someone does contract cholera, there are much more advanced medical treatment available that make the mortality rate &lt;1% if you seek treatment.
<SOS>Furthermore, complications caused by diarrhea such as dehydration and loss of nutrients can be more easily treated today as well.<EOS>
Sentence to be focused on: Furthermore, complications caused by diarrhea such as dehydration and loss of nutrients can be more easily treated today as well.
Facts:
- Diarrhea causes complications.
- Diarrhea cuases dehydration.
- Diarrhea causes loss of nutrients.
- Dehydration can be more easily treated today.
- Loss of nutrients can be more easily treated today.

Extract *verifiable atomic* fact.

{snippet}
Sentence to be focused on: {sentence}
Facts:"""

# Verbatim from prompt/extraction_non_qa_template.txt.
_NON_QA_TEMPLATE = r"""You are trying to verify how factual a piece of text is. To do so, you need to break down a sentence and extract as many fine-grained facts mentioned in the sentence as possible. Each of these fine-grained facts should be verifiable against reliable external world knowledge (e.g., via Wikipedia). Any story, personal experiences, hypotheticals (e.g., "would be" or subjunctive), subjective statements (e.g., opinions), suggestions, advice, instructions, and other such content should not be included in the list. Biographical, historical, scientific, and other such texts are not personal experiences or stories. You should extract verifiable facts from them. Each fact should also be describing either one single event (e.g., "Nvidia is founded in 1993 in Sunnyvale, California, U.S.") or single state (e.g., "UMass Amherst has existed for 161 years.") with necessary time and location information. Quotations should be extracted verbatim with the source when available. Listed references should be ignored.

Extract fine-grained facts from the sentence marked between <SOS> and <EOS>. You should focus on the named entities and numbers in the sentence and extract relevant information from the sentence. Other sentences are only context for you to recover pronouns, definite phrases (e.g., "the victims" or "the pope"), and so on. Each fact should be understandable on its own and require no additional context. This means that all entities must be referred to by name but not pronoun. Use the name of entities rather than definite noun phrases (e.g., 'the teacher') whenever possible. If a definite noun phrase is used, be sure to add modifiers (e.g., a embedded clause, a prepositional phrase, etc.). Each fact must be situated within relevant temporal and location whenever needed. Keep each fact to one sentence with zero or at most one embedded clause. You do not need to justify what you extract.

If there is no verifiable fact in the sentence, please write "No verifiable claim."

Here are some examples:

Text: The sweet potato or sweetpotato (Ipomoea batatas) is a dicotyledonous plant that belongs to the bindweed or morning glory family, Convolvulaceae. <SOS>Its large, starchy, sweet-tasting tuberous roots are used as a root vegetable.<EOS> The young shoots and leaves are sometimes eaten as greens.
Sentence to be focused on: Its large, starchy, sweet-tasting tuberous roots are used as a root vegetable.
Facts:
- Sweet potatoes' roots are large.
- Sweet potatoes' roots are starchy.
- Sweet potatoes' roots are sweet-tasting.
- Sweet potatoes' roots are tuberous.
- Sweet potatoes' roots are used as a root vegetable.

Text: <SOS>After the success of the David in 1504, Michelangelo’s work consisted almost entirely of vast projects.<EOS> He was attracted to these ambitious tasks while at the same time rejecting the use of assistants, so that most of these projects were impractical and remained unfinished.
Sentence to be focused on: After the success of the David in 1504, Michelangelo’s work consisted almost entirely of vast projects.
Facts:
- Michelangelo achieved the success of the David in 1504.
- After 1504, Michelangelo’s work consisted almost entirely of vast projects.

Text: After the success of the David in 1504, Michelangelo’s work consisted almost entirely of vast projects. He was attracted to these ambitious tasks while at the same time rejecting the use of assistants, so that most of these projects were impractical and remained unfinished. <SOS>In 1504 he agreed to paint a huge fresco for the Sala del Gran Consiglio of the Florence city hall to form a pair with another just begun by Leonardo da Vinci.<EOS> Both murals recorded military victories by the city (Michelangelo’s was the Battle of Cascina), but each also gave testimony to the special skills of the city’s much vaunted artists.
Sentence to be focused on: In 1504 he agreed to paint a huge fresco for the Sala del Gran Consiglio of the Florence city hall to form a pair with another just begun by Leonardo da Vinci.
Facts:
- In 1504, Michelangelo agreed to paint a huge fresco for the Sala del Gran Consiglio of the Florence city hall.
- Around 1504, Leonardo da Vinci just began with a mural for the Florence city hall.

Text: After the success of the David in 1504, Michelangelo’s work consisted almost entirely of vast projects. He was attracted to these ambitious tasks while at the same time rejecting the use of assistants, so that most of these projects were impractical and remained unfinished. In 1504 he agreed to paint a huge fresco for the Sala del Gran Consiglio of the Florence city hall to form a pair with another just begun by Leonardo da Vinci. <SOS>Both murals recorded military victories by the city (Michelangelo’s was the Battle of Cascina), but each also gave testimony to the special skills of the city’s much vaunted artists.<EOS> Leonardo’s design shows galloping horses, Michelangelo’s active nudes—soldiers stop swimming and climb out of a river to answer an alarm.
Sentence to be focused on: Both murals recorded military victories by the city (Michelangelo’s was the Battle of Cascina), but each also gave testimony to the special skills of the city’s much vaunted artists.
Facts:
- Michelangelo’s murals for the Florence city hall recorded military victories by the city.
- Leonardo da Vinci’s murals for the Florence city hall recorded military victories by the city.
- Michelangelo’s mural for the Florence city hall was the Battle of Cascina.

Text: I (27f) and my fiance "Leo" (27m) decided to let my FSIL "Maya" (32f) stay at our house because she needed space from her husband due to some relationship struggles they're having. Leo and I had gotten wedding cake samples from an expensive bakery specializing in wedding cakes. We planned to test them along with Maya after we finished up some other wedding plans yesterday. <SOS>However, when I came home from work to see Leo yelling at Maya, the box the samples came in wide open on the living room table, and Maya arguing with him.<EOS> I asked what was happening, and Leo angrily told me that while we were both at work, Maya had some friends over and they ended up eating almost all of our cake samples.
Sentence to be focused on: However, when I came home from work to see Leo yelling at Maya, the box the samples came in wide open on the living room table, and Maya arguing with him.
Facts:
No verifiable claim.

Text: I was a catholic school kid, educated by nuns and somehow on a spring day in 1972, I was called down to the principal’s office by Sister Mary Roberts, who informed me that I had gained admission to Stuyvesant High School. <SOS>I was excited to be freshman in one of New York City’s elite public schools but soon came to realize that my catholic school education did not provide the groundwork for abstract concepts like science and algebra.<EOS> My parochial education in Science at St. Joseph’s was essentially “God made it, what else do you need to know?”
Sentence to be focused on: I was excited to be freshman in one of New York City’s elite public schools but soon came to realize that my catholic school education did not provide the groundwork for abstract concepts like science and algebra.
Facts:
- Stuyvesant High School is in New York City.
- Stuyvesant High School is an elite high school.
- Stuyvesant High School is a public school.
- In 1972, St. Joseph's catholic school education did not provide the groundwork for abstract concepts like science and algebra.

Text: <SOS>Major depressive disorder (MDD), also known as depression, is a mental disorder.<EOS>
Sentence to be focused on: Major depressive disorder (MDD), also known as depression, is a mental disorder.
Facts:
- Major depressive disorder is also known as depression.
- Major depressive disorder is a mental disorder.

Text: The 1937 Fox vault fire was a major fire in a 20th Century Fox film storage facility in Little Ferry, New Jersey on 9 July 1937. It was caused by the spontaneous combustion of nitrate film stored in inadequately-ventilated vaults. The fire resulted in one death and two injuries, and destroyed all of the film present. <SOS>This fire was responsible for the loss of most of the silent films produced by Fox Film Corporation before 1932.<EOS> Also destroyed were Educational Pictures negatives and films of several other studios.
Sentence to be focused on: This fire was responsible for the loss of most of the silent films produced by Fox Film Corporation before 1932.
Facts:
- Fox Film Corporation produced silent films before 1932.
- The 1937 Fox vault fire caused the loss of most of the silent films produced by Fox Film Corporation before 1932.

Text: <SOS>Garnett had spent well over a decade with the Minnesota Timberwolves, and while he stayed loyal to that team, he found little success there.<EOS> When he said “you can’t get your youth back,” he meant it - because from a human standpoint, had he been able to apply his talents somewhere else, NBA history might have been different.
Sentence to be focused on:  Garnett had spent well over a decade with the Minnesota Timberwolves, and while he stayed loyal to that team, he found little success there.
Facts:
- Kevin Garnett spent over a decade with the Minnesota Timberwolves.
- Kevin Garnett was loyal to the Minnesota Timberwolves.
- Kevin Garnett found little success with the Minnesota Timberwolves.

Text: Garnett had spent well over a decade with the Minnesota Timberwolves, and while he stayed loyal to that team, he found little success there. <SOS>When he said “you can’t get your youth back,” he meant it - because from a human standpoint, had he been able to apply his talents somewhere else, NBA history might have been different.<EOS>
Sentence to be focused on: When he said “you can’t get your youth back,” he meant it - because from a human standpoint, had he been able to apply his talents somewhere else, NBA history might have been different.
Facts:
- Kevin Garnett said "you can’t get your youth back."

Text: Unity. Unity. In another January in Washington, on New Year’s Day 1863, Abraham Lincoln signed the Emancipation Proclamation. <SOS>When he put pen to paper, the President said, “If my name ever goes down into history it will be for this act and my whole soul is in it.”<EOS> My whole soul is in it.
Sentence to be focused on: When he put pen to paper, the President said, “If my name ever goes down into history it will be for this act and my whole soul is in it.”
Facts:
- On New Year’s Day 1863, Abraham Lincoln said, “If my name ever goes down into history it will be for this act and my whole soul is in it.”

Text: Ãcariya Mun related the story of a dhutanga monk (ascetic monk) who inadvertently went to stay in a forest located next to a charnel ground. He arrived on foot at a certain village late one afternoon and, being unfamiliar with the area, asked the villagers where he could find a wooded area suitable for meditation. They pointed to a tract of forest, claiming it was suitable, but neglected to tell him that it was situated right on the edge of a charnel ground. <SOS>They then guided him to the forest, where he passed the first night peacefully.<EOS> On the following day he saw the villagers pass by carrying a corpse, which they soon cremated only a short distance from where he was staying.
Sentence to be focused on: They then guided him to the forest, where he passed the first night peacefully.
Facts:
No verifiable claim.

Text: <SOS>The sweet potato or sweetpotato (Ipomoea batatas) is a dicotyledonous plant that belongs to the bindweed or morning glory family, Convolvulaceae.<EOS> Its large, starchy, sweet-tasting tuberous roots are used as a root vegetable. The young shoots and leaves are sometimes eaten as greens.
Sentence to be focused on: The sweet potato or sweetpotato (Ipomoea batatas) is a dicotyledonous plant that belongs to the bindweed or morning glory family, Convolvulaceae.
Facts:
- The scientific name of sweet potatoes is Ipomoea batatas.
- Sweet potatoes are dicotyledonous plants.
- Sweet potatoes belong to the bindweed or morning glory family, Convolvulaceae.

Text: Pope Julius had an ambitious imagination, parallel to Michelangelo’s, but because of other projects, such as the new building of St. Peter’s and his military campaigns, he evidently became disturbed soon by the cost. Michelangelo believed that Bramante, the equally prestigious architect at St. Peter’s, had influenced the pope to cut off his funds. He left Rome, but the pope brought pressure on the city authorities of Florence to send him back. <SOS>He was put to work on a colossal bronze statue of the pope in his newly conquered city of Bologna (which the citizens pulled down soon after when they drove the papal army out) and then on the less expensive project of painting the ceiling of the Sistine Chapel (1508–12).<EOS>
Sentence to be focused on: He was put to work on a colossal bronze statue of the pope in his newly conquered city of Bologna (which the citizens pulled down soon after when they drove the papal army out) and then on the less expensive project of painting the ceiling of the Sistine Chapel (1508–12).
Facts:
- Michelangelo was put to work on a colossal bronze statue of Pope Julius II.
- The city of Bologna was once conquered by Pope Julius II.
- The colossal bronze statue of Pope Julius II was put in Bologna.
- The papal army was driven out of Bologna.
- The citizens of the Bologna pulled down the bronze statue of Pope Julius II after they drove the papal army out.
- Michelangelo worked on painting the ceiling of the Sistine Chapel from 1508 to 1512.

Extract *verifiable atomic* facts.

Text: {snippet}
Sentence to be focused on: {sentence}
Facts:"""

# Whole-generation discard marker, checked as a case-sensitive substring
# exactly like the original (claim_extractor.py).
NO_CLAIM_SENTINEL = "No verifiable claim."


class VeriScoreDecomposer(BaseDecomposer):
    """Sliding-window decomposer (3 before / 1 after) with VeriScore's few-shot prompts."""

    default_context_key = "question"  # selects the QA scanner, as in the original
    dedup_record = True  # the original counts each claim once per response (all_facts_lst)

    def build_requests(self, text: str, sentences: list[str], context: str) -> list:
        """One system+user pair per sentence, mirroring ClaimExtractor's scanners."""
        question = context.strip()
        template = _QA_TEMPLATE if context else _NON_QA_TEMPLATE
        # non_qa_scanner_extractor: for long texts the lead sentence is always
        # prepended, even when the window already contains it. QA mode
        # (qa_scanner_extractor) never prepends it.
        lead = sentences[0].strip() if not context and len(sentences) > 5 else ""
        requests = []
        for i in range(len(sentences)):
            target = sentences[i].strip()
            tagged = f"<SOS>{target}<EOS>"
            context1 = " ".join(sentences[max(0, i - 3):i]).strip()
            context2 = " ".join(sentences[i + 1:i + 2]).strip()
            if context:
                snippet = f"Question: {question}\nResponse: {context1} {tagged} {context2}".strip()
            else:
                snippet = f"{lead} {context1} {tagged} {context2}".strip()
            requests.append([
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": template.format(snippet=snippet, sentence=target)},
            ])
        return requests

    def parse_output(self, raw: str) -> list[str]:
        """Whole-generation "No verifiable claim." discard, then robust parsing.

        Deviation from the original's minimal parsing (split lines, strip
        "- "/numbers): lines go through the shared parse_claims, which also
        drops chat-model boilerplate (preambles, markdown headers, lines
        ending in ":") that GPT-4 — the model the original parser was
        written for — never emitted.
        """
        if not raw or NO_CLAIM_SENTINEL in raw:
            return []
        return parse_claims(raw)
