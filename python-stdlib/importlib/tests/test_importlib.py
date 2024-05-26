import sys
sys.path.append(".")
import importlib

sys.path.append("tests/imp1")
import importlib_test_data
assert importlib_test_data.test_data == "one"

sys.path.remove("tests/imp1")
sys.path.append("tests/imp2")

nm = importlib.reload(importlib_test_data)
import importlib_test_data

assert importlib_test_data.test_data == "two"
