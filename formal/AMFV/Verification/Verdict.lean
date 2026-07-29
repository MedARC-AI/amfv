set_option autoImplicit false

namespace AMFV.Verification.Verdict

inductive Verdict where
  | stronglySupported
  | weaklySupported
  | unclear
  | weaklyUnsubstantiated
  | stronglyUnsubstantiated
  deriving DecidableEq

def score : Verdict → Int
  | .stronglySupported => 2
  | .weaklySupported => 1
  | .unclear => 0
  | .weaklyUnsubstantiated => -1
  | .stronglyUnsubstantiated => -2

def reverse : Verdict → Verdict
  | .stronglySupported => .stronglyUnsubstantiated
  | .weaklySupported => .weaklyUnsubstantiated
  | .unclear => .unclear
  | .weaklyUnsubstantiated => .weaklySupported
  | .stronglyUnsubstantiated => .stronglySupported

theorem reverse_score (verdict : Verdict) :
    score (reverse verdict) = -score verdict := by
  cases verdict <;> decide

theorem unclear_is_not_strongly_unsubstantiated :
    Verdict.unclear ≠ Verdict.stronglyUnsubstantiated := by
  decide

end AMFV.Verification.Verdict
