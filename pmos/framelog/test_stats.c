#include <stdio.h>
#include <string.h>
#include "stats.h"

static int fails, checks;

#define CHECK(cond, ...) do { checks++; if (!(cond)) { fails++; \
	printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static void put8(uint8_t *img, int stride, int idx, int w, uint8_t b0, uint8_t b1, uint8_t b2, uint8_t b3)
{
	uint8_t *p = img + (idx / w) * stride + (idx % w) * 4;
	p[0] = b0; p[1] = b1; p[2] = b2; p[3] = b3;
}

static void put30(uint8_t *img, int stride, int idx, int w, uint32_t word)
{
	uint8_t *p = img + (idx / w) * stride + (idx % w) * 4;
	p[0] = word & 0xff; p[1] = (word >> 8) & 0xff; p[2] = (word >> 16) & 0xff; p[3] = word >> 24;
}

static void test_8888(uint32_t fmt, const char *name)
{
	/* 3x2 pixels, stride 16: 4 bytes of 0xff padding after each row */
	enum { W = 3, H = 2, STRIDE = 16 };
	uint8_t img[STRIDE * H];
	struct fl_stats s;

	memset(img, 0xff, sizeof img);
	put8(img, STRIDE, 0, W, 0, 0, 0, 0xff);     /* only alpha set: value 0 */
	put8(img, STRIDE, 1, W, 16, 3, 2, 0);       /* value 16: not above 16 */
	put8(img, STRIDE, 2, W, 1, 17, 0, 0x80);    /* value 17 */
	put8(img, STRIDE, 3, W, 0, 0, 120, 0);      /* value 120 */
	put8(img, STRIDE, 4, W, 4, 5, 6, 0xff);     /* value 6 */
	put8(img, STRIDE, 5, W, 200, 0, 0, 0);      /* value 200 */

	CHECK(fl_stats(img, W, H, STRIDE, fmt, 16, &s) == 0, "%s rc", name);
	CHECK(s.max == 200, "%s max %u want 200 (padding or alpha counted?)", name, s.max);
	CHECK(s.bright == 3, "%s bright %llu want 3", name, (unsigned long long)s.bright);
	CHECK(s.samples == 6, "%s samples %llu want 6", name, (unsigned long long)s.samples);

	fl_stats(img, W, H, STRIDE, fmt, 0, &s);
	CHECK(s.bright == 5, "%s thr0 bright %llu want 5", name, (unsigned long long)s.bright);
	fl_stats(img, W, H, STRIDE, fmt, 255, &s);
	CHECK(s.bright == 0, "%s thr255 bright %llu want 0", name, (unsigned long long)s.bright);

	/* every colour byte position is read, alone */
	for (int b = 0; b < 3; b++) {
		uint8_t one[4] = {0, 0, 0, 0};
		one[b] = 77;
		fl_stats(one, 1, 1, 4, fmt, 16, &s);
		CHECK(s.max == 77 && s.bright == 1, "%s byte %d max %u bright %llu", name, b, s.max,
		      (unsigned long long)s.bright);
	}
	/* alpha byte alone is never a sample value */
	uint8_t a[4] = {0, 0, 0, 0xff};
	fl_stats(a, 1, 1, 4, fmt, 0, &s);
	CHECK(s.max == 0 && s.bright == 0 && s.samples == 1, "%s alpha-only max %u bright %llu", name,
	      s.max, (unsigned long long)s.bright);
}

static void test_2101010(uint32_t fmt, const char *name)
{
	enum { W = 2, H = 1, STRIDE = 12 };
	uint8_t img[STRIDE * H];
	struct fl_stats s;

	for (int shift = 0; shift <= 20; shift += 10) {
		memset(img, 0xff, sizeof img);
		put30(img, STRIDE, 0, W, 3u << 30);                  /* alpha only */
		put30(img, STRIDE, 1, W, (3u << 30) | (1023u << shift));
		fl_stats(img, W, H, STRIDE, fmt, 16, &s);
		CHECK(s.max == 255 && s.bright == 1 && s.samples == 2,
		      "%s shift %d max %u bright %llu samples %llu", name, shift, s.max,
		      (unsigned long long)s.bright, (unsigned long long)s.samples);
	}
	/* 64 >> 2 = 16 is not above 16, 68 >> 2 = 17 is */
	memset(img, 0xff, sizeof img);
	put30(img, STRIDE, 0, W, 64u << 10);
	put30(img, STRIDE, 1, W, 68u << 20);
	fl_stats(img, W, H, STRIDE, fmt, 16, &s);
	CHECK(s.max == 17 && s.bright == 1, "%s scale max %u bright %llu", name, s.max,
	      (unsigned long long)s.bright);
}

int main(void)
{
	test_8888(FL_FMT_ARGB8888, "ARGB8888");
	test_8888(FL_FMT_XRGB8888, "XRGB8888");
	test_8888(FL_FMT_ABGR8888, "ABGR8888");
	test_8888(FL_FMT_XBGR8888, "XBGR8888");
	test_2101010(FL_FMT_XRGB2101010, "XRGB2101010");
	test_2101010(FL_FMT_XBGR2101010, "XBGR2101010");
	test_2101010(FL_FMT_ARGB2101010, "ARGB2101010");
	test_2101010(FL_FMT_ABGR2101010, "ABGR2101010");

	struct fl_stats s;
	uint8_t px[8] = {0};
	CHECK(fl_stats(px, 1, 1, 4, 0x12345678u, 16, &s) == -1, "unsupported format accepted");
	CHECK(fl_stats(px, 2, 1, 4, FL_FMT_XRGB8888, 16, &s) == -1, "short stride accepted");
	CHECK(fl_stats(px, 0, 1, 4, FL_FMT_XRGB8888, 16, &s) == 0 && s.samples == 0, "zero width");
	CHECK(FL_FMT_ABGR8888 == 0x34324241u && FL_FMT_XBGR8888 == 0x34324258u, "fourcc values");

	printf("%d checks, %d failed\n", checks, fails);
	puts(fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
