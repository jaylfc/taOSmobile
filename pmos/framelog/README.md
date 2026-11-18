# taos-framelog

Logs one CSV line for every new frame of a region of a Wayland output, so
screen timing (frame gaps, a panel going black, a transition appearing) can be
measured against the compositor's own clock. It uses `zwlr_screencopy_manager_v1`
(v3) with `capture_output_region` and `copy_with_damage`, and `wl_shm` buffers.

**Status:** unit tests cover the pixel statistics (`make test`) and option
handling. Capture has run against the live compositor on one device (Nothing
Phone (1), sway 1.12, 120 Hz) with `--every-frame --until-black`: one line per
refresh, median gap 8.37 ms, and a page fading to black was followed frame by
frame. That the timestamps are CLOCK_MONOTONIC is consistent with `--mark` on
that device, not proven in general. `copy_with_damage` (the default mode) has
not been run against the live compositor yet.

Example, as the compositor's user, on the lock page: logging starts, the
script asks the page to close, and the tool exits at the first black frame:

    taos-framelog --mark --every-frame --until-black --duration 4 --out frames.csv

## Output

One line per completed frame, to stdout or `--out FILE`:

    t_ns,dt_us,max,bright,samples

- `t_ns`: frame timestamp from the screencopy `ready` event
  (`tv_sec_hi/lo` and `tv_nsec`), in nanoseconds, in the clock the compositor
  reports (expected to be CLOCK_MONOTONIC).
- `dt_us`: microseconds since the previous frame (0 for the first).
- `max`: brightest sample in the region, 0..255.
- `bright`: samples whose value is above `--threshold`.
- `samples`: total samples in the captured buffer.

A sample is one pixel; its value is the largest of its R, G, B channels (the
alpha or padding byte and the stride padding are ignored). 10-bit formats are
shifted down to 8 bits. Formats: ARGB8888, XRGB8888, ABGR8888, XBGR8888 and the
four 2101010 variants.

Extra lines: `MARK t_ns` with `--mark` (CLOCK_MONOTONIC at the start of
capture, so a script can relate its own timestamps), and `BLACK t_ns` with
`--until-black`. All of them go to the same destination as the CSV lines.
Setup information (output name and logical size, region, buffer format) goes to
stderr.

## Options

    --output NAME        output to capture (default: first)
    --region X,Y,WxH     region in output logical coordinates
                         (default: 4 px wide full-height strip at the centre)
    --duration SECONDS   stop after this long (default 5)
    --threshold N        bright means above N (default 16)
    --until-black        exit 0 at the first frame with bright == 0
    --mark               print the MARK line at start
    --first-timeout MS   exit 3 if no frame arrives in MS (default 1500)
    --every-frame        plain copy in a loop instead of copy_with_damage
    --overlay-cursor     include the cursor (default off)
    --out FILE           write lines to FILE instead of stdout

Exit codes: 0 ok, 1 other error (cannot connect, unsupported buffer format,
unknown output, `--until-black` ran out of time without a black frame,
unexpected protocol error), 2 usage, 3 no frame or output off, 4 protocol
missing.

## Behaviour notes

- `copy_with_damage` completes only when the output has new damage. A screen
  that does not change produces no frames; that is correct. Use
  `--every-frame` for static screens.
- An output that is powered off never delivers a frame. The tool gives up after
  `--first-timeout` and exits 3 instead of blocking.
- A `failed` event before the first frame exits 3. After frames have arrived it
  retries, and exits 3 after more than 5 consecutive failures.
- `--duration` counts from the first capture request. Ending by duration is
  exit 0 (except `--until-black`, see above, and zero frames, which is 3).
- The region is in output logical coordinates (scale and transform applied by
  the compositor). The logical size comes from `zxdg_output_v1`, with a
  fallback from the wl_output mode, scale and transform if it is absent. The
  returned buffer size is whatever the compositor sends in the `buffer` event,
  so on a scaled output `samples` is larger than the logical region.
- The dmabuf event of v3 is ignored; only shm is used. The copy request is sent
  on `buffer_done` (on `buffer` for a compositor that only offers v1 or v2).

## Build

The target is the aarch64 musl handset, so the binary is built inside an
`alpine:edge` container run on the handset itself:

    ./build-on-phone.sh USER@HOST

It copies the sources to `/tmp/framelog-build/src` on the host, runs
`make test` and `make` in the container (packages: build-base pkgconf
wayland-dev wayland-protocols wlr-protocols), leaves the binary at
`/tmp/framelog-build/taos-framelog`, and prints `readelf -d` and `ldd` from the
host. It fails if any needed library is missing on the host. The user needs
docker access; nothing is installed on the host.

`make test` builds and runs `test_stats` (no Wayland needed): max and bright
over synthetic buffers in every supported format, alpha ignored, stride padding
not counted.

## Run (as the compositor's user)

    sudo -u taos env XDG_RUNTIME_DIR=/run/user/999 WAYLAND_DISPLAY=wayland-1 \
        /tmp/framelog-build/taos-framelog --mark --duration 5 --out /tmp/frames.csv

## Open protocol questions

These are from reading the protocol, not from observation:

- Whether the `ready` timestamp is CLOCK_MONOTONIC. The protocol text says it
  is the presentation time; `--mark` gives a same-clock reference to compare.
- Whether the first `copy_with_damage` on a static screen completes at once
  (full initial damage) or waits for a change.
- Whether plain `copy` (`--every-frame`) waits for the next output frame or
  returns the last rendered one immediately; this decides its frame rate.
- How the region maps on a rotated output.
