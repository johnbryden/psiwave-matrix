On Raspberry Pi, the text scroll effect uses pixel BDF fonts from rpi-rgb-led-matrix.

Copy one or more .bdf files here from your rpi-rgb-led-matrix clone, e.g.:
  cp /path/to/rpi-rgb-led-matrix/fonts/9x15.bdf .
  cp /path/to/rpi-rgb-led-matrix/fonts/7x13.bdf .

Preferred: 9x15.bdf (readable). Or set PSIWAVE_BDF_FONT to the full path of any .bdf file.

If no BDF is found, the effect falls back to PIL (works on screen / Windows).
