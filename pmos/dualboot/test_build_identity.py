"""build-identity.sh: the kernel release and the whole build tag are compared as literal strings."""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "build-identity.sh")


def matches(text, rel, tag):
    r = subprocess.run(
        ["sh", "-c", '. "$1"; dualboot_build_matches "$2" "$3" "$4"', "sh", LIB, text, rel, tag],
        capture_output=True,
    )
    return r.returncode == 0


def line(rel, tag):
    return (
        "Linux version %s (builder@host) (gcc (Alpine 14.2.0) 14.2.0, GNU ld (GNU Binutils) 2.43) "
        "%s SMP PREEMPT Mon Oct 26 10:00:00 UTC 2026" % (rel, tag)
    )


class BuildIdentity(unittest.TestCase):
    def test_same_build_matches(self):
        self.assertTrue(matches(line("7.2.1-r9", "#10-postmarketos-qcom"), "7.2.1-r9", "#10-postmarketos-qcom"))

    def test_build_9_does_not_accept_90(self):
        self.assertFalse(matches(line("7.2.1-r9", "#90-postmarketos-qcom"), "7.2.1-r9", "#9-postmarketos-qcom"))
        self.assertFalse(matches(line("7.2.1-r9", "#90"), "7.2.1-r9", "#9"))
        self.assertFalse(matches(line("7.2.1-r9", "#9"), "7.2.1-r9", "#90"))

    def test_same_release_different_build(self):
        self.assertFalse(matches(line("7.2.1-r9", "#10-postmarketos-qcom"), "7.2.1-r9", "#11-postmarketos-qcom"))

    def test_different_release(self):
        self.assertFalse(matches(line("7.2.1-r10", "#10-x"), "7.2.1-r1", "#10-x"))
        self.assertFalse(matches(line("7.2.1-r9", "#10-x"), "7.2.1-r90", "#10-x"))

    def test_regex_metacharacters_are_literal(self):
        self.assertFalse(matches(line("7.2.1-r9", "#10-x"), "7.2.1-r.", "#10-x"))
        self.assertFalse(matches(line("7.2.1-r9", "#10-x"), "7.2.1-r9", "#.*"))

    def test_not_a_version_line(self):
        self.assertFalse(matches("garbage", "7.2.1-r9", "#10-x"))
        self.assertFalse(matches("", "", ""))

    @unittest.skipUnless(shutil.which("strings"), "needs strings")
    def test_stream_function_against_running_kernel(self):
        rel = subprocess.check_output(["uname", "-r"], text=True).strip()
        tag = subprocess.check_output(["uname", "-v"], text=True).split()[0]

        def run(t):
            data = b"\0\0junk\0" + line(rel, t).encode() + b"\0more\0"
            r = subprocess.run(
                ["sh", "-c", '. "$1"; dualboot_stream_matches_running', "sh", LIB],
                input=data, capture_output=True,
            )
            return r.returncode == 0

        self.assertTrue(run(tag))
        self.assertFalse(run(tag + "0"))


if __name__ == "__main__":
    unittest.main()
