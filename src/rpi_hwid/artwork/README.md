# Artwork shipped with the label generator

`rpi_hwid.labels` scales each file to a few millimetres high in the corner
of a label. A `--artwork DIR` on the command line is searched first, so any
of these can be overridden, and a `netv2.svg` can be added. Each maker's
mark is drawn only on the label of hardware that maker made, to identify it.

| file | what | source | terms |
|---|---|---|---|
| `raspberry-pi.svg` | the Raspberry Pi raspberry | [en.wikipedia.org File:Raspberry_Pi_Logo.svg](https://en.wikipedia.org/wiki/File:Raspberry_Pi_Logo.svg), fetched 2026-09-09 | Trademark of Raspberry Pi Ltd. Drawn only on the label of hardware that *is* a Raspberry Pi, to identify it; see the [Raspberry Pi trademark rules](https://www.raspberrypi.com/trademark-rules/). |
| `orange-pi.png` | the Orange Pi orange (the fruit with its two leaves, without the wordmark) | the [Orange Pi GitHub account](https://github.com/orangepi-xunlong) avatar (the account named "Orange Pi" that carries Xunlong's own `orangepi-build` and kernel trees), `avatars.githubusercontent.com/u/10287101`, fetched 2026-09-10 at its full 460 × 460 px and cropped to the fruit at the left of the wordmark, white made transparent (100 × 118 px); orangepi.org serves only a 107 × 38 px header logo and no vector | Trademark of Shenzhen Xunlong Software Co., Ltd. Drawn only on the label of hardware that *is* an Orange Pi, to identify it. |
| `alphamax.png` | the Alphamax "αmax libre video" wordmark, the NeTV2's maker | the [AlphamaxMedia GitHub organisation](https://github.com/AlphamaxMedia) avatar, `avatars.githubusercontent.com/u/39319945`, fetched 2026-09-09 and trimmed of its white padding (384 × 135 px); their own site's certificate had expired, so no vector original was reachable | Alphamax's mark. |
| `digilent.png` | the Digilent triangle and wordmark, the Arty's maker | the [Digilent GitHub organisation](https://github.com/Digilent) avatar, `avatars.githubusercontent.com/u/977222`, fetched 2026-09-09 and trimmed (460 × 417 px) | Digilent's mark. |
| `usb.svg` | the USB trident | [commons.wikimedia.org File:USB_icon.svg](https://commons.wikimedia.org/wiki/File:USB_icon.svg), fetched 2026-09-09 | Public domain, per the Commons file page. |

On a board label (Raspberry Pi or Orange Pi) the mark is fitted into a
box of fixed size beside the title, so the two labels keep the same
geometry however the marks are shaped. The Wi-Fi arcs on the
wireless-adapter label and the NeTV2 mark are drawn by the generator, not
loaded from a file. SQRL has no logo to be had (the
SquirrelsResearch GitHub avatar is a generated identicon and
squirrelsresearch.com is gone), so the Acorn label carries "SQRL" in type.
