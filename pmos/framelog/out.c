#include <errno.h>
#include <stdarg.h>
#include "out.h"

int fl_out_line(FILE *f, const char *fmt, ...)
{
	va_list ap;
	int r;

	errno = 0;
	va_start(ap, fmt);
	r = vfprintf(f, fmt, ap);
	va_end(ap);
	if (r < 0 || fflush(f) != 0 || ferror(f)) {
		if (errno == 0)
			errno = EIO;
		return -1;
	}
	return 0;
}

int fl_out_finish(FILE *f, int close_it)
{
	int bad;

	errno = 0;
	bad = fflush(f) != 0 || ferror(f);
	if (close_it && fclose(f) != 0)
		bad = 1;
	if (bad && errno == 0)
		errno = EIO;
	return bad ? -1 : 0;
}
