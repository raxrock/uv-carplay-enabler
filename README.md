# Ultraviolette CarPlay Enabler

Enable **wireless Apple CarPlay** (and Android Auto) on the Ultraviolette
motorcycle dash. The dash runs on the ZKSWE **FlyThings V2.1** platform
(Allwinner V85x, model `Zkswe_V85X_SPINOR`).

> [!WARNING]
> **This is not an official Ultraviolette procedure.** It changes a hidden
> setting on the dash over adb. Do it only with the bike parked. The change can
> be undone. You use these scripts at your own risk.

---

## Contents

- [How it works](#how-it-works)
- [Windows app (no Python needed)](#windows-app-no-python-needed)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Verify CarPlay works](#verify-carplay-works)
- [Undo the change](#undo-the-change)
- [Reset to factory default](#reset-to-factory-default)
- [Troubleshooting](#troubleshooting)
- [Notes and warnings](#notes-and-warnings)
- [Files in this repo](#files-in-this-repo)

---

## How it works

Wireless CarPlay needs the dash to run its own Wi-Fi hotspot. The iPhone joins
that hotspot. The dash's **Link** screen starts that hotspot and keeps it up
only when the hidden setting **`uv_app_use`** is `true`.

On affected bikes, `uv_app_use` is missing from `/data/zkapp.cfg`. Without it,
the dash starts the hotspot and then shuts it down about 0.2 s later.

The fix adds one key to the config file:

```json
"uv_app_use": true
```

No other setting changes, and no firmware or binary is modified.

---

## Requirements

**Bike:** parked, ignition on, side stand down. The dash screen goes blank for
a few seconds while its UI restarts.

**Network:** the dash must accept **adb over TCP port 5555**. The dash and your
computer must be on the same Wi-Fi network. This repo does not cover how to
turn on adb on a dash that does not already expose it.

**Computer:** pick the script version that fits your OS. Both versions do the
same thing and take the same flags.

| Version | OS | Needs |
|---|---|---|
| `*.sh` | macOS, Linux | `bash`, `python3`, `curl`, `unzip`, `nc` |
| `*.py` | Windows, macOS, Linux | `python3` only |

**adb:** the scripts download Android **platform-tools r28** on the first run.
Newer adb versions break file transfer with this dash's old adbd, so r28 is
pinned. You do not need to install adb yourself.

---

## Windows app (no Python needed)

1. Download `UVCarPlay.exe` from the
   [Releases](https://github.com/raxrock/uv-carplay-enabler/releases) page.
2. Run it. The app is not code-signed, so Windows may show "Windows protected
   your PC". Click **More info**, then **Run anyway**.
3. Enter the dash IP address, or leave it empty to auto-detect.
4. Click **Preview change** to see what will change, then **Enable CarPlay**.

The buttons do the same as the scripts: **Verify: start** and **Verify: check**
run the [verification](#verify-carplay-works), and **Undo** restores the latest
backup. On first run the app downloads adb, so the PC needs internet once.
Windows may also ask to allow adb through the firewall: allow it on private
networks.

The app stores adb and backups in `%LOCALAPPDATA%\UVCarPlay`.

---

## Quick start

```sh
git clone https://github.com/raxrock/uv-carplay-enabler.git
cd uv-carplay-enabler

# macOS / Linux
chmod +x enable_carplay.sh verify_carplay.sh
./enable_carplay.sh --dry-run     # preview the change, write nothing
./enable_carplay.sh               # apply the fix

# Windows (or any OS with Python)
python enable_carplay.py --dry-run
python enable_carplay.py
```

Then set up the dash for CarPlay:

1. On the dash, forget the saved home Wi-Fi network, or turn dash Wi-Fi off.
   See [Wi-Fi conflict](#notes-and-warnings).
2. Open the **Link** screen on the dash.
3. On the iPhone, connect to the dash through CarPlay.

---

## Usage

### `enable_carplay` flags

| Flag | Action |
|---|---|
| *(none)* | Find the dash on the LAN and apply the fix. |
| `--ip <addr>` | Use this dash IP and skip the LAN scan. Works with any other flag. |
| `--dry-run` | Show the config diff. Write nothing. |
| `--restore` | Put back the most recent local backup. |
| `--self-destruct` | Delete the script after a successful apply. |
| `--help` | Print the built-in help. |

Examples:

```sh
./enable_carplay.sh --ip 192.168.1.16
python enable_carplay.py --ip 192.168.1.16 --dry-run
```

### What the apply step does

1. Checks prerequisites and downloads the pinned adb if it is missing.
2. Finds the dash, by LAN scan or `--ip`, and confirms its model is
   `Zkswe_V85X_SPINOR`. The scripts refuse to act on any other device.
3. Backs up `/data/zkapp.cfg` on the dash (`/data/zkapp.cfg.bak`) and on your
   computer (a timestamped copy).
4. Stops if `uv_app_use` is already `true`.
5. Adds `"uv_app_use": true` and keeps every other key unchanged.
6. Uploads the new file to `/tmp` and checks the upload. Then it stops the dash
   UI (`ctl.stop zkswe`), installs the file, and checks the written file. On a
   mismatch, it restores the backup. Last, it restarts the UI
   (`ctl.start zkswe`).
7. Confirms the key is still present after the UI restart.

The scripts run a fixed set of adb steps only. They do not take arbitrary
commands.

### Where files are stored

By default the scripts store their adb download and backups in `~/UVA/`, not
in the repo folder:

| Path | Contents |
|---|---|
| `~/UVA/tools/platform-tools/` | The pinned adb |
| `~/UVA/backup/` | Config backups and verify logs |

To use a different folder, set the `WORKDIR` environment variable:

```sh
WORKDIR="$PWD" ./enable_carplay.sh
```

---

## Verify CarPlay works

adb over Wi-Fi drops as soon as the dash starts its hotspot. For that reason,
verification has two steps, with the bike test in between:

```sh
./verify_carplay.sh --ip 192.168.1.16 --start
```

Then do this on the bike:

1. Open the Link screen.
2. Connect CarPlay from the iPhone.
3. Wait about 15 seconds.
4. Turn dash Wi-Fi back on, so your computer can reach the dash again.

Then read the result:

```sh
./verify_carplay.sh --ip 192.168.1.16 --check
```

Python: `python verify_carplay.py --ip 192.168.1.16 --start` and then
`--check`.

`--start` launches a background `logcat` recorder on the dash. It writes to
internal storage, so it keeps running after adb drops. `--check` reads that log
and prints one of these verdicts:

| Verdict | Meaning | Next step |
|---|---|---|
| **PASS** | The log shows `LYLINK_LINK_ESTABLISH wifi carplay` (or `android auto`). | None. |
| **PARTIAL** | The hotspot came up and stayed up, but no link formed. The dash side works. | Check the iPhone: Bluetooth on, CarPlay allowed. Forget the dash and pair again. |
| **FAIL** | The hotspot was torn down, or `ap still nok` repeated. This is the pre-fix behaviour. | Check that `uv_app_use` is set. Check that dash Wi-Fi was off before you opened the Link screen. |

The verdict comes from the dash's own log. PASS means the dash made the link.
The script cannot see the iPhone screen.

---

## Undo the change

```sh
./enable_carplay.sh --restore
# or
./verify_carplay.sh --ip <dash-ip> --restore
```

This restores the most recent backup from `~/UVA/backup/`. Both scripts have
`--restore`, so undo still works after `--self-destruct` deletes the enable
script. The Python scripts take the same flags.

If the local backup is gone, the dash keeps its own copy at
`/data/zkapp.cfg.bak`.

---

## Reset to factory default

To return the dash to its factory settings, **reset the dashcam module from
its own Settings menu.** This is the normal way to reset the dash, and it
needs no computer or adb.

1. Park the bike, turn the ignition on, and put the side stand down.
2. On the dash, open **Settings**.
3. Open the dashcam module's settings and choose its reset / factory reset
   option.
4. Confirm, then wait for the dash to restart.

> [!NOTE]
> Nobody has tested whether this reset removes the `uv_app_use` key. After the
> reset, run `./enable_carplay.sh --dry-run`. If the output says the key is
> already `true`, run `./enable_carplay.sh --restore` to remove it.

A reset may also clear the saved Wi-Fi, Bluetooth pairings and dashcam
preferences. Note them down first if you want to set them up again.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `dash not found on LAN; pass --ip` | Find the dash IP in your router's client list, then use `--ip`. |
| `could not determine LAN subnet; pass --ip` | Use `--ip` with the dash IP. |
| adb connects but file transfer fails | Use the pinned r28 adb in `~/UVA/tools/`, not a system adb. |
| CarPlay still drops after the fix | Turn dash Wi-Fi off (or forget the home network) before you open the Link screen. |
| adb stops working after you open the Link screen | This is expected. Turn dash Wi-Fi back on to reconnect. |

---

## Notes and warnings

- **Wi-Fi conflict.** The dash has one Wi-Fi radio. While the dash is joined
  to a home network, it cannot run the CarPlay hotspot. For CarPlay, forget the
  saved network or turn dash Wi-Fi off. For the same reason, opening the Link
  screen drops adb over Wi-Fi.
- **Do not run an OTA update while the dash shows "The Ultraviolette dashcam
  software you are using is not genuine."** That warning comes from the server
  and has nothing to do with this fix. An OTA update in that state could fail
  or wipe `/data`.
- **Firmware updates** may reset `/data/zkapp.cfg` or change this logic. Check
  again after each update.
- **Blind-spot detection (BSD) is locked separately.** It needs a licence tied
  to the hardware (`HZ_auth`, chip ID, `/data/hzbsd/raw_data`), not a config
  flag, so no setting change can turn it on.
- **Tested scope.** The fix was verified on one dash only: system version
  `2.6.2`, app `V3.0.25.20260323`. Other firmware may behave differently. Keep
  your backup.

---

## Files in this repo

| File | Contents |
|---|---|
| [`enable_carplay.sh`](enable_carplay.sh) / [`enable_carplay.py`](enable_carplay.py) | Apply the fix. Also `--dry-run`, `--restore`, `--self-destruct`. |
| [`verify_carplay.sh`](verify_carplay.sh) / [`verify_carplay.py`](verify_carplay.py) | Runtime PASS/FAIL check (`--start`, `--check`) and undo (`--restore`). |
| [`carplay_gui.py`](carplay_gui.py) | Desktop window around the Python scripts. Source of `UVCarPlay.exe`. |
| [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml) | Builds `UVCarPlay.exe` on GitHub. A `v*` tag publishes it as a Release. |

---

## Disclaimer

This project has no connection to Ultraviolette Automotive or ZKSWE, and
neither company endorses it. All trademarks belong to their owners. Changing
your vehicle's software may affect your warranty.
