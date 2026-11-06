# RYUO Control — Python + Qt6 for Linux

[Español](README.md) | **English**

An independent desktop application for controlling the ASUS ROG RYUO IV LCD.
It uses PySide6 (Qt6) and this project's USB HID driver, reverse-engineered
from ASUS InfoHub and the display's Android software.

This initial version controls the LCD. It does not yet provide every InfoHub
feature: automatic video conversion, firmware updates, pump or fan curve
control, and video thumbnails are not implemented.

## Differences from the original version

The original version focused on installing the driver to send data to the LCD.
This version retains that driver and extends the project with **RYUO Control**,
a desktop application for configuring the display, managing its videos, and
administering the service on Linux.

| Area | Changes in this version |
| --- | --- |
| Graphical interface | Python application with PySide6/Qt6, available in English and Spanish. English is the default; each user's language preference is saved. |
| Sensor layout | Interactive view over an image of the AIO, with six positions, drag to swap, empty slots, and configurable colors. These are the LCD's supported slots, rather than freely positioned coordinates. |
| Preview | Local background video playback with sensor overlays on a representation of the curved screen. This is a simulation, not a capture of the real LCD. |
| Videos | Preset and playback mode selection; upload and apply custom MP4s in one operation, download files from the AIO, and delete custom videos while protecting presets. |
| Brightness | Adjustment from 0 to 100% through ADB, with USB serial selection or detection. |
| Sensors and hardware | CCD average or Tctl on AMD CPUs, package temperature on Intel CPUs, and AMD/NVIDIA GPU readings depending on available drivers and tools. |
| Configuration and service | Persistent, validated settings in `/etc/ryuo-lcd.json`; start, stop, restart, boot enablement, and log controls, with polkit authentication. |
| Installation | A single `install.sh` detects the distribution family and installs driver and application dependencies, including ADB. Covers Fedora/Nobara, Bazzite/Fedora Atomic, Arch, Ubuntu, and SUSE under the conditions below. |
| Uninstallation and diagnostics | Uninstaller with a dry run and optional system configuration purge; sensor, HID backend, and USB detection diagnostics. |

The driver continues to run as an independent service: closing the interface
does not stop sensor updates. File and brightness operations require ADB access
to the AIO, in addition to the HID communication used by the driver.
This extension focuses on the LCD and does not yet cover all ASUS InfoHub
features. Distribution support does not mean every hardware and operating
system combination has been tested on physical machines; see the limitations
and validation notes below.

## Features

- Live CPU, AMD GPU, and memory dashboard with usage history.
- AMD CPU temperature: CCD average or Tctl; Intel: package via coretemp.
- Selection of the five built-in videos; cycle, random, or single playback.
- Custom MP4 uploads and retrieval of the LCD's video list through ADB.
- Download preset and custom video folders from the interface.
- Delete custom videos with confirmation; presets are protected.
- Assign sensors to six slots, including empty slots and position swapping.
- Title and content colors.
- Local video preview with sensor overlays, playback, and pause.
- Brightness from 0 to 100% through authorized ADB.
- Service start, stop, restart, automatic startup, and logs.
- Persistent configuration in `/etc/ryuo-lcd.json`, validated and written atomically.

## Install or update

Run the unified installer from the project directory:

```bash
sudo bash ./install.sh
```

It detects the distribution using `ID` and `ID_LIKE` from `/etc/os-release`
and selects the appropriate dependencies and package manager:

| Family | Manager | Python dependencies |
| --- | --- | --- |
| Fedora, Nobara, and mutable derivatives | DNF | System packages |
| Bazzite, Fedora Atomic with rpm-ostree | rpm-ostree | Host packages; a reboot phase is required if dependencies are missing |
| Arch, EndeavourOS, Manjaro, Garuda, CachyOS | Pacman | System packages |
| Ubuntu 22.04+, Mint, Pop!_OS, KDE neon, Zorin, and recent derivatives | APT | Isolated Python environment |
| openSUSE Leap 16+, Tumbleweed, Slowroll, and mutable SUSE derivatives | Zypper | Isolated Python environment |

Installation requires systemd on the host and Python 3.10 or later. System
repositories must be enabled and accessible. Sensor support covers Ryzen/Intel
CPUs and AMD/NVIDIA GPUs depending on available drivers; installing the NVIDIA
graphics driver depends on the distribution. Older releases or architectures
without compatible binary wheels may need adaptation; the installer stops
rather than compiling Qt. SLE requires its repositories/subscriptions to
provide the dependencies. Transactional MicroOS/Aeon/Kalpa images and
non-systemd derivatives are not covered.

To inspect the plan without changing packages, files, or services:

```bash
bash ./install.sh --dry-run
# Inspect the plan for another family:
bash ./install.sh --dry-run --family=arch
```

`--family` accepts `auto` (the default), `fedora`, `bazzite`, `arch`, `ubuntu`,
and `suse`. With `--dry-run`, it lets you inspect another family; a real
installation checks that the selected family matches the host. The simulation
does not verify package availability in the target machine's repositories.
Unknown distributions are rejected before installing packages.

All families use `install.sh`. Keep the project directory intact: the entry
point loads `scripts/install-common.sh` and `scripts/install-platforms.sh`
and copies the application's files and assets from the repository.

After installation, open **RYUO Control** from the application menu or run:

```bash
/usr/local/bin/ryuo-control
```

Run the interface as your regular user. Administrative actions request
authentication through polkit. Your desktop session needs an active polkit
agent, as is usual with KDE/GNOME. With a minimal window manager, install and
enable the appropriate agent for your desktop.

The installer uses `scripts/install-common.sh` to copy files, preserve
`/etc/ryuo-lcd.json` and service overrides, restore SELinux labels when the tool
is available, and enable/restart the service. It waits for the initial service
check and displays its logs. It does not reboot automatically or delete
existing videos or settings.

### First-install dependencies

ADB is installed automatically for every family; you do not need to install it
manually before running the script:

| Family | Package providing ADB |
| --- | --- |
| Fedora / Nobara / Bazzite | `android-tools` |
| Arch and derivatives | `android-tools` |
| Ubuntu / Debian and derivatives | `adb` |
| openSUSE / SUSE | `android-tools` |

The installer also includes Python, HID, PySide6/Qt6, Qt Multimedia,
translations, polkit/pkexec, and the required Wayland/X11 libraries. Fedora and
Arch explicitly install native Qt Multimedia and Wayland modules; Ubuntu/SUSE
install Qt wheels and their required system libraries. RYUO USB rules are
installed and reloaded during the same process.

Before copying the application and enabling the service, the installer checks
ADB, pkexec, systemd, and udev executables, runs `adb version` without starting
its server, checks HID/Qt Multimedia imports and Spanish translations, and
reads sensors. If a dependency is missing or an executable fails, installation
stops with an error rather than reporting success. These checks do not require
ADB authorization for the AIO. Device authorization, if requested by the
firmware, is handled separately.

### Fedora and Nobara

These use `dnf` with `python3-hidapi`, `python3-pyside6`, `qt6-qtmultimedia`,
`qt6-qtwayland`, `qt6-qttranslations`, `android-tools`, and `polkit`.
Desktop Fedora/Nobara use the system Python for both the interface and the
service. Language selection remains a per-user preference.

### Bazzite and Fedora Atomic

`install.sh` detects Bazzite and `/run/ostree-booted` within the Fedora family.
If dependencies are missing from the booted deployment, they are staged using
`rpm-ostree install --idempotent`; the installer then exits without copying the
application or changing its services. Reboot manually and run
`sudo bash ./install.sh` again. If all dependencies are already present,
application installation proceeds directly.

The application uses `/usr/local` and `/etc`; `/usr/local` must be writable,
as on images where it links to `/var/usrlocal`. The installer does not unlock
`/usr`, use `apply-live`, add repositories, or install inside Distrobox.
USB service dependencies must be available on the host. RPM layering can affect
future image updates; see the
[Bazzite package layering documentation](https://docs.bazzite.gg/Installing_and_Managing_Software/rpm-ostree/).

### Arch and derivatives

The installer installs `python-hidapi`, `pyside6`, `qt6-multimedia`,
`qt6-wayland`, `qt6-translations`, `android-tools`, and `polkit`. Qt Multimedia
is included explicitly because it is an
[optional PySide6 dependency on Arch](https://archlinux.org/packages/extra/x86_64/pyside6/).
The installer uses the Cython `python-hidapi` variant exposing `hid.device`.
The driver also adapts other HID APIs for existing manual installations.

Keep the system updated before installing. The script uses
`pacman -S --needed` without separately refreshing databases, forcing a full
upgrade, or using the AUR. If databases are outdated, update normally with
`sudo pacman -Syu` before running the installer.

### Ubuntu and SUSE

APT or Zypper installs Python, ADB, polkit, and the necessary HID and desktop
libraries. PySide6 and hidapi are installed from PyPI as binary wheels into
the root-owned `/usr/local/share/ryuo-control/venv`. The installer does not use
`sudo pip` against the system Python or `--break-system-packages`. Ubuntu uses
`venv`; SUSE installs pip/virtualenv matching the actual system Python version.

The service and launchers use the same private Python environment. PySide6
wheels supply their Qt libraries and translations. Access to PyPI is required
in addition to distribution repositories. The installer does not add PPAs,
external OBS repositories, or packages from another distribution release.

### Running from the workspace

On Fedora/Arch with native dependencies:

```bash
python3 ryuo-gui.py
```

On Ubuntu/SUSE after installation:

```bash
/usr/local/share/ryuo-control/venv/bin/python ryuo-gui.py
```

Applying changes from the GUI requires the helper installed at
`/usr/local/bin/ryuo-control.py`. The interface and helper do not need the same
Python environment: the helper only uses the standard library and shared
configuration/translation modules.

## Uninstall

Close the RYUO Control window and run from the workspace:

```bash
sudo bash ./uninstall.sh
```

This works across all covered families. It stops and disables the service
before removing files, removes the application, launchers, icon, udev rule,
and private Python environment if present, and reloads systemd and udev.
If stopping the service fails, it stops before removing files.

To inspect the plan without making changes:

```bash
bash ./uninstall.sh --dry-run
```

By default, `/etc/ryuo-lcd.json` and service overrides are preserved. To remove
these system settings too:

```bash
sudo bash ./uninstall.sh --purge
```

The installer also provides a standalone uninstaller:

```bash
sudo /usr/local/bin/ryuo-uninstall
```

It accepts the same arguments and does not require keeping the workspace.
It does not remove shared packages such as ADB, Qt, Python, or polkit, or
Bazzite RPM layers. User preferences/cache, downloaded videos, and AIO files
are also preserved, even with `--purge`. See the language and preview sections
for preference and cache locations. The project repository itself is preserved.

## Usage

The interface is available in **English** and **Español**. The first launch uses
English regardless of the desktop language. Change the **Language / Idioma**
selector in the sidebar to translate the window immediately. Unsaved changes
and local video playback are preserved.

The preference is saved per user in
`~/.config/RYUOControl/RYUOControl.conf` (or under `XDG_CONFIG_HOME`), without
sudo, and restored on the next launch. It does not change `/etc/ryuo-lcd.json`
or device protocol names. During file or service operations, the selector is
disabled until completion. ADB, systemd, and other program logs remain in their
original language. Qt dialogs use native translations or those bundled with
PySide6 wheels, depending on the installation method.

1. In **Overview**, check the USB connection and service status.
2. In **Display**, select at least one video and assign sensors to the six slots.
3. Select the playback mode, colors, and CPU temperature source.
4. For brightness, click **Detect USB serial** and first verify the connection
   using `sudo adb devices`. Detecting the USB serial does not guarantee ADB authorization.
5. Click **Apply to LCD**. Settings are saved and the service restarts;
   restarting includes an initial ten-second delay.
6. In **Service → Read log**, check the sensor transmission and brightness results.

Changes to controls are not applied automatically. Closing the window leaves
the service sending data to the LCD. **Enable at boot** controls automatic
startup; **Start** changes the current service state.

## Configuration

The service reads `/etc/ryuo-lcd.json` at startup. If the file does not exist,
it uses defaults from `ryuo_config.py`. Invalid configuration produces an
error rather than silently replacing your settings.

Complete example (replace the serial number with your LCD's serial):

```json
{
  "media": ["RYUO_IV_HW_Info_04.mp4"],
  "play_mode": "Cycle",
  "brightness": 100,
  "adb_serial": "YOUR_LCD_SERIAL",
  "cpu_temp_mode": "ccd_average",
  "sensors": ["CPU Temperature", "GPU Temperature", "CPU Load", "GPU Load"],
  "title_color": "#E5252B",
  "content_color": "#FFFFFF"
}
```

Presets range from `RYUO_IV_HW_Info_01.mp4` to `RYUO_IV_HW_Info_05.mp4`.
Playback modes are `Cycle`, `Random`, and `Single`; select just one preset
to play a single video.

### Uploading a custom video

In **Display**, click **Upload and show MP4 video…** and choose a file.
ADB copies it to `/sdcard/pcMedia/` using a unique name. The serial comes from
the ADB field or USB detection. An authorized ADB connection accessible from
your session is required. If errors occur, check **Service** and run
`adb devices` in your terminal.

The video is selected immediately, replacing the previous selection.
After copying the file, the application automatically saves and applies the
settings to the LCD using the usual administrative authentication. You do not
need to select it or click **Apply to LCD** again. Other videos remain on the
device. If copying fails, the previous selection is restored. If copying
succeeds but applying settings is canceled or fails, the video stays selected
so you can retry **Apply to LCD** without uploading again.

You can subsequently select multiple videos in the list. There is a maximum
of five selected videos and an additional configuration packet size limit.
**Refresh LCD videos** retrieves MP4s from the custom video directory.
Selected videos are preserved in the configuration; refresh the LCD list to
retrieve other uploaded videos that are not selected.

Files are copied without conversion or codec verification. As a compatibility
reference: H.264 up to 1920×1080, or HEVC/H.265 with the `hvc1` tag for
2240×1080. An incompatible MP4 may copy successfully but fail to play. See the
[format documentation](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md#encode-targets).
Custom video playback must be verified on your own unit.

### Downloading videos from the AIO

In **Display → Device videos**, click **Download presets…** to copy
`/sdcard/pcMediaPreset`, or **Download custom videos…** to copy
`/sdcard/pcMedia`. Choose a destination folder. Each download creates a unique
subfolder (`ryuo-presets-…` or `ryuo-custom-…`) to avoid overwriting files.
The complete directory is copied, including other file types if present;
this does not depend on which videos are selected for playback.

Transfers use ADB as your user, without sudo or ownership changes. Your session
must be able to access the AIO through `adb devices`. Downloading does not stop
the service, delete AIO files, or change LCD settings. The interface shows the
destination on completion and ADB output in **Service**. If copying fails,
the folder and partial files remain for inspection; another download uses a
new folder.

### Deleting custom videos from the AIO

Click **Refresh LCD videos** to retrieve custom videos missing from the list.
Each custom video has a **Delete…** button that asks for confirmation before
removing it from the device. Presets have no delete button; validation also
rejects preset names, paths, and wildcards.

If the video belongs to the saved playlist, first uncheck it, select another
video, and click **Apply to LCD**. You can then delete it without leaving the
service pointing at a missing file. Once ADB confirms deletion, the entry is
removed from the interface and the same AIO's video list is queried again
automatically. Refreshing removes missing entries, adds new ones, and preserves
selection for videos still available. If only refreshing fails, the interface
reports that deletion succeeded; retry **Refresh LCD videos**. If deletion
fails, the entry remains and the error is available in **Service**.

Deletion targets only a specific MP4 file inside `/sdcard/pcMedia/`; it never
uses recursive deletion or targets `/sdcard/pcMediaPreset/`. Deletion is
permanent: download a copy first if you need to keep the video.

An empty `adb_serial` falls back to the service's `RYUO_ADB_SERIAL`, preserving
the previous setup. If neither is available, brightness is left unchanged.
Brightness is applied at service startup, rather than on every sensor update.
The `--stop` command turns off brightness only when a working ADB connection
is available.

### Sensor layout

In **Display**, the editor shows an official RYUO IV image with sensor overlays
and live local readings. Drag one sensor onto another to swap them. If the
destination is empty, the sensor moves there and its previous slot becomes
empty. Click a sensor, or focus it with Tab and press Enter, to edit it in the
integrated bar below the image. Select another measurement or **Empty · hide**.
**Hide all** leaves only the video.

The separate six-row panel has been removed. Configuration retains the six
`sysinfoDisplay` slots; only the visual editor has changed. Changes are saved
and sent when you click **Apply to LCD**.

The layout is based on `tv_title1..6` and `tv_value1..6` bounds read through
UIAutomator from the connected AIO: 01/02 on the left, 03/04/05 on the right,
and 06 near the bottom center. Projection onto the photo is approximate;
firmware retains the physical positions and sizes of its slots. The photo is
a product reference, not a capture of the playing video. Its source and
attribution are documented in [assets/README.md](assets/README.md).

### Video and sensor preview

Check the videos you want to use and choose one in the selector above the
device. Click **Play preview**. The video loops without audio, with live sensors
and the positions you are editing. You can pause it and continue rearranging
sensors before applying changes. With several videos checked, the selector
lets you preview each one; this view does not automatically play the complete
playlist or synchronize with the real AIO's current playback position.

A recently uploaded video's original local file is used while available.
Otherwise, a copy is downloaded from the AIO through ADB and stored in the
user cache:

```text
~/.cache/ryuo-control/videos/<AIO-identifier>/<filename.mp4>
```

`XDG_CACHE_HOME` is respected if set. Each AIO has a separate directory.
The application does not write videos into `/usr/local` or require sudo for
the cache. Incomplete downloads do not replace an existing valid copy.
**Refresh cached copy** downloads the file again, useful after replacing it
on the AIO. The cache is a temporary copy rather than a backup; use the download
buttons to keep videos permanently. There is no automatic size-based cleanup.
You may delete the cache while the application is closed; files will be
downloaded again when needed.

Perspective is drawn by projecting the video onto two surfaces over the photo:
the main face and curved side. This approximates the curvature without
modeling reflections, viewing angle, or exact optical distortion. Video content
is clipped to these surfaces, with sensors drawn above it. Playing, pausing,
or changing this view does not modify the AIO. Qt Multimedia must have a
decoder for the video on your system; errors appear in the preview. Actual
configuration changes only when settings are applied.

The editor swaps six predefined slots rather than using free coordinates.
See the
[protocol documentation, Widget slots](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md#widget-slots).

JSON retains six entries, including empty positions, for example:

```json
"sensors": ["CPU Temperature", "", "GPU Temperature", "", "CPU Load", ""]
```

Older configurations with fewer entries are padded with empty slots at the end,
preserving their order. Other firmware versions may need adjustments to
`DevicePreview` visual anchors.

Do not edit the Python `media` field anymore: use the interface or JSON.
After editing JSON manually, run `sudo systemctl restart ryuo-lcd.service`.

## Hardware and sensors

- Supported USB IDs: `0b05:1c76` and `1c75:1c76` (ROG RYUO IV; USB vendor may
  vary with firmware). Detection requires both vendor and product IDs;
  it does not open other ASUS devices just because they share a vendor.
- Ryzen: average of `k10temp` sensors labeled `Tccd*`, with one decimal place;
  falls back to Tctl if no CCD readings are available. Tctl can also be selected directly.
- AMD GPU: temperature, utilization, fan speed, power, and frequency from `amdgpu` in sysfs.
- NVIDIA GPU: temperature, utilization, frequency, and power through `nvidia-smi`.
- Memory: usage from `/proc/meminfo`. Time zone from `TZ` or `/etc/localtime`.
- Unimplemented measurements are sent as zero, including RAM speed. Unavailable
  fields appear as “—” in the interface when the backend reports their absence;
  numerical LCD telemetry retains zero for those fields for protocol compatibility.

### Intel and NVIDIA

Intel is detected through `coretemp` hwmon. **Package id** is preferred, or
**Physical id** on older kernels. Without a package reading, the hottest core
is used. With multiple packages, the hottest package is shown. CPU utilization
and frequency still come from procfs/cpufreq, as on AMD. CCD/Tctl modes only
affect AMD; Intel always uses package/core readings. See the
[coretemp documentation](https://docs.kernel.org/hwmon/coretemp.html).
If the kernel did not load it automatically, on supported Intel hardware you
can run `sudo modprobe coretemp` and check `--diagnose` again.

NVIDIA requires its driver module to be loaded and `nvidia-smi` available.
The application queries temperature, utilization, power, and frequency using
unitless CSV with a one-second timeout. It does not change power limits,
clocks, or fan settings. See
[NVIDIA System Management Interface](https://docs.nvidia.com/deploy/nvidia-smi/index.html).
To check the driver tools:

```bash
nvidia-smi --query-gpu=index,temperature.gpu,utilization.gpu,power.draw,clocks.gr,memory.total --format=csv,noheader,nounits
```

The application uses the graphics driver already provided by the system.
Its installer does not replace graphics drivers or manage Secure Boot.
If `nvidia-smi` is missing, install tools matching your NVIDIA driver version
using your distribution's method. Nouveau and Intel GPU metrics readers
are not yet implemented.

On hybrid systems, a valid NVIDIA reading takes priority over AMD. Within each
vendor, the GPU with the most VRAM is preferred; NVIDIA breaks ties by choosing
the lowest index. Metrics come from a single GPU, without mixing values from
two cards. If NVIDIA fails, available AMD readings are retained.

`N/A`, errors, and unsupported fields do not block LCD updates. Diagnostics list
missing fields and `nvidia-smi` problems; the GUI displays them as “—”.
NVIDIA fan speed is not implemented: `fan.speed` is a percentage, while the LCD
field used here expects RPM. Intel CPU power/voltage and GPU voltage are not
implemented. **GPU frequency** and **GPU power** can already be selected as LCD
sensors.

### Driver compatibility across distributions

The USB protocol is independent of the package manager. The driver reads Linux
`sysfs`/`procfs` interfaces and uses `ryuo_hid.py` to normalize these APIs:

| Binding | Adapted API | Intended use |
| --- | --- | --- |
| cython-hidapi | `hid.device()` | Fedora/Arch packages and Ubuntu/SUSE installer wheels |
| ctypes-based pyhidapi | `hid.Device(vid=…, pid=…)` | Alternative for manual installations |
| Classic pyhidapi | `hidapi.hid_*` functions | Compatibility with older packages, such as those in some Ubuntu releases |

The backend loads only when diagnosing or opening USB. `--sensors` works even
without HID bindings or native libraries. Load errors instruct you to run the
installer for your distribution. The handle is closed even if initial
configuration or a write fails.

Readings tolerate missing optional sensors; a GPU without `fan1_input` still
provides temperature. With multiple AMD GPUs, the one with most VRAM is
preferred, with `boot_vga` breaking ties. There are no fixed `hwmonN`/`cardN`
paths. CPU frequency averages cpufreq readings and falls back to
`/proc/cpuinfo`. Time zone detection accepts `TZ`, the `/etc/localtime` symlink,
or `/etc/timezone` when localtime is a copy. Insufficient data produces
zero/UTC rather than an invented reading.

The system service accesses HID as root. `70-ryuo-lcd.rules`, installed for
all families, grants USB access for ADB to the active local session through
`uaccess`, only for the two RYUO IV IDs. It does not grant global permissions
to other USB devices or change permissions for all hidraw devices. Access from
SSH sessions without a local seat is not guaranteed. Distributions need
udev/logind; the installer reloads rules without rebooting the computer.

Available validation includes contract tests for all three HID APIs,
sysfs/procfs fixtures, two-CCD cases, Intel coretemp, NVIDIA CSV, multiple GPUs,
time zones, Python 3.10 syntax, udev rules, and packaging into a temporary root.
Real sensor readings and detection were verified on the Fedora development
machine. Unprivileged HID opening was denied; transmission by the updated
driver running as a root service and native installations on
Arch/Ubuntu/SUSE/Bazzite still require field testing. The new Intel/NVIDIA
readers were validated using fixtures and simulated responses; testing on real
Intel/NVIDIA hardware remains outstanding. This prepares the project for
portability rather than certifying every release.

## Diagnostics and development

```bash
/usr/local/bin/ryuo-lcd --sensors
/usr/local/bin/ryuo-lcd --diagnose
sudo systemctl status ryuo-lcd.service --no-pager
sudo journalctl -u ryuo-lcd.service -n 60 --no-pager
```

From the workspace, you can also run `python3 ryuo-lcd.py --diagnose`.
The report includes Python, kernel, HID backend, detected USB devices, sensors,
and reading sources (`coretemp`, `k10temp`, `amdgpu`, or `nvidia-smi`). It does
not open the device or send USB commands; it explicitly distinguishes detection
from access/transmission testing. It does not test pump or fan performance.

For a single USB update, avoid two processes writing at the same time:

```bash
sudo systemctl stop ryuo-lcd.service
sudo /usr/local/bin/ryuo-lcd --once
sudo systemctl start ryuo-lcd.service
```

Tests require PySide6 and do not write to USB or change services:

```bash
python3 -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen python3 ryuo-gui.py --smoke-test
```

The smoke test closes the window automatically and saves a screenshot to
`/tmp/ryuo-control-preview.png`.

Main files:

- `ryuo-gui.py`: Qt6 interface and background sensor monitor.
- `ryuo-lcd.py`: HID protocol and sensor transmission.
- `ryuo_hid.py`: USB detection and HID binding adapters.
- `ryuo_config.py`: validation, defaults, and display format.
- `ryuo-control.py`: administrative actions limited to the service and its settings.
- `install.sh`: unified installer with distribution detection.
- `scripts/install-platforms.sh`: family detection and distribution dependencies.
- `scripts/install-common.sh`: shared runtime validation, files, launchers, and service setup.

## How It Works

The RYUO IV LCD is an Android 13 device connected via USB HID. Communication uses a custom binary protocol:

1. **Framing**: `0x5A` start/end markers with escape sequences (`0x5B01` = `0x5A`, `0x5B02` = `0x5B`)
2. **Header**: HTTP-like text protocol with CRLF line endings (`POST {cmdType} 1\r\n`)
3. **Payload**: JSON sensor data or screen configuration
4. **Checksum**: Single byte (`sum & 0xFF`)

Two command types are used:

- `waterBlockScreenId` — sets the display layout, video background, and which sensors to show
- `all` — sends live sensor data updates (every ~1 second)

## Protocol Details

The LCD runs an Android app called "HomeUI" that receives data via a serial service. The protocol was reverse-engineered by decompiling both the Windows Electron app (`ASUS Info Hub`) and the Android APKs (`HomeUI.apk`, `SerialService.apk`).

Key findings:

- Request state must be exactly `POST` (not `POST1.0`)
- Line endings must be `\r\n` (CRLF), not `\n`
- HID reports are 1025 bytes (1 byte report ID `0x00` + 1024 bytes data)
- LCD has a 5-second disconnect timeout — if no data is received, it reverts to standby
- `playMode` supports `Single`, `Random`, and `Cycle` (not "loop")
- Preset videos are stored on the LCD at `/sdcard/pcMediaPreset/`

## License

MIT

## Credits

Reverse-engineered with the help of Claude (Anthropic).
