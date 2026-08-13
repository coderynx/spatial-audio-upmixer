/** Fallbacks for `GET /api/v1/configuration`'s `choices`, mirroring core's
 * `FORMAT_MAP` and `manifest/validate.py`'s `format.type` choices. */

export const CHANNEL_LAYOUTS = ["stereo", "5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"];

export const OUTPUT_TYPES = ["wav", "adm-bwf", "binaural", "transaural"];

export const STEREO_LAYOUT = "stereo";

/** Two-channel delivery: no centre, surrounds, height or LFE, so no bed
 * collapse (binaural/transaural/downmix) and no ADM-BWF master. */
export function isStereoLayout(layout: string | undefined): boolean {
  return layout === STEREO_LAYOUT;
}

/** Retarget a delivery type the layout cannot carry, so switching layout does
 * not leave `format.type` on a value the server would reject. Mirrors
 * `_delivery_type_for_layout` in `apps/api`, which is authoritative for
 * projects; bed-only types (binaural/transaural) are handled there since the
 * bed lists come from the server. */
export function deliveryTypeForLayout(layout: string, type: string): string {
  return isStereoLayout(layout) && type !== "wav" ? "wav" : type;
}
