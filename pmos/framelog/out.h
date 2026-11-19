/* Checked output for taos-framelog: every line is written and flushed, and a
 * failure of either is reported to the caller instead of being lost. */
#ifndef FL_OUT_H
#define FL_OUT_H

#include <stdio.h>

/* Write one formatted line to f and flush it. Returns 0 on success, -1 on
 * failure with errno set (EIO if the stream failed without setting one). */
int fl_out_line(FILE *f, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Final flush, then fclose unless close_it is 0 (stdout). Returns 0 on
 * success, -1 on failure with errno set. */
int fl_out_finish(FILE *f, int close_it);

#endif
