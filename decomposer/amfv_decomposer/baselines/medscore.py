"""MedScore baseline: medical-adapted decomposition with conditional facts (Huang et al. 2025).

System prompt and user format are verbatim from the reference repo
(github.com/Heyuan9/MedScore, medscore/prompts.py MEDSCORE_PROMPT and
decomposer.py MedScoreDecomposer.format_input) — the main-method prompt
(paper Table 19: 10 full-context examples), not the 8-example ablation
variant (Table 20). The stray backslash-quote sequences are preserved: the
repo double-escapes quotes, so this is the exact string the paper's model
saw — as is its trailing whitespace (hence the W291/W293 per-file ignores).

Deviations: we parse with the shared robust parse_claims (theirs keeps
numbered prefixes and only strips hyphens), and we do not cap generation at
their max_tokens=256.
"""

from __future__ import annotations

from ..base import BaseDecomposer

# Verbatim from medscore/prompts.py (MEDSCORE_PROMPT).
_SYSTEM = r"""You are a medical expert in evaluating how factual a medical sentence is. You break down a sentence into as many facts as possible. The facts should be objective and verifiable against reliable external information such as Wikipedia and PubMed. All subjective personal experiences (\"I was or someone did\") and personal narratives (stating a past event) are not verifiable and should not be included in the fact list. Facts should be situated within conditions in the sentence. Suggestions (e.g. \"I recommend or Your doctor suggest\") and opinions (e.g. \"I think\") should be transformed into objective facts by removing subjective words and pronouns to only retain the core information that can be verified. Imperative instructions (\"do something\") should be transformed into declarative facts (\"doing something is helpful for some conditions\").

If there is an overly specific entity such as \"Your partner\" or vague references (pronouns, this or that) in the fact, replace it with a general phrase with conditional modifiers using information in the provided context (e.g. \"People in some conditions\"). Each fact should be verifiable on its own and require no additional context. Do not add additional information outside of the sentence and context. You do not need to justify what you extract.

If there is no verifiable fact in the sentence, please write \"No verifiable claim\".

Here are some examples:

Context: I spoke to your doctor and they wanted to address your concerns about tetanus. Since you've had your primary tetanus shots as a child, you don't need immunoglobulin (IGG) shots, and they were actually unnecessary during your last visit. 

 Considering your tetanus vaccine expired in 2020 and you've got a dirty wound from the Spartan race, your doctor recommends getting a tetanus booster vaccine as soon as possible. They also mentioned that you were due for a booster anyway since it's been more than 3 years since your last vaccine.

Your doctor is a bit puzzled as to why you were given IGG shots instead of a vaccine during your last visit, but that's not a concern for now. They just want to make sure you get the booster vaccine to be on the safe side. It's best to schedule an appointment for the booster vaccine as soon as possible to avoid any potential risks.
Please breakdown the following sentence into independent facts: I spoke to your doctor and they wanted to address your concerns about tetanus.
Facts:
- No verifiable claim

Context: I spoke to your doctor, and they expressed concerns about the safety of using anabolic steroids, particularly in combination with the medications your partner is already taking for Addison's disease. The doctor noted that while these substances may have positive effects on muscle and bone health, they also carry significant risks and potential side effects.

The doctor mentioned that the anabolic cycle your partner is on is quite intense and requires careful monitoring for potential issues such as infertility, mood swings, and problems related to weight gain, including snoring and possible sleep apnea. They also emphasized the importance of considering the long-term effects of using these substances, particularly when they are stopped.

The doctor's primary concern is that your partner's underlying condition, Addison's disease, may not significantly complicate things if well-treated, but it could become an issue when the anabolic cycle is stopped. They strongly advise that your partner consult with a medical professional, ideally their endocrinologist, to discuss the potential risks and consequences of using these substances, especially given their pre-existing condition.

It's essential to have an open and honest conversation with a healthcare professional to ensure your partner's safety and well-being. I would encourage you to support your partner in seeking medical advice, and I'm happy to facilitate a discussion with their doctor if needed.
Please breakdown the following sentence into independent facts: The doctor noted that while these substances may have positive effects on muscle and bone health, they also carry significant risks and potential side effects.
Facts:
- Anabolic steroids may have positive effects on muscle health.
- Anabolic steroids may have positive effects on bone health.
- Anabolic steroids may also carry significant risks.
- Anabolic steroids may carry potential side effects.

Context: I spoke to your doctor and they would like to know more about the specific tests you are interested in having done. They mentioned that their willingness to order these tests may depend on what you are asking for. If the tests are rare or uncommon, they may recommend that you see a specialist who is more familiar with those types of tests and their results. Your doctor wants to make sure that any tests that are ordered are properly interpreted and that you receive the best possible care. Could you please provide more information about the tests you are interested in having done?
Please breakdown the following sentence into independent facts: If the tests are rare or uncommon, they may recommend that you see a specialist who is more familiar with those types of tests and their results. 
Facts:
- If the tests are rare or uncommon, seeing a specialist is helpful.
- The specialist is more familiar with rare or uncommon types of tests.
- The specialist is more familiar with the results of rare or uncommon tests.

Context: Hi there,

I spoke to your doctor and they wanted to reassure you that getting the immunoglobulin shot within 38 hours of the potential exposure is still considered a safe timeframe. They mentioned that as long as you received the shot before 7 days from the incident, you should be okay.

Your doctor recommends that you follow up on the scheduled dates for your remaining rabies shots. They also wanted to remind you that rabies has a relatively long incubation period, typically ranging from 1-3 months, before symptoms start to show. This means that even if you were bitten by an infected bat, you would likely have developed immunity by the time symptoms appear.

Please try to take care of yourself and manage your anxiety during this time. If you have any further concerns or questions, don't hesitate to reach out.

Best regards,

[Your Doctor's Name]
Please breakdown the following sentence into independent facts: They also wanted to remind you that rabies has a relatively long incubation period, typically ranging from 1-3 months, before symptoms start to show.
Facts:
- Rabies has a relatively long incubation period.
- The incubation period for rabies typically ranges from 1-3 months.
- Rabies symptoms start to show after the incubation period.

Context: I spoke to your doctor and they wanted to thank you for your interest in creating a language course to help physicians better communicate with patients who speak different languages. 

They mentioned that while language barriers can contribute to the \"revolving door syndrome,\" it's just one of many factors. Other important factors include education, home support, medication noncompliance, and lack of primary care. 

In terms of a language course, your doctor thinks that Duolingo is a good option. However, they noted that it's challenging for doctors to find the time to learn multiple languages, as there are many languages spoken by patients in their area, including Spanish, Hmong, Chinese, and Polish. They also mentioned that many Spanish-speaking patients have some knowledge of English or have family members who are fluent in English.

Your doctor didn't specify a preferred medium for the course, but they seemed to appreciate the idea of a convenient and accessible program. They also didn't provide specific vocabulary recommendations, but it's likely that a course focused on medical terminology and common patient interactions would be most useful.
Please breakdown the following sentence into independent facts: However, they noted that it's challenging for doctors to find the time to learn multiple languages, as there are many languages spoken by patients in their area, including Spanish, Hmong, Chinese, and Polish.
Facts:
- It is challenging for doctors to find the time to learn multiple languages.
- Many languages are spoken by patients in doctors' area.
- Spanish is one of the languages spoken by patients in doctors' area.
- Hmong is one of the languages spoken by patients in doctors' area.
- Chinese is one of the languages spoken by patients in doctors' area.
- Polish is one of the languages spoken by patients in doctors' area.

Context: I spoke to your doctor and they think that you just need a bit more time to recover from your surgery. They noted that your usual lifestyle is quite sedentary, and having surgery can be a significant strain on your body, similar to intense physical activity. This, combined with your extreme anxiety, which can cause muscle tension, is likely contributing to your soreness. 

As long as you don't develop a fever and your wounds show no signs of infection, your doctor believes that there's not much more the hospital can do for you that you can't do at home. Their advice is to focus on meeting your daily needs, such as eating, drinking, and using the bathroom, and not to worry too much about the soreness right now. 

Once the soreness starts to subside, they recommend that you try to gradually increase your activities, starting with small steps like sitting in a chair, standing, and eventually walking, until you're back to your normal self.
Please breakdown the following sentence into independent facts: Once the soreness starts to subside, they recommend that you try to gradually increase your activities, starting with small steps like sitting in a chair, standing, and eventually walking, until you're back to your normal self.
Facts:
- Once the soreness starts to subside, trying to gradually increase activities is helpful.
- People with soreness should start with small steps.
- Small steps for people with soreness include sitting in a chair.
- Small steps for people with soreness include standing.
- Small steps for people with soreness include walking.
- Gradually increasing activities helps people with soreness return to normal self.
  
Context: I spoke to your doctor and they wanted to address your concerns regarding the leakage you experienced after your bowel surgery in 2013. According to them, it is possible for an abnormal connection to form between your bowel and your bladder or vagina, which is known as a fistula. This could potentially cause the leakage of substances from your bowel into your urinary tract or vagina.

Your doctor recommends reviewing the notes from your second surgery to understand the nature of the repairs that were performed. This information may help clarify what happened in your specific case.

Regarding your concerns about the quality of care you received from your initial surgeon, your doctor advises that medical malpractice is a complex issue that depends on many factors, including the specific circumstances of your case and the laws in your location. If you're interested in exploring this further, they recommend consulting with a lawyer who can provide guidance on whether you have a valid case.

Please let us know if you have any further questions or concerns, and we'll be happy to help.
Please breakdown the following sentence into independent facts: Please let us know if you have any further questions or concerns, and we'll be happy to help.
Facts:
- No verifiable claim

Context: Dear Patient,

I spoke to your doctor and they wanted to address your concerns about the sharp pain you've been experiencing above your left eyebrow when exposed to strong wind. After reviewing your symptoms, they think that trigeminal neuralgia could be a possible cause of your pain. 

They considered cluster headaches as well, but they believe it's less likely in your case because cluster headaches typically occur at the same time every day, last for several days in a row, and involve pain behind or around the entire eye on one side.

Your doctor would like to discuss this further with you and explore the possibility of trigeminal neuralgia being the cause of your pain. They recommend that you schedule a follow-up appointment to discuss your symptoms in more detail and determine the best course of action.

Please let us know if you have any questions or concerns before your appointment.

Best regards,
[Your Name]
On behalf of [Doctor's Name]
Please breakdown the following sentence into independent facts: Dear Patient,

I spoke to your doctor and they wanted to address your concerns about the sharp pain you've been experiencing above your left eyebrow when exposed to strong wind.
Facts:
- No verifiable claim

Context: I spoke to your doctor, and they recommended that you visit a pharmacy to get an over-the-counter anti-nausea medication, such as Dramamine (also known as Gravol), to help alleviate your symptoms. They would like to know how you're feeling now, 4 hours after your initial message, to assess if your condition is improving or if further action is needed. If your symptoms persist, your doctor may want to investigate further to determine the cause of your discomfort. Please let us know your current status so we can provide further guidance.
Please breakdown the following sentence into independent facts: They would like to know how you're feeling now, 4 hours after your initial message, to assess if your condition is improving or if further action is needed.
Facts:
- No verifiable claim

Context: I spoke to your doctor and they wanted to reassure you that, given you are not sexually active and have never had penetrative intercourse, it is not possible for you to be pregnant. They understand that you have been experiencing anxiety and pregnancy scares, and they think it's a great idea for you to see a psychologist to help you manage these feelings.

Regarding your symptoms, your doctor believes that starting a stable contraceptive therapy, such as the pill or other hormonal methods, could be helpful in regulating your periods and alleviating some of the symptoms you're experiencing. They think this could be a useful approach to help you feel better and more in control of your situation.

Please keep in mind that your upcoming ultrasound will likely provide more insight into what's going on with your body, and your doctor will be able to discuss the results with you and determine the best course of action.
Please breakdown the following sentence into independent facts: They understand that you have been experiencing anxiety and pregnancy scares, and they think it's a great idea for you to see a psychologist to help you manage these feelings.
Facts:
- Seeing a psychologist can help manage feelings of anxiety and pregnancy scares.
"""


class MedScoreDecomposer(BaseDecomposer):
    """Context-conditioned medical decomposer, faithful to the reference repo."""

    # The reference pipeline passes only the response text as context
    # (medscore.py: context = item[response_key]); the question is never
    # included for AskDocs (question context is a PUMA-specific rule).
    default_context_key = None

    def build_requests(self, text: str, sentences: list[str], context: str) -> list:
        """One system+user pair per sentence; context is the full response text."""
        return [
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": (
                    f"Context: {text}\n"
                    f"Please breakdown the following sentence into independent facts: {sent}\n"
                    f"Facts:\n"
                )},
            ]
            for sent in sentences
        ]
