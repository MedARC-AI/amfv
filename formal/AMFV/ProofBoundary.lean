set_option autoImplicit false

namespace AMFV.ProofBoundary

structure StructuralReceipt where
  safeAuthority : Bool
  accounted : Bool

def structurallyValid (receipt : StructuralReceipt) : Bool :=
  receipt.safeAuthority && receipt.accounted

def validReceipt : StructuralReceipt where
  safeAuthority := true
  accounted := true

theorem structural_validity_does_not_force_medical_truth :
    ¬(∀ medicalTruth : Bool, structurallyValid validReceipt = true → medicalTruth = true) := by
  intro forcesTruth
  have falseIsTrue := forcesTruth false rfl
  contradiction

end AMFV.ProofBoundary
