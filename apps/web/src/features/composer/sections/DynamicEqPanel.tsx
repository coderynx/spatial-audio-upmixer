import * as React from "react";
import { FIELD_GRID, SelectField } from "@/components/forms/fields";
import { EffectPanel, titleCase } from "./EffectPanel";

/** What each preset is for, in the terms someone picking one thinks in — the
 * same treatment the bass profiles get, since "3.5 kHz, Q 1.8, −32 dB" is not
 * a choice anyone makes without knowing what it fixes. */
const PROFILE_NOTES: Record<string, string> = {
  "tame-harshness": "Softens 3-4 kHz glare, only when it flares",
  "tame-sibilance": "Catches harsh S sounds on bright mixes",
  "clear-low-mid": "Clears 250 Hz build-up on loud passages",
  "tighten-low-end": "Keeps the low end from swamping loud passages",
  "immersive-polish": "All three, gently — the usual set",
};

export function DynamicEqPanel({
  profile,
  profiles,
  onChange,
}: {
  profile: string | null;
  /** Served names; the panel never authors a preset list of its own. */
  profiles: string[] | undefined;
  onChange: (profile: string | null) => void;
}) {
  // Switching off clears the profile, which is what the manifest means by
  // "off"; remembering it restores the choice instead of resetting it — the
  // same behaviour the other profile-driven effects have.
  const remembered = React.useRef(profile);

  return (
    <EffectPanel
      title="Dynamic EQ"
      enabled={profile !== null}
      toggleDisabled={profile === null && !profiles?.length}
      onEnabledChange={(enabled) => {
        if (!enabled) {
          remembered.current = profile;
          onChange(null);
          return;
        }
        onChange(remembered.current || profiles?.[0] || null);
      }}
    >
      <div className={FIELD_GRID}>
        <SelectField
          label="Profile"
          value={profile || ""}
          disabled={!profile}
          onChange={onChange}
          options={(profiles || []).map((value) => ({
            value,
            label: titleCase(value),
          }))}
        />
      </div>
      {profile && PROFILE_NOTES[profile] && (
        <p className="text-[11px] text-muted-foreground">{PROFILE_NOTES[profile]}</p>
      )}
    </EffectPanel>
  );
}
