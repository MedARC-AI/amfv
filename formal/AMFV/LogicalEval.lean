import AMFV
import Lean

set_option autoImplicit false

namespace AMFV.LogicalEval

open AMFV.Verification
open Lean

def schemaVersion := "amfv.logic.v1"
def maxLogicalTime : Nat := 253402300799

def getField (json : Json) (name : String) : Except String Json :=
  json.getObjVal? name

def getString (json : Json) (name : String) : Except String String := do
  (← getField json name).getStr?

def getNat (json : Json) (name : String) : Except String Nat := do
  (← getField json name).getNat?

def getLogicalTime (json : Json) (name : String) : Except String Nat := do
  let value ← getNat json name
  if value ≤ maxLogicalTime then
    pure value
  else
    throw s!"logical timestamp {name} exceeds the supported UTC range"

def getBool (json : Json) (name : String) : Except String Bool := do
  (← getField json name).getBool?

def getNatList (json : Json) (name : String) : Except String (List Nat) := do
  let values ← (← getField json name).getArr?
  values.toList.mapM Json.getNat?

def parseVerdict (value : String) : Except String Verdict.Verdict :=
  match value with
  | "strongly_supported" => .ok .stronglySupported
  | "weakly_supported" => .ok .weaklySupported
  | "unclear" => .ok .unclear
  | "weakly_unsubstantiated" => .ok .weaklyUnsubstantiated
  | "strongly_unsubstantiated" => .ok .stronglyUnsubstantiated
  | _ => .error s!"unknown verdict {value}"

def receiptViolations (receipt : Receipt.Receipt) : List String :=
  let unknown :=
    if Receipt.referencesKnownEvidence receipt then [] else ["unknown_evidence_id"]
  let overlap :=
    if Receipt.evidenceDisjoint receipt then [] else ["overlapping_evidence_id"]
  let duplicate :=
    if Receipt.evidenceUnique receipt then [] else ["duplicate_evidence_id"]
  let extreme :=
    if Receipt.extremeVerdictHasWitness receipt then [] else ["extreme_verdict_without_witness"]
  let directional :=
    if Receipt.directionalVerdictHasWitness receipt then [] else ["directional_verdict_without_witness"]
  unknown ++ overlap ++ duplicate ++ extreme ++ directional

def evaluateReceipt (json : Json) : Except String (List String) := do
  let receipt : Receipt.Receipt := {
    knownEvidence := ← getNatList json "known_evidence"
    supporting := ← getNatList json "supporting"
    contradicting := ← getNatList json "contradicting"
    missingContext := ← getBool json "missing_context"
    verdict := ← parseVerdict (← getString json "verdict")
  }
  pure (receiptViolations receipt)

def cacheViolations (entry : CacheAdmission.Entry) (query : CacheAdmission.Query) : List String :=
  let claim :=
    if entry.claimKey == query.claimKey then [] else ["claim_mismatch"]
  let scope :=
    if entry.scopeKey == query.scopeKey then [] else ["scope_mismatch"]
  let evidence :=
    if entry.evidenceCount > 0 then [] else ["empty_evidence"]
  let trace :=
    if entry.traceCount > 0 then [] else ["empty_trace"]
  let future :=
    if entry.verifiedAt ≤ query.asOf then [] else ["verified_in_future"]
  let stale :=
    if query.asOf ≤ entry.validUntil then [] else ["stale_entry"]
  let interval :=
    if entry.verifiedAt ≤ entry.validUntil then [] else ["invalid_validity_interval"]
  claim ++ scope ++ evidence ++ trace ++ interval ++ future ++ stale

def evaluateCache (json : Json) : Except String (List String) := do
  let entry : CacheAdmission.Entry := {
    claimKey := ← getNat json "entry_claim_key"
    scopeKey := ← getNat json "entry_scope_key"
    verifiedAt := ← getLogicalTime json "verified_at"
    validUntil := ← getLogicalTime json "valid_until"
    evidenceCount := ← getNat json "evidence_count"
    traceCount := ← getNat json "trace_count"
  }
  let query : CacheAdmission.Query := {
    claimKey := ← getNat json "query_claim_key"
    scopeKey := ← getNat json "query_scope_key"
    asOf := ← getLogicalTime json "as_of"
  }
  pure (cacheViolations entry query)

def evaluationViolations (result : Evaluation.CaseResult) : List String :=
  let retrieval :=
    if result.retrievalHit then [] else ["retrieval_miss"]
  let verdict :=
    if result.verdictMatch then [] else ["verdict_mismatch"]
  let score :=
    if result.scorePass then [] else ["score_failure"]
  retrieval ++ verdict ++ score

def evaluateCase (json : Json) : Except String (List String) := do
  let result : Evaluation.CaseResult := {
    retrievalHit := ← getBool json "retrieval_hit"
    verdictMatch := ← getBool json "verdict_match"
    scorePass := ← getBool json "score_pass"
  }
  pure (evaluationViolations result)

def evaluate (json : Json) : Except String (List String) := do
  let schema ← getString json "schema"
  if schema != schemaVersion then
    throw s!"unsupported schema {schema}"
  match ← getString json "kind" with
  | "verifier_receipt" => evaluateReceipt json
  | "cache_admission" => evaluateCache json
  | "evaluation_case" => evaluateCase json
  | kind => throw s!"unknown logical record kind {kind}"

def response (violations : List String) : Json :=
  Json.mkObj [
    ("valid", Json.bool violations.isEmpty),
    ("violations", Json.arr (violations.toArray.map Json.str))
  ]

def errorResponse (message : String) : Json :=
  Json.mkObj [
    ("valid", Json.bool false),
    ("violations", Json.arr #[Json.str "invalid_input"]),
    ("error", Json.str message)
  ]

def evaluateLine (line : String) : Json :=
  match Json.parse line >>= evaluate with
  | .ok violations => response violations
  | .error message => errorResponse message

partial def processLines (input : IO.FS.Stream) : IO Unit := do
  let line ← input.getLine
  if line.isEmpty then
    pure ()
  else
    IO.println (Json.compress (evaluateLine line.trimAscii.toString))
    processLines input

end AMFV.LogicalEval

def main : IO Unit := do
  AMFV.LogicalEval.processLines (← IO.getStdin)
