# Test data

`test-only-ssh-ed25519.pub` is a throwaway public key for `tests/test_exports.py`
(encrypting a snapshot download to an SSH key). It protects nothing. No private
key is stored here: the RSA test key is made by the test itself with `openssl`.
