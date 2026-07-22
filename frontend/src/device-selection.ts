export interface AudioDeviceLike {
  readonly deviceId: string;
  readonly kind: string;
  readonly label: string;
}

export interface ResolvedAudioDevices {
  readonly inputs: readonly AudioDeviceLike[];
  readonly outputs: readonly AudioDeviceLike[];
  readonly inputId: string;
  readonly outputId: string;
  readonly missingInput: boolean;
  readonly missingOutput: boolean;
}

const VIRTUAL_LOOPBACK_LABEL_RE = /CABLE|VB-Audio|VoiceMeeter|Stereo Mix|loopback/i;

export function isLikelyVirtualLoopbackDevice(device: Pick<AudioDeviceLike, "label"> | null | undefined): boolean {
  const label = device?.label?.trim() || "";
  return label.length > 0 && VIRTUAL_LOOPBACK_LABEL_RE.test(label);
}

function uniqueDevices(devices: readonly AudioDeviceLike[], kind: string): AudioDeviceLike[] {
  const seen = new Set<string>();
  return devices.filter((device) => {
    // The browser's pseudo-device named "default" is represented by the
    // explicit System Default option in the UI, so do not add it twice.
    if (device.kind !== kind || !device.deviceId || device.deviceId === "default" || seen.has(device.deviceId)) {
      return false;
    }
    seen.add(device.deviceId);
    return true;
  });
}

export function resolveAudioDevices(
  devices: readonly AudioDeviceLike[],
  savedInput: string,
  savedOutput: string,
): ResolvedAudioDevices {
  const inputs = uniqueDevices(devices, "audioinput");
  const outputs = uniqueDevices(devices, "audiooutput");
  const missingInput = savedInput !== "default" && !inputs.some((device) => device.deviceId === savedInput);
  const missingOutput = savedOutput !== "default" && !outputs.some((device) => device.deviceId === savedOutput);

  return {
    inputs,
    outputs,
    inputId: missingInput ? "default" : savedInput,
    outputId: missingOutput ? "default" : savedOutput,
    missingInput,
    missingOutput,
  };
}
