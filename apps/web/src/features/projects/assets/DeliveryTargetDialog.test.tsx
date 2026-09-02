import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { defaultProjectManifest, normalizeManifest } from "@/lib/manifest";
import { DeleteDeliveryTargetDialog, DeliveryTargetDialog } from "./DeliveryTargetDialog";

describe("DeliveryTargetDialog", () => {
  it("applies the Dolby Atmos Music ADM-BWF preset", async () => {
    const onCreate = vi.fn(async () => undefined);
    const { rerender } = render(<DeliveryTargetDialog open configuration={null} initial={defaultProjectManifest} onOpenChange={vi.fn()} onCreate={onCreate} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Delivery profile" }), "atmos-music");
    rerender(<DeliveryTargetDialog open configuration={null} initial={normalizeManifest({})} onOpenChange={vi.fn()} onCreate={onCreate} />);
    expect(screen.getByRole("combobox", { name: "Delivery profile" })).toHaveValue("atmos-music");
    expect(screen.getByRole("combobox", { name: "Target channel layout" })).toHaveValue("7.1.2");
    expect(screen.getByRole("combobox", { name: "Format" })).toHaveValue("adm-bwf");
    expect(screen.getByRole("spinbutton", { name: "Target loudness" })).toHaveValue(-18);
    expect(screen.getByRole("spinbutton", { name: "Maximum true peak" })).toHaveValue(-1);

    await userEvent.click(screen.getByRole("button", { name: "Create target" }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      mixing: expect.objectContaining({ channel_layout: "7.1.2" }),
      format: expect.objectContaining({ type: "adm-bwf", codec: "wav_pcm", subtype: "PCM_24", sample_rate: 48000, delivery_profile: "atmos-music" }),
      mastering: expect.objectContaining({ loudness: expect.objectContaining({ target_preset: null, target: -18, max_tp: -1 }) }),
    }));
  });
});

describe("DeleteDeliveryTargetDialog", () => {
  it("requires confirmation before deleting", async () => {
    const onConfirm = vi.fn(async () => undefined);
    render(<DeleteDeliveryTargetDialog open target="Dolby Atmos Music ADM-BWF · 7.1.2" onOpenChange={vi.fn()} onConfirm={onConfirm} />);

    expect(onConfirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Delete target" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});
