"""MedScore baseline: medical-adapted FActScore with domain-specific few-shot examples."""

from __future__ import annotations

from ..base import BaseDecomposer, split_sentences, parse_claims
from ..vllm_client import QWEN3_8B, chat_generate

# Medical few-shot prompt with verifiability instructions.
# Source: Huang et al. 2025 / MedScore reference repo (medscore/prompts.py)
_PROMPT = """\
You are a medical expert in evaluating how factual a medical sentence is. You break down a sentence into as many facts as possible. The facts should be objective and verifiable against reliable external information such as Wikipedia and PubMed. All subjective personal experiences ("I was or someone did") and personal narratives (stating a past event) are not verifiable and should not be included in the fact list. Facts should be situated within conditions in the sentence. Suggestions (e.g. "I recommend or Your doctor suggest") and opinions (e.g. "I think") should be transformed into objective facts by removing subjective words and pronouns to only retain the core information that can be verified. Imperative instructions ("do something") should be transformed into declarative facts ("doing something is helpful for some conditions").

If there is an overly specific entity such as "Your partner" or vague references (pronouns, this or that) in the fact, replace it with a general phrase with conditional modifiers using information in the provided context (e.g. "People in some conditions"). Each fact should be verifiable on its own and require no additional context. Do not add additional information outside of the sentence and context. You do not need to justify what you extract.

If there is no verifiable fact in the sentence, please write "No verifiable claim".

Here are some examples:

Context: I spoke to your doctor and they wanted to address your concerns about tetanus. Since you've had your primary tetanus shots as a child, you don't need immunoglobulin (IGG) shots, and they were actually unnecessary during your last visit. Considering your tetanus vaccine expired in 2020 and you've got a dirty wound from the Spartan race, your doctor recommends getting a tetanus booster vaccine as soon as possible. They also mentioned that you were due for a booster anyway since it's been more than 3 years since your last vaccine.
Please breakdown the following sentence into independent facts: I spoke to your doctor and they wanted to address your concerns about tetanus.
Facts:
- No verifiable claim

Context: I spoke to your doctor, and they expressed concerns about the safety of using anabolic steroids, particularly in combination with the medications your partner is already taking for Addison's disease. The doctor noted that while these substances may have positive effects on muscle and bone health, they also carry significant risks and potential side effects.
Please breakdown the following sentence into independent facts: The doctor noted that while these substances may have positive effects on muscle and bone health, they also carry significant risks and potential side effects.
Facts:
- Anabolic steroids may have positive effects on muscle health.
- Anabolic steroids may have positive effects on bone health.
- Anabolic steroids may also carry significant risks.
- Anabolic steroids may carry potential side effects.

Context: I spoke to your doctor and they would like to know more about the specific tests you are interested in having done. They mentioned that their willingness to order these tests may depend on what you are asking for. If the tests are rare or uncommon, they may recommend that you see a specialist who is more familiar with those types of tests and their results.
Please breakdown the following sentence into independent facts: If the tests are rare or uncommon, they may recommend that you see a specialist who is more familiar with those types of tests and their results.
Facts:
- If the tests are rare or uncommon, seeing a specialist is helpful.
- The specialist is more familiar with rare or uncommon types of tests.
- The specialist is more familiar with the results of rare or uncommon tests.

Context: I spoke to your doctor and they wanted to reassure you that getting the immunoglobulin shot within 38 hours of the potential exposure is still considered a safe timeframe. They mentioned that as long as you received the shot before 7 days from the incident, you should be okay. Your doctor recommends that you follow up on the scheduled dates for your remaining rabies shots. They also wanted to remind you that rabies has a relatively long incubation period, typically ranging from 1-3 months, before symptoms start to show.
Please breakdown the following sentence into independent facts: They also wanted to remind you that rabies has a relatively long incubation period, typically ranging from 1-3 months, before symptoms start to show.
Facts:
- Rabies has a relatively long incubation period.
- The incubation period for rabies typically ranges from 1-3 months.
- Rabies symptoms start to show after the incubation period.

Context: I spoke to your doctor and they wanted to thank you for your interest in creating a language course to help physicians better communicate with patients who speak different languages. They mentioned that while language barriers can contribute to the "revolving door syndrome," it's just one of many factors. Other important factors include education, home support, medication noncompliance, and lack of primary care. In terms of a language course, your doctor thinks that Duolingo is a good option. However, they noted that it's challenging for doctors to find the time to learn multiple languages, as there are many languages spoken by patients in their area, including Spanish, Hmong, Chinese, and Polish.
Please breakdown the following sentence into independent facts: However, they noted that it's challenging for doctors to find the time to learn multiple languages, as there are many languages spoken by patients in their area, including Spanish, Hmong, Chinese, and Polish.
Facts:
- It is challenging for doctors to find the time to learn multiple languages.
- Many languages are spoken by patients in doctors' area.
- Spanish is one of the languages spoken by patients in doctors' area.
- Hmong is one of the languages spoken by patients in doctors' area.
- Chinese is one of the languages spoken by patients in doctors' area.
- Polish is one of the languages spoken by patients in doctors' area.

Context: I spoke to your doctor and they think that you just need a bit more time to recover from your surgery. They noted that your usual lifestyle is quite sedentary, and having surgery can be a significant strain on your body. As long as you don't develop a fever and your wounds show no signs of infection, your doctor believes that there's not much more the hospital can do for you that you can't do at home. Once the soreness starts to subside, they recommend that you try to gradually increase your activities, starting with small steps like sitting in a chair, standing, and eventually walking, until you're back to your normal self.
Please breakdown the following sentence into independent facts: Once the soreness starts to subside, they recommend that you try to gradually increase your activities, starting with small steps like sitting in a chair, standing, and eventually walking, until you're back to your normal self.
Facts:
- Once the soreness starts to subside, trying to gradually increase activities is helpful.
- People with soreness should start with small steps.
- Small steps for people with soreness include sitting in a chair.
- Small steps for people with soreness include standing.
- Small steps for people with soreness include walking.
- Gradually increasing activities helps people with soreness return to normal self.

Context: I spoke to your doctor and they wanted to address your concerns regarding the leakage you experienced after your bowel surgery in 2013. According to them, it is possible for an abnormal connection to form between your bowel and your bladder or vagina, which is known as a fistula. This could potentially cause the leakage of substances from your bowel into your urinary tract or vagina. Your doctor recommends reviewing the notes from your second surgery to understand the nature of the repairs that were performed.
Please breakdown the following sentence into independent facts: Please let us know if you have any further questions or concerns, and we'll be happy to help.
Facts:
- No verifiable claim

Context: I spoke to your doctor and they wanted to address your concerns about the sharp pain you've been experiencing above your left eyebrow when exposed to strong wind. They think that trigeminal neuralgia could be a possible cause of your pain. They considered cluster headaches as well, but they believe it's less likely in your case because cluster headaches typically occur at the same time every day, last for several days in a row, and involve pain behind or around the entire eye on one side.
Please breakdown the following sentence into independent facts: Dear Patient, I spoke to your doctor and they wanted to address your concerns about the sharp pain you've been experiencing above your left eyebrow when exposed to strong wind.
Facts:
- No verifiable claim

"""


class MedScoreDecomposer(BaseDecomposer):
    """Medical-adapted FActScore with context-aware few-shot examples."""

    def decompose(self, text: str, context: str = "") -> list[str]:
        sentences = split_sentences(text)
        if not sentences:
            return []

        full_context = context if context else text

        messages_batch = [
            [{"role": "user", "content": (
                f"{_PROMPT}"
                f"Context: {full_context}\n"
                f"Please breakdown the following sentence into independent facts: {sent}\n"
                f"Facts:"
            )}]
            for sent in sentences
        ]

        outputs = chat_generate(messages_batch, model=QWEN3_8B)

        all_claims: list[str] = []
        for output in outputs:
            all_claims.extend(parse_claims(output))
        return all_claims
