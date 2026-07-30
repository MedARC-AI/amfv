set_option autoImplicit false

namespace AMFV.Scraping.ExtractionReceipt

structure Receipt where
  discovered : Nat
  retained : Nat
  omitted : Nat

def Accounted (receipt : Receipt) : Prop :=
  receipt.discovered = receipt.retained + receipt.omitted

def silentOmission : Receipt where
  discovered := 3
  retained := 1
  omitted := 1

theorem silent_omission_is_not_accounted :
    ¬Accounted silentOmission := by
  change ¬(3 = 2)
  decide

theorem accounted_receipt_matches_disposition_count (receipt : Receipt)
    (isAccounted : Accounted receipt) :
    receipt.discovered = receipt.retained + receipt.omitted :=
  isAccounted

end AMFV.Scraping.ExtractionReceipt
