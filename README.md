# DualSense Mic Mute for Windows

Make the **mute button** on a PS5 DualSense controller work on Windows the way it does on a PS5:
press it and your mic is muted, **and the button lights up orange**. Press again: mic is live, light is off.

[Українська версія](README.uk.md)

## The problem

You plug a DualSense into a Windows PC. The mic button does nothing useful:

- Windows has no idea what the button is for.
- Steam Input and DS4Windows can map it to a key, but **the LED never turns on**, so you can't see whether you're muted.
- Existing tools that can light it up either need a virtual gamepad driver (ViGEm) or are paid.

## What this does

A small background script that:

1. Watches the mute button (USB and Bluetooth).
2. Mutes the microphone in Windows:
   - **USB**: mutes the controller's own mic, both inside the controller (hardware) and in Windows (`Headset Microphone (DualSense)`);
   - **Bluetooth**: Windows can't use the DualSense mic over Bluetooth, so it mutes your **default recording device** (e.g. your headset) instead.
3. Turns the button LED on (muted) or off (live).
4. Resends the LED state every 2 seconds, because Steam and some games overwrite it.

- Pure Python standard library (`ctypes` + Windows `hid.dll`, SetupAPI, Core Audio). **Nothing to `pip install`, no drivers.**
- Doesn't take over the controller: games and Steam keep working as usual.
- Reconnects automatically when the controller is unplugged or turned off; the mute state is kept.
- On exit/restart, any mic it muted is unmuted again, so you never get stuck muted.

## Requirements

- Windows 10 or 11
- Python 3.8+ from [python.org](https://www.python.org/downloads/) (needs `pythonw.exe` for the silent background mode)
- DualSense or DualSense Edge, connected by USB cable (data cable, not charge-only) or Bluetooth

## Install

1. Download this repo (**Code → Download ZIP**) and unpack it somewhere permanent, e.g. `C:\Tools\dualsense-mic-mute`.
2. Open PowerShell in that folder and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File install-autostart.ps1
   ```

   This adds a "DualSense Mute" shortcut to your Startup folder and starts it right away, without a window.
3. Press the mute button. The LED should turn orange.

## Usage

| What | How |
|------|-----|
| Test interactively | stop the background copy (see [Uninstall](#uninstall)), then `python dualsense_mute.py --probe` and press the button |
| Unmute everything and turn the LED off | `python dualsense_mute.py --release` (only while the background copy is stopped) |
| Log | `dualsense_mute.log` next to the script (`MUTED` / `LIVE` on every press) |
| Current state | `state.json` (`"muted": true/false`) |

Only one copy runs at a time; a second launch exits by itself.

## Uninstall

In the script folder run:

```powershell
powershell -ExecutionPolicy Bypass -File install-autostart.ps1 -Remove
```

This stops the script, removes it from startup, **unmutes any mic it muted** and turns the LED off.
Then just delete the folder. Nothing else is left on the system: no registry entries, no drivers, no services.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| LED doesn't react | Open `dualsense_mute.log`. No `DualSense connected over ...` line means the script isn't running; run `install-autostart.ps1` again. |
| LED is on but people still hear you | Your app (Discord, OBS, etc.) uses a different mic. Over USB it should use the DualSense mic; over Bluetooth, the Windows default mic. |
| Over Bluetooth the wrong mic gets muted | The button mutes the **default** mic: Settings → System → Sound → Input, pick the right one. |
| Windows switched your sound output to `Speakers (DualSense)` | Settings → System → Sound → Output, choose your headphones again. |
| LED flickers or turns off in a game | That game rewrites the LED often; the script fixes it within 2 seconds. |

## How it works

The script opens the controller as a plain HID device (shared access, so nothing else breaks):

- reads input reports (USB `0x01`, Bluetooth `0x31`) and watches the mute button bit;
- writes output reports (USB `0x02`, Bluetooth `0x31` with CRC32) that set the mute LED and the hardware mic mute;
- mutes Windows audio endpoints through the Core Audio `IAudioEndpointVolume` COM interface.

Report layouts follow the Linux kernel driver [`hid-playstation`](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-playstation.c).

## Tested on

- Windows 11, DualSense over Bluetooth and USB.
- DualSense Edge is supported by product ID but hasn't been tested. Reports are welcome.

## License

[MIT](LICENSE)
