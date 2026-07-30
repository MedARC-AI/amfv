set_option autoImplicit false

namespace AMFV.Scraping.UrlPolicy

inductive Scheme where
  | http
  | https
  | other
  deriving DecidableEq

inductive Authority where
  | nice
  | other
  deriving DecidableEq

structure Candidate where
  scheme : Scheme
  authority : Authority
  chapterPath : Bool
  sameGuidance : Bool
  hasCredentials : Bool
  hasNondefaultPort : Bool
  hasQuery : Bool
  hasFragment : Bool
  deriving DecidableEq

def canonicalize (candidate : Candidate) : Candidate :=
  { candidate with hasQuery := false, hasFragment := false }

def pathOnlyGate (candidate : Candidate) : Bool :=
  candidate.chapterPath

def acceptedChapter (candidate : Candidate) : Bool :=
  candidate.chapterPath &&
    candidate.sameGuidance &&
    (candidate.scheme == .http || candidate.scheme == .https) &&
    candidate.authority == .nice &&
    !candidate.hasCredentials &&
    !candidate.hasNondefaultPort

theorem canonicalize_idempotent (candidate : Candidate) :
    canonicalize (canonicalize candidate) = canonicalize candidate := by
  cases candidate
  rfl

theorem canonicalize_preserves_authority (candidate : Candidate) :
    (canonicalize candidate).authority = candidate.authority := by
  rfl

theorem accepted_chapter_has_allowed_authority (candidate : Candidate)
    (accepted : acceptedChapter candidate = true) :
    candidate.authority = .nice := by
  cases candidate with
  | mk scheme authority chapterPath sameGuidance hasCredentials hasNondefaultPort hasQuery hasFragment =>
      cases authority with
      | nice => rfl
      | other =>
          cases scheme <;>
            cases chapterPath <;>
              cases sameGuidance <;>
                cases hasCredentials <;>
                  cases hasNondefaultPort <;>
                    cases accepted

theorem accepted_chapter_matches_guidance (candidate : Candidate)
    (accepted : acceptedChapter candidate = true) :
    candidate.sameGuidance = true := by
  cases candidate with
  | mk scheme authority chapterPath sameGuidance hasCredentials hasNondefaultPort hasQuery hasFragment =>
      cases sameGuidance with
      | false =>
          cases scheme <;>
            cases authority <;>
              cases chapterPath <;>
                cases hasCredentials <;>
                  cases hasNondefaultPort <;>
                    cases accepted
      | true => rfl

theorem accepted_chapter_respects_source_boundary (candidate : Candidate)
    (accepted : acceptedChapter candidate = true) :
    candidate.authority = .nice ∧ candidate.sameGuidance = true :=
  ⟨accepted_chapter_has_allowed_authority candidate accepted,
    accepted_chapter_matches_guidance candidate accepted⟩

def offAuthorityChapter : Candidate where
  scheme := .https
  authority := .other
  chapterPath := true
  sameGuidance := true
  hasCredentials := false
  hasNondefaultPort := false
  hasQuery := false
  hasFragment := false

theorem path_only_gate_has_off_authority_blindspot :
    pathOnlyGate offAuthorityChapter = true ∧ offAuthorityChapter.authority = .other := by
  decide

theorem hardened_gate_closes_off_authority_blindspot :
    acceptedChapter offAuthorityChapter = false := by
  decide

end AMFV.Scraping.UrlPolicy
