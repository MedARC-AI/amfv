import AMFV.Verification.Verdict

set_option autoImplicit false

namespace AMFV.Verification.Receipt

open AMFV.Verification.Verdict

structure Receipt where
  knownEvidence : List Nat
  supporting : List Nat
  contradicting : List Nat
  missingContext : Bool
  verdict : Verdict

def containsNat (identifier : Nat) : List Nat → Bool
  | [] => false
  | candidate :: rest => Nat.beq identifier candidate || containsNat identifier rest

def referencesKnownEvidence (receipt : Receipt) : Bool :=
  receipt.supporting.all (fun identifier => containsNat identifier receipt.knownEvidence) &&
    receipt.contradicting.all (fun identifier => containsNat identifier receipt.knownEvidence)

def evidenceDisjoint (receipt : Receipt) : Bool :=
  receipt.supporting.all (fun identifier => !containsNat identifier receipt.contradicting)

def noDuplicates : List Nat → Bool
  | [] => true
  | identifier :: rest => !containsNat identifier rest && noDuplicates rest

def evidenceUnique (receipt : Receipt) : Bool :=
  noDuplicates receipt.supporting && noDuplicates receipt.contradicting

def extremeVerdictHasWitness (receipt : Receipt) : Bool :=
  match receipt.verdict with
  | .stronglySupported =>
      !receipt.supporting.isEmpty && receipt.contradicting.isEmpty && !receipt.missingContext
  | .stronglyUnsubstantiated =>
      !receipt.contradicting.isEmpty && receipt.supporting.isEmpty && !receipt.missingContext
  | .weaklySupported => true
  | .unclear => true
  | .weaklyUnsubstantiated => true

def directionalVerdictHasWitness (receipt : Receipt) : Bool :=
  match receipt.verdict with
  | .weaklySupported => !receipt.supporting.isEmpty
  | .weaklyUnsubstantiated => !receipt.contradicting.isEmpty
  | .stronglySupported => true
  | .unclear => true
  | .stronglyUnsubstantiated => true

def valid (receipt : Receipt) : Bool :=
  referencesKnownEvidence receipt &&
    evidenceDisjoint receipt &&
    evidenceUnique receipt &&
    extremeVerdictHasWitness receipt &&
    directionalVerdictHasWitness receipt

def unknownEvidenceReceipt : Receipt where
  knownEvidence := [1]
  supporting := [2]
  contradicting := []
  missingContext := false
  verdict := .stronglySupported

def overlappingEvidenceReceipt : Receipt where
  knownEvidence := [1]
  supporting := [1]
  contradicting := [1]
  missingContext := false
  verdict := .unclear

def unwitnessedStrongReceipt : Receipt where
  knownEvidence := []
  supporting := []
  contradicting := []
  missingContext := true
  verdict := .stronglySupported

def duplicateEvidenceReceipt : Receipt where
  knownEvidence := [1]
  supporting := [1, 1]
  contradicting := []
  missingContext := false
  verdict := .weaklySupported

theorem unknown_evidence_invalid : valid unknownEvidenceReceipt = false := by
  rfl

theorem overlapping_evidence_invalid : valid overlappingEvidenceReceipt = false := by
  rfl

theorem strong_verdict_requires_witness : valid unwitnessedStrongReceipt = false := by
  rfl

theorem duplicate_evidence_invalid : valid duplicateEvidenceReceipt = false := by
  rfl

theorem known_counterexamples_invalid :
    valid unknownEvidenceReceipt = false ∧
      valid overlappingEvidenceReceipt = false ∧
      valid duplicateEvidenceReceipt = false ∧
      valid unwitnessedStrongReceipt = false := by
  exact ⟨unknown_evidence_invalid, overlapping_evidence_invalid, duplicate_evidence_invalid,
    strong_verdict_requires_witness⟩

end AMFV.Verification.Receipt
