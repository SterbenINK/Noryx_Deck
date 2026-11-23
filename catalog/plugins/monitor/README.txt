MONITOR (DDC/CI) — the «Monitor» plugin: monitor brightness, contrast and colors from the deck (like the monitor's buttons).

Install: Settings → «Plugins» → «Catalog» → «Monitor» → «Install».
1. Turn DDC/CI on in the monitor menu (usually «System» → «DDC/CI»). Linux: sudo dnf install ddcutil and access to /dev/i2c-*
   (the i2c group).
2. In the dock — the brightness of each monitor and presets.
3. Presets: on the first start «Stock» (as it was set up) and «Night» (brightness 0); your own — set the sliders and
   «Save current» with a name. Tiles: «+» in a mode → «Plugins» — a tile per preset and «Monitor» (a screen with all
   the sliders). Pressed a preset — the monitor changes everything at once; what already matches isn't touched.
