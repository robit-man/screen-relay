# Screen Relay

Screen Relay puts nearby wireless displays in the Ubuntu tray. Pick an Apple TV, Chromecast, or compatible smart TV on this computer, choose a screen or window in the system picker, and playback starts on the selected display.

## Install on Ubuntu 24.04

```sh
sudo apt install curl python3-venv python3-gi python3-gst-1.0 gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 gir1.2-gstreamer-1.0 \
  gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav \
  gstreamer1.0-pulseaudio libfdk-aac2 pipewire-pulse pulseaudio-utils \
  xdg-desktop-portal-gnome gnome-shell-extension-appindicator iproute2
./install.sh
```

The installer sets up a per-user Python environment and fetches a checksum-pinned [Doubletake release](https://github.com/omarroth/doubletake/releases/tag/v0.6.0) for AirPlay. Open **Screen Relay** from the application launcher. Enable the GNOME AppIndicator extension if the tray icon is missing.

Install `gnome-network-displays` with APT to add **Open Miracast picker** to the tray. GNOME Network Displays handles the Miracast connection in its own window. [GNOME Network Displays](https://gitlab.gnome.org/GNOME/gnome-network-displays)

## Cast

1. Open **Screen Relay** or use **Cast to a display** in the tray. Keep the display and computer on the same local network for AirPlay and Google Cast.
2. Choose the display by name. If prompted for an AirPlay code, enter the code shown on the TV. Set **Desktop audio** in the control window before starting if you want to change whether desktop sound is included.
3. Approve a screen or window in the GNOME picker.
4. Choose **Stop casting** in Screen Relay or the tray to disconnect an AirPlay or Google Cast session. For Miracast, disconnect in GNOME Network Displays.

| Protocol | Receivers | Playback |
| --- | --- | --- |
| AirPlay mirroring | Apple TV and compatible AirPlay displays | Doubletake sends the selected screen and optional playback audio with the native mirroring protocol. |
| Google Cast | Chromecast, Google TV and compatible TVs | The app sends a Cast load command and the TV plays the live screen. Playback can lag by a few seconds. |
| Miracast | Wi-Fi Display receivers | The tray opens GNOME’s Miracast picker. Support depends on the Wi-Fi adapter and receiver. |

Use **Refresh displays** if a TV was just switched on. Guest and hotspot networks can block discovery or traffic between devices. On Apple TV, enable AirPlay and allow devices on the same network. Receiver compatibility and pairing requirements vary by model. [Doubletake receiver compatibility](https://github.com/omarroth/doubletake/blob/v0.6.0/docs/compatibility.md)

## Implementation

Screen Relay discovers AirPlay and Google Cast video receivers using mDNS on active IPv4 LAN interfaces. Each AirPlay cast runs in its own Doubletake session with a private Unix socket. Pairing credentials are stored under `$XDG_CONFIG_HOME/screen-relay` or `~/.config/screen-relay`.

Google Cast uses the [XDG desktop ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html), PipeWire, and GStreamer for capture. The receiver pulls H.264/AAC from a temporary endpoint reachable only from its selected IPv4 address. Stop casting to close the endpoint and clear its rolling media buffer. [Google Cast streaming protocols](https://developers.google.com/cast/docs/media/streaming_protocols)

For development, create a virtual environment using `/usr/bin/python3 -m venv --system-site-packages .venv`, install `requirements.txt` into it, and run `.venv/bin/python run.py`.
