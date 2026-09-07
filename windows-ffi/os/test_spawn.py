import os

# spawnv/spawnl don't search PATH, so a full path is required.
cmd = os.getenv("SystemRoot") + "\\System32\\cmd.exe"

r = os.spawnv(os.P_WAIT, cmd, [cmd, "/c", "exit", "3"])
assert r == 3

r = os.spawnl(os.P_WAIT, cmd, cmd, "/c", "exit", "4")
assert r == 4

# The non-blocking modes return a handle instead, which waitpid() turns back
# into an exit code (in the POSIX status high byte) and closes.
h = os.spawnv(os.P_NOWAIT, cmd, [cmd, "/c", "exit", "5"])
pid, status = os.waitpid(h, 0)
assert pid == h, (pid, h)
assert status >> 8 == 5, status

# The handle is gone now, so a second wait must fail rather than hang.
try:
    os.waitpid(h, 0)
    raise AssertionError("waitpid() on a reaped handle should raise")
except OSError:
    pass

print("spawn tests passed")
