# Artwork shipped with the label generator

`rpi_hwid.labels` scales each file to a few millimetres high in the corner
of a label. A `--artwork DIR` on the command line is searched first, so any
of these can be overridden, and `alphamax.png`, `digilent.png` and
`netv2.svg` can be added.

| file | what | source | terms |
|---|---|---|---|
| `raspberry-pi.svg` | the Raspberry Pi raspberry | [en.wikipedia.org File:Raspberry_Pi_Logo.svg](https://en.wikipedia.org/wiki/File:Raspberry_Pi_Logo.svg), fetched 2026-09-09 | Trademark of Raspberry Pi Ltd. Drawn only on the label of hardware that *is* a Raspberry Pi, to identify it; see the [Raspberry Pi trademark rules](https://www.raspberrypi.com/trademark-rules/). |
| `usb.svg` | the USB trident | [commons.wikimedia.org File:USB_icon.svg](https://commons.wikimedia.org/wiki/File:USB_icon.svg), fetched 2026-09-09 | Public domain, per the Commons file page. |

The Wi-Fi arcs on the wireless-adapter label and the NeTV2 mark are drawn
by the generator, not loaded from a file.
