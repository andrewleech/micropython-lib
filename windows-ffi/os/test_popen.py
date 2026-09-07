import os

f = os.popen("echo hello popen")
out = f.read()
assert f.close() is None  # None means the child exited cleanly
assert out.strip() == "hello popen"

# Write mode exercises the other half of the fd redirection.
f = os.popen("sort", "w")
f.write("banana\napple\n")
assert f.close() is None

# A failing child reports its exit code out of close() instead.
f = os.popen("exit 6")
f.read()
assert f.close() == 6

# close() is idempotent, so __exit__ after an explicit close is harmless.
with os.popen("echo ctx") as f:
    assert f.read().strip() == "ctx"
    assert f.close() is None

print("popen tests passed")
