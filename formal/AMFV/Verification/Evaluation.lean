set_option autoImplicit false

namespace AMFV.Verification.Evaluation

structure CaseResult where
  retrievalHit : Bool
  verdictMatch : Bool
  scorePass : Bool

def casePass (result : CaseResult) : Bool :=
  result.retrievalHit && result.verdictMatch && result.scorePass

def twoFactorPass (result : CaseResult) : Bool :=
  result.retrievalHit && result.scorePass

def wrongVerdict : CaseResult where
  retrievalHit := true
  verdictMatch := false
  scorePass := true

theorem case_pass_implies_all_checks (result : CaseResult)
    (passed : casePass result = true) :
    result.retrievalHit = true ∧ result.verdictMatch = true ∧ result.scorePass = true := by
  simp [casePass] at passed
  exact ⟨passed.1.1, passed.1.2, passed.2⟩

theorem two_factor_gate_accepts_wrong_verdict :
    twoFactorPass wrongVerdict = true := by
  decide

theorem three_factor_gate_rejects_wrong_verdict :
    casePass wrongVerdict = false := by
  decide

end AMFV.Verification.Evaluation
