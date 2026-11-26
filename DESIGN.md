---
version: alpha
name: Screen Relay
description: A tray control for selecting nearby wireless displays.
colors:
  primary: "#7ce5dd"
  background: "#0f1725"
  surface: "#182436"
  line: "#354a64"
  quiet: "#aabbd0"
  ink: "#e6edf7"
  fault: "#ffb4a5"
typography:
  sans:
    fontFamily: "Lato, sans-serif"
  mono:
    fontFamily: "DejaVu Sans Mono, monospace"
rounded:
  md: "7px"
  lg: "10px"
spacing:
  gutter: "24px"
components:
  button: {}
  statusCard: {}
  deviceRow: {}
---

# Screen Relay design system

## Overview

Screen Relay is a desktop utility for casting a screen to a nearby TV. The installed surfaces are the GTK console and a system tray menu. The interface uses English and delegates capture permissions to the operating system.

The status card takes its visual cue from an AV monitor with a thin cyan input edge. The tray uses the same display shape with a filled signal dot during active casting.

## Colors

The navy canvas and raised panel distinguish the status and audio controls. Cyan identifies the active signal, primary action and keyboard focus. Pale red marks the stop action. Text carries the same meaning as color.

[The palette](screenrelay/theme.py) owns the values and emits GTK style tokens. The primary and background roles map to `signal` and `night`.

## Typography

Use Lato with the platform sans family as a fallback. Use DejaVu Sans Mono for the short connection state.

## Layout

The status card comes first, then nearby receivers and desktop audio. Each receiver row has a full-width action tagged with the protocol. The casting state gives pairing and connection progress the same space and keeps Cancel or Stop within reach. The tray offers the same receiver names without opening the console first.

## Elevation & Depth

Panels use a fill and a thin border. The title bar is flat.

## Shapes

Use 7 px radii for buttons and 10 px for panels. The display mark uses a detached stand and a small filled lamp in the active state.

## Components

Use native GTK buttons, switches, entry controls and tray actions. Show a visible focus ring. Keep discovery results visible during a refresh. Pairing uses a masked input and moves keyboard focus into that input when a receiver requests a credential. Clear the credential when it is submitted or canceled.

## Do's and Don'ts

Use device names from discovery. Show the active device and protocol beside the stop action. Use the operating system’s screen picker.
