#include "stats.h"

int fl_format_supported(uint32_t fmt)
{
	switch (fmt) {
	case FL_FMT_ARGB8888: case FL_FMT_XRGB8888:
	case FL_FMT_ABGR8888: case FL_FMT_XBGR8888:
	case FL_FMT_XRGB2101010: case FL_FMT_XBGR2101010:
	case FL_FMT_ARGB2101010: case FL_FMT_ABGR2101010:
		return 1;
	}
	return 0;
}

int fl_stats(const void *data, uint32_t width, uint32_t height, uint32_t stride,
	     uint32_t fmt, int threshold, struct fl_stats *out)
{
	out->max = 0;
	out->bright = 0;
	out->samples = 0;
	if (!fl_format_supported(fmt))
		return -1;
	if ((uint64_t)width * 4 > stride)
		return -1;

	int deep = (fmt == FL_FMT_XRGB2101010 || fmt == FL_FMT_XBGR2101010 ||
		    fmt == FL_FMT_ARGB2101010 || fmt == FL_FMT_ABGR2101010);

	for (uint32_t y = 0; y < height; y++) {
		const uint8_t *p = (const uint8_t *)data + (uint64_t)y * stride;
		for (uint32_t x = 0; x < width; x++, p += 4) {
			unsigned v;
			if (deep) {
				uint32_t w = (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
					     ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
				unsigned c0 = (w & 0x3ff) >> 2;
				unsigned c1 = ((w >> 10) & 0x3ff) >> 2;
				unsigned c2 = ((w >> 20) & 0x3ff) >> 2;
				v = c0 > c1 ? c0 : c1;
				if (c2 > v)
					v = c2;
			} else {
				/* bytes 0..2 are colour in either channel order,
				 * byte 3 is alpha or unused */
				v = p[0] > p[1] ? p[0] : p[1];
				if (p[2] > v)
					v = p[2];
			}
			if (v > out->max)
				out->max = v;
			if ((int)v > threshold)
				out->bright++;
		}
		out->samples += width;
	}
	return 0;
}
