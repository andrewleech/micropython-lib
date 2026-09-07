import winreg

TEST_KEY = r"Software\micropython-lib\_winreg_test"

key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, TEST_KEY, access=winreg.KEY_ALL_ACCESS)

winreg.SetValueEx(key, "a_string", 0, winreg.REG_SZ, "hello")
value, type_ = winreg.QueryValueEx(key, "a_string")
assert value == "hello"
assert type_ == winreg.REG_SZ

winreg.SetValueEx(key, "a_dword", 0, winreg.REG_DWORD, 12345)
value, type_ = winreg.QueryValueEx(key, "a_dword")
assert value == 12345
assert type_ == winreg.REG_DWORD

winreg.SetValueEx(key, "a_multi_sz", 0, winreg.REG_MULTI_SZ, ["one", "two", "three"])
value, type_ = winreg.QueryValueEx(key, "a_multi_sz")
assert value == ["one", "two", "three"]
assert type_ == winreg.REG_MULTI_SZ

winreg.DeleteValue(key, "a_string")
winreg.DeleteValue(key, "a_dword")
winreg.DeleteValue(key, "a_multi_sz")
winreg.CloseKey(key)
winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_KEY)

print("winreg tests passed")
