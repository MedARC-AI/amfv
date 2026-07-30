set_option autoImplicit false

namespace AMFV.Verification.CacheAdmission

structure Entry where
  claimKey : Nat
  scopeKey : Nat
  verifiedAt : Nat
  validUntil : Nat
  evidenceCount : Nat
  traceCount : Nat

structure Query where
  claimKey : Nat
  scopeKey : Nat
  asOf : Nat

def admissible (entry : Entry) (query : Query) : Bool :=
  entry.claimKey == query.claimKey &&
    entry.scopeKey == query.scopeKey &&
    entry.evidenceCount > 0 &&
    entry.traceCount > 0 &&
    entry.verifiedAt ≤ query.asOf &&
    query.asOf ≤ entry.validUntil

def staleEntry : Entry where
  claimKey := 7
  scopeKey := 3
  verifiedAt := 10
  validUntil := 20
  evidenceCount := 2
  traceCount := 1

def lateQuery : Query where
  claimKey := 7
  scopeKey := 3
  asOf := 21

def hashAndScopeOnly (entry : Entry) (query : Query) : Bool :=
  entry.claimKey == query.claimKey && entry.scopeKey == query.scopeKey

theorem hash_scope_only_accepts_stale_entry :
    hashAndScopeOnly staleEntry lateQuery = true := by
  decide

theorem admissible_rejects_stale_entry :
    admissible staleEntry lateQuery = false := by
  decide

end AMFV.Verification.CacheAdmission
