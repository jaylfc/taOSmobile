/* Pixel statistics for one captured frame. No Wayland dependency, so the
 * unit test can build it anywhere. */
#ifndef FL_STATS_H
#define FL_STATS_H

#include <stdint.h>

#define FL_FOURCC(a, b, c, d) \
	((uint32_t)(a) | ((uint32_t)(b) << 8) | ((uint32_t)(c) << 16) | ((uint32_t)(d) << 24))

/* Same numeric values as the wl_shm.format enum. */
#define FL_FMT_ARGB8888    0u
#define FL_FMT_XRGB8888    1u
#define FL_FMT_ABGR8888    FL_FOURCC('A', 'B', '2', '4')
#define FL_FMT_XBGR8888    FL_FOURCC('X', 'B', '2', '4')
#define FL_FMT_XRGB2101010 FL_FOURCC('X', 'R', '3', '0')
#define FL_FMT_XBGR2101010 FL_FOURCC('X', 'B', '3', '0')
#define FL_FMT_ARGB2101010 FL_FOURCC('A', 'R', '3', '0')
#define FL_FMT_ABGR2101010 FL_FOURCC('A', 'B', '3', '0')

struct fl_stats {
	unsigned max;           /* brightest colour sample, 0..255 */
	uint64_t bright;        /* samples whose value is above the threshold */
	uint64_t samples;       /* samples counted (width * height) */
};

/* Non-zero if the format can be measured. */
int fl_format_supported(uint32_t fmt);

/* A sample is one pixel and its value is the largest of its three colour
 * channels (scaled to 8 bits). The alpha / padding component is ignored and
 * so are the stride padding bytes after each row's width * 4 bytes.
 * Little-endian memory layout is assumed.
 * Returns 0, or -1 for an unsupported format or a stride below width * 4. */
int fl_stats(const void *data, uint32_t width, uint32_t height, uint32_t stride,
	     uint32_t fmt, int threshold, struct fl_stats *out);

#endif
