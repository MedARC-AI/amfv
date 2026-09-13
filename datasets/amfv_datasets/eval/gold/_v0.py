"""One-shot writer for AMFV-Bench v0 gold JSONL. Not imported at runtime."""

from __future__ import annotations

from pathlib import Path

from amfv_datasets.eval.schema import (
    ANNOTATOR_SEED,
    GOLD_V0_PATH,
    EvalCase,
    EvidenceRef,
    FailureMode,
    GoldClaim,
    InputKind,
    PyramidTier,
    Scope,
    Stratum,
    Verdict,
    case_to_dict,
    dump_jsonl,
    validate_gold_set,
)

AS_OF = "2026-08-13"
TIER = PyramidTier.GUIDELINE


def _ev(
    source_id: str,
    url: str,
    title: str,
    section: str,
    quoted_span: str,
    *,
    published_or_updated: str | None,
    replaces: str | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        source_id=source_id,
        url=url,
        title=title,
        section=section,
        quoted_span=quoted_span,
        pyramid_tier=TIER,
        published_or_updated=published_or_updated,
        replaces=replaces,
    )


def _claim(
    claim_id: str,
    text: str,
    verdict: Verdict,
    scope: Scope,
    evidence: EvidenceRef,
    *failure_modes: FailureMode,
) -> GoldClaim:
    return GoldClaim(
        claim_id=claim_id,
        text=text,
        atomicity_ok=True,
        verdict=verdict,
        scope=scope,
        as_of=AS_OF,
        evidence=(evidence,),
        failure_modes=failure_modes,
    )


def _case(
    case_id: str,
    stratum: Stratum,
    input_kind: InputKind,
    input_text: str,
    claim: GoldClaim,
    notes: str,
) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        stratum=stratum,
        input_kind=input_kind,
        input_text=input_text,
        gold_claims=(claim,),
        annotator=ANNOTATOR_SEED,
        notes=notes,
    )


NG136 = "Hypertension in adults: diagnosis and management"
NG238 = "Cardiovascular disease: risk assessment and reduction, including lipid modification"
NG203 = "Chronic kidney disease: assessment and management"
NG28 = "Type 2 diabetes in adults: management"
NG133 = "Hypertension in pregnancy: diagnosis and management"
NG253 = "Suspected sepsis in people aged 16 or over: recognition, assessment and early management"


def cases() -> list[EvalCase]:
    """Return the 48 v0 gold cases."""
    ng136_abpm = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Diagnosing hypertension",
        "If clinic blood pressure is between 140/90 mmHg and 180/120 mmHg, offer ambulatory blood pressure monitoring (ABPM) to confirm the diagnosis of hypertension.",
        published_or_updated="2026-02-26",
    )
    ng136_diagnose = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Diagnosing hypertension",
        "clinic blood pressure of 140/90 mmHg or higher and ABPM daytime average or HBPM average of 135/85 mmHg or higher.",
        published_or_updated="2026-02-26",
    )
    ng136_target = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Monitoring treatment and blood pressure targets",
        "For adults with hypertension aged under 80, reduce clinic blood pressure to below 140/90 mmHg and ensure that it is maintained below that level.",
        published_or_updated="2026-02-26",
    )
    ng136_target80 = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Monitoring treatment and blood pressure targets",
        "For adults with hypertension aged 80 and over, reduce clinic blood pressure to below 150/90 mmHg and ensure that it is maintained below that level.",
        published_or_updated="2026-02-26",
    )
    ng136_ace_arb = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Choosing antihypertensive drug treatment",
        "Do not combine an ACE inhibitor with an ARB to treat hypertension. [2019]",
        published_or_updated="2026-02-26",
    )
    ng136_step1 = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Choosing antihypertensive drug treatment",
        "Offer an ACE inhibitor or an ARB to adults starting step 1 antihypertensive treatment who: have type 2 diabetes and are of any age or family origin or are aged under 55 but not of Black African or African–Caribbean family origin.",
        published_or_updated="2026-02-26",
    )
    ng136_ccb = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Choosing antihypertensive drug treatment",
        "Offer a calcium-channel blocker (CCB) to adults starting step 1 antihypertensive treatment who: are aged 55 or over and do not have type 2 diabetes or are of Black African or African–Caribbean family origin and do not have type 2 diabetes.",
        published_or_updated="2026-02-26",
    )
    ng136_ca = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Lifestyle interventions",
        "Do not offer calcium, magnesium or potassium supplements as a method for reducing blood pressure. [2004]",
        published_or_updated="2026-02-26",
    )
    ng136_hbpm = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Diagnosing hypertension",
        "If ABPM is unsuitable or the person is unable to tolerate it, offer home blood pressure monitoring (HBPM) to confirm the diagnosis of hypertension. [2019]",
        published_or_updated="2026-02-26",
    )
    ng136_advice = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136/chapter/Recommendations",
        NG136,
        "Diagnosing hypertension",
        "Offer advice on healthy living in line with the NHS information on healthy living to people who have raised blood pressure but have not been diagnosed with hypertension. [2026]",
        published_or_updated="2026-02-26",
    )
    ng136_overview = _ev(
        "nice-ng136",
        "https://www.nice.org.uk/guidance/ng136",
        NG136,
        "Overview",
        "This guideline covers identifying and treating primary hypertension (high blood pressure) in people aged 18 and over, including people with type 2 diabetes.",
        published_or_updated="2026-02-26",
        replaces="nice-cg127",
    )
    ng238_qrisk = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238/chapter/Recommendations",
        NG238,
        "Identifying and assessing CVD risk",
        "Use the QRISK3 tool to calculate the estimated CVD risk within the next 10 years for people aged between 25 and 84 without CVD. [May 2023]",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng238_statin = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238/chapter/Recommendations",
        NG238,
        "Lipid-lowering treatment for primary prevention of CVD",
        "Offer atorvastatin 20 mg for the primary prevention of CVD to people who have a 10-year QRISK3 score of 10% or more. [May 2023]",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng238_aspirin = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238/chapter/Recommendations",
        NG238,
        "Aspirin for primary prevention of CVD",
        "Do not routinely offer aspirin for primary prevention of CVD. [January 2023]",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng238_no_tool = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238/chapter/Recommendations",
        NG238,
        "Identifying and assessing CVD risk",
        "Do not use a risk assessment tool for people who are at high risk of CVD, including people with: type 1 diabetes; an estimated glomerular filtration rate less than 60 ml per minute per 1.73 m2 and/or albuminuria; familial hypercholesterolaemia.",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng238_under10 = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238/chapter/Recommendations",
        NG238,
        "Lipid-lowering treatment for primary prevention of CVD",
        "Do not rule out treatment with atorvastatin 20 mg for the primary prevention of CVD just because the person's 10-year QRISK3 score is less than 10% if they have an informed preference for taking a statin.",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng238_age85 = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238/chapter/Recommendations",
        NG238,
        "Lipid-lowering treatment for primary prevention of CVD",
        "For people aged 85 and older consider treatment with atorvastatin 20 mg.",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng238_lifetime = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238/chapter/Recommendations",
        NG238,
        "Identifying and assessing CVD risk",
        "Consider using a lifetime risk tool such as QRISK3-lifetime to inform discussions on CVD risk and to motivate lifestyle changes, particularly for people with a 10-year QRISK3 score less than 10%.",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng238_secondary = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238/chapter/Recommendations",
        NG238,
        "Lipid-lowering treatment for secondary prevention of CVD",
        "Offer atorvastatin 80 mg to people with CVD, whatever their cholesterol level, unless the person meets the criteria in recommendation 1.7.3.",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng238_overview = _ev(
        "nice-ng238",
        "https://www.nice.org.uk/guidance/ng238",
        NG238,
        "Overview",
        "This guideline updates and replaces NICE guideline CG181 (July 2014).",
        published_or_updated="2025-09-02",
        replaces="nice-cg181",
    )
    ng203_acr = _ev(
        "nice-ng203",
        "https://www.nice.org.uk/guidance/ng203/chapter/Recommendations",
        NG203,
        "Investigations for proteinuria",
        "Regard a confirmed ACR of 3 mg/mmol or more as clinically important proteinuria. [2021]",
        published_or_updated="2025-08-19",
        replaces="nice-cg182",
    )
    ng203_bp = _ev(
        "nice-ng203",
        "https://www.nice.org.uk/guidance/ng203/chapter/Recommendations",
        NG203,
        "Blood pressure control",
        "In adults with CKD and an ACR under 70 mg/mmol, aim for a clinic systolic blood pressure below 140 mmHg (target range 120 to 139 mmHg) and a clinic diastolic blood pressure below 90 mmHg. [2021]",
        published_or_updated="2025-08-19",
        replaces="nice-cg182",
    )
    ng203_strips = _ev(
        "nice-ng203",
        "https://www.nice.org.uk/guidance/ng203/chapter/Recommendations",
        NG203,
        "Investigations for proteinuria",
        "Do not use reagent strips to identify proteinuria in children and young people. [2021]",
        published_or_updated="2025-08-19",
        replaces="nice-cg182",
    )
    ng203_protein = _ev(
        "nice-ng203",
        "https://www.nice.org.uk/guidance/ng203/chapter/Recommendations",
        NG203,
        "Lifestyle advice",
        "Do not offer low-protein diets (dietary protein intake less than 0.6 to 0.8 g/kg/day) to adults with CKD. [2014]",
        published_or_updated="2025-08-19",
        replaces="nice-cg182",
    )
    ng203_sglt = _ev(
        "nice-ng203",
        "https://www.nice.org.uk/guidance/ng203/chapter/Recommendations",
        NG203,
        "Pharmacotherapy",
        "For SGLT2 inhibitors recommended as options in NICE technology appraisal guidance as an add-on to optimised standard care for some adults with CKD, see the guidance on:",
        published_or_updated="2025-08-19",
        replaces="nice-cg182",
    )
    ng203_overview = _ev(
        "nice-ng203",
        "https://www.nice.org.uk/guidance/ng203",
        NG203,
        "Overview",
        "This guideline updates and replaces NICE guidelines CG182 (published July 2014), NG8 (published June 2015), CG157 (published March 2013).",
        published_or_updated="2025-08-19",
        replaces="nice-cg182",
    )
    ng28_overview = _ev(
        "nice-ng28",
        "https://www.nice.org.uk/guidance/ng28",
        NG28,
        "Overview",
        "This guideline covers care and management for adults (aged 18 and over) with type 2 diabetes. This guideline updates and replaces: NICE guidelines CG66 (published May 2008) and CG87 (published May 2009).",
        published_or_updated="2026-02-18",
        replaces="nice-cg87",
    )
    ng133_overview = _ev(
        "nice-ng133",
        "https://www.nice.org.uk/guidance/ng133",
        NG133,
        "Overview",
        "This guideline covers diagnosing and managing hypertension (high blood pressure), including pre-eclampsia, during pregnancy, labour and birth. This guideline updates and replaces NICE guideline CG107 (August 2010).",
        published_or_updated="2023-04-27",
        replaces="nice-cg107",
    )
    ng253_overview = _ev(
        "nice-ng253",
        "https://www.nice.org.uk/guidance/ng253",
        NG253,
        "Overview",
        "This guideline covers the recognition, diagnosis and early management of suspected sepsis in people aged 16 or over who are not and have not recently been pregnant. This guideline partially updates and replaces NICE guideline NG51 (2016).",
        published_or_updated="2025-12-05",
        replaces="nice-ng51",
    )

    adults_htn = Scope("adults aged 18 and over", "primary hypertension", "primary or secondary care")
    adults_cvd = Scope("adults aged 25 to 84 without established CVD", "primary prevention of CVD", "primary care")
    adults_ckd = Scope("adults with CKD", "chronic kidney disease", "primary or secondary care")
    adults_t2d = Scope("adults aged 18 and over", "type 2 diabetes", "primary or secondary care")

    return [
        _case(
            "amfv-v0-ss-01",
            Stratum.STRONGLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "In an adult whose clinic blood pressure is 148/92 mmHg, NICE recommends offering ambulatory blood pressure monitoring to confirm hypertension rather than diagnosing from that single clinic pair alone.",
            _claim(
                "c1",
                "An adult with clinic blood pressure 148/92 mmHg should be offered ABPM to confirm hypertension.",
                Verdict.STRONG_AGREEMENT,
                adults_htn,
                ng136_abpm,
            ),
            "NG136 offer-ABPM wording.",
        ),
        _case(
            "amfv-v0-ss-02",
            Stratum.STRONGLY_SUPPORTED,
            InputKind.DOCUMENT,
            "Hypertension is diagnosed when clinic blood pressure is 140/90 mmHg or higher and the ABPM daytime average or HBPM average is 135/85 mmHg or higher.",
            _claim(
                "c1",
                "Hypertension diagnosis in adults requires clinic BP of 140/90 mmHg or higher plus ABPM or HBPM average of 135/85 mmHg or higher.",
                Verdict.STRONG_AGREEMENT,
                adults_htn,
                ng136_diagnose,
            ),
            "NG136 diagnostic thresholds.",
        ),
        _case(
            "amfv-v0-ss-03",
            Stratum.STRONGLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "For an adult aged 62 with treated hypertension, the clinic blood pressure target is below 140/90 mmHg.",
            _claim(
                "c1",
                "An adult aged 62 with treated hypertension should have clinic blood pressure reduced below 140/90 mmHg.",
                Verdict.STRONG_AGREEMENT,
                Scope("adults with hypertension aged under 80", "hypertension", "primary care"),
                ng136_target,
            ),
            "NG136 under-80 clinic target.",
        ),
        _case(
            "amfv-v0-ss-04",
            Stratum.STRONGLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "Do not combine an ACE inhibitor with an ARB when treating hypertension in adults.",
            _claim(
                "c1",
                "An ACE inhibitor should not be combined with an ARB to treat hypertension.",
                Verdict.STRONG_AGREEMENT,
                adults_htn,
                ng136_ace_arb,
                FailureMode.CONTRAINDICATION,
            ),
            "NG136 explicit do-not-combine.",
        ),
        _case(
            "amfv-v0-ss-05",
            Stratum.STRONGLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "A 48-year-old White adult starting step 1 treatment for hypertension should be offered an ACE inhibitor or an ARB.",
            _claim(
                "c1",
                "A 48-year-old White adult starting step 1 treatment for hypertension should be offered an ACE inhibitor or an ARB.",
                Verdict.STRONG_AGREEMENT,
                Scope(
                    "adults aged under 55 not of Black African or African-Caribbean family origin",
                    "hypertension",
                    "primary care",
                ),
                ng136_step1,
            ),
            "NG136 step 1 ACE/ARB.",
        ),
        _case(
            "amfv-v0-ss-06",
            Stratum.STRONGLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "For a 40-year-old without CVD, estimate 10-year cardiovascular risk with QRISK3, not an older QRISK2-only workflow.",
            _claim(
                "c1",
                "QRISK3 should be used to estimate 10-year CVD risk in a 40-year-old without CVD.",
                Verdict.STRONG_AGREEMENT,
                adults_cvd,
                ng238_qrisk,
            ),
            "NG238 QRISK3.",
        ),
        _case(
            "amfv-v0-ss-07",
            Stratum.STRONGLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "If the 10-year QRISK3 score is 12%, offer atorvastatin 20 mg for primary prevention after discussion of benefits and harms.",
            _claim(
                "c1",
                "Atorvastatin 20 mg should be offered for primary prevention of CVD when 10-year QRISK3 is 10% or more.",
                Verdict.STRONG_AGREEMENT,
                adults_cvd,
                ng238_statin,
                FailureMode.DOSAGE,
            ),
            "NG238 atorvastatin 20 mg threshold.",
        ),
        _case(
            "amfv-v0-ss-08",
            Stratum.STRONGLY_SUPPORTED,
            InputKind.DOCUMENT,
            "Regard a confirmed urine ACR of 3 mg/mmol or more as clinically important proteinuria.",
            _claim(
                "c1",
                "A confirmed ACR of 3 mg/mmol or more is clinically important proteinuria.",
                Verdict.STRONG_AGREEMENT,
                adults_ckd,
                ng203_acr,
                FailureMode.RENAL_DOSING,
                FailureMode.UNIT,
            ),
            "NG203 ACR threshold.",
        ),
        _case(
            "amfv-v0-ws-01",
            Stratum.WEAKLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "Ambulatory monitoring is often used in adults when clinic readings sit around 148/92 mmHg, because a single clinic pair is not treated as a definitive diagnosis.",
            _claim(
                "c1",
                "ABPM is often used to confirm hypertension when clinic blood pressure is about 148/92 mmHg.",
                Verdict.PARTIAL_AGREEMENT,
                adults_htn,
                ng136_abpm,
                FailureMode.HEDGE,
            ),
            "True direction, hedged 'often' vs 'offer'.",
        ),
        _case(
            "amfv-v0-ws-02",
            Stratum.WEAKLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "A calcium-channel blocker may be a reasonable first-line choice in a 60-year-old without type 2 diabetes.",
            _claim(
                "c1",
                "A calcium-channel blocker may be appropriate first-line antihypertensive treatment in a 60-year-old without type 2 diabetes.",
                Verdict.PARTIAL_AGREEMENT,
                Scope("adults aged 55 or over without type 2 diabetes", "hypertension", "primary care"),
                ng136_ccb,
                FailureMode.HEDGE,
            ),
            "NG136 offers CCB; 'may' is weaker.",
        ),
        _case(
            "amfv-v0-ws-03",
            Stratum.WEAKLY_SUPPORTED,
            InputKind.REASONING_TRACE,
            "QRISK3 came back at 8%. The person wants a statin after discussing absolute benefit. NICE does not forbid atorvastatin 20 mg solely because the score is under 10%.",
            _claim(
                "c1",
                "Atorvastatin 20 mg can still be considered for primary prevention when QRISK3 is less than 10% if the person prefers a statin.",
                Verdict.PARTIAL_AGREEMENT,
                adults_cvd,
                ng238_under10,
                FailureMode.HEDGE,
                FailureMode.DOSAGE,
            ),
            "NG238 'do not rule out', not a default offer.",
        ),
        _case(
            "amfv-v0-ws-04",
            Stratum.WEAKLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "In some adults with CKD already on optimised standard care, SGLT2 inhibitors are NICE technology-appraisal options as add-on therapy.",
            _claim(
                "c1",
                "SGLT2 inhibitors are options as add-on therapy for some adults with CKD on optimised standard care.",
                Verdict.PARTIAL_AGREEMENT,
                adults_ckd,
                ng203_sglt,
                FailureMode.HEDGE,
                FailureMode.RENAL_DOSING,
            ),
            "Points to TA options, not a blanket offer.",
        ),
        _case(
            "amfv-v0-ws-05",
            Stratum.WEAKLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "Lifestyle advice can help adults with raised clinic readings who do not yet meet the diagnostic threshold for hypertension.",
            _claim(
                "c1",
                "Healthy-living advice may help people with raised blood pressure who have not been diagnosed with hypertension.",
                Verdict.PARTIAL_AGREEMENT,
                adults_htn,
                ng136_advice,
                FailureMode.HEDGE,
            ),
            "NG136 offers advice; claim is hedged.",
        ),
        _case(
            "amfv-v0-ws-06",
            Stratum.WEAKLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "A lifetime risk tool such as QRISK3-lifetime can be used in the consultation when 10-year QRISK3 is under 10%, mainly to discuss risk rather than to set a statin threshold.",
            _claim(
                "c1",
                "QRISK3-lifetime may be used to inform CVD risk discussions when 10-year QRISK3 is less than 10%.",
                Verdict.PARTIAL_AGREEMENT,
                adults_cvd,
                ng238_lifetime,
                FailureMode.HEDGE,
            ),
            "NG238 'consider', not 'offer'.",
        ),
        _case(
            "amfv-v0-ws-07",
            Stratum.WEAKLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "In a robust 87-year-old without CVD, atorvastatin 20 mg can be considered; it is not an automatic offer.",
            _claim(
                "c1",
                "Atorvastatin 20 mg can be considered for primary prevention in an 87-year-old.",
                Verdict.PARTIAL_AGREEMENT,
                Scope("people aged 85 and older", "primary prevention of CVD", "primary care"),
                ng238_age85,
                FailureMode.HEDGE,
                FailureMode.DOSAGE,
            ),
            "NG238 consider, not offer.",
        ),
        _case(
            "amfv-v0-ws-08",
            Stratum.WEAKLY_SUPPORTED,
            InputKind.MODEL_OUTPUT,
            "If an adult cannot tolerate ABPM, home blood pressure monitoring is an acceptable way to confirm hypertension.",
            _claim(
                "c1",
                "HBPM may be used to confirm hypertension when ABPM is unsuitable.",
                Verdict.PARTIAL_AGREEMENT,
                adults_htn,
                ng136_hbpm,
                FailureMode.HEDGE,
            ),
            "NG136 offers HBPM if ABPM unsuitable; claim hedges.",
        ),
        _case(
            "amfv-v0-rf-01",
            Stratum.REFUTED,
            InputKind.MODEL_OUTPUT,
            "For resistant hypertension, combine ramipril with losartan to block the renin-angiotensin system more completely.",
            _claim(
                "c1",
                "An ACE inhibitor should be combined with an ARB to treat hypertension.",
                Verdict.STRONG_CONTRADICTION,
                adults_htn,
                ng136_ace_arb,
                FailureMode.CONTRAINDICATION,
            ),
            "Directly contradicts NG136.",
        ),
        _case(
            "amfv-v0-rf-02",
            Stratum.REFUTED,
            InputKind.MODEL_OUTPUT,
            "Start aspirin 75 mg routinely for primary prevention in adults with a QRISK3 of 12% who do not have CVD.",
            _claim(
                "c1",
                "Aspirin should be routinely offered for primary prevention of CVD.",
                Verdict.STRONG_CONTRADICTION,
                adults_cvd,
                ng238_aspirin,
                FailureMode.CONTRAINDICATION,
                FailureMode.DOSAGE,
            ),
            "NG238 do not routinely offer aspirin.",
        ),
        _case(
            "amfv-v0-rf-03",
            Stratum.REFUTED,
            InputKind.MODEL_OUTPUT,
            "Use QRISK3 as the main 10-year risk tool in adults with type 1 diabetes to decide on a statin.",
            _claim(
                "c1",
                "QRISK3 should be used as the CVD risk tool in adults with type 1 diabetes.",
                Verdict.STRONG_CONTRADICTION,
                Scope("adults with type 1 diabetes", "primary prevention of CVD", "primary care"),
                ng238_no_tool,
            ),
            "NG238: do not use a risk tool in T1DM.",
        ),
        _case(
            "amfv-v0-rf-04",
            Stratum.REFUTED,
            InputKind.MODEL_OUTPUT,
            "Offer calcium and magnesium supplements to lower blood pressure as first-line lifestyle therapy.",
            _claim(
                "c1",
                "Calcium supplements should be offered to reduce blood pressure.",
                Verdict.STRONG_CONTRADICTION,
                adults_htn,
                ng136_ca,
                FailureMode.CONTRAINDICATION,
            ),
            "NG136 do not offer calcium supplements.",
        ),
        _case(
            "amfv-v0-rf-05",
            Stratum.REFUTED,
            InputKind.MODEL_OUTPUT,
            "In an 82-year-old with hypertension, treat to a clinic target below 140/90 mmHg as in younger adults.",
            _claim(
                "c1",
                "An 82-year-old with hypertension should have clinic blood pressure reduced below 140/90 mmHg.",
                Verdict.STRONG_CONTRADICTION,
                Scope("adults with hypertension aged 80 and over", "hypertension", "primary care"),
                ng136_target80,
            ),
            "Target is below 150/90, not 140/90.",
        ),
        _case(
            "amfv-v0-rf-06",
            Stratum.REFUTED,
            InputKind.MODEL_OUTPUT,
            "Dipstick reagent strips are sufficient to identify proteinuria in children and young people.",
            _claim(
                "c1",
                "Reagent strips should be used to identify proteinuria in children and young people.",
                Verdict.STRONG_CONTRADICTION,
                Scope("children and young people", "proteinuria / CKD", "paediatric care"),
                ng203_strips,
                FailureMode.PEDIATRICS,
            ),
            "NG203 do not use reagent strips in children.",
        ),
        _case(
            "amfv-v0-rf-07",
            Stratum.REFUTED,
            InputKind.MODEL_OUTPUT,
            "Prescribe a low-protein diet of 0.6 g/kg/day to slow CKD progression in adults.",
            _claim(
                "c1",
                "Adults with CKD should be offered a low-protein diet of 0.6 g/kg/day.",
                Verdict.STRONG_CONTRADICTION,
                adults_ckd,
                ng203_protein,
                FailureMode.RENAL_DOSING,
                FailureMode.DOSAGE,
            ),
            "NG203 do not offer low-protein diets.",
        ),
        _case(
            "amfv-v0-rf-08",
            Stratum.REFUTED,
            InputKind.MODEL_OUTPUT,
            "A single clinic reading of 142/91 mmHg is enough to diagnose hypertension and start lifelong drug treatment the same day.",
            _claim(
                "c1",
                "Hypertension can be diagnosed from a single clinic blood pressure of 142/91 mmHg without ABPM or HBPM confirmation.",
                Verdict.STRONG_CONTRADICTION,
                adults_htn,
                ng136_abpm,
            ),
            "NG136 requires ABPM/HBPM in this range.",
        ),
        _case(
            "amfv-v0-in-01",
            Stratum.INSUFFICIENT,
            InputKind.MODEL_OUTPUT,
            "Laparoscopic TEP mesh repair is the preferred operation for a primary unilateral inguinal hernia in adults.",
            _claim(
                "c1",
                "Laparoscopic TEP mesh repair is the preferred operation for primary unilateral inguinal hernia in adults.",
                Verdict.NEUTRAL,
                Scope("adults", "inguinal hernia", "elective surgery"),
                ng136_target,
            ),
            "Hernia surgery is not addressed by NG136 BP targets.",
        ),
        _case(
            "amfv-v0-in-02",
            Stratum.INSUFFICIENT,
            InputKind.MODEL_OUTPUT,
            "Adults should receive a second COVID-19 booster every six months regardless of age or clinical risk group.",
            _claim(
                "c1",
                "Adults should receive a COVID-19 booster every six months regardless of clinical risk group.",
                Verdict.NEUTRAL,
                Scope("adults", "COVID-19 immunisation", "public health"),
                ng238_statin,
            ),
            "Immunisation schedule is not in NG238.",
        ),
        _case(
            "amfv-v0-in-03",
            Stratum.INSUFFICIENT,
            InputKind.MODEL_OUTPUT,
            "Spacer technique with a pMDI is mandatory for all children with asthma before any preventer is issued.",
            _claim(
                "c1",
                "All children with asthma must use a spacer with a pMDI before a preventer is issued.",
                Verdict.NEUTRAL,
                Scope("children", "asthma", "primary care"),
                ng203_acr,
                FailureMode.PEDIATRICS,
            ),
            "Asthma inhaler technique is not in NG203 ACR text.",
        ),
        _case(
            "amfv-v0-in-04",
            Stratum.INSUFFICIENT,
            InputKind.MODEL_OUTPUT,
            "Sertraline is first-line for a first episode of moderate depression in a 30-year-old without contraindications.",
            _claim(
                "c1",
                "Sertraline is first-line drug treatment for a first episode of moderate depression in adults.",
                Verdict.NEUTRAL,
                Scope("adults", "depression", "primary care"),
                ng136_step1,
            ),
            "Antidepressant choice is not NG136 step 1.",
        ),
        _case(
            "amfv-v0-in-05",
            Stratum.INSUFFICIENT,
            InputKind.MODEL_OUTPUT,
            "Start alendronate when FRAX 10-year hip-fracture risk exceeds 3% in a 68-year-old woman.",
            _claim(
                "c1",
                "Alendronate should be started when FRAX 10-year hip-fracture risk exceeds 3%.",
                Verdict.NEUTRAL,
                Scope("adults at risk of fragility fracture", "osteoporosis", "primary care"),
                ng238_qrisk,
            ),
            "FRAX thresholds are not QRISK3 recommendations.",
        ),
        _case(
            "amfv-v0-in-06",
            Stratum.INSUFFICIENT,
            InputKind.MODEL_OUTPUT,
            "Use 14-day bismuth quadruple therapy as first-line H. pylori eradication in all UK adults.",
            _claim(
                "c1",
                "14-day bismuth quadruple therapy is first-line H. pylori eradication in all UK adults.",
                Verdict.NEUTRAL,
                Scope("adults", "Helicobacter pylori", "primary care"),
                ng238_aspirin,
            ),
            "H. pylori regimens are not in the aspirin primary-prevention rec.",
        ),
        _case(
            "amfv-v0-in-07",
            Stratum.INSUFFICIENT,
            InputKind.REASONING_TRACE,
            "The murmur sounds like mitral stenosis, so start gentamicin 7 mg/kg once daily for native-valve endocarditis while waiting for cultures.",
            _claim(
                "c1",
                "Gentamicin 7 mg/kg once daily is the recommended empiric regimen for native-valve endocarditis.",
                Verdict.NEUTRAL,
                Scope("adults", "infective endocarditis", "acute hospital"),
                ng136_abpm,
                FailureMode.DOSAGE,
            ),
            "Endocarditis dosing is not in NG136 ABPM text.",
        ),
        _case(
            "amfv-v0-in-08",
            Stratum.INSUFFICIENT,
            InputKind.MODEL_OUTPUT,
            "DWI is required in every suspected TIA before the patient can leave the emergency department.",
            _claim(
                "c1",
                "Diffusion-weighted MRI is required in every suspected TIA before ED discharge.",
                Verdict.NEUTRAL,
                Scope("adults", "TIA / stroke", "emergency care"),
                ng203_bp,
            ),
            "Stroke imaging is not CKD BP control.",
        ),
        _case(
            "amfv-v0-pm-01",
            Stratum.POPULATION_MISMATCH,
            InputKind.MODEL_OUTPUT,
            "A 7-year-old with clinic blood pressure 142/90 mmHg should be offered adult-protocol ABPM to confirm hypertension as in NG136.",
            _claim(
                "c1",
                "A 7-year-old with clinic BP 142/90 mmHg should be offered adult NG136 ABPM to confirm hypertension.",
                Verdict.NEUTRAL,
                Scope("children aged 7 years", "hypertension", "paediatric care"),
                ng136_overview,
                FailureMode.POPULATION_MISMATCH,
                FailureMode.PEDIATRICS,
            ),
            "NG136 is adults 18+; abstain, do not support.",
        ),
        _case(
            "amfv-v0-pm-02",
            Stratum.POPULATION_MISMATCH,
            InputKind.MODEL_OUTPUT,
            "Start atorvastatin 20 mg for primary prevention in a 10-year-old because a paediatric QRISK-style score is over 10%.",
            _claim(
                "c1",
                "Atorvastatin 20 mg should be offered for primary prevention in a 10-year-old with estimated 10-year CVD risk of 10% or more.",
                Verdict.NEUTRAL,
                Scope("children aged 10 years", "primary prevention of CVD", "paediatric care"),
                ng238_statin,
                FailureMode.POPULATION_MISMATCH,
                FailureMode.PEDIATRICS,
                FailureMode.DOSAGE,
            ),
            "NG238 QRISK3 is ages 25-84.",
        ),
        _case(
            "amfv-v0-pm-03",
            Stratum.POPULATION_MISMATCH,
            InputKind.MODEL_OUTPUT,
            "A pregnant woman at 18 weeks with chronic hypertension should start ramipril because ACE inhibitors are step 1 in adult hypertension guidance.",
            _claim(
                "c1",
                "Ramipril should be started as step 1 treatment for chronic hypertension in pregnancy because ACE inhibitors are first-line in adults.",
                Verdict.NEUTRAL,
                Scope("pregnant women", "chronic hypertension in pregnancy", "maternity care"),
                ng136_step1,
                FailureMode.POPULATION_MISMATCH,
                FailureMode.PREGNANCY,
                FailureMode.CONTRAINDICATION,
            ),
            "Adult step-1 ACE/ARB offer does not apply in pregnancy; abstain, do not refute from NG136. Use NG133.",
        ),
        _case(
            "amfv-v0-pm-04",
            Stratum.POPULATION_MISMATCH,
            InputKind.MODEL_OUTPUT,
            "Apply the NG253 suspected-sepsis pathway for people aged 16 or over to a febrile 3-year-old in ED.",
            _claim(
                "c1",
                "The NG253 suspected-sepsis pathway for people aged 16 or over should be applied to a 3-year-old.",
                Verdict.NEUTRAL,
                Scope("children aged 3 years", "suspected sepsis", "emergency care"),
                ng253_overview,
                FailureMode.POPULATION_MISMATCH,
                FailureMode.PEDIATRICS,
            ),
            "NG253 excludes under 16s and pregnancy.",
        ),
        _case(
            "amfv-v0-pm-05",
            Stratum.POPULATION_MISMATCH,
            InputKind.MODEL_OUTPUT,
            "Use the adult CKD clinic BP target below 140/90 mmHg for a neonate with reduced eGFR.",
            _claim(
                "c1",
                "A neonate with CKD should be treated to an adult clinic blood pressure target below 140/90 mmHg.",
                Verdict.NEUTRAL,
                Scope("neonates", "CKD", "neonatal care"),
                ng203_bp,
                FailureMode.POPULATION_MISMATCH,
                FailureMode.PEDIATRICS,
                FailureMode.RENAL_DOSING,
            ),
            "NG203 adult BP targets do not apply to neonates.",
        ),
        _case(
            "amfv-v0-pm-06",
            Stratum.POPULATION_MISMATCH,
            InputKind.MODEL_OUTPUT,
            "Calculate QRISK3 in a 14-year-old with obesity to decide on a statin.",
            _claim(
                "c1",
                "QRISK3 should be used to estimate CVD risk in a 14-year-old.",
                Verdict.NEUTRAL,
                Scope("children aged 14 years", "primary prevention of CVD", "paediatric care"),
                ng238_qrisk,
                FailureMode.POPULATION_MISMATCH,
                FailureMode.PEDIATRICS,
            ),
            "QRISK3 is validated 25-84.",
        ),
        _case(
            "amfv-v0-pm-07",
            Stratum.POPULATION_MISMATCH,
            InputKind.MODEL_OUTPUT,
            "After a myocardial infarction in a 16-year-old, offer atorvastatin 80 mg as in adult secondary prevention.",
            _claim(
                "c1",
                "Atorvastatin 80 mg should be offered for secondary prevention after myocardial infarction in a 16-year-old.",
                Verdict.NEUTRAL,
                Scope("adolescents aged 16 years", "secondary prevention of CVD", "cardiology"),
                ng238_secondary,
                FailureMode.POPULATION_MISMATCH,
                FailureMode.PEDIATRICS,
                FailureMode.DOSAGE,
            ),
            "Adult 80 mg secondary-prevention dose is out of scope.",
        ),
        _case(
            "amfv-v0-pm-08",
            Stratum.POPULATION_MISMATCH,
            InputKind.MODEL_OUTPUT,
            "Manage a 5-year-old with type 1 diabetes using the adult type 2 diabetes guideline because both are diabetes.",
            _claim(
                "c1",
                "A 5-year-old with type 1 diabetes should be managed using the adult type 2 diabetes guideline.",
                Verdict.NEUTRAL,
                Scope("children aged 5 years with type 1 diabetes", "type 1 diabetes", "paediatric diabetes"),
                ng28_overview,
                FailureMode.POPULATION_MISMATCH,
                FailureMode.PEDIATRICS,
            ),
            "NG28 is adults with T2DM; child T1DM is NG18.",
        ),
        _case(
            "amfv-v0-tm-01",
            Stratum.TEMPORAL,
            InputKind.MODEL_OUTPUT,
            "NG51 remains the current NICE guideline for suspected sepsis across all ages, including adults, children, and pregnancy.",
            _claim(
                "c1",
                "NG51 is the current NICE guideline for suspected sepsis in all age groups.",
                Verdict.STRONG_CONTRADICTION,
                Scope("people with suspected sepsis", "suspected sepsis", "acute care"),
                ng253_overview,
                FailureMode.GUIDELINE_UPDATE,
            ),
            "NG51 replaced by NG253/NG254/NG255.",
        ),
        _case(
            "amfv-v0-tm-02",
            Stratum.TEMPORAL,
            InputKind.MODEL_OUTPUT,
            "Follow CG181 as the current NICE lipid-modification guideline for primary prevention.",
            _claim(
                "c1",
                "CG181 is the current NICE guideline for lipid modification and CVD risk assessment.",
                Verdict.STRONG_CONTRADICTION,
                adults_cvd,
                ng238_overview,
                FailureMode.GUIDELINE_UPDATE,
            ),
            "CG181 replaced by NG238.",
        ),
        _case(
            "amfv-v0-tm-03",
            Stratum.TEMPORAL,
            InputKind.MODEL_OUTPUT,
            "QRISK2 is the NICE-recommended tool for 10-year CVD risk in adults without CVD.",
            _claim(
                "c1",
                "QRISK2 is the current NICE-recommended tool for 10-year CVD risk in adults without CVD.",
                Verdict.STRONG_CONTRADICTION,
                adults_cvd,
                ng238_qrisk,
                FailureMode.GUIDELINE_UPDATE,
            ),
            "NG238 recommends QRISK3.",
        ),
        _case(
            "amfv-v0-tm-04",
            Stratum.TEMPORAL,
            InputKind.MODEL_OUTPUT,
            "CG127 is still the current NICE hypertension guideline for adults.",
            _claim(
                "c1",
                "CG127 is the current NICE guideline for hypertension in adults.",
                Verdict.STRONG_CONTRADICTION,
                adults_htn,
                ng136_overview,
                FailureMode.GUIDELINE_UPDATE,
            ),
            "CG127 replaced by NG136.",
        ),
        _case(
            "amfv-v0-tm-05",
            Stratum.TEMPORAL,
            InputKind.MODEL_OUTPUT,
            "Older UK practice of routinely offering aspirin for primary prevention remains NICE's current recommendation.",
            _claim(
                "c1",
                "NICE currently recommends routinely offering aspirin for primary prevention of CVD.",
                Verdict.STRONG_CONTRADICTION,
                adults_cvd,
                ng238_aspirin,
                FailureMode.GUIDELINE_UPDATE,
                FailureMode.CONTRAINDICATION,
            ),
            "January 2023: do not routinely offer.",
        ),
        _case(
            "amfv-v0-tm-06",
            Stratum.TEMPORAL,
            InputKind.MODEL_OUTPUT,
            "Use CG87 as the current NICE guideline for type 2 diabetes in adults.",
            _claim(
                "c1",
                "CG87 is the current NICE guideline for type 2 diabetes in adults.",
                Verdict.STRONG_CONTRADICTION,
                adults_t2d,
                ng28_overview,
                FailureMode.GUIDELINE_UPDATE,
            ),
            "CG66/CG87 replaced by NG28.",
        ),
        _case(
            "amfv-v0-tm-07",
            Stratum.TEMPORAL,
            InputKind.MODEL_OUTPUT,
            "CG182 remains the current NICE CKD assessment guideline.",
            _claim(
                "c1",
                "CG182 is the current NICE guideline for CKD assessment and management.",
                Verdict.STRONG_CONTRADICTION,
                adults_ckd,
                ng203_overview,
                FailureMode.GUIDELINE_UPDATE,
            ),
            "CG182 replaced by NG203.",
        ),
        _case(
            "amfv-v0-tm-08",
            Stratum.TEMPORAL,
            InputKind.MODEL_OUTPUT,
            "Follow CG107 as the current NICE guideline for hypertension in pregnancy.",
            _claim(
                "c1",
                "CG107 is the current NICE guideline for hypertension in pregnancy.",
                Verdict.STRONG_CONTRADICTION,
                Scope("pregnant women", "hypertension in pregnancy", "maternity care"),
                ng133_overview,
                FailureMode.GUIDELINE_UPDATE,
                FailureMode.PREGNANCY,
            ),
            "CG107 replaced by NG133.",
        ),
    ]


def write_gold(path: Path = GOLD_V0_PATH) -> int:
    """Validate and write v0.jsonl.

    Args:
        path: Output JSONL path (default: GOLD_V0_PATH).
    """
    gold = validate_gold_set(cases())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        return dump_jsonl((case_to_dict(case) for case in gold), handle)


if __name__ == "__main__":
    count = write_gold()
    print(f"wrote {count} cases to {GOLD_V0_PATH}")
