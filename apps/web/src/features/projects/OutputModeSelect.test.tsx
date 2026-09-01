import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OutputModeSelect } from "./OutputModeSelect";

function device(deviceId: string, label: string): MediaDeviceInfo {
  return { deviceId, label, kind: "audiooutput", groupId: "g" } as MediaDeviceInfo;
}

function renderSelect(props: Partial<React.ComponentProps<typeof OutputModeSelect>> = {}) {
  return render(
    <OutputModeSelect
      value="native"
      onChange={vi.fn()}
      nativeSupported
      devices={[]}
      deviceId=""
      onDeviceChange={vi.fn()}
      spatialProfile="studio"
      onSpatialProfileChange={vi.fn()}
      transauralProfile="stereo"
      onTransauralProfileChange={vi.fn()}
      onAppleHeadTrackingChange={vi.fn()}
      {...props}
    />,
  );
}

describe("OutputModeSelect", () => {
  it("names the native mode 'Stereo' on a two-channel layout", () => {
    renderSelect({ nativeOnly: true });
    expect(screen.getByRole("button", { name: /Preview output mode: Stereo/ })).toBeInTheDocument();
  });

  it("keeps the 'Native' name on a multichannel layout", () => {
    renderSelect();
    expect(screen.getByRole("button", { name: /Preview output mode: Native/ })).toBeInTheDocument();
  });

  it("hides the device picker when only one real output exists", () => {
    // Chrome's synthetic `default` entry aliases the real device, so this is
    // one output, not two.
    renderSelect({ devices: [device("default", "Default - Speakers"), device("abc", "Speakers")] });
    expect(screen.queryByRole("combobox", { name: "Output device" })).not.toBeInTheDocument();
  });

  it("shows the device picker once there is a real choice", () => {
    renderSelect({ devices: [device("abc", "Speakers"), device("def", "Headphones")] });
    const picker = screen.getByRole("combobox", { name: "Output device" });
    expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "System default",
      "Speakers",
      "Headphones",
    ]);
  });

  it("shows Apple Spatial only when the desktop capability is available", () => {
    const view = renderSelect();
    fireEvent.click(screen.getByRole("button", { name: /Preview output mode/ }));
    expect(screen.queryByRole("button", { name: "Apple Spatial" })).not.toBeInTheDocument();
    view.unmount();

    renderSelect({ appleSpatialAvailable: true });
    fireEvent.click(screen.getByRole("button", { name: /Preview output mode/ }));
    expect(screen.getByRole("button", { name: /^Apple Spatial/ })).toBeInTheDocument();
  });

  it("selects Apple Spatial head tracking from its submenu", () => {
    const onChange = vi.fn();
    const onAppleHeadTrackingChange = vi.fn();
    renderSelect({ appleSpatialAvailable: true, onChange, onAppleHeadTrackingChange });

    fireEvent.click(screen.getByRole("button", { name: /Preview output mode/ }));
    fireEvent.mouseEnter(screen.getByRole("button", { name: /^Apple Spatial/ }));
    fireEvent.click(screen.getByRole("button", { name: "Head tracking off" }));

    expect(onAppleHeadTrackingChange).toHaveBeenCalledWith(false);
    expect(onChange).toHaveBeenCalledWith("apple_spatial");
  });

  it("uses the macOS system output instead of a desktop device picker", () => {
    renderSelect({ systemOutput: true, devices: [device("abc", "Speakers"), device("def", "Headphones")] });
    expect(screen.getByText("System output")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Output device" })).not.toBeInTheDocument();
  });
});
