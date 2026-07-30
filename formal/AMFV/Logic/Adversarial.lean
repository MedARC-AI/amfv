set_option autoImplicit false

namespace AMFV.Logic.Adversarial

abbrev Gate (α : Type) := α → Bool

def Blindspot {α : Type} (gate unsafePred : Gate α) : Prop :=
  ∃ candidate, unsafePred candidate = true ∧ gate candidate = true

def Sound {α : Type} (gate unsafePred : Gate α) : Prop :=
  ∀ candidate, gate candidate = true → unsafePred candidate = false

theorem witnessed_blindspot_refutes_sound {α : Type} {gate unsafePred : Gate α}
    (witness : Blindspot gate unsafePred) : ¬Sound gate unsafePred := by
  intro sound
  obtain ⟨candidate, unsafeTrue, admitted⟩ := witness
  have unsafeFalse := sound candidate admitted
  simp_all

def hardened {α : Type} (gate unsafePred : Gate α) : Gate α :=
  fun candidate => gate candidate && !unsafePred candidate

theorem hardened_gate_rejects_unsafe {α : Type} (gate unsafePred : Gate α) :
    Sound (hardened gate unsafePred) unsafePred := by
  intro candidate admitted
  simp [hardened] at admitted
  exact admitted.2

end AMFV.Logic.Adversarial
