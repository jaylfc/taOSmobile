/* taos-framelog: log one CSV line per new frame of a region of a Wayland
 * output, using wlr-screencopy (copy_with_damage). See README.md. */
#define _GNU_SOURCE
#include <errno.h>
#include <getopt.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include <wayland-client.h>
#include "wlr-screencopy-unstable-v1-client-protocol.h"
#include "xdg-output-unstable-v1-client-protocol.h"
#include "stats.h"
#include "out.h"

#define EXIT_OK 0
#define EXIT_ERR 1
#define EXIT_USAGE 2
#define EXIT_NOFRAME 3
#define EXIT_PROTO 4

#define MAX_OUTPUTS 16
#define MAX_FAILS 5

_Static_assert(FL_FMT_ABGR8888 == WL_SHM_FORMAT_ABGR8888, "format value");
_Static_assert(FL_FMT_XBGR8888 == WL_SHM_FORMAT_XBGR8888, "format value");
_Static_assert(FL_FMT_XRGB2101010 == WL_SHM_FORMAT_XRGB2101010, "format value");
_Static_assert(FL_FMT_ABGR2101010 == WL_SHM_FORMAT_ABGR2101010, "format value");

struct output {
	struct wl_output *wl;
	uint32_t ver;
	struct zxdg_output_v1 *xdg;
	char *name;      /* wl_output.name (v4) or xdg_output name */
	int32_t mode_w, mode_h, scale, transform;
	int32_t lx, ly, lw, lh;
	bool have_logical;
};

static struct {
	/* options */
	const char *want_output;
	const char *out_path;
	bool have_region;
	int rx, ry, rw, rh;
	double duration;
	int threshold;
	bool until_black, mark, overlay_cursor, every_frame;
	long first_timeout_ms;

	/* wayland */
	struct wl_display *display;
	struct wl_registry *registry;
	struct wl_shm *shm;
	struct zwlr_screencopy_manager_v1 *mgr;
	uint32_t mgr_ver;
	struct zxdg_output_manager_v1 *xdg_mgr;
	struct output outputs[MAX_OUTPUTS];
	int n_outputs;
	struct output *sel;

	/* capture state */
	struct zwlr_screencopy_frame_v1 *frame;
	uint32_t f_fmt, f_w, f_h, f_stride;
	bool f_have_buf;
	struct wl_buffer *buf;
	void *map;
	size_t map_size;
	uint32_t b_fmt, b_w, b_h, b_stride;
	bool printed_format;

	FILE *out;
	uint64_t t_start, deadline, first_deadline;
	uint64_t prev_t;
	unsigned long frames;
	int fails;
	bool done;
	bool out_failed;
	int exit_code;
} A = {
	.duration = 5.0, .threshold = 16, .first_timeout_ms = 1500,
};

static uint64_t now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ull + ts.tv_nsec;
}

static void finish(int code)
{
	if (!A.done) {
		A.done = true;
		A.exit_code = code;
	}
}

/* Record an output failure once: message, then stop. The failure beats any
 * other exit code at shutdown, so a later success cannot hide it. */
static void out_fail(void)
{
	if (!A.out_failed) {
		A.out_failed = true;
		fprintf(stderr, "taos-framelog: cannot write output: %s\n", strerror(errno));
	}
	finish(EXIT_ERR);
}

/* ---- wl_output / xdg_output ---- */

static struct output *output_of(void *data) { return data; }

static void o_geometry(void *d, struct wl_output *o, int32_t x, int32_t y, int32_t pw, int32_t ph,
		       int32_t sp, const char *make, const char *model, int32_t tr)
{
	output_of(d)->transform = tr;
}
static void o_mode(void *d, struct wl_output *o, uint32_t flags, int32_t w, int32_t h, int32_t r)
{
	if (flags & WL_OUTPUT_MODE_CURRENT) {
		output_of(d)->mode_w = w;
		output_of(d)->mode_h = h;
	}
}
static void o_done(void *d, struct wl_output *o) {}
static void o_scale(void *d, struct wl_output *o, int32_t f) { output_of(d)->scale = f; }
static void o_name(void *d, struct wl_output *o, const char *name)
{
	free(output_of(d)->name);
	output_of(d)->name = strdup(name);
}
static void o_desc(void *d, struct wl_output *o, const char *desc) {}

static const struct wl_output_listener output_listener = {
	.geometry = o_geometry, .mode = o_mode, .done = o_done, .scale = o_scale,
	.name = o_name, .description = o_desc,
};

static void x_pos(void *d, struct zxdg_output_v1 *x, int32_t px, int32_t py)
{
	output_of(d)->lx = px;
	output_of(d)->ly = py;
}
static void x_size(void *d, struct zxdg_output_v1 *x, int32_t w, int32_t h)
{
	output_of(d)->lw = w;
	output_of(d)->lh = h;
	output_of(d)->have_logical = true;
}
static void x_done(void *d, struct zxdg_output_v1 *x) {}
static void x_name(void *d, struct zxdg_output_v1 *x, const char *name)
{
	if (!output_of(d)->name)
		output_of(d)->name = strdup(name);
}
static void x_desc(void *d, struct zxdg_output_v1 *x, const char *desc) {}

static const struct zxdg_output_v1_listener xdg_output_listener = {
	.logical_position = x_pos, .logical_size = x_size, .done = x_done,
	.name = x_name, .description = x_desc,
};

/* ---- registry ---- */

static uint32_t minu(uint32_t a, uint32_t b) { return a < b ? a : b; }

static void reg_global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t ver)
{
	if (!strcmp(iface, wl_shm_interface.name)) {
		A.shm = wl_registry_bind(r, name, &wl_shm_interface, 1);
	} else if (!strcmp(iface, zwlr_screencopy_manager_v1_interface.name)) {
		A.mgr_ver = minu(ver, 3);
		A.mgr = wl_registry_bind(r, name, &zwlr_screencopy_manager_v1_interface, A.mgr_ver);
	} else if (!strcmp(iface, zxdg_output_manager_v1_interface.name)) {
		/* version 2 still sends name and done; no need for more */
		A.xdg_mgr = wl_registry_bind(r, name, &zxdg_output_manager_v1_interface, minu(ver, 2));
	} else if (!strcmp(iface, wl_output_interface.name)) {
		if (A.n_outputs >= MAX_OUTPUTS)
			return;
		struct output *o = &A.outputs[A.n_outputs++];
		o->ver = minu(ver, 4);
		o->scale = 1;
		o->wl = wl_registry_bind(r, name, &wl_output_interface, o->ver);
		wl_output_add_listener(o->wl, &output_listener, o);
	}
}
static void reg_remove(void *d, struct wl_registry *r, uint32_t name) {}

static const struct wl_registry_listener registry_listener = {
	.global = reg_global, .global_remove = reg_remove,
};

/* ---- shm buffer ---- */

static void drop_buffer(void)
{
	if (A.buf)
		wl_buffer_destroy(A.buf);
	if (A.map)
		munmap(A.map, A.map_size);
	A.buf = NULL;
	A.map = NULL;
}

static bool ensure_buffer(void)
{
	if (A.buf && A.b_fmt == A.f_fmt && A.b_w == A.f_w && A.b_h == A.f_h && A.b_stride == A.f_stride)
		return true;
	drop_buffer();

	size_t size = (size_t)A.f_stride * A.f_h;
	int fd = memfd_create("taos-framelog", MFD_CLOEXEC);
	if (fd < 0) {
		fprintf(stderr, "taos-framelog: memfd_create: %s\n", strerror(errno));
		return false;
	}
	if (ftruncate(fd, size) < 0) {
		fprintf(stderr, "taos-framelog: ftruncate: %s\n", strerror(errno));
		close(fd);
		return false;
	}
	void *map = mmap(NULL, size, PROT_READ, MAP_SHARED, fd, 0);
	if (map == MAP_FAILED) {
		fprintf(stderr, "taos-framelog: mmap: %s\n", strerror(errno));
		close(fd);
		return false;
	}
	struct wl_shm_pool *pool = wl_shm_create_pool(A.shm, fd, size);
	A.buf = wl_shm_pool_create_buffer(pool, 0, A.f_w, A.f_h, A.f_stride, A.f_fmt);
	wl_shm_pool_destroy(pool);
	close(fd);
	A.map = map;
	A.map_size = size;
	A.b_fmt = A.f_fmt;
	A.b_w = A.f_w;
	A.b_h = A.f_h;
	A.b_stride = A.f_stride;
	return true;
}

static const char *format_name(uint32_t fmt, char *tmp)
{
	if (fmt == 0)
		return "ARGB8888";
	if (fmt == 1)
		return "XRGB8888";
	tmp[0] = fmt & 0xff; tmp[1] = (fmt >> 8) & 0xff;
	tmp[2] = (fmt >> 16) & 0xff; tmp[3] = (fmt >> 24) & 0xff; tmp[4] = 0;
	return tmp;
}

/* ---- screencopy frame ---- */

static void start_capture(void);

static void do_copy(void)
{
	if (!A.f_have_buf)
		return;
	if (!fl_format_supported(A.f_fmt)) {
		char tmp[5];
		fprintf(stderr, "taos-framelog: unsupported buffer format %s (0x%08x)\n",
			format_name(A.f_fmt, tmp), A.f_fmt);
		finish(EXIT_ERR);
		return;
	}
	if (!ensure_buffer()) {
		finish(EXIT_ERR);
		return;
	}
	if (!A.printed_format) {
		char tmp[5];
		fprintf(stderr, "taos-framelog: buffer %ux%u stride %u format %s\n", A.f_w, A.f_h,
			A.f_stride, format_name(A.f_fmt, tmp));
		A.printed_format = true;
	}
	if (A.every_frame)
		zwlr_screencopy_frame_v1_copy(A.frame, A.buf);
	else
		zwlr_screencopy_frame_v1_copy_with_damage(A.frame, A.buf);
}

static void f_buffer(void *d, struct zwlr_screencopy_frame_v1 *f, uint32_t fmt, uint32_t w,
		     uint32_t h, uint32_t stride)
{
	A.f_fmt = fmt;
	A.f_w = w;
	A.f_h = h;
	A.f_stride = stride;
	A.f_have_buf = true;
	/* Before v3 there is no buffer_done: the single buffer event is the cue. */
	if (A.mgr_ver < 3)
		do_copy();
}
static void f_flags(void *d, struct zwlr_screencopy_frame_v1 *f, uint32_t flags) {}
static void f_damage(void *d, struct zwlr_screencopy_frame_v1 *f, uint32_t x, uint32_t y, uint32_t w,
		     uint32_t h) {}
static void f_dmabuf(void *d, struct zwlr_screencopy_frame_v1 *f, uint32_t fmt, uint32_t w,
		     uint32_t h) {}
static void f_buffer_done(void *d, struct zwlr_screencopy_frame_v1 *f) { do_copy(); }

static void f_failed(void *d, struct zwlr_screencopy_frame_v1 *f)
{
	zwlr_screencopy_frame_v1_destroy(A.frame);
	A.frame = NULL;
	A.fails++;
	if (A.frames == 0 || A.fails > MAX_FAILS) {
		fprintf(stderr, "taos-framelog: capture failed (is the output powered off?)\n");
		finish(EXIT_NOFRAME);
		return;
	}
	start_capture();
}

static void f_ready(void *d, struct zwlr_screencopy_frame_v1 *f, uint32_t hi, uint32_t lo,
		    uint32_t nsec)
{
	uint64_t t = ((((uint64_t)hi << 32) | lo) * 1000000000ull) + nsec;
	struct fl_stats s;

	zwlr_screencopy_frame_v1_destroy(A.frame);
	A.frame = NULL;
	A.fails = 0;

	if (fl_stats(A.map, A.b_w, A.b_h, A.b_stride, A.b_fmt, A.threshold, &s) < 0) {
		fprintf(stderr, "taos-framelog: cannot measure buffer\n");
		finish(EXIT_ERR);
		return;
	}
	uint64_t dt_us = A.frames ? (t - A.prev_t) / 1000 : 0;
	A.prev_t = t;
	A.frames++;
	if (fl_out_line(A.out, "%llu,%llu,%u,%llu,%llu\n", (unsigned long long)t,
			(unsigned long long)dt_us, s.max, (unsigned long long)s.bright,
			(unsigned long long)s.samples) < 0) {
		out_fail();
		return;
	}
	if (A.until_black && s.bright == 0) {
		if (fl_out_line(A.out, "BLACK %llu\n", (unsigned long long)t) < 0) {
			out_fail();
			return;
		}
		finish(EXIT_OK);
		return;
	}
	if (!A.done)
		start_capture();
}

static const struct zwlr_screencopy_frame_v1_listener frame_listener = {
	.buffer = f_buffer, .flags = f_flags, .ready = f_ready, .failed = f_failed,
	.damage = f_damage, .linux_dmabuf = f_dmabuf, .buffer_done = f_buffer_done,
};

static void start_capture(void)
{
	A.f_have_buf = false;
	A.frame = zwlr_screencopy_manager_v1_capture_output_region(
		A.mgr, A.overlay_cursor ? 1 : 0, A.sel->wl, A.rx, A.ry, A.rw, A.rh);
	zwlr_screencopy_frame_v1_add_listener(A.frame, &frame_listener, NULL);
}

/* ---- command line ---- */

static void usage(FILE *to)
{
	fputs("usage: taos-framelog [options]\n"
	      "  --output NAME        output to capture (default: first)\n"
	      "  --region X,Y,WxH     region in output logical coordinates\n"
	      "                       (default: 4 px wide full-height strip at the centre)\n"
	      "  --duration SECONDS   stop after this long (default 5)\n"
	      "  --threshold N        a sample is bright above N, 0..255 (default 16)\n"
	      "  --until-black        exit 0 at the first frame with bright == 0,\n"
	      "                       printing a final line 'BLACK t_ns'\n"
	      "  --mark               print 'MARK t_ns' (CLOCK_MONOTONIC now) at start\n"
	      "  --first-timeout MS   exit 3 if no frame arrives in MS (default 1500)\n"
	      "  --every-frame        use plain copy in a loop (for static screens)\n"
	      "  --overlay-cursor     include the cursor (default off)\n"
	      "  --out FILE           write CSV lines to FILE instead of stdout\n"
	      "  --help               this text\n"
	      "output lines: t_ns,dt_us,max,bright,samples\n"
	      "exit codes: 0 ok, 1 other error, 2 usage, 3 no frame / output off,\n"
	      "            4 protocol missing\n", to);
}

static void die_usage(const char *fmt, const char *arg)
{
	fprintf(stderr, "taos-framelog: ");
	fprintf(stderr, fmt, arg);
	fputc('\n', stderr);
	usage(stderr);
	exit(EXIT_USAGE);
}

static long parse_long(const char *opt, const char *s, long lo, long hi)
{
	char *end;
	errno = 0;
	long v = strtol(s, &end, 10);
	if (errno || end == s || *end || v < lo || v > hi)
		die_usage("bad value for %s", opt);
	return v;
}

static void parse_args(int argc, char **argv)
{
	enum { O_OUTPUT = 256, O_REGION, O_DURATION, O_THRESHOLD, O_BLACK, O_MARK, O_FIRST, O_EVERY,
	       O_CURSOR, O_OUT, O_HELP };
	static const struct option opts[] = {
		{"output", 1, 0, O_OUTPUT}, {"region", 1, 0, O_REGION},
		{"duration", 1, 0, O_DURATION}, {"threshold", 1, 0, O_THRESHOLD},
		{"until-black", 0, 0, O_BLACK}, {"mark", 0, 0, O_MARK},
		{"first-timeout", 1, 0, O_FIRST}, {"every-frame", 0, 0, O_EVERY},
		{"overlay-cursor", 0, 0, O_CURSOR}, {"out", 1, 0, O_OUT},
		{"help", 0, 0, O_HELP}, {0, 0, 0, 0},
	};
	int c;
	while ((c = getopt_long(argc, argv, "h", opts, NULL)) != -1) {
		switch (c) {
		case O_OUTPUT: A.want_output = optarg; break;
		case O_REGION: {
			int n = 0;
			if (sscanf(optarg, "%d,%d,%d%*[xX]%d%n", &A.rx, &A.ry, &A.rw, &A.rh, &n) != 4 ||
			    optarg[n] || A.rx < 0 || A.ry < 0 || A.rw <= 0 || A.rh <= 0)
				die_usage("bad --region '%s' (want X,Y,WxH)", optarg);
			A.have_region = true;
			break;
		}
		case O_DURATION: {
			char *end;
			errno = 0;
			A.duration = strtod(optarg, &end);
			if (errno || end == optarg || *end || !(A.duration > 0))
				die_usage("bad --duration '%s'", optarg);
			break;
		}
		case O_THRESHOLD: A.threshold = parse_long("--threshold", optarg, 0, 255); break;
		case O_BLACK: A.until_black = true; break;
		case O_MARK: A.mark = true; break;
		case O_FIRST: A.first_timeout_ms = parse_long("--first-timeout", optarg, 1, 3600000); break;
		case O_EVERY: A.every_frame = true; break;
		case O_CURSOR: A.overlay_cursor = true; break;
		case O_OUT: A.out_path = optarg; break;
		case 'h':
		case O_HELP: usage(stdout); exit(EXIT_OK);
		default: usage(stderr); exit(EXIT_USAGE);
		}
	}
	if (optind < argc)
		die_usage("unexpected argument '%s'", argv[optind]);
}

/* ---- main ---- */

static void logical_fallback(struct output *o)
{
	int32_t w = o->mode_w, h = o->mode_h;
	if (o->transform & 1) { /* 90/270 and the flipped variants */
		int32_t t = w; w = h; h = t;
	}
	int32_t s = o->scale > 0 ? o->scale : 1;
	o->lw = w / s;
	o->lh = h / s;
	o->lx = o->ly = 0;
}

static int next_timeout_ms(void)
{
	uint64_t now = now_ns();
	uint64_t limit = A.frames ? A.deadline : (A.first_deadline < A.deadline ? A.first_deadline : A.deadline);
	if (now >= limit)
		return 0;
	return (int)((limit - now + 999999) / 1000000);
}

int main(int argc, char **argv)
{
	parse_args(argc, argv);

	A.display = wl_display_connect(NULL);
	if (!A.display) {
		const char *wd = getenv("WAYLAND_DISPLAY");
		const char *xr = getenv("XDG_RUNTIME_DIR");
		int e = errno;
		if (!wd && !xr)
			fprintf(stderr, "taos-framelog: cannot connect: neither WAYLAND_DISPLAY nor "
				"XDG_RUNTIME_DIR is set\n");
		else
			fprintf(stderr, "taos-framelog: cannot connect to the compositor "
				"(WAYLAND_DISPLAY=%s, XDG_RUNTIME_DIR=%s): %s\n",
				wd ? wd : "(unset)", xr ? xr : "(unset)", strerror(e));
		return EXIT_ERR;
	}

	A.registry = wl_display_get_registry(A.display);
	wl_registry_add_listener(A.registry, &registry_listener, NULL);
	if (wl_display_roundtrip(A.display) < 0) {
		fprintf(stderr, "taos-framelog: protocol error during setup\n");
		return EXIT_ERR;
	}

	if (!A.mgr || !A.shm) {
		fprintf(stderr, "taos-framelog: compositor lacks %s\n",
			!A.mgr ? "zwlr_screencopy_manager_v1" : "wl_shm");
		return EXIT_PROTO;
	}
	if (!A.every_frame && A.mgr_ver < 2) {
		fprintf(stderr, "taos-framelog: screencopy v%u has no copy_with_damage; "
			"use --every-frame\n", A.mgr_ver);
		return EXIT_PROTO;
	}
	if (A.n_outputs == 0) {
		fprintf(stderr, "taos-framelog: compositor offers no wl_output\n");
		return EXIT_PROTO;
	}

	if (A.xdg_mgr)
		for (int i = 0; i < A.n_outputs; i++) {
			A.outputs[i].xdg = zxdg_output_manager_v1_get_xdg_output(A.xdg_mgr, A.outputs[i].wl);
			zxdg_output_v1_add_listener(A.outputs[i].xdg, &xdg_output_listener, &A.outputs[i]);
		}
	if (wl_display_roundtrip(A.display) < 0) {
		fprintf(stderr, "taos-framelog: protocol error during setup\n");
		return EXIT_ERR;
	}

	for (int i = 0; i < A.n_outputs; i++) {
		struct output *o = &A.outputs[i];
		if (!o->have_logical)
			logical_fallback(o);
		if (!A.sel && (!A.want_output || (o->name && !strcmp(o->name, A.want_output))))
			A.sel = o;
	}
	if (!A.sel) {
		fprintf(stderr, "taos-framelog: no output named '%s'; available:", A.want_output);
		for (int i = 0; i < A.n_outputs; i++)
			fprintf(stderr, " %s", A.outputs[i].name ? A.outputs[i].name : "(unnamed)");
		fputc('\n', stderr);
		return EXIT_ERR;
	}
	if (A.sel->lw <= 0 || A.sel->lh <= 0) {
		fprintf(stderr, "taos-framelog: output has no usable size (powered off?)\n");
		return EXIT_NOFRAME;
	}

	if (!A.have_region) {
		A.rw = A.sel->lw < 4 ? A.sel->lw : 4;
		A.rh = A.sel->lh;
		A.rx = (A.sel->lw - A.rw) / 2;
		A.ry = 0;
	}
	fprintf(stderr, "taos-framelog: output %s %dx%d logical, region %d,%d,%dx%d, protocol v%u%s\n",
		A.sel->name ? A.sel->name : "(unnamed)", A.sel->lw, A.sel->lh, A.rx, A.ry, A.rw, A.rh,
		A.mgr_ver, A.every_frame ? ", every-frame" : "");

	A.out = stdout;
	if (A.out_path) {
		A.out = fopen(A.out_path, "w");
		if (!A.out) {
			fprintf(stderr, "taos-framelog: %s: %s\n", A.out_path, strerror(errno));
			return EXIT_ERR;
		}
	}

	A.t_start = now_ns();
	A.deadline = A.t_start + (uint64_t)(A.duration * 1e9);
	A.first_deadline = A.t_start + (uint64_t)A.first_timeout_ms * 1000000ull;
	if (A.mark) {
		if (fl_out_line(A.out, "MARK %llu\n", (unsigned long long)A.t_start) < 0)
			out_fail();
	}

	if (!A.done)
		start_capture();

	while (!A.done) {
		while (wl_display_prepare_read(A.display) != 0)
			wl_display_dispatch_pending(A.display);
		bool blocked = false;
		if (wl_display_flush(A.display) < 0) {
			if (errno != EAGAIN) {
				wl_display_cancel_read(A.display);
				fprintf(stderr, "taos-framelog: flush: %s\n", strerror(errno));
				finish(EXIT_ERR);
				break;
			}
			blocked = true;
		}

		struct pollfd pfd = { wl_display_get_fd(A.display),
				      (short)(POLLIN | (blocked ? POLLOUT : 0)), 0 };
		int tmo = next_timeout_ms();
		int r = tmo > 0 ? poll(&pfd, 1, tmo) : 0;
		if (r > 0)
			wl_display_read_events(A.display);
		else
			wl_display_cancel_read(A.display);
		if (r < 0 && errno != EINTR) {
			fprintf(stderr, "taos-framelog: poll: %s\n", strerror(errno));
			finish(EXIT_ERR);
			break;
		}
		if (wl_display_dispatch_pending(A.display) < 0) {
			fprintf(stderr, "taos-framelog: connection error: %s\n", strerror(errno));
			finish(EXIT_ERR);
			break;
		}
		if (A.done)
			break;

		uint64_t now = now_ns();
		if (A.frames == 0 && now >= A.first_deadline) {
			fprintf(stderr, "taos-framelog: no frame within %ld ms (output powered off, "
				"or nothing new on screen; try --every-frame)\n", A.first_timeout_ms);
			finish(EXIT_NOFRAME);
		} else if (now >= A.deadline) {
			if (A.frames == 0) {
				fprintf(stderr, "taos-framelog: no frame within the duration\n");
				finish(EXIT_NOFRAME);
			} else if (A.until_black) {
				fprintf(stderr, "taos-framelog: duration elapsed without a black frame\n");
				finish(EXIT_ERR);
			} else {
				finish(EXIT_OK);
			}
		}
	}

	if (A.out && fl_out_finish(A.out, A.out != stdout) < 0)
		out_fail();
	_exit(A.out_failed ? EXIT_ERR : A.exit_code);
}
