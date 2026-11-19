/* Unit test for out.c: no compositor needed. */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "out.h"

static int fails;
#define CHECK(c) do { if (!(c)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #c); } } while (0)

int main(void)
{
	FILE *f;
	char path[] = "/tmp/test_out_XXXXXX";
	char buf[128];
	int fd;

	/* full device: the write must fail, with ENOSPC, and keep failing */
	f = fopen("/dev/full", "w");
	if (f) {
		CHECK(fl_out_line(f, "%d,%s\n", 1, "row") == -1);
		CHECK(errno == ENOSPC);
		CHECK(fl_out_line(f, "BLACK %d\n", 2) == -1);
		CHECK(fl_out_finish(f, 1) == -1);
		printf("full: ok\n");
	} else {
		printf("SKIP /dev/full not available\n");
	}

	/* regular file: success and exact bytes */
	fd = mkstemp(path);
	CHECK(fd >= 0);
	f = fdopen(fd, "w");
	CHECK(f != NULL);
	CHECK(fl_out_line(f, "MARK %llu\n", 42ull) == 0);
	CHECK(fl_out_line(f, "%d,%d,%u\n", 1, 2, 3u) == 0);
	CHECK(fl_out_finish(f, 1) == 0);
	f = fopen(path, "r");
	CHECK(f != NULL);
	memset(buf, 0, sizeof buf);
	CHECK(fread(buf, 1, sizeof buf - 1, f) > 0);
	CHECK(strcmp(buf, "MARK 42\n1,2,3\n") == 0);
	fclose(f);

	/* read-only stream: the write must fail */
	f = fopen(path, "r");
	CHECK(f != NULL);
	CHECK(fl_out_line(f, "x\n") == -1);
	CHECK(fl_out_finish(f, 1) == -1);

	/* a stream that failed once stays failed at shutdown */
	f = fopen("/dev/full", "w");
	if (f) {
		fputs("x\n", f);
		CHECK(fl_out_finish(f, 0) == -1);
		fclose(f);
	}
	unlink(path);
	if (fails) {
		printf("%d failure(s)\n", fails);
		return 1;
	}
	printf("test_out: all passed\n");
	return 0;
}
