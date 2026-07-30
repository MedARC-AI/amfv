set_option autoImplicit false

namespace AMFV.Scraping.ListingAccounting

structure Inventory where
  sourceTotal : Nat
  eligibleTotal : Nat
  emittedTotal : Nat

def HonestExactTotal (inventory : Inventory) : Prop :=
  inventory.sourceTotal = inventory.eligibleTotal

def filteredExample : Inventory where
  sourceTotal := 6
  eligibleTotal := 5
  emittedTotal := 5

theorem source_total_is_not_exact_after_filtering :
    ¬HonestExactTotal filteredExample := by
  change ¬(6 = 5)
  decide

theorem emitted_under_cap (eligible cap : Nat) :
    Nat.min eligible cap ≤ cap :=
  Nat.min_le_right eligible cap

theorem emitted_not_above_eligible (eligible cap : Nat) :
    Nat.min eligible cap ≤ eligible :=
  Nat.min_le_left eligible cap

end AMFV.Scraping.ListingAccounting
